"""Provider: NHTSA vPIC (free, no API key).

The US National Highway Traffic Safety Administration publishes the VIN
patterns that manufacturers are legally required to file. It is free, has no
quota, needs no credential, and is the reason this application can be developed
and operated at zero API cost.

Its native batch endpoint decodes up to 50 VINs per HTTP request, which is what
makes bulk lookup cheap.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.providers.base import ProviderError, ProviderResponse, VINDecoderProvider
from app.schemas.vehicle import Confidence, FieldValue, Origin, ProviderKind
from app.services import normalize as nz

#: vPIC accepts at most 50 VINs per batch request.
BATCH_LIMIT = 50

# canonical_name -> (vpic_key, parser, confidence, origin)
#
# `origin` distinguishes facts vPIC reads out of the manufacturer-filed VIN
# pattern (effectively VIN-decoded) from facts it stores alongside the pattern
# and which vary within it (trim, horsepower, equipment) - those are enrichment.
_VIN_PATTERN = Origin.VIN_DECODED
_LOOKUP = Origin.ENRICHED

_FIELD_MAP: dict[str, tuple[str, Any, Confidence, Origin]] = {
    "year": ("ModelYear", lambda v: nz.to_int(v, minimum=1900, maximum=2100), Confidence.HIGH, _VIN_PATTERN),
    "make": ("Make", nz.canonical_make, Confidence.HIGH, _VIN_PATTERN),
    "model": ("Model", nz.title_case, Confidence.HIGH, _VIN_PATTERN),
    "trim": ("Trim", nz.clean, Confidence.MEDIUM, _LOOKUP),
    "series": ("Series", nz.clean, Confidence.MEDIUM, _LOOKUP),
    "body_type": ("BodyClass", nz.canonical_body, Confidence.HIGH, _VIN_PATTERN),
    "vehicle_type": ("VehicleType", nz.title_case, Confidence.HIGH, _VIN_PATTERN),
    "doors": ("Doors", lambda v: nz.to_int(v, minimum=1, maximum=8), Confidence.HIGH, _VIN_PATTERN),
    "engine_displacement_l": ("DisplacementL", lambda v: nz.to_float(v, minimum=0.1, maximum=20), Confidence.HIGH, _VIN_PATTERN),
    "engine_configuration": ("EngineConfiguration", nz.title_case, Confidence.HIGH, _VIN_PATTERN),
    "engine_cylinders": ("EngineCylinders", lambda v: nz.to_int(v, minimum=1, maximum=16), Confidence.HIGH, _VIN_PATTERN),
    "engine_model": ("EngineModel", nz.clean, Confidence.MEDIUM, _LOOKUP),
    "engine_manufacturer": ("EngineManufacturer", nz.clean, Confidence.MEDIUM, _LOOKUP),
    "horsepower": ("EngineHP", lambda v: nz.to_int(v, minimum=1, maximum=2000), Confidence.MEDIUM, _LOOKUP),
    "fuel": ("FuelTypePrimary", nz.canonical_fuel, Confidence.HIGH, _VIN_PATTERN),
    "fuel_secondary": ("FuelTypeSecondary", nz.canonical_fuel, Confidence.MEDIUM, _LOOKUP),
    "drivetrain": ("DriveType", nz.canonical_drivetrain, Confidence.HIGH, _VIN_PATTERN),
    "transmission": ("TransmissionStyle", nz.canonical_transmission, Confidence.MEDIUM, _LOOKUP),
    "transmission_speeds": ("TransmissionSpeeds", lambda v: nz.to_int(v, minimum=1, maximum=12), Confidence.MEDIUM, _LOOKUP),
    "manufacturer": ("Manufacturer", nz.title_case, Confidence.HIGH, _VIN_PATTERN),
    "plant_country": ("PlantCountry", nz.title_case, Confidence.HIGH, _VIN_PATTERN),
    "plant_city": ("PlantCity", nz.title_case, Confidence.HIGH, _VIN_PATTERN),
    "plant_state": ("PlantState", nz.title_case, Confidence.MEDIUM, _VIN_PATTERN),
    "plant_company": ("PlantCompanyName", nz.title_case, Confidence.MEDIUM, _VIN_PATTERN),
    "gvwr": ("GVWR", nz.clean, Confidence.MEDIUM, _LOOKUP),
    "seats": ("Seats", lambda v: nz.to_int(v, minimum=1, maximum=60), Confidence.MEDIUM, _LOOKUP),
    "seat_rows": ("SeatRows", lambda v: nz.to_int(v, minimum=1, maximum=10), Confidence.MEDIUM, _LOOKUP),
    "abs": ("ABS", nz.to_bool_label, Confidence.MEDIUM, _LOOKUP),
    "esc": ("ESC", nz.to_bool_label, Confidence.MEDIUM, _LOOKUP),
    "traction_control": ("TractionControl", nz.to_bool_label, Confidence.MEDIUM, _LOOKUP),
    "tpms": ("TPMS", nz.clean, Confidence.MEDIUM, _LOOKUP),
    "airbag_front": ("AirBagLocFront", nz.clean, Confidence.MEDIUM, _LOOKUP),
    "airbag_side": ("AirBagLocSide", nz.clean, Confidence.MEDIUM, _LOOKUP),
    "airbag_curtain": ("AirBagLocCurtain", nz.clean, Confidence.MEDIUM, _LOOKUP),
    "backup_camera": ("RearVisibilitySystem", nz.to_bool_label, Confidence.MEDIUM, _LOOKUP),
    "forward_collision_warning": ("ForwardCollisionWarning", nz.to_bool_label, Confidence.MEDIUM, _LOOKUP),
    "steering_location": ("SteeringLocation", nz.clean, Confidence.MEDIUM, _VIN_PATTERN),
    "base_price_usd": ("BasePrice", lambda v: nz.to_int(v, minimum=1), Confidence.LOW, _LOOKUP),
    "top_speed_mph": ("TopSpeedMPH", lambda v: nz.to_int(v, minimum=1, maximum=400), Confidence.LOW, _LOOKUP),
    "wheelbase_in": ("WheelBaseShort", lambda v: nz.to_float(v, minimum=20, maximum=400), Confidence.MEDIUM, _LOOKUP),
    "wheels": ("Wheels", lambda v: nz.to_int(v, minimum=2, maximum=20), Confidence.MEDIUM, _VIN_PATTERN),
    "axles": ("Axles", lambda v: nz.to_int(v, minimum=1, maximum=10), Confidence.MEDIUM, _VIN_PATTERN),
}

# vPIC error codes that mean "nothing useful came back".
_FATAL_ERROR_CODES = {"6", "7", "11", "400"}


class NhtsaProvider(VINDecoderProvider):
    name = "nhtsa_vpic"
    label = "NHTSA vPIC"
    kind = ProviderKind.FREE
    priority = 10
    cost_per_call = 0.0
    requires_key = False
    provides = tuple(_FIELD_MAP.keys())
    description = (
        "US Department of Transportation VIN database. Free, unlimited, no key. "
        "Authoritative for manufacturer-filed VIN pattern data."
    )

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    def is_enabled(self) -> bool:
        return bool(self.settings.nhtsa_enabled)

    async def _get_client(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(
                    base_url=self.settings.nhtsa_base_url.rstrip("/"),
                    timeout=httpx.Timeout(self.settings.nhtsa_timeout_seconds),
                    headers={"Accept": "application/json", "User-Agent": "vin-decoder/1.0"},
                )
            return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # --- Payload -> canonical fields ----------------------------------------

    def _map_payload(self, payload: dict) -> dict[str, FieldValue]:
        fields: dict[str, FieldValue] = {}
        for canonical, (key, parse, confidence, origin) in _FIELD_MAP.items():
            raw = payload.get(key)
            value = parse(raw)
            if value is None:
                continue          # absent stays absent - never a placeholder
            fields[canonical] = FieldValue(
                value=value,
                source=self.name,
                source_kind=self.kind,
                confidence=confidence,
                origin=origin,
                raw_value=raw,
            )

        # Aspiration is not a first-class vPIC field; it appears in the free-text
        # engine notes for many vehicles.
        aspiration = self._infer_aspiration(payload)
        if aspiration is not None:
            fields["engine_type"] = FieldValue(
                value=aspiration,
                source=self.name,
                source_kind=self.kind,
                confidence=Confidence.LOW,
                origin=_LOOKUP,
                raw_value=payload.get("OtherEngineInfo") or payload.get("Turbo"),
                note="Inferred from engine notes rather than a dedicated field.",
            )
        return fields

    @staticmethod
    def _infer_aspiration(payload: dict) -> str | None:
        turbo = nz.clean(payload.get("Turbo"))
        if turbo:
            low = turbo.lower()
            if low in {"yes", "standard", "true"}:
                return "Turbocharged"
            if low in {"no", "false"}:
                return "Naturally Aspirated"
        notes = " ".join(
            str(payload.get(k) or "") for k in ("OtherEngineInfo", "EngineModel", "Note")
        ).lower()
        if "supercharg" in notes and "turbo" in notes:
            return "Twin-Charged"
        if "turbo" in notes:
            return "Turbocharged"
        if "supercharg" in notes:
            return "Supercharged"
        return None

    @staticmethod
    def _error_note(payload: dict) -> tuple[bool, str | None]:
        """Return (is_fatal, human note) from the vPIC error fields."""
        codes = {c.strip() for c in str(payload.get("ErrorCode") or "").split(",") if c.strip()}
        text = nz.clean(payload.get("ErrorText"))
        if not codes or codes == {"0"}:
            return False, None
        fatal = bool(codes & _FATAL_ERROR_CODES)
        return fatal, text

    def _response_from_payload(self, vin: str, payload: dict, status: int) -> ProviderResponse:
        fatal, note = self._error_note(payload)
        fields = self._map_payload(payload)

        if fatal and not fields:
            return ProviderResponse(
                provider=self.name, kind=self.kind, success=False,
                error=note or "vPIC could not decode this VIN.",
                error_code="NOT_FOUND", status_code=status, raw=payload,
            )
        if not fields:
            return ProviderResponse(
                provider=self.name, kind=self.kind, success=False,
                error=note or "vPIC returned no usable fields for this VIN.",
                error_code="NO_DATA", status_code=status, raw=payload,
            )
        if note:
            for fv in fields.values():
                if fv.note is None:
                    fv.note = note
        return ProviderResponse(
            provider=self.name, kind=self.kind, success=True,
            fields=fields, raw=payload, status_code=status,
        )

    # --- Single ------------------------------------------------------------

    async def _decode(self, vin: str, *, hint_year: int | None = None) -> ProviderResponse:
        client = await self._get_client()
        params: dict[str, Any] = {"format": "json"}
        if hint_year:
            params["modelyear"] = hint_year
        try:
            resp = await client.get(f"/DecodeVinValuesExtended/{vin}", params=params)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"NHTSA vPIC timed out after {self.settings.nhtsa_timeout_seconds}s.",
                code="TIMEOUT",
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"NHTSA vPIC unreachable: {exc}", code="NETWORK_ERROR") from exc

        if resp.status_code >= 500:
            raise ProviderError(
                f"NHTSA vPIC returned {resp.status_code}.",
                code="UPSTREAM_ERROR", status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"NHTSA vPIC rejected the request ({resp.status_code}).",
                code="BAD_REQUEST", status_code=resp.status_code,
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError("NHTSA vPIC returned a non-JSON body.", code="BAD_PAYLOAD") from exc

        results = body.get("Results") or []
        if not results:
            raise ProviderError("NHTSA vPIC returned no results.", code="NOT_FOUND")
        return self._response_from_payload(vin, results[0], resp.status_code)

    # --- Batch -------------------------------------------------------------

    async def decode_many(
        self, vins: list[str], *, hints: dict[str, int | None] | None = None
    ) -> dict[str, ProviderResponse]:
        """Decode via the native batch endpoint, 50 VINs per HTTP request."""
        if not vins:
            return {}
        hints = hints or {}
        chunks = [vins[i:i + BATCH_LIMIT] for i in range(0, len(vins), BATCH_LIMIT)]
        out: dict[str, ProviderResponse] = {}
        for chunk in chunks:
            out.update(await self._decode_batch(chunk, hints))
        return out

    async def _decode_batch(
        self, vins: list[str], hints: dict[str, int | None]
    ) -> dict[str, ProviderResponse]:
        import time

        client = await self._get_client()
        data = ";".join(
            f"{vin},{hints[vin]}" if hints.get(vin) else vin for vin in vins
        )
        started = time.perf_counter()
        try:
            resp = await client.post(
                "/DecodeVINValuesBatch/", data={"format": "json", "data": data}
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            # A failed batch must not lose the whole request: fall back to
            # individual calls so one bad VIN cannot poison the others.
            if len(vins) == 1:
                return {vins[0]: await self.decode(vins[0], hint_year=hints.get(vins[0]))}
            return await super().decode_many(vins, hints=hints)

        latency = int((time.perf_counter() - started) * 1000)
        per_vin_latency = max(1, latency // max(1, len(vins)))

        by_vin: dict[str, dict] = {}
        for payload in body.get("Results") or []:
            key = str(payload.get("VIN") or "").upper()
            if key:
                by_vin[key] = payload

        out: dict[str, ProviderResponse] = {}
        for vin in vins:
            payload = by_vin.get(vin.upper())
            if payload is None:
                out[vin] = ProviderResponse(
                    provider=self.name, kind=self.kind, success=False,
                    error="VIN missing from the vPIC batch response.",
                    error_code="NOT_FOUND", latency_ms=per_vin_latency,
                )
                continue
            response = self._response_from_payload(vin, payload, resp.status_code)
            response.latency_ms = per_vin_latency
            out[vin] = response
        return out
