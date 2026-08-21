"""Provider: the VIN itself.

Zero cost, zero latency, always available, and the highest-confidence source
in the system for the handful of facts the 17 characters actually encode:
model year, manufacturer and country of manufacture.

This provider exists so those facts are attributed honestly. When NHTSA and the
VIN agree on the model year, that agreement is visible; when they disagree, the
discrepancy is surfaced rather than hidden behind whichever source happened to
be queried first.
"""

from __future__ import annotations

from app.providers.base import ProviderResponse, VINDecoderProvider
from app.schemas.vehicle import Confidence, FieldValue, Origin, ProviderKind
from app.vin.validate import analyze_vin


class LocalVinProvider(VINDecoderProvider):
    name = "vin_structure"
    label = "VIN Structure (ISO 3779)"
    kind = ProviderKind.LOCAL
    priority = 5           # runs first; its fields are definitionally correct
    cost_per_call = 0.0
    requires_key = False
    provides = ("year", "manufacturer", "wmi_country")
    description = (
        "Decoded directly from the 17 VIN characters using ISO 3779 and "
        "49 CFR 565. No network call, no cost."
    )

    def is_enabled(self) -> bool:
        return True

    async def _decode(self, vin: str, *, hint_year: int | None = None) -> ProviderResponse:
        analysis = analyze_vin(vin)
        fields: dict[str, FieldValue] = {}

        def add(name: str, value, confidence: Confidence, note: str | None = None) -> None:
            if value is None:
                return
            fields[name] = FieldValue(
                value=value,
                source=self.name,
                source_kind=self.kind,
                confidence=confidence,
                origin=Origin.VIN_DECODED,
                raw_value=value,
                note=note,
            )

        if not analysis.valid:
            return ProviderResponse(
                provider=self.name, kind=self.kind, success=False,
                error="; ".join(i.message for i in analysis.errors) or "Invalid VIN.",
                error_code="INVALID_VIN",
                raw=analysis.to_dict(),
            )

        # The model year is only unambiguous when position 7 follows the North
        # American convention; a failing check digit hints the VIN may not.
        year_confidence = (
            Confidence.HIGH if analysis.check_digit_valid else Confidence.MEDIUM
        )
        add(
            "year", analysis.model_year, year_confidence,
            note=None if analysis.check_digit_valid
            else "Check digit did not validate; model year derived from position 10 only.",
        )
        add("manufacturer", analysis.manufacturer, Confidence.HIGH)
        # Deliberately NOT `plant_country`: the WMI records the country the
        # manufacturer is *registered* in, which is often not where the car was
        # assembled (a "WA1" Audi is registered in Germany but assembled in
        # Mexico). Conflating the two would manufacture a false discrepancy.
        add("wmi_country", analysis.country, Confidence.HIGH)

        return ProviderResponse(
            provider=self.name,
            kind=self.kind,
            success=True,
            fields=fields,
            raw=analysis.to_dict(),
            latency_ms=0,
            cost=0.0,
        )
