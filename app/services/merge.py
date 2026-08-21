"""Field-level merge and discrepancy detection.

The rule this module enforces: **when sources disagree, say so.** A merge that
quietly picks a winner and discards the loser is how a decoding service starts
publishing confident falsehoods.

Selection order for the winning value:

1. higher confidence (HIGH > MEDIUM > LOW),
2. then values read directly from the VIN over values looked up elsewhere,
3. then lower provider priority (the ordering set in the registry),
4. then whichever arrived first, for determinism.

Whatever loses is still attached to the field as an alternative, and if it
*conflicts* rather than merely being less preferred, a
:class:`~app.schemas.vehicle.Discrepancy` is raised alongside it.
"""

from __future__ import annotations

from app.schemas.vehicle import (
    CORE_FIELDS,
    Confidence,
    Discrepancy,
    FieldValue,
    Origin,
    ResolvedField,
    label_for,
)
from app.services.normalize import values_conflict

#: Disagreement on these fields means the two sources are describing different
#: vehicles, not describing one vehicle differently.
CRITICAL_FIELDS = frozenset({"year", "make", "model", "engine_cylinders", "fuel"})


def _sort_key(item: tuple[int, str, FieldValue], priorities: dict[str, int]) -> tuple:
    order, _source, fv = item
    return (
        -fv.confidence.rank,                          # confidence first
        0 if fv.origin is Origin.VIN_DECODED else 1,  # VIN beats lookup
        priorities.get(fv.source, 999),               # then registry priority
        order,                                        # then arrival order
    )


def resolve_field(
    field_name: str,
    candidates: list[FieldValue],
    *,
    provider_priorities: dict[str, int] | None = None,
) -> tuple[ResolvedField, Discrepancy | None]:
    """Pick a winning value and report any genuine conflict."""
    label = label_for(field_name)
    priorities = provider_priorities or {}

    usable = [fv for fv in candidates if fv.value is not None]
    if not usable:
        return ResolvedField(field=field_name, label=label), None

    ordered = sorted(
        ((i, fv.source, fv) for i, fv in enumerate(usable)),
        key=lambda item: _sort_key(item, priorities),
    )
    _, _, winner = ordered[0]
    others = [fv for _, _, fv in ordered[1:]]

    # A conflict requires two *different* sources genuinely disagreeing.
    conflicting = [
        fv for fv in others
        if fv.source != winner.source and values_conflict(field_name, winner.value, fv.value)
    ]

    resolved = ResolvedField(
        field=field_name,
        label=label,
        value=winner.value,
        source=winner.source,
        source_kind=winner.source_kind,
        confidence=winner.confidence,
        origin=winner.origin,
        retrieved_at=winner.retrieved_at,
        disputed=bool(conflicting),
        alternatives=others,
        note=winner.note,
    )

    if not conflicting:
        return resolved, None

    # A disputed value cannot also be a high-confidence value.
    if resolved.confidence is Confidence.HIGH:
        resolved.confidence = Confidence.MEDIUM

    severity = "critical" if field_name in CRITICAL_FIELDS else "warning"
    rival = ", ".join(f"{fv.source} reports {fv.value!r}" for fv in conflicting)
    discrepancy = Discrepancy(
        field=field_name,
        label=label,
        selected_value=winner.value,
        selected_source=winner.source,
        conflicting=conflicting,
        severity=severity,
        message=(
            f"{label}: {winner.source} reports {winner.value!r} but {rival}. "
            f"The higher-confidence value is shown; both are recorded."
        ),
    )
    return resolved, discrepancy


def merge_fields(
    contributions: dict[str, dict[str, FieldValue]],
    *,
    provider_priorities: dict[str, int] | None = None,
) -> tuple[dict[str, ResolvedField], list[Discrepancy]]:
    """Merge every provider's fields into one resolved set.

    ``contributions`` maps provider name -> {field name -> FieldValue}.
    """
    by_field: dict[str, list[FieldValue]] = {}
    for provider_name, fields in contributions.items():
        for field_name, fv in fields.items():
            if fv is None or fv.value is None:
                continue
            by_field.setdefault(field_name, []).append(fv)

    resolved: dict[str, ResolvedField] = {}
    discrepancies: list[Discrepancy] = []
    for field_name, candidates in by_field.items():
        field, discrepancy = resolve_field(
            field_name, candidates, provider_priorities=provider_priorities
        )
        resolved[field_name] = field
        if discrepancy is not None:
            discrepancies.append(discrepancy)

    # Critical conflicts first, then alphabetically for stable output.
    discrepancies.sort(key=lambda d: (d.severity != "critical", d.label))
    return resolved, discrepancies


def overall_confidence(resolved: dict[str, ResolvedField]) -> Confidence:
    """Single headline confidence for a vehicle record.

    Driven by the core identity fields only. A missing wheelbase should not
    drag down a vehicle whose year, make, model, engine and fuel are all
    confirmed by agreeing sources.
    """
    core = [resolved.get(name) for name in CORE_FIELDS]
    present = [f for f in core if f is not None and f.value is not None]
    if not present:
        return Confidence.UNKNOWN

    coverage = len(present) / len(CORE_FIELDS)
    if any(f.disputed and f.field in CRITICAL_FIELDS for f in present):
        return Confidence.LOW

    high = sum(1 for f in present if f.confidence is Confidence.HIGH)
    high_ratio = high / len(present)
    disputed = any(f.disputed for f in present)

    if coverage >= 0.8 and high_ratio >= 0.6 and not disputed:
        return Confidence.HIGH
    if coverage >= 0.5 and high_ratio >= 0.3:
        return Confidence.MEDIUM
    return Confidence.LOW


def confidence_breakdown(resolved: dict[str, ResolvedField]) -> dict[str, str]:
    """Per-field confidence plus the overall figure, ready for the API."""
    out = {name: field.confidence.value for name, field in sorted(resolved.items())}
    out["overall"] = overall_confidence(resolved).value
    return out
