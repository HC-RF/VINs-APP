"""Provider: Auto.dev (commercial, optional).

Included as the worked example of adding a paid vendor. It is inert unless
``AUTODEV_API_KEY`` is set, so the application runs at zero cost out of the box.

The credential is read from settings on the server and attached as a bearer
token; it is never sent to, or referenced by, the frontend.

Adapting this file to a different vendor (DataOne, CarsXE, Vehicle Databases,
ChromeData) means changing :meth:`_request` and :attr:`_FIELD_MAP`. Nothing
outside this file needs to know.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.providers.base import ProviderError, ProviderResponse, VINDecoderProvider
from app.schemas.vehicle import Confidence, FieldValue, Origin, ProviderKind
from app.services import normalize as nz

# canonical_name -> (dotted path into the vendor payload, parser, confidence)
_FIELD_MAP: dict[str, tuple[str, Any, Confidence]] = {
    "year": ("years.0.year", lambda v: nz.to_int(v, minimum=1900, maximum=2100), Confidence.HIGH),
    "make": ("make.name", nz.canonical_make, Confidence.HIGH),
    "model": ("model.name", nz.title_case, Confidence.HIGH),
    "trim": ("years.0.styles.0.trim", nz.clean, Confidence.HIGH),
    "body_type": ("years.0.styles.0.submodel.body", nz.canonical_body, Confidence.HIGH),
    "doors": ("years.0.styles.0.numDoors", lambda v: nz.to_int(v, minimum=1, maximum=8), Confidence.HIGH),
    "engine_displacement_l": ("engine.size", lambda v: nz.to_float(v, minimum=0.1, maximum=20), Confidence.HIGH),
    "engine_cylinders": ("engine.cylinder", lambda v: nz.to_int(v, minimum=1, maximum=16), Confidence.HIGH),
    "engine_configuration": ("engine.configuration", nz.title_case, Confidence.HIGH),
    "engine_type": ("engine.compressorType", nz.canonical_engine_type, Confidence.HIGH),
    "horsepower": ("engine.horsepower", lambda v: nz.to_int(v, minimum=1, maximum=2000), Confidence.HIGH),
    "torque_lb_ft": ("engine.torque", lambda v: nz.to_int(v, minimum=1, maximum=3000), Confidence.HIGH),
    "fuel": ("engine.fuelType", nz.canonical_fuel, Confidence.HIGH),
    "drivetrain": ("drivenWheels", nz.canonical_drivetrain, Confidence.HIGH),
    "transmission": ("transmission.transmissionType", nz.canonical_transmission, Confidence.HIGH),
    "transmission_speeds": ("transmission.numberOfSpeeds", lambda v: nz.to_int(v, minimum=1, maximum=12), Confidence.HIGH),
    "manufacturer": ("manufacturer", nz.title_case, Confidence.MEDIUM),
    "mpg_city": ("mpg.city", lambda v: nz.to_int(v, minimum=1, maximum=200), Confidence.HIGH),
    "mpg_highway": ("mpg.highway", lambda v: nz.to_int(v, minimum=1, maximum=200), Confidence.HIGH),
    "curb_weight_lb": ("categories.curbWeight", lambda v: nz.to_int(v, minimum=100), Confidence.MEDIUM),
}


def _dig(payload: dict, path: str) -> Any:
    """Resolve a dotted path, tolerating list indices and missing branches."""
    node: Any = payload
    for part in path.split("."):
        if node is None:
            return None
        if part.isdigit():
            if not isinstance(node, list) or int(part) >= len(node):
                return None
            node = node[int(part)]
        else:
            if not isinstance(node, dict):
                return None
            node = node.get(part)
    return node


class AutoDevProvider(VINDecoderProvider):
    name = "autodev"
    label = "Auto.dev Vehicle API"
    kind = ProviderKind.COMMERCIAL
    priority = 30
    requires_key = True
    provides = tuple(_FIELD_MAP.keys())
    description = (
        "Commercial vehicle specification API. Higher trim and equipment fidelity "
        "than free sources. Disabled until an API key is configured."
    )

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self.cost_per_call = float(getattr(settings, "autodev_cost_per_call", 0.0))

    def is_enabled(self) -> bool:
        return bool(self.settings.autodev_enabled)

    def unavailable_reason(self) -> str | None:
        if not self.is_enabled():
            return "Disabled by configuration (AUTODEV_ENABLED=false)."
        if not self.settings.autodev_api_key.strip():
            return "No API key configured (set AUTODEV_API_KEY)."
        return None

    async def _get_client(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(
                    base_url=self.settings.autodev_base_url.rstrip("/"),
                    timeout=httpx.Timeout(self.settings.autodev_timeout_seconds),
                    headers={
                        # Credential stays server-side. It is never serialised
                        # into any API response or template.
                        "Authorization": f"Bearer {self.settings.autodev_api_key.strip()}",
                        "Accept": "application/json",
                        "User-Agent": "vin-decoder/1.0",
                    },
                )
            return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _request(self, vin: str) -> tuple[dict, int]:
        client = await self._get_client()
        try:
            resp = await client.get(f"/vin/{vin}")
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"Auto.dev timed out after {self.settings.autodev_timeout_seconds}s.",
                code="TIMEOUT",
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Auto.dev unreachable: {exc}", code="NETWORK_ERROR") from exc

        if resp.status_code in (401, 403):
            raise ProviderError(
                "Auto.dev rejected the credential. Check AUTODEV_API_KEY.",
                code="AUTH_FAILED", status_code=resp.status_code,
            )
        if resp.status_code == 404:
            raise ProviderError(
                "Auto.dev has no record for this VIN.",
                code="NOT_FOUND", status_code=404,
            )
        if resp.status_code == 429:
            raise ProviderError(
                "Auto.dev quota exceeded. Falling back to free sources.",
                code="QUOTA_EXCEEDED", status_code=429,
            )
        if resp.status_code >= 500:
            raise ProviderError(
                f"Auto.dev returned {resp.status_code}.",
                code="UPSTREAM_ERROR", status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"Auto.dev rejected the request ({resp.status_code}).",
                code="BAD_REQUEST", status_code=resp.status_code,
            )

        try:
            return resp.json(), resp.status_code
        except ValueError as exc:
            raise ProviderError("Auto.dev returned a non-JSON body.", code="BAD_PAYLOAD") from exc

    async def _decode(self, vin: str, *, hint_year: int | None = None) -> ProviderResponse:
        payload, status = await self._request(vin)

        fields: dict[str, FieldValue] = {}
        for canonical, (path, parse, confidence) in _FIELD_MAP.items():
            raw = _dig(payload, path)
            value = parse(raw)
            if value is None:
                continue
            fields[canonical] = FieldValue(
                value=value,
                source=self.name,
                source_kind=self.kind,
                confidence=confidence,
                origin=Origin.ENRICHED,
                raw_value=raw,
            )

        city = fields.get("mpg_city")
        highway = fields.get("mpg_highway")
        if city and highway and "mpg_combined" not in fields:
            # EPA combined weighting: 55% city, 45% highway.
            combined = round(0.55 * float(city.value) + 0.45 * float(highway.value))
            fields["mpg_combined"] = FieldValue(
                value=int(combined),
                source=self.name,
                source_kind=self.kind,
                confidence=Confidence.LOW,
                origin=Origin.ENRICHED,
                note="Computed from city/highway using the EPA 55/45 weighting, "
                     "not a reported figure.",
            )

        if not fields:
            return ProviderResponse(
                provider=self.name, kind=self.kind, success=False,
                error="Auto.dev returned no usable fields.", error_code="NO_DATA",
                status_code=status, raw=payload,
            )
        return ProviderResponse(
            provider=self.name, kind=self.kind, success=True, fields=fields,
            raw=payload, status_code=status, cost=self.cost_per_call,
        )
