"""Decode orchestration.

The sequence for one VIN:

1. **Validate.** Structurally broken input never reaches a provider - it costs
   money and always fails.
2. **Cache.** A fresh row in ``vehicles`` short-circuits everything below.
3. **Free tier.** VIN structure and NHTSA run concurrently. For most vehicles
   this is already the whole answer.
4. **Enrich.** The local spec catalog fills what the VIN cannot carry
   (fuel economy, gearbox detail) using the identity just established.
5. **Escalate, only if needed.** Commercial providers are called when required
   fields are still missing, or when the caller explicitly asked to verify.
   With no API key configured this step is a no-op and the app costs nothing.
6. **Merge.** Field-by-field, with disagreements surfaced rather than hidden.
7. **Persist.** Cache the record so the same VIN is never paid for twice.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import time

from app.config import Settings, get_settings
from app.db import repository as repo
from app.db.base import session_scope
from app.providers.base import ProviderResponse
from app.providers.registry import ProviderRegistry, get_registry
from app.schemas.vehicle import (
    REQUIRED_FIELDS,
    Confidence,
    DecodeResponse,
    DecodeStatus,
    DecodeSummary,
    EngineSpec,
    FieldValue,
    ProviderCallResult,
    ProviderKind,
    VehicleRecord,
)
from app.services.merge import confidence_breakdown, merge_fields
from app.vin.validate import VinAnalysis, analyze_vin

log = logging.getLogger(__name__)


class DecodeService:
    def __init__(
        self,
        settings: Settings | None = None,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.registry = registry or get_registry()

    # --- Public API ----------------------------------------------------------

    async def decode_many(
        self,
        vins: list[str],
        *,
        refresh: bool = False,
        verify: bool = False,
        duplicates: list[str] | None = None,
        client_ip: str | None = None,
    ) -> DecodeResponse:
        started = time.perf_counter()
        summary = DecodeSummary(
            requested=len(vins), duplicates_removed=list(duplicates or [])
        )

        analyses = [analyze_vin(v) for v in vins]
        valid = [a for a in analyses if a.valid]
        invalid = [a for a in analyses if not a.valid]

        results: list[VehicleRecord] = [self._invalid_record(a) for a in invalid]
        summary.invalid = len(invalid)

        if valid:
            results.extend(await self._decode_valid(valid, refresh=refresh, verify=verify,
                                                    client_ip=client_ip))

        # Preserve the caller's input order.
        order = {a.vin: i for i, a in enumerate(analyses)}
        results.sort(key=lambda r: order.get(r.vin, 999_999))

        for record in results:
            if record.status is DecodeStatus.OK:
                summary.decoded += 1
            elif record.status in (DecodeStatus.NOT_FOUND, DecodeStatus.ERROR):
                summary.failed += 1
            elif record.status is DecodeStatus.PARTIAL:
                summary.decoded += 1
            if record.cached:
                # A cache hit carries the *original* decode's call log and cost.
                # Counting them here would report spend this request never
                # incurred, which is the opposite of what the summary is for.
                summary.from_cache += 1
            else:
                summary.provider_calls += len(record.provider_calls)
                summary.total_cost += record.total_cost
            summary.discrepancy_count += len(record.discrepancies)

        summary.total_cost = round(summary.total_cost, 4)
        summary.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return DecodeResponse(results=results, summary=summary)

    async def decode_one(self, vin: str, *, refresh: bool = False, verify: bool = False,
                         client_ip: str | None = None) -> VehicleRecord:
        response = await self.decode_many([vin], refresh=refresh, verify=verify,
                                          client_ip=client_ip)
        return response.results[0]

    # --- Pipeline ------------------------------------------------------------

    async def _decode_valid(
        self, analyses: list[VinAnalysis], *, refresh: bool, verify: bool,
        client_ip: str | None,
    ) -> list[VehicleRecord]:
        records: list[VehicleRecord] = []
        pending: list[VinAnalysis] = []

        # --- Step 2: cache -------------------------------------------------
        if self.settings.cache_enabled and not refresh:
            with session_scope() as session:
                for analysis in analyses:
                    cached, age = repo.get_cached_vehicle(
                        session, analysis.vin, ttl_hours=self.settings.cache_ttl_hours
                    )
                    if cached is not None:
                        cached.input = analysis.input
                        records.append(cached)
                        repo.record_usage(session, "cache", success=True, cache_hit=True)
                        repo.log_lookup(
                            session, vin=analysis.vin, raw_input=analysis.input,
                            valid=True, status=cached.status.value, cache_hit=True,
                            provider_calls=0, cost=0.0, elapsed_ms=0, client_ip=client_ip,
                        )
                    else:
                        pending.append(analysis)
        else:
            pending = list(analyses)

        if not pending:
            return records

        started = time.perf_counter()
        vins = [a.vin for a in pending]
        hints = {a.vin: a.model_year for a in pending}

        # --- Step 3: free providers, concurrently ---------------------------
        free_providers = [
            p for p in self.registry.free()
            if p.name != "spec_catalog"          # needs identity; runs in step 4
        ]
        contributions: dict[str, dict[str, dict[str, FieldValue]]] = {v: {} for v in vins}
        calls: dict[str, list[ProviderCallResult]] = {v: [] for v in vins}

        free_results = await asyncio.gather(
            *(p.decode_many(vins, hints=hints) for p in free_providers),
            return_exceptions=True,
        )
        for provider, outcome in zip(free_providers, free_results):
            if isinstance(outcome, BaseException):
                log.warning("Provider %s failed wholesale: %s", provider.name, outcome)
                for vin in vins:
                    calls[vin].append(
                        ProviderCallResult(
                            provider=provider.name, kind=provider.kind, success=False,
                            error=f"{type(outcome).__name__}: {outcome}",
                            error_code="PROVIDER_EXCEPTION",
                        )
                    )
                continue
            for vin, response in outcome.items():
                self._absorb(vin, response, contributions, calls)

        # --- Step 4: enrichment from the local catalog ----------------------
        catalog = self.registry.spec_catalog()
        if catalog is not None:
            identities = {vin: self._identity(contributions[vin]) for vin in vins}
            enriched = await asyncio.gather(
                *(
                    catalog.enrich(
                        make=identities[vin]["make"], model=identities[vin]["model"],
                        year=identities[vin]["year"], engine_l=identities[vin]["engine_l"],
                        trim=identities[vin]["trim"],
                    )
                    for vin in vins
                ),
                return_exceptions=True,
            )
            for vin, response in zip(vins, enriched):
                if isinstance(response, BaseException):
                    continue
                self._absorb(vin, response, contributions, calls)

        # --- Step 5: escalate to commercial providers, only where needed ----
        await self._escalate(vins, contributions, calls, hints, verify=verify)

        # --- Steps 6 & 7: merge and persist ---------------------------------
        elapsed = int((time.perf_counter() - started) * 1000)
        by_vin = {a.vin: a for a in pending}
        with session_scope() as session:
            for vin in vins:
                record = self._build_record(by_vin[vin], contributions[vin], calls[vin])
                records.append(record)

                for call in calls[vin]:
                    repo.log_provider_response(session, vin, call)
                    repo.record_usage(
                        session, call.provider, success=call.success,
                        cost=call.cost, latency_ms=call.latency_ms,
                    )
                repo.log_lookup(
                    session, vin=vin, raw_input=by_vin[vin].input, valid=True,
                    status=record.status.value, cache_hit=False,
                    provider_calls=len(calls[vin]), cost=record.total_cost,
                    elapsed_ms=elapsed, client_ip=client_ip,
                    error=record.errors[0]["message"] if record.errors else None,
                )
                if record.status in (DecodeStatus.OK, DecodeStatus.PARTIAL):
                    repo.save_vehicle(session, record)

        return records

    # --- Cost policy ---------------------------------------------------------

    async def _escalate(
        self,
        vins: list[str],
        contributions: dict[str, dict[str, dict[str, FieldValue]]],
        calls: dict[str, list[ProviderCallResult]],
        hints: dict[str, int | None],
        *,
        verify: bool,
    ) -> None:
        """Call paid providers only where free sources were not enough."""
        commercial = self.registry.commercial()
        if not commercial:
            return

        if verify or not self.settings.prefer_free_providers:
            targets = list(vins)
        else:
            targets = [v for v in vins if self._missing_required(contributions[v])]

        if not targets:
            return

        # Respect the daily ceiling before spending anything.
        budget = self.settings.max_commercial_calls_per_day
        if budget > 0:
            names = [p.name for p in commercial]
            with session_scope() as session:
                used = repo.commercial_calls_today(session, names)
            remaining = max(0, budget - used)
            allowance = remaining // max(1, len(commercial))
            if allowance <= 0:
                log.warning(
                    "Daily commercial call ceiling (%s) reached; serving free data only.",
                    budget,
                )
                for vin in targets:
                    calls[vin].append(
                        ProviderCallResult(
                            provider="cost_policy", kind=ProviderKind.COMMERCIAL,
                            success=False,
                            error=f"Daily commercial API ceiling of {budget} calls reached. "
                                  f"Free-source data returned instead.",
                            error_code="QUOTA_EXCEEDED",
                        )
                    )
                return
            targets = targets[:allowance]

        for provider in commercial:
            outcome = await provider.decode_many(
                targets, hints={v: hints.get(v) for v in targets}
            )
            for vin, response in outcome.items():
                self._absorb(vin, response, contributions, calls)

    def _missing_required(self, provider_fields: dict[str, dict[str, FieldValue]]) -> bool:
        present = {
            name
            for fields in provider_fields.values()
            for name, fv in fields.items()
            if fv.value is not None
        }
        return not set(REQUIRED_FIELDS).issubset(present)

    # --- Helpers -------------------------------------------------------------

    @staticmethod
    def _absorb(
        vin: str,
        response: ProviderResponse,
        contributions: dict[str, dict[str, dict[str, FieldValue]]],
        calls: dict[str, list[ProviderCallResult]],
    ) -> None:
        calls.setdefault(vin, []).append(response.to_call_result())
        if response.success and response.fields:
            contributions.setdefault(vin, {})[response.provider] = response.fields

    @staticmethod
    def _identity(provider_fields: dict[str, dict[str, FieldValue]]) -> dict:
        """Best-known make/model/year/engine before enrichment runs."""
        def pick(name: str):
            best = None
            for fields in provider_fields.values():
                fv = fields.get(name)
                if fv is None or fv.value is None:
                    continue
                if best is None or fv.confidence.rank > best.confidence.rank:
                    best = fv
            return best.value if best else None

        return {
            "make": pick("make"),
            "model": pick("model"),
            "year": pick("year"),
            "engine_l": pick("engine_displacement_l"),
            "trim": pick("trim"),
        }

    def _build_record(
        self,
        analysis: VinAnalysis,
        provider_fields: dict[str, dict[str, FieldValue]],
        calls: list[ProviderCallResult],
    ) -> VehicleRecord:
        priorities = {p.name: p.priority for p in self.registry.all}
        resolved, discrepancies = merge_fields(provider_fields, provider_priorities=priorities)

        def value(name: str):
            field = resolved.get(name)
            return field.value if field is not None else None

        successful = [c for c in calls if c.success]
        failed = [c for c in calls if not c.success]

        if not resolved:
            status = DecodeStatus.NOT_FOUND if successful or failed else DecodeStatus.ERROR
        elif self._missing_required(provider_fields):
            status = DecodeStatus.PARTIAL
        else:
            status = DecodeStatus.OK

        record = VehicleRecord(
            vin=analysis.vin,
            input=analysis.input,
            valid=analysis.valid,
            status=status,
            check_digit_valid=analysis.check_digit_valid,
            year=value("year"),
            make=value("make"),
            model=value("model"),
            trim=value("trim"),
            series=value("series"),
            body_type=value("body_type"),
            vehicle_type=value("vehicle_type"),
            doors=value("doors"),
            engine=EngineSpec(
                displacement_l=value("engine_displacement_l"),
                type=value("engine_type"),
                configuration=value("engine_configuration"),
                cylinders=value("engine_cylinders"),
                model=value("engine_model"),
                horsepower=value("horsepower"),
                torque_lb_ft=value("torque_lb_ft"),
            ),
            horsepower=value("horsepower"),
            fuel=value("fuel"),
            fuel_secondary=value("fuel_secondary"),
            drivetrain=value("drivetrain"),
            transmission=value("transmission"),
            transmission_speeds=value("transmission_speeds"),
            manufacturer=value("manufacturer"),
            plant_country=value("plant_country"),
            plant_city=value("plant_city"),
            plant_company=value("plant_company"),
            mpg_city=value("mpg_city"),
            mpg_highway=value("mpg_highway"),
            mpg_combined=value("mpg_combined"),
            fields=resolved,
            discrepancies=discrepancies,
            confidence=confidence_breakdown(resolved),
            sources=sorted({c.provider for c in successful}),
            provider_calls=calls,
            issues=[i.to_dict() for i in analysis.issues],
            errors=[
                {"provider": c.provider, "code": c.error_code, "message": c.error}
                for c in failed
            ],
            total_cost=round(sum(c.cost for c in calls), 4),
        )

        record.warnings = self._warnings(record, analysis, failed)
        return record

    @staticmethod
    def _warnings(record: VehicleRecord, analysis: VinAnalysis,
                  failed: list[ProviderCallResult]) -> list[str]:
        warnings: list[str] = []
        if analysis.check_digit_valid is False:
            warnings.append(
                f"Check digit does not validate (expected "
                f"'{analysis.expected_check_digit}', found '{analysis.actual_check_digit}'). "
                f"Verify the VIN was entered correctly."
            )
        if record.discrepancies:
            critical = sum(1 for d in record.discrepancies if d.severity == "critical")
            warnings.append(
                f"Data discrepancy detected across sources on "
                f"{len(record.discrepancies)} field(s)"
                + (f", {critical} critical" if critical else "")
                + "."
            )
        if record.status is DecodeStatus.PARTIAL:
            missing = [
                f for f in REQUIRED_FIELDS
                if record.fields.get(f) is None or record.fields[f].value is None
            ]
            if missing:
                warnings.append(
                    "Incomplete decode; no source supplied: " + ", ".join(missing) + "."
                )
        for call in failed:
            if call.error_code in {"QUOTA_EXCEEDED", "AUTH_FAILED", "TIMEOUT"}:
                warnings.append(f"{call.provider}: {call.error}")
        return warnings

    def _invalid_record(self, analysis: VinAnalysis) -> VehicleRecord:
        """A rejected VIN, with the reason spelled out. No provider was called."""
        return VehicleRecord(
            vin=analysis.vin or (analysis.input or "").upper(),
            input=analysis.input,
            valid=False,
            status=DecodeStatus.INVALID_VIN,
            check_digit_valid=analysis.check_digit_valid,
            confidence={"overall": Confidence.UNKNOWN.value},
            issues=[i.to_dict() for i in analysis.issues],
            errors=[
                {"provider": "validation", "code": i.code.value, "message": i.message}
                for i in analysis.errors
            ],
            warnings=[i.message for i in analysis.warnings],
            decoded_at=_dt.datetime.now(_dt.UTC),
        )


_service: DecodeService | None = None


def get_decode_service() -> DecodeService:
    global _service
    if _service is None:
        _service = DecodeService()
    return _service


def reset_decode_service() -> None:
    global _service
    _service = None
