"""Normalization: junk rejection, parsing, canonicalization, conflict detection.

The central guarantee under test: a placeholder from a provider becomes
``None``, never a value. "Not Applicable" must not turn into a specification.
"""

from __future__ import annotations

import pytest

from app.services import normalize as nz


class TestJunkRejection:
    @pytest.mark.parametrize("junk", [
        "", "   ", "N/A", "n/a", "NA", "None", "null", "-", "--",
        "Not Applicable", "not applicable", "Not Available", "NOT AVAILABLE",
        "Unknown", "unknown", "0", "No Data", "Undetermined", "Not Reported",
    ])
    def test_placeholders_become_none(self, junk):
        assert nz.clean(junk) is None

    @pytest.mark.parametrize("real", ["Gasoline", "BMW", "AWD", "4", "3.0", "X5"])
    def test_real_values_survive(self, real):
        assert nz.clean(real) == real

    def test_none_input(self):
        assert nz.clean(None) is None

    def test_whitespace_is_collapsed(self):
        assert nz.clean("  Sport   Utility   Vehicle  ") == "Sport Utility Vehicle"

    def test_zero_string_is_absent_not_zero(self):
        """Providers write '0' to mean 'no data'; a real zero cylinder count
        does not exist, so treating it as absent is correct."""
        assert nz.clean("0") is None
        assert nz.to_int("0") is None


class TestNumericParsing:
    @pytest.mark.parametrize("raw,expected", [
        ("6", 6), ("6.0", 6), (" 4 ", 4), ("300 hp", 300), ("1,200", 1200),
        (8, 8), (8.0, 8), ("8-speed", 8),
    ])
    def test_to_int(self, raw, expected):
        assert nz.to_int(raw) == expected

    @pytest.mark.parametrize("raw", ["", "N/A", None, "abc", "Not Applicable"])
    def test_to_int_rejects_junk(self, raw):
        assert nz.to_int(raw) is None

    def test_to_int_respects_bounds(self):
        assert nz.to_int("6", minimum=1, maximum=16) == 6
        assert nz.to_int("99", minimum=1, maximum=16) is None
        assert nz.to_int("0", minimum=1) is None

    @pytest.mark.parametrize("raw,expected", [
        ("3.0", 3.0), ("2", 2.0), ("3.0L", 3.0), (" 2.5 ", 2.5),
        ("1,5", 1.5), ("1,200", 1200.0),
    ])
    def test_to_float(self, raw, expected):
        assert nz.to_float(raw) == expected

    def test_to_float_bounds(self):
        assert nz.to_float("3.0", minimum=0.1, maximum=20) == 3.0
        assert nz.to_float("99", minimum=0.1, maximum=20) is None


class TestBooleanLabels:
    @pytest.mark.parametrize("raw,expected", [
        ("Yes", "Standard"), ("Standard", "Standard"), ("true", "Standard"),
        ("No", "Not equipped"), ("false", "Not equipped"),
        ("Optional", "Optional"), ("Indirect", "Indirect"),
    ])
    def test_labels(self, raw, expected):
        assert nz.to_bool_label(raw) == expected

    def test_junk_is_none(self):
        assert nz.to_bool_label("Not Applicable") is None


class TestTitleCase:
    def test_all_caps_is_titled(self):
        assert nz.title_case("SPORT UTILITY VEHICLE") == "Sport Utility Vehicle"

    def test_acronyms_survive(self):
        assert nz.title_case("BMW OF NORTH AMERICA") == "BMW Of North America"
        assert nz.title_case("AWD") == "AWD"

    def test_mixed_case_is_left_alone(self):
        assert nz.title_case("xDrive35i") == "xDrive35i"


class TestCanonicalisation:
    @pytest.mark.parametrize("raw,expected", [
        ("AWD/All-Wheel Drive", "AWD"), ("All-Wheel Drive", "AWD"), ("AWD", "AWD"),
        ("FWD/Front-Wheel Drive", "FWD"), ("Front Wheel Drive", "FWD"),
        ("RWD/ Rear-Wheel Drive", "RWD"), ("4WD/4-Wheel Drive/4x4", "4WD"),
        ("4x2", "RWD"),
    ])
    def test_drivetrain(self, raw, expected):
        assert nz.canonical_drivetrain(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("Gasoline", "Gasoline"), ("Petrol", "Gasoline"), ("Diesel", "Diesel"),
        ("Electric", "Electric"), ("Plug-in Hybrid", "Plug-in Hybrid"),
        ("PHEV", "Plug-in Hybrid"), ("Hybrid", "Hybrid"),
        ("Compressed Natural Gas (CNG)", "CNG"),
        ("Natural Gas", "CNG"), ("Liquefied Petroleum Gas (propane)", "LPG"),
        ("Hydrogen Fuel Cell", "Hydrogen"), ("Flexible Fuel Vehicle (FFV)", "Ethanol"),
    ])
    def test_fuel(self, raw, expected):
        assert nz.canonical_fuel(raw) == expected

    def test_plugin_hybrid_beats_hybrid_and_gasoline(self):
        """Ordering matters: a PHEV must not collapse to 'Hybrid' or 'Gasoline'."""
        assert nz.canonical_fuel("Gasoline Plug-in Hybrid") == "Plug-in Hybrid"

    @pytest.mark.parametrize("raw,expected", [
        ("Automatic", "Automatic"), ("Manual", "Manual"),
        ("Continuously Variable Transmission (CVT)", "CVT"),
        ("Dual-Clutch Transmission (DCT)", "Dual-Clutch"),
        ("S tronic", "Dual-Clutch"), ("Steptronic", "Automatic"),
    ])
    def test_transmission(self, raw, expected):
        assert nz.canonical_transmission(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("Sport Utility Vehicle [SUV]/Multipurpose Vehicle [MPV]", "SUV"),
        ("Sedan/Saloon", "Sedan"), ("Coupe", "Coupe"),
        ("Hatchback/Liftback/Notchback", "Hatchback"),
        ("Convertible/Cabriolet", "Convertible"),
        ("Wagon", "Wagon"), ("Pickup", "Pickup"),
    ])
    def test_body(self, raw, expected):
        assert nz.canonical_body(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("Turbocharged", "Turbocharged"), ("turbo", "Turbocharged"),
        ("Supercharged", "Supercharged"),
        ("Twin-turbo supercharged", "Twin-Charged"),
        ("Naturally Aspirated", "Naturally Aspirated"),
    ])
    def test_engine_type(self, raw, expected):
        assert nz.canonical_engine_type(raw) == expected

    def test_make_overrides(self):
        assert nz.canonical_make("BMW") == "BMW"
        assert nz.canonical_make("AUDI") == "Audi"
        assert nz.canonical_make("MERCEDES-BENZ") == "Mercedes-Benz"


class TestManufacturerIdentity:
    def test_corporate_boilerplate_is_stripped(self):
        a = nz.manufacturer_identity("BMW Manufacturing Co. (Spartanburg, USA)")
        b = nz.manufacturer_identity("BMW MANUFACTURER CORPORATION / BMW NORTH AMERICA")
        assert a == b == "bmw"

    def test_distinct_marques_stay_distinct(self):
        assert nz.manufacturer_identity("BMW AG") != nz.manufacturer_identity("Audi AG")

    def test_all_boilerplate_name_is_preserved(self):
        """A name made entirely of stopwords must not reduce to nothing."""
        assert nz.manufacturer_identity("Motors Corporation") is not None


class TestConflictDetection:
    @pytest.mark.parametrize("field,a,b", [
        ("drivetrain", "AWD/All-Wheel Drive", "AWD"),
        ("fuel", "Gasoline", "Petrol"),
        ("transmission", "Automatic", "automatic"),
        ("engine_displacement_l", 3.0, 3),
        ("body_type", "Sport Utility Vehicle [SUV]/Multipurpose Vehicle [MPV]", "SUV"),
        ("manufacturer", "BMW AG", "BMW Manufacturing Co. (Spartanburg, USA)"),
        ("model", "X5", "X5 xDrive35i"),
    ])
    def test_formatting_differences_are_not_conflicts(self, field, a, b):
        assert nz.values_conflict(field, a, b) is False

    @pytest.mark.parametrize("field,a,b", [
        ("drivetrain", "AWD", "FWD"),
        ("fuel", "Gasoline", "Diesel"),
        ("transmission", "Automatic", "Manual"),
        ("engine_cylinders", 6, 4),
        ("make", "BMW", "Audi"),
        ("year", 2018, 2019),
    ])
    def test_genuine_disagreements_are_conflicts(self, field, a, b):
        assert nz.values_conflict(field, a, b) is True

    def test_numeric_tolerance_absorbs_rounding(self):
        """Horsepower re-ratings move by a few units; that is not a conflict."""
        assert nz.values_conflict("horsepower", 300, 303) is False
        assert nz.values_conflict("horsepower", 300, 248) is True

    def test_displacement_tolerance(self):
        assert nz.values_conflict("engine_displacement_l", 2.0, 2.0) is False
        assert nz.values_conflict("engine_displacement_l", 2.0, 3.0) is True

    def test_missing_value_is_never_a_conflict(self):
        assert nz.values_conflict("fuel", None, "Gasoline") is False
        assert nz.values_conflict("fuel", "Gasoline", None) is False
        assert nz.values_conflict("fuel", None, None) is False

    def test_real_world_case_nhtsa_vs_catalog_transmission(self):
        """The exact case the live system detects on a 2018 Audi Q5:
        vPIC reports the generic 'Automatic'; the catalog knows it is a
        7-speed S tronic dual-clutch. Those genuinely differ."""
        assert nz.values_conflict("transmission", "Automatic", "Dual-Clutch") is True


class TestComparisonKey:
    def test_numbers_compare_numerically(self):
        assert nz.comparison_key("horsepower", 300) == nz.comparison_key("horsepower", 300.0)

    def test_none_stays_none(self):
        assert nz.comparison_key("make", None) is None

    def test_punctuation_is_ignored(self):
        assert nz.comparison_key("trim", "xDrive 35i") == nz.comparison_key("trim", "xdrive35i")
