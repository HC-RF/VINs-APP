"""VIN structural validation and direct decoding.

Everything in this module is derived from the 17 characters of the VIN itself -
no network, no database. Values produced here are the highest-confidence data
the system has, because they come from the identifier rather than from a
lookup that could be stale or wrong.

Reference: ISO 3779 (VIN structure) and 49 CFR 565 (US check digit).
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from enum import Enum

from app.vin.wmi import decode_country, decode_manufacturer, is_small_manufacturer, wmi_of
from app.vin.year import decode_model_year

VIN_LENGTH = 17

# I, O and Q are excluded from VINs so they cannot be confused with 1 and 0.
VIN_ALLOWED = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
_VIN_RE = re.compile(rf"^[{VIN_ALLOWED}]{{{VIN_LENGTH}}}$")
_ILLEGAL_RE = re.compile(r"[IOQ]")

# Transliteration table from 49 CFR 565.15.
_TRANSLITERATION: dict[str, int] = {
    **{str(d): d for d in range(10)},
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}

# Positional weights; position 9 (the check digit itself) has weight 0.
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)

CHECK_DIGIT_INDEX = 8  # 0-based index of VIN position 9


class VinErrorCode(str, Enum):
    """Stable machine-readable codes surfaced to API clients."""

    EMPTY = "VIN_EMPTY"
    BAD_LENGTH = "VIN_BAD_LENGTH"
    ILLEGAL_CHARACTER = "VIN_ILLEGAL_CHARACTER"
    BAD_CHARSET = "VIN_BAD_CHARSET"
    CHECK_DIGIT_MISMATCH = "VIN_CHECK_DIGIT_MISMATCH"
    UNKNOWN_MODEL_YEAR = "VIN_UNKNOWN_MODEL_YEAR"
    UNKNOWN_MANUFACTURER = "VIN_UNKNOWN_MANUFACTURER"


@dataclass(slots=True)
class VinIssue:
    code: VinErrorCode
    message: str
    severity: str  # "error" | "warning"

    def to_dict(self) -> dict:
        return {"code": self.code.value, "message": self.message, "severity": self.severity}


@dataclass(slots=True)
class VinAnalysis:
    """Result of validating and directly decoding a VIN."""

    input: str
    vin: str                       # normalized (uppercase, whitespace stripped)
    valid: bool                    # structurally usable for decoding
    check_digit_valid: bool | None  # None when it cannot be evaluated
    expected_check_digit: str | None
    actual_check_digit: str | None
    wmi: str | None
    vds: str | None                # positions 4-9
    vis: str | None                # positions 10-17
    serial: str | None             # positions 12-17
    model_year: int | None
    manufacturer: str | None
    country: str | None
    small_manufacturer: bool
    issues: list[VinIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[VinIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[VinIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def to_dict(self) -> dict:
        return {
            "input": self.input,
            "vin": self.vin,
            "valid": self.valid,
            "check_digit_valid": self.check_digit_valid,
            "expected_check_digit": self.expected_check_digit,
            "actual_check_digit": self.actual_check_digit,
            "wmi": self.wmi,
            "vds": self.vds,
            "vis": self.vis,
            "serial": self.serial,
            "model_year": self.model_year,
            "manufacturer": self.manufacturer,
            "country": self.country,
            "small_manufacturer": self.small_manufacturer,
            "issues": [i.to_dict() for i in self.issues],
        }


def normalize_vin(raw: str) -> str:
    """Uppercase and strip whitespace, hyphens and common separators."""
    if raw is None:
        return ""
    return re.sub(r"[\s\-_.]", "", str(raw)).upper()


def compute_check_digit(vin: str) -> str | None:
    """Compute VIN position 9 per 49 CFR 565. Returns '0'-'9' or 'X'."""
    if len(vin) != VIN_LENGTH:
        return None
    total = 0
    for i, ch in enumerate(vin.upper()):
        value = _TRANSLITERATION.get(ch)
        if value is None:
            return None
        total += value * _WEIGHTS[i]
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


def has_valid_check_digit(vin: str) -> bool:
    expected = compute_check_digit(vin)
    return expected is not None and expected == vin[CHECK_DIGIT_INDEX].upper()


def analyze_vin(raw: str, *, today: _dt.date | None = None) -> VinAnalysis:
    """Validate and directly decode a VIN.

    A VIN is reported as ``valid`` when it is structurally sound (17 legal
    characters). A failing check digit is recorded as a **warning**, not an
    error: vehicles built outside North America frequently do not implement
    the check-digit standard, and rejecting them outright would make the tool
    useless for imported stock.
    """
    today = today or _dt.date.today()
    vin = normalize_vin(raw)
    issues: list[VinIssue] = []

    if not vin:
        issues.append(VinIssue(VinErrorCode.EMPTY, "No VIN supplied.", "error"))
        return VinAnalysis(
            input=str(raw or ""), vin="", valid=False, check_digit_valid=None,
            expected_check_digit=None, actual_check_digit=None, wmi=None, vds=None,
            vis=None, serial=None, model_year=None, manufacturer=None, country=None,
            small_manufacturer=False, issues=issues,
        )

    if len(vin) != VIN_LENGTH:
        issues.append(VinIssue(
            VinErrorCode.BAD_LENGTH,
            f"A VIN must be exactly {VIN_LENGTH} characters; got {len(vin)}.",
            "error",
        ))

    illegal = sorted(set(_ILLEGAL_RE.findall(vin)))
    if illegal:
        issues.append(VinIssue(
            VinErrorCode.ILLEGAL_CHARACTER,
            f"The letters I, O and Q never appear in a VIN; found {', '.join(illegal)}.",
            "error",
        ))
    elif len(vin) == VIN_LENGTH and not _VIN_RE.match(vin):
        issues.append(VinIssue(
            VinErrorCode.BAD_CHARSET,
            "VIN contains characters outside the permitted A-Z (no I/O/Q) and 0-9 set.",
            "error",
        ))

    structurally_valid = not any(i.severity == "error" for i in issues)

    expected = actual = None
    check_ok: bool | None = None
    if structurally_valid:
        expected = compute_check_digit(vin)
        actual = vin[CHECK_DIGIT_INDEX]
        if expected is not None:
            check_ok = expected == actual
            if not check_ok:
                issues.append(VinIssue(
                    VinErrorCode.CHECK_DIGIT_MISMATCH,
                    f"Check digit mismatch: position 9 is '{actual}' but the VIN "
                    f"computes to '{expected}'. Common for vehicles not built to the "
                    f"North American standard; verify the VIN was transcribed correctly.",
                    "warning",
                ))

    model_year = manufacturer = country = None
    wmi = vds = vis = serial = None
    small = False
    if structurally_valid:
        wmi = wmi_of(vin)
        vds = vin[3:9]
        vis = vin[9:]
        serial = vin[11:]
        small = is_small_manufacturer(vin)
        model_year = decode_model_year(vin, current_year=today.year)
        manufacturer = decode_manufacturer(vin)
        country = decode_country(vin)

        if model_year is None:
            issues.append(VinIssue(
                VinErrorCode.UNKNOWN_MODEL_YEAR,
                "Position 10 does not hold a recognised model-year code.",
                "warning",
            ))
        if manufacturer is None:
            issues.append(VinIssue(
                VinErrorCode.UNKNOWN_MANUFACTURER,
                f"WMI '{vin[:3]}' is not in the local manufacturer table; "
                f"relying on external providers for manufacturer identity.",
                "warning",
            ))

    return VinAnalysis(
        input=str(raw or ""),
        vin=vin,
        valid=structurally_valid,
        check_digit_valid=check_ok,
        expected_check_digit=expected,
        actual_check_digit=actual,
        wmi=wmi,
        vds=vds,
        vis=vis,
        serial=serial,
        model_year=model_year,
        manufacturer=manufacturer,
        country=country,
        small_manufacturer=small,
        issues=issues,
    )


def parse_vin_list(text: str, *, limit: int | None = None) -> tuple[list[str], list[str]]:
    """Split pasted text into unique VINs, preserving order.

    Accepts newline-, comma-, semicolon- and tab-separated input. Returns
    ``(unique_vins, duplicates)`` where *duplicates* lists VINs that appeared
    more than once so the caller can tell the user what was collapsed.
    """
    if not text:
        return [], []
    tokens = [t for t in re.split(r"[\s,;]+", text) if t]
    seen: dict[str, int] = {}
    unique: list[str] = []
    duplicates: list[str] = []
    for token in tokens:
        vin = normalize_vin(token)
        if not vin:
            continue
        if vin in seen:
            seen[vin] += 1
            if seen[vin] == 2:
                duplicates.append(vin)
            continue
        seen[vin] = 1
        unique.append(vin)
        if limit is not None and len(unique) >= limit:
            break
    return unique, duplicates
