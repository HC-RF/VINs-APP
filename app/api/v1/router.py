"""API v1 routes."""

from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.errors import InvalidInput, NotFound, TooManyItems
from app.db import repository as repo
from app.db.base import get_db
from app.providers.registry import get_registry
from app.schemas.vehicle import DecodeRequest, DecodeResponse, VehicleRecord
from app.services.compare_service import build_comparison
from app.services.decode_service import get_decode_service
from app.services.export_service import export_filename, to_csv, to_xlsx
from app.vin.validate import analyze_vin, normalize_vin, parse_vin_list

router = APIRouter(prefix="/api/v1", tags=["vin"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _collect_vins(payload: DecodeRequest, settings: Settings) -> tuple[list[str], list[str]]:
    """Merge the `vins` array and pasted `text` into one deduplicated list."""
    combined = " ".join(
        [*(payload.vins or []), payload.text or ""]
    ).strip()
    if not combined:
        raise InvalidInput(
            "No VINs supplied. Provide a `vins` array, a `text` block, or both."
        )

    vins, duplicates = parse_vin_list(combined, limit=settings.max_vins_per_request + 1)
    if not vins:
        raise InvalidInput("No VIN-like tokens were found in the request.")
    if len(vins) > settings.max_vins_per_request:
        raise TooManyItems(settings.max_vins_per_request, len(vins))
    return vins, duplicates


# --- Decode -----------------------------------------------------------------

@router.post("/decode", response_model=DecodeResponse, summary="Decode one or many VINs")
async def decode(
    request: Request,
    payload: DecodeRequest = Body(...),
    settings: Settings = Depends(get_settings),
) -> DecodeResponse:
    """Decode VINs, returning normalized records with per-field provenance.

    Free sources are used first; commercial providers are called only when a
    required field is still missing or ``verify`` is set.
    """
    vins, duplicates = _collect_vins(payload, settings)
    service = get_decode_service()
    return await service.decode_many(
        vins,
        refresh=payload.refresh,
        verify=payload.verify,
        duplicates=duplicates,
        client_ip=_client_ip(request),
    )


@router.get("/decode/{vin}", response_model=VehicleRecord, summary="Decode a single VIN")
async def decode_single(
    request: Request,
    vin: str,
    refresh: bool = Query(False, description="Bypass the cache."),
    verify: bool = Query(False, description="Cross-check against every provider."),
) -> VehicleRecord:
    normalized = normalize_vin(vin)
    if not normalized:
        raise InvalidInput("No VIN supplied.")
    service = get_decode_service()
    return await service.decode_one(
        normalized, refresh=refresh, verify=verify, client_ip=_client_ip(request)
    )


@router.get("/validate/{vin}", summary="Validate a VIN without calling any provider")
async def validate_only(vin: str) -> dict:
    """Structure, charset, check digit and directly-decoded facts. Always free."""
    return analyze_vin(normalize_vin(vin)).to_dict()


# --- Stored vehicles --------------------------------------------------------

@router.get("/vehicles/recent", summary="Recently decoded vehicles")
async def recent(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    return {"results": repo.recent_lookups(db, limit=limit)}


@router.get("/vehicles/{vin}", response_model=VehicleRecord, summary="Fetch a cached vehicle")
async def get_vehicle(vin: str, db: Session = Depends(get_db)) -> VehicleRecord:
    record = repo.get_vehicle_record(db, normalize_vin(vin))
    if record is None:
        raise NotFound(f"No stored record for VIN {normalize_vin(vin)}. Decode it first.")
    record.cached = True
    return record


@router.delete("/vehicles/{vin}", summary="Remove a VIN from the cache")
async def delete_vehicle(vin: str, db: Session = Depends(get_db)) -> dict:
    removed = repo.invalidate_vin(db, normalize_vin(vin))
    if not removed:
        raise NotFound(f"No cached record for VIN {normalize_vin(vin)}.")
    return {"deleted": True, "vin": normalize_vin(vin)}


# --- Export -----------------------------------------------------------------

class ExportRequest(BaseModel):
    vins: list[str] = Field(default_factory=list)
    format: str = Field(default="csv", pattern="^(csv|xlsx)$")


async def _records_for(vins: list[str], db: Session, settings: Settings) -> list[VehicleRecord]:
    """Prefer stored records; decode anything not yet in the cache."""
    normalized, _ = parse_vin_list(" ".join(vins), limit=settings.max_vins_per_request)
    if not normalized:
        raise InvalidInput("No VINs supplied for export.")

    records: list[VehicleRecord] = []
    missing: list[str] = []
    for vin in normalized:
        record = repo.get_vehicle_record(db, vin)
        if record is None:
            missing.append(vin)
        else:
            record.cached = True
            records.append(record)

    if missing:
        decoded = await get_decode_service().decode_many(missing)
        records.extend(decoded.results)

    order = {v: i for i, v in enumerate(normalized)}
    records.sort(key=lambda r: order.get(r.vin, 999_999))
    return records


@router.post("/export", summary="Export decoded vehicles as CSV or Excel")
async def export(
    payload: ExportRequest = Body(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    records = await _records_for(payload.vins, db, settings)

    if payload.format == "xlsx":
        content = to_xlsx(records)
        filename = export_filename("xlsx")
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        # UTF-8 BOM so Excel opens the CSV with the right encoding.
        content = to_csv(records).encode("utf-8-sig")
        filename = export_filename("csv")
        media = "text/csv; charset=utf-8"

    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Compare ----------------------------------------------------------------

class CompareRequest(BaseModel):
    vins: list[str] = Field(default_factory=list, min_length=2)


@router.post("/compare", summary="Compare decoded vehicles side by side")
async def compare(
    payload: CompareRequest = Body(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    if len(payload.vins) < 2:
        raise InvalidInput("Select at least two vehicles to compare.")
    if len(payload.vins) > 6:
        raise InvalidInput("Compare at most six vehicles at a time.")
    records = await _records_for(payload.vins, db, settings)
    return build_comparison(records)


# --- Meta -------------------------------------------------------------------

@router.get("/providers", summary="Configured data providers")
async def providers() -> dict:
    """Provider names, availability and cost class. Never returns credentials."""
    return {"providers": get_registry().describe()}


@router.get("/usage", summary="API usage and spend")
async def usage(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> dict:
    return repo.usage_summary(db, days=days)


@router.get("/health", summary="Health check")
async def health(settings: Settings = Depends(get_settings)) -> dict:
    registry = get_registry()
    db_ok, db_error = True, None
    try:
        from sqlalchemy import text

        from app.db.base import session_scope
        with session_scope() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_ok, db_error = False, str(exc)

    return {
        "status": "ok" if db_ok else "degraded",
        "time": _dt.datetime.now(_dt.UTC).isoformat(),
        "environment": settings.environment,
        "database": {
            "ok": db_ok,
            "engine": "sqlite" if settings.using_sqlite else "postgresql",
            "error": db_error,
        },
        "providers": {
            "available": [p.name for p in registry.available()],
            "unavailable": [
                {"name": p.name, "reason": p.unavailable_reason()}
                for p in registry.all if not p.is_available()
            ],
        },
        "cache": {
            "enabled": settings.cache_enabled,
            "ttl_hours": settings.cache_ttl_hours,
        },
    }
