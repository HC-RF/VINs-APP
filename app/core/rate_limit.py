"""Per-client rate limiting for the public API.

A sliding-window counter held in process memory. Adequate for a single
instance; behind multiple workers or replicas, swap :class:`SlidingWindowLimiter`
for a Redis-backed counter - the middleware does not care which it talks to.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = max(1, limit)
        self.window = max(1, window_seconds)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int, int]:
        """Return ``(allowed, remaining, retry_after_seconds)``."""
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            if len(bucket) >= self.limit:
                retry_after = max(1, int(bucket[0] + self.window - now) + 1)
                return False, 0, retry_after

            bucket.append(now)
            return True, self.limit - len(bucket), 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def prune(self) -> None:
        """Drop empty buckets so idle clients do not accumulate forever."""
        cutoff = time.monotonic() - self.window
        with self._lock:
            for key in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
                del self._hits[key]


def client_key(request: Request) -> str:
    """Identify the caller.

    ``X-Forwarded-For`` is only trusted when the app is behind a proxy that
    sets it; taking the leftmost entry unconditionally would let any client
    spoof its identity and bypass the limit.
    """
    if request.app.state.settings.is_production:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies the limiter to ``/api/`` routes only."""

    def __init__(self, app, limiter: SlidingWindowLimiter, enabled: bool = True,
                 exempt_paths: tuple[str, ...] = ()) -> None:
        super().__init__(app)
        self.limiter = limiter
        self.enabled = enabled
        self.exempt_paths = exempt_paths
        self._requests_since_prune = 0

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not self.enabled or not path.startswith("/api/") or path in self.exempt_paths:
            return await call_next(request)

        allowed, remaining, retry_after = self.limiter.check(client_key(request))

        self._requests_since_prune += 1
        if self._requests_since_prune >= 1000:
            self._requests_since_prune = 0
            self.limiter.prune()

        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self.limiter.limit),
                    "X-RateLimit-Remaining": "0",
                },
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": (
                            f"Rate limit exceeded: {self.limiter.limit} requests per "
                            f"{self.limiter.window}s. Retry in {retry_after}s."
                        ),
                        "retry_after": retry_after,
                    }
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.limiter.limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
