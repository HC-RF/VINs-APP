"""Provider: local specification catalog (free, offline).

The VIN encodes identity, not capability. Horsepower, torque, fuel economy and
gearbox detail have to come from somewhere else. This provider is that
somewhere else, at zero cost and zero latency, for the vehicles it covers.

It serves two purposes:

* **Enrichment** - filling fields no VIN decoder can supply (EPA MPG in
  particular, which NHTSA does not carry at all).
* **Verification** - giving the merge step a second opinion on horsepower and
  transmission so genuine disagreements between sources become visible.

A vehicle that is not in the catalog produces *no* fields. It never guesses.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any

from app.providers.base import ProviderResponse, VINDecoderProvider
from app.schemas.vehicle import Confidence, FieldValue, Origin, ProviderKind
from app.services import normalize as nz

CATALOG_PATH = Path(__file__).parent / "data" / "spec_catalog.json"

# Fields where the catalog is a genuine authority (published/EPA figures) vs
# fields it merely corroborates.
_HIGH_CONFIDENCE_FIELDS = {"mpg_city", "mpg_highway", "mpg_combined", "torque_lb_ft"}


@functools.lru_cache(maxsize=1)
def load_catalog(path: str | None = None) -> dict[str, Any]:
    target = Path(path) if path else CATALOG_PATH
    with target.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _key(value: object) -> str:
    """Loose key for make/model comparison: lowercase, alphanumerics only."""
    text = nz.clean(value)
    return re.sub(r"[^a-z0-9]+", "", text.lower()) if text else ""


class SpecCatalogProvider(VINDecoderProvider):
    name = "spec_catalog"
    label = "Specification Catalog (local)"
    kind = ProviderKind.LOCAL
    priority = 20          # runs after identity is established
    cost_per_call = 0.0
    requires_key = False
    provides = (
        "horsepower", "torque_lb_ft", "engine_type", "engine_configuration",
        "transmission", "transmission_speeds", "mpg_city", "mpg_highway",
        "mpg_combined", "curb_weight_lb", "towing_capacity_lb", "zero_to_sixty_s",
    )
    description = (
        "Curated specification data bundled with the application. Supplies fuel "
        "economy and drivetrain detail that VIN decoders do not carry, and acts "
        "as a second opinion on horsepower. Free and offline."
    )

    def __init__(self, settings, catalog_path: str | None = None) -> None:
        super().__init__(settings)
        self._catalog_path = catalog_path

    def is_enabled(self) -> bool:
        return bool(self.settings.spec_catalog_enabled)

    @property
    def catalog(self) -> dict[str, Any]:
        return load_catalog(self._catalog_path)

    # --- Matching ------------------------------------------------------------

    def find_entry(
        self,
        *,
        make: str | None,
        model: str | None,
        year: int | None,
        engine_l: float | None = None,
        trim: str | None = None,
    ) -> tuple[dict[str, Any] | None, int]:
        """Best-matching catalog entry and its specificity score.

        Returns ``(None, 0)`` when nothing matches - the caller must then
        report the fields as unavailable rather than approximating.
        """
        make_key, model_key = _key(make), _key(model)
        if not make_key or not model_key or year is None:
            return None, 0

        trim_key = (nz.clean(trim) or "").lower()
        best: dict[str, Any] | None = None
        best_score = 0

        for entry in self.catalog.get("entries", []):
            if _key(entry.get("make")) != make_key:
                continue
            if _key(entry.get("model")) != model_key:
                continue
            if not (int(entry.get("year_from", 0)) <= year <= int(entry.get("year_to", 9999))):
                continue

            score = 1  # make + model + year

            entry_engine = entry.get("engine_l")
            if entry_engine is not None:
                if engine_l is None:
                    continue                       # entry is engine-specific; we cannot confirm
                if abs(float(entry_engine) - float(engine_l)) > 0.15:
                    continue                       # different engine, not this entry
                score += 2

            patterns = entry.get("trim_match") or []
            if patterns:
                if not trim_key or not any(p.lower() in trim_key for p in patterns):
                    continue
                score += 2

            if score > best_score:
                best, best_score = entry, score

        return best, best_score

    # --- Decode --------------------------------------------------------------

    async def _decode(self, vin: str, *, hint_year: int | None = None) -> ProviderResponse:
        """Not usable standalone: identity must be known first.

        The decode service calls :meth:`enrich` with the identity resolved from
        the free VIN decoders. Calling this directly is a programming error, so
        it fails loudly rather than silently returning nothing.
        """
        return ProviderResponse(
            provider=self.name, kind=self.kind, success=False,
            error="The specification catalog requires a decoded make/model/year; "
                  "call enrich() after identity resolution.",
            error_code="NEEDS_IDENTITY",
        )

    async def enrich(
        self,
        *,
        make: str | None,
        model: str | None,
        year: int | None,
        engine_l: float | None = None,
        trim: str | None = None,
    ) -> ProviderResponse:
        entry, score = self.find_entry(
            make=make, model=model, year=year, engine_l=engine_l, trim=trim
        )
        if entry is None:
            return ProviderResponse(
                provider=self.name, kind=self.kind, success=False,
                error=f"No catalog entry for {year or '?'} {make or '?'} {model or '?'}.",
                error_code="NO_CATALOG_ENTRY",
            )

        # A make/model/year-only match is weaker evidence than one pinned to a
        # specific engine or trim, and its confidence reflects that.
        base_confidence = Confidence.MEDIUM if score >= 3 else Confidence.LOW

        fields: dict[str, FieldValue] = {}
        for name, value in (entry.get("specs") or {}).items():
            if value is None:
                continue
            confidence = (
                Confidence.HIGH
                if name in _HIGH_CONFIDENCE_FIELDS and score >= 3
                else base_confidence
            )
            fields[name] = FieldValue(
                value=value,
                source=self.name,
                source_kind=self.kind,
                confidence=confidence,
                origin=Origin.ENRICHED,
                raw_value=value,
                note=(
                    f"Catalog entry: {entry.get('year_from')}-{entry.get('year_to')} "
                    f"{entry.get('make')} {entry.get('model')}"
                    + (f" {entry['engine_l']}L" if entry.get("engine_l") else "")
                ),
            )

        return ProviderResponse(
            provider=self.name, kind=self.kind, success=bool(fields), fields=fields,
            raw={"matched_entry": entry, "specificity": score},
            error=None if fields else "Catalog entry contained no usable specs.",
            error_code=None if fields else "NO_DATA",
            cost=0.0,
        )
