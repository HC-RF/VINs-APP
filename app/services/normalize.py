"""Normalization of raw provider payloads into canonical values.

Two jobs:

1. **Reject junk.** Vehicle APIs are full of placeholder strings - "Not
   Applicable", "N/A", "0", "" - that mean *absent*, not *zero*. Every value
   passes through :func:`clean` before it is trusted. Anything that does not
   survive becomes ``None``, which the UI renders as "Not available". The
   system never invents a value to fill a gap.

2. **Make values comparable.** "AWD/All-Wheel Drive", "All-Wheel Drive" and
   "AWD" are the same fact written three ways. Canonicalising them is what
   lets the merge step tell a real disagreement from a formatting difference.
"""

from __future__ import annotations

import re

# Strings that providers use to mean "no data".
_NULL_TOKENS = {
    "", "-", "--", "n/a", "na", "none", "null", "not applicable", "notapplicable",
    "not available", "notavailable", "unknown", "undetermined", "not reported",
    "no data", "nodata", "0", "not applicable/not available",
}


def clean(value: object) -> str | None:
    """Trim a raw provider string, returning None for placeholder values."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower() in _NULL_TOKENS:
        return None
    # Collapse internal whitespace.
    return re.sub(r"\s+", " ", text)


def to_int(value: object, *, minimum: int | None = None, maximum: int | None = None) -> int | None:
    """Parse an integer, tolerating '6', '6.0', '300 hp', ' 4 '."""
    text = clean(value)
    if text is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", _strip_grouping(text))
    if not match:
        return None
    try:
        number = int(round(float(match.group())))
    except (TypeError, ValueError):
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def _strip_grouping(text: str) -> str:
    """Remove thousands separators without mangling a decimal comma.

    "1,200" is twelve hundred; "1,5" is one and a half. Blindly deleting every
    comma turns the second into fifteen.
    """
    if re.search(r"\d,\d{3}(?!\d)", text):
        return text.replace(",", "")
    return re.sub(r"(\d),(\d)", r"\1.\2", text)


def to_float(value: object, *, minimum: float | None = None, maximum: float | None = None) -> float | None:
    text = clean(value)
    if text is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", _strip_grouping(text))
    if not match:
        return None
    try:
        number = float(match.group())
    except (TypeError, ValueError):
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return round(number, 2)


def to_bool_label(value: object) -> str | None:
    """Normalize the Yes/No/Standard/Optional soup that safety fields use."""
    text = clean(value)
    if text is None:
        return None
    low = text.lower()
    if low in {"yes", "y", "true", "1", "standard", "std"}:
        return "Standard"
    if low in {"no", "n", "false"}:
        return "Not equipped"
    if low in {"optional", "opt"}:
        return "Optional"
    return text


def title_case(value: object) -> str | None:
    """Title-case an ALL-CAPS provider string without mangling acronyms."""
    text = clean(value)
    if text is None:
        return None
    if not text.isupper():
        return text
    keep = {"BMW", "GMC", "AWD", "FWD", "RWD", "4WD", "USA", "UK", "AG", "LLC",
            "SUV", "MPV", "ABS", "ESC", "TPMS", "GVWR", "NA", "CVT", "DCT",
            "AMG", "SRT", "STI", "GTI", "TDI", "MPG", "HP", "L", "V6", "V8", "V12"}
    parts = []
    for word in text.split(" "):
        core = word.strip("().,/-")
        parts.append(word if core in keep else word.capitalize())
    return " ".join(parts)


# --- Canonicalisation for cross-source comparison ---------------------------

_DRIVETRAIN_PATTERNS: list[tuple[str, str]] = [
    (r"\b(awd|all[\s-]*wheel)\b", "AWD"),
    (r"\b(4wd|4x4|four[\s-]*wheel)\b", "4WD"),
    (r"\b(fwd|front[\s-]*wheel)\b", "FWD"),
    (r"\b(rwd|rear[\s-]*wheel|4x2)\b", "RWD"),
]

_FUEL_PATTERNS: list[tuple[str, str]] = [
    (r"\b(gasoline|gas|petrol|flexible fuel|flex)\b", "Gasoline"),
    (r"\bdiesel\b", "Diesel"),
    (r"\b(electric|bev)\b", "Electric"),
    (r"\b(plug[\s-]*in hybrid|phev)\b", "Plug-in Hybrid"),
    (r"\b(hybrid|hev)\b", "Hybrid"),
    (r"\b(compressed natural gas|cng)\b", "CNG"),
    (r"\b(liquefied petroleum|lpg|propane)\b", "LPG"),
    (r"\b(hydrogen|fcev)\b", "Hydrogen"),
    (r"\bethanol|e85\b", "Ethanol"),
]

_TRANSMISSION_PATTERNS: list[tuple[str, str]] = [
    (r"\b(continuously variable|cvt)\b", "CVT"),
    (r"\b(dual[\s-]*clutch|dct|dsg|pdk|s[\s-]*tronic)\b", "Dual-Clutch"),
    (r"\b(automated manual|amt)\b", "Automated Manual"),
    (r"\b(manual|m/t|mt)\b", "Manual"),
    (r"\b(automatic|auto|a/t|at|steptronic|tiptronic)\b", "Automatic"),
]

_BODY_PATTERNS: list[tuple[str, str]] = [
    (r"\b(sport utility|suv|mpv|multipurpose)\b", "SUV"),
    (r"\bcrossover\b", "Crossover"),
    (r"\b(pickup|truck)\b", "Pickup"),
    (r"\b(sedan|saloon)\b", "Sedan"),
    (r"\b(coupe)\b", "Coupe"),
    (r"\b(convertible|cabriolet|roadster|spyder)\b", "Convertible"),
    (r"\b(hatchback|liftback)\b", "Hatchback"),
    (r"\b(wagon|estate|touring|avant|sportbrake)\b", "Wagon"),
    (r"\b(van|minivan)\b", "Van"),
]


def _match_first(text: str, patterns: list[tuple[str, str]]) -> str | None:
    low = text.lower()
    for pattern, canonical in patterns:
        if re.search(pattern, low):
            return canonical
    return None


def canonical_drivetrain(value: object) -> str | None:
    text = clean(value)
    return _match_first(text, _DRIVETRAIN_PATTERNS) if text else None


# Checked strictly in order. Specific fuels must precede the broad gasoline
# pattern: "Compressed Natural Gas" contains "Gas", and classifying a CNG
# vehicle as petrol is exactly the kind of quiet error this system must not make.
_FUEL_PRIORITY: list[tuple[str, str]] = [
    (r"\b(compressed natural gas|natural gas|cng)\b", "CNG"),
    (r"\b(liquefied petroleum(?: gas)?|lpg|propane)\b", "LPG"),
    (r"\b(liquefied natural gas|lng)\b", "LNG"),
    (r"\b(hydrogen|fuel cell|fcev)\b", "Hydrogen"),
    (r"\b(plug[\s-]*in hybrid|phev)\b", "Plug-in Hybrid"),
    (r"\b(hybrid|hev)\b", "Hybrid"),
    (r"\b(electric|bev)\b", "Electric"),
    (r"\b(ethanol|e85|flex(?:ible)?[\s-]*fuel)\b", "Ethanol"),
    (r"\b(biodiesel|b20)\b", "Biodiesel"),
    (r"\bdiesel\b", "Diesel"),
    (r"\b(gasoline|petrol|gas)\b", "Gasoline"),
]


def canonical_fuel(value: object) -> str | None:
    text = clean(value)
    if not text:
        return None
    low = text.lower()
    for pattern, canonical in _FUEL_PRIORITY:
        if re.search(pattern, low):
            return canonical
    return title_case(text)


def canonical_transmission(value: object) -> str | None:
    text = clean(value)
    return _match_first(text, _TRANSMISSION_PATTERNS) if text else None


def canonical_body(value: object) -> str | None:
    text = clean(value)
    if not text:
        return None
    return _match_first(text, _BODY_PATTERNS) or title_case(text)


def canonical_engine_type(value: object) -> str | None:
    """Aspiration: Turbocharged / Supercharged / Naturally Aspirated."""
    text = clean(value)
    if not text:
        return None
    low = text.lower()
    turbo = "turbo" in low
    super_ = "supercharg" in low
    if turbo and super_:
        return "Twin-Charged"
    if turbo:
        return "Turbocharged"
    if super_:
        return "Supercharged"
    if "naturally aspirated" in low or low in {"na", "n/a aspiration"}:
        return "Naturally Aspirated"
    return title_case(text)


def canonical_make(value: object) -> str | None:
    text = clean(value)
    if not text:
        return None
    fixed = title_case(text)
    overrides = {
        "Bmw": "BMW", "Gmc": "GMC", "Mini": "MINI", "Ram": "RAM",
        "Mercedes-benz": "Mercedes-Benz", "Land rover": "Land Rover",
        "Alfa romeo": "Alfa Romeo", "Rolls-royce": "Rolls-Royce",
        "Aston martin": "Aston Martin", "Kia": "Kia", "Byd": "BYD",
    }
    return overrides.get(fixed or "", fixed)


# Corporate boilerplate and geography that carry no identifying information.
# "BMW Manufacturing Co. (Spartanburg, USA)" and "BMW Manufacturer Corporation /
# BMW North America" name the same marque; without stripping this, every
# vehicle would report a manufacturer discrepancy.
_CORPORATE_STOPWORDS = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "companies",
    "ag", "gmbh", "kg", "llc", "ltd", "limited", "sa", "spa", "srl", "nv", "bv",
    "plc", "aktiengesellschaft", "holding", "holdings", "group", "groupe",
    "motor", "motors", "motoren", "automobile", "automobiles", "automotive",
    "manufacturing", "manufacturer", "manufacturers", "mfg", "works", "werke",
    "north", "south", "america", "american", "usa", "us", "canada", "mexico",
    "europe", "european", "international", "worldwide", "global", "division",
    "of", "the", "and", "de", "do", "du", "and",
})


def manufacturer_identity(value: object) -> str | None:
    """The identifying core of a manufacturer name.

    Drops parenthetical qualifiers, punctuation and corporate boilerplate, then
    returns the remaining tokens sorted. Two names reduce to the same identity
    when they refer to the same marque.
    """
    text = clean(value)
    if not text:
        return None
    text = re.sub(r"\([^)]*\)", " ", text)                # "(Spartanburg, USA)"
    tokens = [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]
    meaningful = [t for t in tokens if t not in _CORPORATE_STOPWORDS]
    if not meaningful:
        meaningful = tokens                                # all boilerplate; keep as-is
    return " ".join(sorted(set(meaningful))) or None


# Comparison keys per canonical field. The merge step uses these to decide
# whether two sources genuinely disagree.
_COMPARATORS = {
    "drivetrain": canonical_drivetrain,
    "fuel": canonical_fuel,
    "fuel_secondary": canonical_fuel,
    "transmission": canonical_transmission,
    "body_type": canonical_body,
    "engine_type": canonical_engine_type,
    "make": canonical_make,
    "manufacturer": manufacturer_identity,
    "plant_company": manufacturer_identity,
    "engine_manufacturer": manufacturer_identity,
}


def comparison_key(field_name: str, value: object) -> object:
    """A normalized key for equality testing across sources.

    Numeric fields compare numerically (so 3.0 == 3). Free-text fields compare
    case-insensitively with punctuation stripped. Known enumerations compare on
    their canonical form.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 2)

    comparator = _COMPARATORS.get(field_name)
    if comparator is not None:
        canonical = comparator(value)
        if canonical is not None:
            return canonical.lower()

    text = clean(value)
    if text is None:
        return None
    # Horsepower reported as "300" vs "300 hp".
    stripped = re.sub(r"[^a-z0-9]+", "", text.lower())
    return stripped or None


# Tolerances for numeric fields: differences within these bounds are treated as
# rounding noise rather than a genuine conflict between sources.
NUMERIC_TOLERANCE: dict[str, float] = {
    "horsepower": 5,          # trim-level variation and SAE re-ratings
    "torque_lb_ft": 8,
    "engine_displacement_l": 0.05,
    "mpg_city": 1,
    "mpg_highway": 1,
    "mpg_combined": 1,
}


def values_conflict(field_name: str, a: object, b: object) -> bool:
    """True when two values from different sources genuinely disagree."""
    if a is None or b is None:
        return False

    tolerance = NUMERIC_TOLERANCE.get(field_name)
    if tolerance is not None:
        fa, fb = to_float(a), to_float(b)
        if fa is not None and fb is not None:
            return abs(fa - fb) > tolerance

    ka, kb = comparison_key(field_name, a), comparison_key(field_name, b)
    if ka is None or kb is None:
        return False
    if ka == kb:
        return False

    # One value being a prefix/substring of the other is usually a granularity
    # difference ("X5" vs "X5 xDrive35i"), not a contradiction.
    if isinstance(ka, str) and isinstance(kb, str):
        if ka in kb or kb in ka:
            return False
    return True
