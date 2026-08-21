"""Persistence for decoded vehicles, provenance, discrepancies and usage.

The cache lives here. :func:`get_cached_vehicle` is the function that decides
whether an external API gets called at all, so its TTL handling is the
difference between a free lookup and a paid one.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ApiUsage,
    ProviderResponseLog,
    SourceDiscrepancy,
    Vehicle,
    VehicleSpecification,
    VinLookup,
    utcnow,
)
from app.schemas.vehicle import VehicleRecord

log = logging.getLogger(__name__)

#: Raw payloads above this size are dropped rather than stored; they are a
#: debugging aid, not worth unbounded disk.
MAX_RAW_PAYLOAD_BYTES = 64_000


def _aware(value: _dt.datetime | None) -> _dt.datetime | None:
    """SQLite loses tzinfo on round-trip; restore UTC so arithmetic works."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=_dt.UTC)


# --- Cache ------------------------------------------------------------------

def get_cached_vehicle(
    session: Session, vin: str, *, ttl_hours: int
) -> tuple[VehicleRecord | None, int | None]:
    """Return ``(record, age_seconds)`` for a fresh cache entry, else ``(None, None)``.

    ``ttl_hours <= 0`` means entries never expire.
    """
    row = session.execute(select(Vehicle).where(Vehicle.vin == vin)).scalar_one_or_none()
    if row is None or not row.record:
        return None, None

    decoded_at = _aware(row.last_decoded_at) or utcnow()
    age = int((utcnow() - decoded_at).total_seconds())

    if ttl_hours > 0 and age > ttl_hours * 3600:
        return None, None

    try:
        record = VehicleRecord.model_validate(row.record)
    except Exception as exc:  # noqa: BLE001 - a stale schema must not break decoding
        log.warning("Discarding unreadable cache entry for %s: %s", vin, exc)
        return None, None

    record.cached = True
    record.cache_age_seconds = age
    return record, age


def save_vehicle(session: Session, record: VehicleRecord) -> Vehicle:
    """Upsert the merged record plus its field-level provenance."""
    payload = record.model_dump(mode="json")
    # Raw provider payloads are logged separately; keeping them inside the
    # cached record too would bloat every cache read.
    for call in payload.get("provider_calls", []):
        call.pop("raw_response", None)

    row = session.execute(select(Vehicle).where(Vehicle.vin == record.vin)).scalar_one_or_none()
    if row is None:
        row = Vehicle(vin=record.vin, first_decoded_at=utcnow(), lookup_count=0)
        session.add(row)

    row.valid = bool(record.valid)
    row.check_digit_valid = record.check_digit_valid
    row.status = record.status.value if hasattr(record.status, "value") else str(record.status)
    row.year = record.year
    row.make = record.make
    row.model = record.model
    row.trim = record.trim
    row.body_type = record.body_type
    row.vehicle_type = record.vehicle_type
    row.engine_displacement_l = record.engine.displacement_l
    row.engine_cylinders = record.engine.cylinders
    row.engine_type = record.engine.type
    row.horsepower = record.horsepower
    row.fuel = record.fuel
    row.drivetrain = record.drivetrain
    row.transmission = record.transmission
    row.manufacturer = record.manufacturer
    row.plant_country = record.plant_country
    row.overall_confidence = record.confidence.get("overall", "UNKNOWN")
    row.discrepancy_count = len(record.discrepancies)
    row.record = payload
    row.last_decoded_at = utcnow()
    row.lookup_count = (row.lookup_count or 0) + 1
    row.total_cost = (row.total_cost or 0.0) + float(record.total_cost or 0.0)

    session.flush()

    _replace_specifications(session, row, record)
    _replace_discrepancies(session, row, record)
    return row


def _replace_specifications(session: Session, row: Vehicle, record: VehicleRecord) -> None:
    session.query(VehicleSpecification).filter(
        VehicleSpecification.vehicle_id == row.id
    ).delete(synchronize_session=False)

    for name, field in record.fields.items():
        if field.value is None:
            continue
        number = float(field.value) if isinstance(field.value, (int, float)) and not isinstance(field.value, bool) else None
        session.add(
            VehicleSpecification(
                vehicle_id=row.id,
                field_name=name,
                label=field.label,
                value_text=str(field.value),
                value_number=number,
                source_name=field.source or "unknown",
                source_kind=field.source_kind.value if field.source_kind else "FREE",
                confidence=field.confidence.value,
                origin=field.origin.value if field.origin else "ENRICHED",
                disputed=bool(field.disputed),
                note=field.note,
                alternatives=[a.model_dump(mode="json") for a in field.alternatives],
                retrieved_at=field.retrieved_at or utcnow(),
            )
        )


def _replace_discrepancies(session: Session, row: Vehicle, record: VehicleRecord) -> None:
    session.query(SourceDiscrepancy).filter(
        SourceDiscrepancy.vehicle_id == row.id
    ).delete(synchronize_session=False)

    for d in record.discrepancies:
        session.add(
            SourceDiscrepancy(
                vehicle_id=row.id,
                field_name=d.field,
                label=d.label,
                selected_value=str(d.selected_value),
                selected_source=d.selected_source,
                conflicting=[c.model_dump(mode="json") for c in d.conflicting],
                severity=d.severity,
                message=d.message,
            )
        )


def invalidate_vin(session: Session, vin: str) -> bool:
    row = session.execute(select(Vehicle).where(Vehicle.vin == vin)).scalar_one_or_none()
    if row is None:
        return False
    session.delete(row)
    return True


# --- Logging ----------------------------------------------------------------

def log_provider_response(session: Session, vin: str, call: Any) -> None:
    raw = getattr(call, "raw_response", None)
    if raw is not None:
        try:
            if len(json.dumps(raw)) > MAX_RAW_PAYLOAD_BYTES:
                raw = {"_truncated": True, "_reason": "payload exceeded storage limit"}
        except (TypeError, ValueError):
            raw = None

    session.add(
        ProviderResponseLog(
            vin=vin,
            provider_name=call.provider,
            success=bool(call.success),
            status_code=call.status_code,
            latency_ms=int(call.latency_ms or 0),
            error=call.error,
            error_code=call.error_code,
            fields_returned=int(call.fields_returned or 0),
            cost=float(call.cost or 0.0),
            raw_response=raw,
        )
    )


def log_lookup(
    session: Session,
    *,
    vin: str,
    raw_input: str | None,
    valid: bool,
    status: str,
    cache_hit: bool,
    provider_calls: int,
    cost: float,
    elapsed_ms: int,
    client_ip: str | None = None,
    error: str | None = None,
) -> None:
    session.add(
        VinLookup(
            vin=vin[:32], raw_input=(raw_input or "")[:64] or None, valid=valid,
            status=status, cache_hit=cache_hit, provider_calls=provider_calls,
            cost=cost, elapsed_ms=elapsed_ms, client_ip=client_ip, error=error,
        )
    )


def record_usage(
    session: Session,
    provider_name: str,
    *,
    success: bool,
    cost: float = 0.0,
    latency_ms: int = 0,
    cache_hit: bool = False,
) -> None:
    """Increment today's counters for a provider."""
    today = _dt.datetime.combine(_dt.date.today(), _dt.time.min, tzinfo=_dt.UTC)
    row = session.execute(
        select(ApiUsage).where(
            ApiUsage.provider_name == provider_name, ApiUsage.usage_date == today
        )
    ).scalar_one_or_none()
    if row is None:
        row = ApiUsage(provider_name=provider_name, usage_date=today)
        session.add(row)
        session.flush()

    if cache_hit:
        row.cache_hits += 1
        return
    row.calls += 1
    row.successes += 1 if success else 0
    row.failures += 0 if success else 1
    row.total_cost += float(cost or 0.0)
    row.total_latency_ms += int(latency_ms or 0)


def commercial_calls_today(session: Session, provider_names: list[str]) -> int:
    if not provider_names:
        return 0
    today = _dt.datetime.combine(_dt.date.today(), _dt.time.min, tzinfo=_dt.UTC)
    total = session.execute(
        select(func.coalesce(func.sum(ApiUsage.calls), 0)).where(
            ApiUsage.provider_name.in_(provider_names), ApiUsage.usage_date == today
        )
    ).scalar_one()
    return int(total or 0)


# --- Reads for the UI -------------------------------------------------------

def recent_lookups(session: Session, limit: int = 20) -> list[dict]:
    """Distinct recently-decoded vehicles, newest first."""
    rows = session.execute(
        select(Vehicle).order_by(Vehicle.last_decoded_at.desc()).limit(limit)
    ).scalars().all()
    return [
        {
            "vin": r.vin,
            "year": r.year,
            "make": r.make,
            "model": r.model,
            "trim": r.trim,
            "confidence": r.overall_confidence,
            "discrepancies": r.discrepancy_count,
            "last_decoded_at": (_aware(r.last_decoded_at) or utcnow()).isoformat(),
            "lookup_count": r.lookup_count,
        }
        for r in rows
    ]


def get_vehicle_record(session: Session, vin: str) -> VehicleRecord | None:
    row = session.execute(select(Vehicle).where(Vehicle.vin == vin)).scalar_one_or_none()
    if row is None or not row.record:
        return None
    try:
        return VehicleRecord.model_validate(row.record)
    except Exception:  # noqa: BLE001
        return None


def usage_summary(session: Session, days: int = 30) -> dict:
    since = utcnow() - _dt.timedelta(days=days)
    rows = session.execute(
        select(
            ApiUsage.provider_name,
            func.sum(ApiUsage.calls),
            func.sum(ApiUsage.successes),
            func.sum(ApiUsage.failures),
            func.sum(ApiUsage.cache_hits),
            func.sum(ApiUsage.total_cost),
            func.sum(ApiUsage.total_latency_ms),
        )
        .where(ApiUsage.usage_date >= since)
        .group_by(ApiUsage.provider_name)
    ).all()

    providers = []
    for name, calls, ok, fail, hits, cost, latency in rows:
        calls = int(calls or 0)
        providers.append(
            {
                "provider": name,
                "calls": calls,
                "successes": int(ok or 0),
                "failures": int(fail or 0),
                "cache_hits": int(hits or 0),
                "total_cost": round(float(cost or 0.0), 4),
                "avg_latency_ms": int((latency or 0) / calls) if calls else 0,
            }
        )
    providers.sort(key=lambda p: -p["calls"])

    total_lookups = session.execute(
        select(func.count()).select_from(VinLookup).where(VinLookup.created_at >= since)
    ).scalar_one()
    cache_hits = session.execute(
        select(func.count()).select_from(VinLookup).where(
            VinLookup.created_at >= since, VinLookup.cache_hit.is_(True)
        )
    ).scalar_one()
    vehicles_cached = session.execute(select(func.count()).select_from(Vehicle)).scalar_one()

    total_lookups = int(total_lookups or 0)
    cache_hits = int(cache_hits or 0)
    spend = round(sum(p["total_cost"] for p in providers), 4)

    return {
        "window_days": days,
        "total_lookups": total_lookups,
        "cache_hits": cache_hits,
        "cache_hit_rate": round(cache_hits / total_lookups, 3) if total_lookups else 0.0,
        "vehicles_cached": int(vehicles_cached or 0),
        "total_cost": spend,
        "providers": providers,
    }
