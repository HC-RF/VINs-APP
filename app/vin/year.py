"""Model-year decoding from VIN position 10.

The model-year code cycles every 30 years (1980-2009, 2010-2039, ...).
Position 7 disambiguates the cycle for vehicles built to the North American
standard: it is *numeric* for model years 1980-2009 and *alphabetic* for
2010-2039.
"""

from __future__ import annotations

# 30-character cycle, in order. Note the absence of I, O, Q (never used in a
# VIN) and of U and Z (not used as year codes).
YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"

CYCLE_LENGTH = 30
FIRST_CYCLE_START = 1980


def year_code_offset(code: str) -> int | None:
    """Return the 0-based offset of a model-year character, or None."""
    idx = YEAR_CODES.find(code.upper())
    return idx if idx >= 0 else None


def decode_model_year(vin: str, current_year: int | None = None) -> int | None:
    """Decode the model year from a 17-character VIN.

    Returns None when position 10 does not hold a valid year code.
    """
    if len(vin) < 10:
        return None

    offset = year_code_offset(vin[9])
    if offset is None:
        return None

    base = FIRST_CYCLE_START + offset          # 1980-2009
    position_7 = vin[6].upper() if len(vin) >= 7 else ""

    # Alphabetic position 7 => second cycle (2010-2039).
    if position_7.isalpha():
        base += CYCLE_LENGTH

    # Guard against a model year implausibly far in the future. Manufacturers
    # start selling a model year up to ~1 year early, so allow +2.
    if current_year is not None and base > current_year + 2:
        base -= CYCLE_LENGTH

    return base


def candidate_model_years(vin: str) -> list[int]:
    """All model years the position-10 code could represent.

    Useful when position 7 is ambiguous (e.g. heavy trucks, which do not
    follow the position-7 convention).
    """
    offset = year_code_offset(vin[9]) if len(vin) >= 10 else None
    if offset is None:
        return []
    return [FIRST_CYCLE_START + offset, FIRST_CYCLE_START + offset + CYCLE_LENGTH]
