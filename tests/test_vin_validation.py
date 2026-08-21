"""VIN validation: check digit, charset, length, model year, WMI, list parsing."""

from __future__ import annotations

import pytest

from app.vin.validate import (
    VinErrorCode,
    analyze_vin,
    compute_check_digit,
    has_valid_check_digit,
    normalize_vin,
    parse_vin_list,
)
from app.vin.wmi import decode_country, decode_manufacturer, is_small_manufacturer, wmi_of
from app.vin.year import candidate_model_years, decode_model_year

# Real, check-digit-valid VINs.
VALID_VINS = [
    "WA1ANAFY5J2213924",   # 2018 Audi Q5
    "WBXHT3C38J5K23394",   # 2018 BMW X1
    "5UXKR0C56JL070851",   # 2018 BMW X5
    "WBA2J3C53JVA52449",   # 2018 BMW 230i
    "WBA5R7C59KAE82587",   # 2019 BMW 330i
    "WBA4J1C58JBG77203",   # 2018 BMW 430i
    "1HGCM82633A004352",   # 2003 Honda Accord
]


class TestCheckDigit:
    @pytest.mark.parametrize("vin", VALID_VINS)
    def test_known_good_vins_validate(self, vin):
        assert has_valid_check_digit(vin), f"{vin} should have a valid check digit"

    @pytest.mark.parametrize("vin", VALID_VINS)
    def test_computed_digit_matches_position_nine(self, vin):
        assert compute_check_digit(vin) == vin[8]

    def test_single_character_corruption_is_caught(self):
        """Substituting one character breaks the check digit.

        This is the property that makes the check digit worth computing: it
        catches transcription errors, the failure mode this tool exists for.

        The one documented exception is that the 49 CFR 565 transliteration
        table is not injective - A, J and 1 all carry the value 1, as do
        B/K/S/2 and so on. Swapping a character for a same-valued one leaves
        the weighted sum unchanged, so the standard cannot detect it. The
        test asserts exactly that boundary rather than papering over it.
        """
        from app.vin.validate import _TRANSLITERATION, VIN_ALLOWED

        vin = "5UXKR0C56JL070851"
        undetected: list[tuple[int, str, str]] = []

        for index in range(17):
            if index == 8:
                continue                       # the check digit itself
            original = vin[index]
            for replacement in VIN_ALLOWED:
                if replacement == original:
                    continue
                mutated = vin[:index] + replacement + vin[index + 1:]
                if has_valid_check_digit(mutated):
                    undetected.append((index, original, replacement))

        # Every miss must be explained by the transliteration collision above.
        for index, original, replacement in undetected:
            assert _TRANSLITERATION[original] == _TRANSLITERATION[replacement], (
                f"position {index + 1}: {original}->{replacement} slipped through "
                f"without a transliteration collision"
            )
        # And collisions are the only reason any slip through at all.
        assert undetected, "expected the known transliteration collisions to exist"

    def test_transposition_is_detected(self):
        vin = "5UXKR0C56JL070851"
        swapped = vin[:11] + vin[12] + vin[11] + vin[13:]
        assert swapped != vin
        assert not has_valid_check_digit(swapped)

    def test_check_digit_x_is_supported(self):
        """A remainder of 10 is written as 'X', not '10'."""
        digit = compute_check_digit("1M8GDM9AXKP042788")
        assert digit in set("0123456789X")

    def test_returns_none_for_wrong_length(self):
        assert compute_check_digit("TOOSHORT") is None

    def test_returns_none_for_illegal_character(self):
        assert compute_check_digit("5UXKR0C56JL07085I") is None


class TestNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("5uxkr0c56jl070851", "5UXKR0C56JL070851"),
        ("  5UXKR0C56JL070851  ", "5UXKR0C56JL070851"),
        ("5UXKR0C5-6JL070851", "5UXKR0C56JL070851"),
        ("5UXKR0C5 6JL070851", "5UXKR0C56JL070851"),
        ("", ""),
        (None, ""),
    ])
    def test_normalize(self, raw, expected):
        assert normalize_vin(raw) == expected


class TestStructuralValidation:
    def test_valid_vin_is_accepted(self):
        result = analyze_vin("5UXKR0C56JL070851")
        assert result.valid is True
        assert result.check_digit_valid is True
        assert result.errors == []

    def test_empty_input_is_rejected(self):
        result = analyze_vin("")
        assert result.valid is False
        assert result.errors[0].code is VinErrorCode.EMPTY

    @pytest.mark.parametrize("vin", ["5UXKR0C56JL07085", "5UXKR0C56JL0708512", "ABC"])
    def test_wrong_length_is_rejected(self, vin):
        result = analyze_vin(vin)
        assert result.valid is False
        assert any(e.code is VinErrorCode.BAD_LENGTH for e in result.errors)

    @pytest.mark.parametrize("letter", ["I", "O", "Q"])
    def test_forbidden_letters_are_rejected(self, letter):
        vin = "5UXKR0C56JL07085" + letter
        result = analyze_vin(vin)
        assert result.valid is False
        assert any(e.code is VinErrorCode.ILLEGAL_CHARACTER for e in result.errors)

    def test_bad_check_digit_is_a_warning_not_an_error(self):
        """Imported vehicles often skip the North American check digit.

        Rejecting them outright would make the tool useless for imported
        stock, so this is surfaced as a warning and decoding continues.
        """
        vin = "5UXKR0C57JL070851"          # digit changed from 6 to 7
        result = analyze_vin(vin)
        assert result.valid is True
        assert result.check_digit_valid is False
        assert result.errors == []
        assert any(w.code is VinErrorCode.CHECK_DIGIT_MISMATCH for w in result.warnings)
        assert result.expected_check_digit == "6"
        assert result.actual_check_digit == "7"

    def test_segments_are_split_correctly(self):
        result = analyze_vin("5UXKR0C56JL070851")
        assert result.wmi == "5UX"
        assert result.vds == "KR0C56"
        assert result.vis == "JL070851"
        assert result.serial == "070851"


class TestModelYear:
    @pytest.mark.parametrize("vin,year", [
        ("5UXKR0C56JL070851", 2018),
        ("WA1ANAFY5J2213924", 2018),
        ("WBA5R7C59KAE82587", 2019),
        ("1HGCM82633A004352", 2003),
    ])
    def test_year_decoding(self, vin, year):
        assert decode_model_year(vin, current_year=2026) == year

    def test_numeric_position_seven_means_first_cycle(self):
        """Position 7 numeric => 1980-2009; alphabetic => 2010-2039."""
        assert decode_model_year("1HGCM82633A004352", current_year=2026) == 2003

    def test_both_cycle_candidates_are_available(self):
        candidates = candidate_model_years("5UXKR0C56JL070851")
        assert candidates == [1988, 2018]

    def test_implausible_future_year_falls_back_a_cycle(self):
        """A 2030-coded VIN read in 2020 is a 2000 vehicle, not a time traveller."""
        vin = "WBAAA1305H2320000"          # position 7 alphabetic, year code H
        assert decode_model_year(vin, current_year=2000) == 1987

    def test_invalid_year_code_returns_none(self):
        assert decode_model_year("5UXKR0C56UL070851") is None    # 'U' is not a year code


class TestWmi:
    @pytest.mark.parametrize("vin,country", [
        ("5UXKR0C56JL070851", "United States"),
        ("WA1ANAFY5J2213924", "Germany"),
        ("1HGCM82633A004352", "United States"),
        ("JTDKN3DU0A0012345", "Japan"),
        ("2HGES16575H500000", "Canada"),
        ("3VWFE21C04M000000", "Mexico"),
        ("KMHDU46D67U000000", "South Korea"),
        ("YV1RS592892000000", "Sweden"),
        ("ZFF65LJA0F0000000", "Italy"),
    ])
    def test_country_lookup(self, vin, country):
        assert decode_country(vin) == country

    @pytest.mark.parametrize("vin,manufacturer", [
        ("5UXKR0C56JL070851", "BMW Manufacturing Co. (Spartanburg, USA)"),
        ("WBA2J3C53JVA52449", "BMW AG"),
        ("WA1ANAFY5J2213924", "Audi AG (SUV)"),
        ("5YJ3E1EA7JF000000", "Tesla, Inc."),
    ])
    def test_manufacturer_lookup(self, vin, manufacturer):
        assert decode_manufacturer(vin) == manufacturer

    def test_unknown_wmi_returns_none_rather_than_guessing(self):
        assert decode_manufacturer("ZZZ12345678901234") is None

    def test_small_manufacturer_extends_the_wmi(self):
        vin = "1G9AB1234C1234567"
        assert is_small_manufacturer(vin)
        assert wmi_of(vin) == "1G9" + vin[11:14]

    def test_regular_wmi_is_three_characters(self):
        assert wmi_of("5UXKR0C56JL070851") == "5UX"


class TestDirectDecoding:
    def test_vin_yields_year_manufacturer_and_country(self):
        result = analyze_vin("5UXKR0C56JL070851")
        assert result.model_year == 2018
        assert result.manufacturer == "BMW Manufacturing Co. (Spartanburg, USA)"
        assert result.country == "United States"

    def test_unknown_manufacturer_is_a_warning_not_a_fabrication(self):
        result = analyze_vin("ZZZAA11A1AA000000")
        assert result.manufacturer is None
        assert any(w.code is VinErrorCode.UNKNOWN_MANUFACTURER for w in result.warnings)

    def test_analysis_serialises(self):
        payload = analyze_vin("5UXKR0C56JL070851").to_dict()
        assert payload["vin"] == "5UXKR0C56JL070851"
        assert payload["valid"] is True
        assert isinstance(payload["issues"], list)


class TestVinListParsing:
    def test_newline_separated(self):
        vins, dupes = parse_vin_list("5UXKR0C56JL070851\nWA1ANAFY5J2213924")
        assert vins == ["5UXKR0C56JL070851", "WA1ANAFY5J2213924"]
        assert dupes == []

    @pytest.mark.parametrize("separator", [",", ";", "\t", "  ", "\r\n"])
    def test_other_separators(self, separator):
        text = f"5UXKR0C56JL070851{separator}WA1ANAFY5J2213924"
        vins, _ = parse_vin_list(text)
        assert len(vins) == 2

    def test_duplicates_are_collapsed_and_reported(self):
        text = "5UXKR0C56JL070851\n5uxkr0c56jl070851\nWA1ANAFY5J2213924\n5UXKR0C56JL070851"
        vins, dupes = parse_vin_list(text)
        assert vins == ["5UXKR0C56JL070851", "WA1ANAFY5J2213924"]
        assert dupes == ["5UXKR0C56JL070851"]

    def test_order_is_preserved(self):
        vins, _ = parse_vin_list("\n".join(VALID_VINS))
        assert vins == VALID_VINS

    def test_limit_is_respected(self):
        vins, _ = parse_vin_list("\n".join(VALID_VINS), limit=3)
        assert len(vins) == 3

    def test_empty_input(self):
        assert parse_vin_list("") == ([], [])
        assert parse_vin_list("   \n  \n ") == ([], [])

    def test_lowercase_input_is_uppercased(self):
        vins, _ = parse_vin_list("5uxkr0c56jl070851")
        assert vins == ["5UXKR0C56JL070851"]
