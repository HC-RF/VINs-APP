"""Merge: resolution order, discrepancy reporting, confidence roll-up.

The behaviour under test is the one that separates this system from a naive
decoder: when two sources disagree, the loser is kept and the conflict is
reported, rather than silently discarded.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from app.schemas.vehicle import Confidence, FieldValue, Origin, ProviderKind
from app.services.merge import (
    CRITICAL_FIELDS,
    confidence_breakdown,
    merge_fields,
    overall_confidence,
    resolve_field,
)

PRIORITIES = {"vin_structure": 5, "nhtsa_vpic": 10, "spec_catalog": 20, "autodev": 30}


def fv(value, source, confidence=Confidence.MEDIUM, origin=Origin.ENRICHED, kind=ProviderKind.FREE):
    return FieldValue(
        value=value, source=source, source_kind=kind,
        confidence=confidence, origin=origin, raw_value=value,
        retrieved_at=_dt.datetime.now(_dt.UTC),
    )


class TestSelectionOrder:
    def test_higher_confidence_wins(self):
        resolved, _ = resolve_field("horsepower", [
            fv(248, "spec_catalog", Confidence.LOW),
            fv(300, "nhtsa_vpic", Confidence.HIGH),
        ], provider_priorities=PRIORITIES)
        assert resolved.value == 300
        assert resolved.source == "nhtsa_vpic"

    def test_vin_decoded_beats_lookup_at_equal_confidence(self):
        resolved, _ = resolve_field("year", [
            fv(2017, "nhtsa_vpic", Confidence.HIGH, Origin.ENRICHED),
            fv(2018, "vin_structure", Confidence.HIGH, Origin.VIN_DECODED),
        ], provider_priorities=PRIORITIES)
        assert resolved.value == 2018
        assert resolved.origin is Origin.VIN_DECODED

    def test_provider_priority_breaks_remaining_ties(self):
        resolved, _ = resolve_field("trim", [
            fv("Base", "autodev", Confidence.MEDIUM),
            fv("xDrive35i", "nhtsa_vpic", Confidence.MEDIUM),
        ], provider_priorities=PRIORITIES)
        assert resolved.source == "nhtsa_vpic"      # priority 10 beats 30

    def test_none_values_are_ignored_entirely(self):
        resolved, discrepancy = resolve_field("fuel", [
            fv(None, "nhtsa_vpic", Confidence.HIGH),
            fv("Gasoline", "spec_catalog", Confidence.LOW),
        ], provider_priorities=PRIORITIES)
        assert resolved.value == "Gasoline"
        assert discrepancy is None

    def test_no_candidates_yields_an_empty_field(self):
        resolved, discrepancy = resolve_field("horsepower", [])
        assert resolved.value is None
        assert resolved.available is False
        assert resolved.confidence is Confidence.UNKNOWN
        assert discrepancy is None


class TestDiscrepancies:
    def test_conflict_is_reported_and_loser_retained(self):
        resolved, discrepancy = resolve_field("transmission", [
            fv("Automatic", "nhtsa_vpic", Confidence.HIGH),
            fv("Dual-Clutch", "spec_catalog", Confidence.MEDIUM),
        ], provider_priorities=PRIORITIES)

        assert resolved.value == "Automatic"
        assert resolved.disputed is True
        assert discrepancy is not None
        assert discrepancy.field == "transmission"
        # The losing value survives, both on the field and in the discrepancy.
        assert any(a.value == "Dual-Clutch" for a in resolved.alternatives)
        assert discrepancy.conflicting[0].value == "Dual-Clutch"
        assert "Automatic" in discrepancy.message and "Dual-Clutch" in discrepancy.message

    def test_disputed_value_is_downgraded_from_high(self):
        """A contested value cannot honestly still be called HIGH confidence."""
        resolved, _ = resolve_field("fuel", [
            fv("Gasoline", "nhtsa_vpic", Confidence.HIGH),
            fv("Diesel", "autodev", Confidence.MEDIUM),
        ], provider_priorities=PRIORITIES)
        assert resolved.value == "Gasoline"
        assert resolved.confidence is Confidence.MEDIUM

    @pytest.mark.parametrize("field", sorted(CRITICAL_FIELDS))
    def test_identity_field_conflicts_are_critical(self, field):
        values = {"year": (2018, 2019), "make": ("BMW", "Audi"), "model": ("X5", "Q5"),
                  "engine_cylinders": (6, 4), "fuel": ("Gasoline", "Diesel")}[field]
        _, discrepancy = resolve_field(field, [
            fv(values[0], "nhtsa_vpic", Confidence.HIGH),
            fv(values[1], "autodev", Confidence.MEDIUM),
        ], provider_priorities=PRIORITIES)
        assert discrepancy is not None
        assert discrepancy.severity == "critical"

    def test_non_identity_conflicts_are_warnings(self):
        _, discrepancy = resolve_field("transmission", [
            fv("Automatic", "nhtsa_vpic", Confidence.HIGH),
            fv("Manual", "autodev", Confidence.MEDIUM),
        ], provider_priorities=PRIORITIES)
        assert discrepancy.severity == "warning"

    def test_same_source_twice_is_not_a_conflict(self):
        """One provider reporting twice is a payload quirk, not disagreement."""
        resolved, discrepancy = resolve_field("horsepower", [
            fv(300, "nhtsa_vpic", Confidence.HIGH),
            fv(248, "nhtsa_vpic", Confidence.MEDIUM),
        ], provider_priorities=PRIORITIES)
        assert discrepancy is None
        assert resolved.disputed is False

    def test_formatting_difference_is_not_a_conflict(self):
        resolved, discrepancy = resolve_field("drivetrain", [
            fv("AWD/All-Wheel Drive", "nhtsa_vpic", Confidence.HIGH),
            fv("AWD", "autodev", Confidence.MEDIUM),
        ], provider_priorities=PRIORITIES)
        assert discrepancy is None
        assert resolved.disputed is False

    def test_three_way_conflict_lists_both_rivals(self):
        _, discrepancy = resolve_field("horsepower", [
            fv(300, "nhtsa_vpic", Confidence.HIGH),
            fv(248, "spec_catalog", Confidence.MEDIUM),
            fv(190, "autodev", Confidence.MEDIUM),
        ], provider_priorities=PRIORITIES)
        assert len(discrepancy.conflicting) == 2


class TestMergeFields:
    def test_merges_across_providers(self):
        resolved, discrepancies = merge_fields({
            "vin_structure": {"year": fv(2018, "vin_structure", Confidence.HIGH, Origin.VIN_DECODED)},
            "nhtsa_vpic": {
                "make": fv("BMW", "nhtsa_vpic", Confidence.HIGH),
                "model": fv("X5", "nhtsa_vpic", Confidence.HIGH),
                "horsepower": fv(300, "nhtsa_vpic", Confidence.MEDIUM),
            },
            "spec_catalog": {
                "mpg_combined": fv(20, "spec_catalog", Confidence.HIGH),
                "horsepower": fv(300, "spec_catalog", Confidence.MEDIUM),
            },
        }, provider_priorities=PRIORITIES)

        assert resolved["year"].value == 2018
        assert resolved["make"].value == "BMW"
        assert resolved["mpg_combined"].value == 20
        assert discrepancies == []
        # Agreeing sources are still recorded as alternatives.
        assert resolved["horsepower"].alternatives

    def test_critical_discrepancies_are_sorted_first(self):
        _, discrepancies = merge_fields({
            "a": {"transmission": fv("Automatic", "a", Confidence.HIGH),
                  "make": fv("BMW", "a", Confidence.HIGH)},
            "b": {"transmission": fv("Manual", "b", Confidence.MEDIUM),
                  "make": fv("Audi", "b", Confidence.MEDIUM)},
        }, provider_priorities={"a": 1, "b": 2})
        assert len(discrepancies) == 2
        assert discrepancies[0].severity == "critical"
        assert discrepancies[0].field == "make"

    def test_labels_are_human_readable(self):
        resolved, _ = merge_fields(
            {"a": {"engine_displacement_l": fv(3.0, "a", Confidence.HIGH)}}
        )
        assert resolved["engine_displacement_l"].label == "Engine Displacement (L)"


class TestOverallConfidence:
    def _complete(self, confidence=Confidence.HIGH, disputed=False):
        from app.schemas.vehicle import CORE_FIELDS
        values = {"year": 2018, "make": "BMW", "model": "X5", "trim": "xDrive35i",
                  "engine_displacement_l": 3.0, "engine_cylinders": 6, "horsepower": 300,
                  "fuel": "Gasoline", "drivetrain": "AWD", "transmission": "Automatic"}
        contributions = {"src": {k: fv(values[k], "src", confidence) for k in CORE_FIELDS}}
        resolved, _ = merge_fields(contributions)
        if disputed:
            resolved["make"].disputed = True
        return resolved

    def test_complete_and_agreeing_is_high(self):
        assert overall_confidence(self._complete()) is Confidence.HIGH

    def test_disputed_identity_field_drops_to_low(self):
        assert overall_confidence(self._complete(disputed=True)) is Confidence.LOW

    def test_all_low_sources_is_low(self):
        assert overall_confidence(self._complete(Confidence.LOW)) is Confidence.LOW

    def test_nothing_resolved_is_unknown(self):
        assert overall_confidence({}) is Confidence.UNKNOWN

    def test_sparse_coverage_is_not_high(self):
        resolved, _ = merge_fields({"src": {"year": fv(2018, "src", Confidence.HIGH)}})
        assert overall_confidence(resolved) is not Confidence.HIGH

    def test_breakdown_includes_overall_and_each_field(self):
        breakdown = confidence_breakdown(self._complete())
        assert breakdown["overall"] == "HIGH"
        assert breakdown["make"] == "HIGH"
        assert all(isinstance(v, str) for v in breakdown.values())
