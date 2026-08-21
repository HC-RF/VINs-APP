"""Canonical vehicle model and the field-level provenance system.

Every decoded value travels with four things attached: where it came from, how
much we trust it, when it was obtained, and whether it was read straight out of
the VIN or enriched from an external database. Nothing in the system stores a
bare value.
"""

from __future__ import annotations

import datetime as _dt
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --- Enumerations -----------------------------------------------------------

class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"

    @property
    def rank(self) -> int:
        return _CONFIDENCE_RANK[self]


_CONFIDENCE_RANK = {
    Confidence.HIGH: 3,
    Confidence.MEDIUM: 2,
    Confidence.LOW: 1,
    Confidence.UNKNOWN: 0,
}


class Origin(str, Enum):
    """How a value came to be known."""

    VIN_DECODED = "VIN_DECODED"    # read directly out of the 17 characters
    ENRICHED = "ENRICHED"          # looked up in an external specification source


class ProviderKind(str, Enum):
    FREE = "FREE"
    COMMERCIAL = "COMMERCIAL"
    LOCAL = "LOCAL"


class DecodeStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"            # decoded, but core fields are missing
    INVALID_VIN = "INVALID_VIN"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"


# --- Field-level records ----------------------------------------------------

class FieldValue(BaseModel):
    """A single value as reported by a single source."""

    model_config = ConfigDict(use_enum_values=False)

    value: Any
    source: str
    source_kind: ProviderKind = ProviderKind.FREE
    confidence: Confidence = Confidence.MEDIUM
    origin: Origin = Origin.ENRICHED
    retrieved_at: _dt.datetime = Field(default_factory=lambda: _dt.datetime.now(_dt.UTC))
    raw_value: Any = None
    note: str | None = None


class ResolvedField(BaseModel):
    """The winning value for a field, plus everything that disagreed with it."""

    field: str
    label: str
    value: Any = None
    source: str | None = None
    source_kind: ProviderKind | None = None
    confidence: Confidence = Confidence.UNKNOWN
    origin: Origin | None = None
    retrieved_at: _dt.datetime | None = None
    disputed: bool = False
    alternatives: list[FieldValue] = Field(default_factory=list)
    note: str | None = None

    @property
    def available(self) -> bool:
        return self.value is not None


class Discrepancy(BaseModel):
    """Two or more sources reporting materially different values."""

    field: str
    label: str
    selected_value: Any
    selected_source: str
    conflicting: list[FieldValue]
    severity: str = "warning"      # "warning" | "critical"
    message: str


# --- Provider plumbing ------------------------------------------------------

class ProviderCallResult(BaseModel):
    """Bookkeeping for one call to one provider."""

    provider: str
    kind: ProviderKind
    success: bool
    status_code: int | None = None
    latency_ms: int = 0
    error: str | None = None
    error_code: str | None = None
    cost: float = 0.0
    cached: bool = False
    fields_returned: int = 0
    raw_response: dict[str, Any] | None = None


# --- Aggregate vehicle record ----------------------------------------------

class EngineSpec(BaseModel):
    displacement_l: float | None = None
    type: str | None = None            # aspiration / configuration, e.g. "Turbocharged"
    configuration: str | None = None   # e.g. "Inline"
    cylinders: int | None = None
    model: str | None = None
    horsepower: int | None = None
    torque_lb_ft: int | None = None


class VehicleRecord(BaseModel):
    """The normalized, merged answer for one VIN."""

    model_config = ConfigDict(use_enum_values=False)

    # Identity and validity
    vin: str
    input: str | None = None
    valid: bool = False
    status: DecodeStatus = DecodeStatus.OK
    check_digit_valid: bool | None = None

    # Headline fields (flattened for convenience; provenance lives in `fields`)
    year: int | None = None
    make: str | None = None
    model: str | None = None
    trim: str | None = None
    series: str | None = None
    body_type: str | None = None
    vehicle_type: str | None = None
    doors: int | None = None

    engine: EngineSpec = Field(default_factory=EngineSpec)
    horsepower: int | None = None
    fuel: str | None = None
    fuel_secondary: str | None = None
    drivetrain: str | None = None
    transmission: str | None = None
    transmission_speeds: int | None = None

    manufacturer: str | None = None
    plant_country: str | None = None
    plant_city: str | None = None
    plant_company: str | None = None

    mpg_city: int | None = None
    mpg_highway: int | None = None
    mpg_combined: int | None = None

    # Provenance and quality
    fields: dict[str, ResolvedField] = Field(default_factory=dict)
    discrepancies: list[Discrepancy] = Field(default_factory=list)
    confidence: dict[str, str] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    provider_calls: list[ProviderCallResult] = Field(default_factory=list)

    # Diagnostics
    issues: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    cached: bool = False
    cache_age_seconds: int | None = None
    decoded_at: _dt.datetime = Field(default_factory=lambda: _dt.datetime.now(_dt.UTC))
    total_cost: float = 0.0

    @field_validator("vin")
    @classmethod
    def _upper_vin(cls, v: str) -> str:
        return (v or "").upper()


# --- API request / response envelopes ---------------------------------------

class DecodeRequest(BaseModel):
    vins: list[str] = Field(default_factory=list, description="VINs to decode.")
    text: str | None = Field(
        default=None,
        description="Raw pasted text; split on newlines/commas/semicolons. "
                    "Merged with `vins` when both are given.",
    )
    refresh: bool = Field(default=False, description="Bypass the cache for these VINs.")
    verify: bool = Field(
        default=False,
        description="Query every configured provider, including commercial ones, "
                    "to cross-check fields. Costs money when commercial keys are set.",
    )


class DecodeSummary(BaseModel):
    requested: int = 0
    decoded: int = 0
    invalid: int = 0
    failed: int = 0
    duplicates_removed: list[str] = Field(default_factory=list)
    from_cache: int = 0
    provider_calls: int = 0
    total_cost: float = 0.0
    elapsed_ms: int = 0
    discrepancy_count: int = 0


class DecodeResponse(BaseModel):
    results: list[VehicleRecord] = Field(default_factory=list)
    summary: DecodeSummary = Field(default_factory=DecodeSummary)


class ProviderInfo(BaseModel):
    """Safe-to-expose description of a provider. Never includes credentials."""

    name: str
    label: str
    kind: ProviderKind
    enabled: bool
    available: bool
    priority: int
    cost_per_call: float
    provides: list[str]
    description: str
    requires_key: bool = False
    unavailable_reason: str | None = None


# --- Human-readable field labels -------------------------------------------

FIELD_LABELS: dict[str, str] = {
    "year": "Model Year",
    "make": "Make",
    "model": "Model",
    "trim": "Trim",
    "series": "Series",
    "body_type": "Body Type",
    "vehicle_type": "Vehicle Type",
    "doors": "Doors",
    "engine_displacement_l": "Engine Displacement (L)",
    "engine_type": "Engine Type",
    "engine_configuration": "Engine Configuration",
    "engine_cylinders": "Cylinders",
    "engine_model": "Engine Model",
    "horsepower": "Horsepower",
    "torque_lb_ft": "Torque (lb-ft)",
    "fuel": "Fuel Type",
    "fuel_secondary": "Secondary Fuel",
    "drivetrain": "Drivetrain",
    "transmission": "Transmission",
    "transmission_speeds": "Transmission Speeds",
    "manufacturer": "Manufacturer",
    "plant_country": "Country of Manufacture",
    "plant_city": "Plant City",
    "plant_state": "Plant State",
    "plant_company": "Plant Company",
    "wmi_country": "WMI Region (manufacturer registration)",
    "engine_manufacturer": "Engine Manufacturer",
    "mpg_city": "MPG (City)",
    "mpg_highway": "MPG (Highway)",
    "mpg_combined": "MPG (Combined)",
    "gvwr": "GVWR",
    "seats": "Seats",
    "abs": "ABS",
    "esc": "Electronic Stability Control",
    "traction_control": "Traction Control",
    "tpms": "TPMS",
    "airbag_front": "Front Airbags",
    "airbag_side": "Side Airbags",
    "airbag_curtain": "Curtain Airbags",
    "backup_camera": "Backup Camera",
    "forward_collision_warning": "Forward Collision Warning",
    "steering_location": "Steering Location",
    "base_price_usd": "Base Price (USD, at launch)",
    "top_speed_mph": "Top Speed (mph)",
    "wheelbase_in": "Wheelbase (in)",
    "wheels": "Wheels",
    "axles": "Axles",
    "seat_rows": "Seat Rows",
    "curb_weight_lb": "Curb Weight (lb)",
    "towing_capacity_lb": "Towing Capacity (lb)",
    "zero_to_sixty_s": "0-60 mph (s)",
    "series2": "Series (secondary)",
    "note": "Note",
}

# Fields shown in the headline table, in display order.
CORE_FIELDS: tuple[str, ...] = (
    "year", "make", "model", "trim", "engine_displacement_l", "engine_cylinders",
    "horsepower", "fuel", "drivetrain", "transmission",
)

# Fields the decoder considers essential when judging whether free sources were
# sufficient (drives the cost-optimisation policy).
REQUIRED_FIELDS: tuple[str, ...] = ("year", "make", "model", "fuel", "engine_cylinders")


def label_for(field_name: str) -> str:
    return FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())
