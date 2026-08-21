"""SQLAlchemy models.

The same models run on PostgreSQL and on the SQLite dev fallback. JSON columns
use the portable ``JSON`` type; on PostgreSQL the DDL in ``schema_postgres.sql``
promotes them to ``JSONB`` with the appropriate indexes.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


class Base(DeclarativeBase):
    pass


class DataSource(Base):
    """A provider the system knows about. Rows are upserted at startup."""

    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)     # FREE/COMMERCIAL/LOCAL
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    cost_per_call: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Vehicle(Base):
    """One row per VIN: the merged, canonical answer.

    This is the cache. A VIN present here and inside its TTL is served without
    touching any external provider - which is the whole cost-control story.
    """

    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vin: Mapped[str] = mapped_column(String(17), unique=True, nullable=False, index=True)

    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    check_digit_valid: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OK")

    year: Mapped[int | None] = mapped_column(Integer, index=True)
    make: Mapped[str | None] = mapped_column(String(64), index=True)
    model: Mapped[str | None] = mapped_column(String(128), index=True)
    trim: Mapped[str | None] = mapped_column(String(128))
    body_type: Mapped[str | None] = mapped_column(String(64))
    vehicle_type: Mapped[str | None] = mapped_column(String(64))

    engine_displacement_l: Mapped[float | None] = mapped_column(Float)
    engine_cylinders: Mapped[int | None] = mapped_column(Integer)
    engine_type: Mapped[str | None] = mapped_column(String(64))
    horsepower: Mapped[int | None] = mapped_column(Integer)
    fuel: Mapped[str | None] = mapped_column(String(48), index=True)
    drivetrain: Mapped[str | None] = mapped_column(String(16))
    transmission: Mapped[str | None] = mapped_column(String(48))

    manufacturer: Mapped[str | None] = mapped_column(String(128))
    plant_country: Mapped[str | None] = mapped_column(String(64))

    overall_confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    discrepancy_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: The full serialized VehicleRecord, so a cache hit reconstructs the exact
    #: response - provenance, alternatives and all - without re-merging.
    record: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    first_decoded_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_decoded_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow, index=True
    )
    lookup_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    specifications: Mapped[list["VehicleSpecification"]] = relationship(
        back_populates="vehicle", cascade="all, delete-orphan", lazy="selectin"
    )
    discrepancies: Mapped[list["SourceDiscrepancy"]] = relationship(
        back_populates="vehicle", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_vehicles_make_model_year", "make", "model", "year"),
    )


class VehicleSpecification(Base):
    """One row per resolved field: the field-level confidence record.

    Keeping this normalized (rather than only inside ``vehicles.record``) is
    what makes questions like "which fields do we most often have to buy?"
    answerable in SQL.
    """

    __tablename__ = "vehicle_specifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )

    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(128))
    value_text: Mapped[str | None] = mapped_column(Text)
    value_number: Mapped[float | None] = mapped_column(Float)

    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="FREE")
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="ENRICHED")
    disputed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(Text)

    #: Losing candidates, kept so the audit trail survives.
    alternatives: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    retrieved_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    vehicle: Mapped[Vehicle] = relationship(back_populates="specifications")

    __table_args__ = (
        UniqueConstraint("vehicle_id", "field_name", name="uq_spec_vehicle_field"),
        Index("ix_spec_field_confidence", "field_name", "confidence"),
    )


class SourceDiscrepancy(Base):
    """A recorded disagreement between two or more providers."""

    __tablename__ = "source_discrepancies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )

    field_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String(128))
    selected_value: Mapped[str | None] = mapped_column(Text)
    selected_source: Mapped[str | None] = mapped_column(String(64))
    conflicting: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")
    message: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    vehicle: Mapped[Vehicle] = relationship(back_populates="discrepancies")


class ProviderResponseLog(Base):
    """Raw provider payloads, retained for debugging and re-normalization.

    When a normalization bug is found, the fix can be replayed against stored
    responses instead of re-querying (and re-paying for) every VIN.
    """

    __tablename__ = "provider_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vin: Mapped[str] = mapped_column(String(17), nullable=False, index=True)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(48))
    fields_returned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    raw_response: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )


class VinLookup(Base):
    """Audit log: every decode request, cached or not."""

    __tablename__ = "vin_lookups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vin: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    raw_input: Mapped[str | None] = mapped_column(String(64))
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OK")
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    client_ip: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )


class ApiUsage(Base):
    """Per-provider, per-day call and spend counters.

    Backs both the usage dashboard and the daily commercial-call ceiling.
    """

    __tablename__ = "api_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    usage_date: Mapped[_dt.date] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("provider_name", "usage_date", name="uq_usage_provider_date"),
    )
