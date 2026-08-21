"""The ``VINDecoderProvider`` abstraction.

Every data source - free, commercial or local - implements this one interface.
The decode service knows nothing about HTTP, API keys or vendor payload shapes;
it only knows how to ask a provider for a VIN and receive
``dict[field_name, FieldValue]`` back.

Adding a commercial vendor later is a new subclass plus one registry entry.
Nothing else in the system changes.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field

from app.schemas.vehicle import (
    Confidence,
    FieldValue,
    Origin,
    ProviderCallResult,
    ProviderInfo,
    ProviderKind,
)


@dataclass(slots=True)
class ProviderResponse:
    """What one provider returned for one VIN."""

    provider: str
    kind: ProviderKind
    success: bool
    fields: dict[str, FieldValue] = field(default_factory=dict)
    raw: dict | None = None
    error: str | None = None
    error_code: str | None = None
    status_code: int | None = None
    latency_ms: int = 0
    cost: float = 0.0

    def to_call_result(self) -> ProviderCallResult:
        return ProviderCallResult(
            provider=self.provider,
            kind=self.kind,
            success=self.success,
            status_code=self.status_code,
            latency_ms=self.latency_ms,
            error=self.error,
            error_code=self.error_code,
            cost=self.cost,
            fields_returned=len(self.fields),
            raw_response=self.raw,
        )


class ProviderError(Exception):
    """Provider failed in a way the caller should surface, not swallow."""

    def __init__(self, message: str, *, code: str = "PROVIDER_ERROR", status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class VINDecoderProvider(abc.ABC):
    """Base class for all VIN data sources."""

    #: Stable identifier used in the database and API responses.
    name: str = "provider"
    #: Human-readable name for the UI.
    label: str = "Provider"
    #: FREE / COMMERCIAL / LOCAL. Drives the cost-optimisation policy.
    kind: ProviderKind = ProviderKind.FREE
    #: Lower runs first. Also breaks ties when two sources have equal confidence.
    priority: int = 100
    #: USD per call, for spend reporting.
    cost_per_call: float = 0.0
    #: Canonical field names this provider can supply.
    provides: tuple[str, ...] = ()
    #: Shown in the provider list in the UI.
    description: str = ""
    #: True when the provider needs a credential to function.
    requires_key: bool = False

    def __init__(self, settings) -> None:
        self.settings = settings

    # --- Capability reporting ------------------------------------------------

    def is_enabled(self) -> bool:
        """Whether the operator has switched this provider on."""
        return True

    def unavailable_reason(self) -> str | None:
        """Why the provider cannot be used right now, or None if it can."""
        if not self.is_enabled():
            return "Disabled by configuration."
        return None

    def is_available(self) -> bool:
        return self.unavailable_reason() is None

    def info(self) -> ProviderInfo:
        reason = self.unavailable_reason()
        return ProviderInfo(
            name=self.name,
            label=self.label,
            kind=self.kind,
            enabled=self.is_enabled(),
            available=reason is None,
            priority=self.priority,
            cost_per_call=self.cost_per_call,
            provides=list(self.provides),
            description=self.description,
            requires_key=self.requires_key,
            unavailable_reason=reason,
        )

    # --- The one method subclasses must implement ---------------------------

    @abc.abstractmethod
    async def _decode(self, vin: str, *, hint_year: int | None = None) -> ProviderResponse:
        """Fetch and normalize data for a single VIN."""

    async def decode(self, vin: str, *, hint_year: int | None = None) -> ProviderResponse:
        """Public entry point: times the call and converts failures to responses.

        A provider raising is never allowed to abort a decode; the failure is
        recorded against that provider and the remaining sources continue.
        """
        started = time.perf_counter()
        try:
            response = await self._decode(vin, hint_year=hint_year)
        except ProviderError as exc:
            response = ProviderResponse(
                provider=self.name, kind=self.kind, success=False,
                error=str(exc), error_code=exc.code, status_code=exc.status_code,
            )
        except Exception as exc:  # noqa: BLE001 - deliberately broad
            response = ProviderResponse(
                provider=self.name, kind=self.kind, success=False,
                error=f"{type(exc).__name__}: {exc}", error_code="PROVIDER_EXCEPTION",
            )
        if not response.latency_ms:
            response.latency_ms = int((time.perf_counter() - started) * 1000)
        if response.success and not response.cost:
            response.cost = self.cost_per_call
        return response

    async def decode_many(
        self, vins: list[str], *, hints: dict[str, int | None] | None = None
    ) -> dict[str, ProviderResponse]:
        """Decode several VINs at once.

        The default implementation fans out to :meth:`decode` concurrently.
        Providers with a native batch endpoint override this to collapse N
        HTTP round-trips into one - the difference between 100 requests and 2
        when a user pastes a full inventory list.
        """
        import asyncio

        hints = hints or {}
        semaphore = asyncio.Semaphore(getattr(self.settings, "decode_concurrency", 8))

        async def one(vin: str) -> tuple[str, ProviderResponse]:
            async with semaphore:
                return vin, await self.decode(vin, hint_year=hints.get(vin))

        pairs = await asyncio.gather(*(one(v) for v in vins))
        return dict(pairs)

    async def aclose(self) -> None:
        """Release network resources. Overridden by HTTP-backed providers."""

    # --- Helper for subclasses ----------------------------------------------

    def make_field(
        self,
        value,
        *,
        confidence: Confidence = Confidence.MEDIUM,
        origin: Origin = Origin.ENRICHED,
        raw_value=None,
        note: str | None = None,
    ) -> FieldValue:
        return FieldValue(
            value=value,
            source=self.name,
            source_kind=self.kind,
            confidence=confidence,
            origin=origin,
            raw_value=raw_value,
            note=note,
        )
