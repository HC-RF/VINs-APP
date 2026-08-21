"""World Manufacturer Identifier (WMI) lookup.

Two independent things are derived from the first three VIN characters:

* the **country / region of manufacture**, from a range-based table defined by
  ISO 3780; and
* the **manufacturer**, from a curated table of common WMIs.

Both are *directly decoded from the VIN* and therefore carry HIGH confidence.
Unknown WMIs return ``None`` - never a guess.
"""

from __future__ import annotations

# ISO 3780 orders the second WMI character as letters (minus I, O, Q) followed
# by digits 1-9 then 0. Ranges below are expressed against this ordering.
_ISO_ORDER = "ABCDEFGHJKLMNPRSTUVWXYZ1234567890"


def _rank(ch: str) -> int:
    idx = _ISO_ORDER.find(ch.upper())
    return idx if idx >= 0 else -1


# (first_char, second_char_start, second_char_end, country)
_COUNTRY_RANGES: list[tuple[str, str, str, str]] = [
    ("A", "A", "H", "South Africa"),
    ("A", "J", "N", "Ivory Coast"),
    ("B", "A", "E", "Angola"),
    ("B", "F", "K", "Kenya"),
    ("B", "L", "R", "Tanzania"),
    ("C", "A", "E", "Benin"),
    ("C", "F", "K", "Madagascar"),
    ("C", "L", "R", "Tunisia"),
    ("D", "A", "E", "Egypt"),
    ("D", "F", "K", "Morocco"),
    ("D", "L", "R", "Zambia"),
    ("E", "A", "E", "Ethiopia"),
    ("E", "F", "K", "Mozambique"),
    ("F", "A", "E", "Ghana"),
    ("F", "F", "K", "Nigeria"),
    ("J", "A", "0", "Japan"),
    ("K", "A", "E", "Sri Lanka"),
    ("K", "F", "K", "Israel"),
    ("K", "L", "R", "South Korea"),
    ("K", "S", "0", "Kazakhstan"),
    ("L", "A", "0", "China"),
    ("M", "A", "E", "India"),
    ("M", "F", "K", "Indonesia"),
    ("M", "L", "R", "Thailand"),
    ("M", "S", "0", "Myanmar"),
    ("N", "A", "E", "Iran"),
    ("N", "F", "K", "Pakistan"),
    ("N", "L", "R", "Turkey"),
    ("P", "A", "E", "Philippines"),
    ("P", "F", "K", "Singapore"),
    ("P", "L", "R", "Malaysia"),
    ("R", "A", "E", "United Arab Emirates"),
    ("R", "F", "K", "Taiwan"),
    ("R", "L", "R", "Vietnam"),
    ("R", "S", "0", "Saudi Arabia"),
    ("S", "A", "M", "United Kingdom"),
    ("S", "N", "T", "Germany"),
    ("S", "U", "Z", "Poland"),
    ("S", "1", "4", "Latvia"),
    ("T", "A", "H", "Switzerland"),
    ("T", "J", "P", "Czech Republic"),
    ("T", "R", "V", "Hungary"),
    ("T", "W", "1", "Portugal"),
    ("U", "H", "M", "Denmark"),
    ("U", "N", "T", "Ireland"),
    ("U", "U", "Z", "Romania"),
    ("U", "5", "7", "Slovakia"),
    ("V", "A", "E", "Austria"),
    ("V", "F", "R", "France"),
    ("V", "S", "W", "Spain"),
    ("V", "X", "2", "Serbia"),
    ("V", "3", "5", "Croatia"),
    ("V", "6", "0", "Estonia"),
    ("W", "A", "0", "Germany"),
    ("X", "A", "E", "Bulgaria"),
    ("X", "F", "K", "Greece"),
    ("X", "L", "R", "Netherlands"),
    ("X", "S", "W", "Russia"),
    ("X", "X", "2", "Luxembourg"),
    ("X", "3", "0", "Russia"),
    ("Y", "A", "E", "Belgium"),
    ("Y", "F", "K", "Finland"),
    ("Y", "L", "R", "Malta"),
    ("Y", "S", "W", "Sweden"),
    ("Y", "X", "2", "Norway"),
    ("Y", "3", "0", "Belarus"),
    ("Z", "A", "R", "Italy"),
    ("Z", "X", "2", "Slovenia"),
    ("Z", "3", "5", "Lithuania"),
    ("Z", "6", "0", "Russia"),
    ("1", "A", "0", "United States"),
    ("2", "A", "0", "Canada"),
    ("3", "A", "7", "Mexico"),
    ("3", "8", "0", "Costa Rica"),
    ("4", "A", "0", "United States"),
    ("5", "A", "0", "United States"),
    ("6", "A", "W", "Australia"),
    ("7", "A", "E", "New Zealand"),
    ("8", "A", "E", "Argentina"),
    ("8", "F", "K", "Chile"),
    ("8", "L", "R", "Ecuador"),
    ("8", "S", "W", "Peru"),
    ("8", "X", "2", "Venezuela"),
    ("9", "A", "E", "Brazil"),
    ("9", "F", "K", "Colombia"),
    ("9", "L", "R", "Paraguay"),
    ("9", "S", "W", "Uruguay"),
    ("9", "X", "2", "Trinidad and Tobago"),
    ("9", "3", "9", "Brazil"),
]


def decode_country(vin_or_wmi: str) -> str | None:
    """Country of manufacture from the first two VIN characters."""
    if len(vin_or_wmi) < 2:
        return None
    first, second = vin_or_wmi[0].upper(), vin_or_wmi[1].upper()
    r = _rank(second)
    if r < 0:
        return None
    for f, lo, hi, country in _COUNTRY_RANGES:
        if f != first:
            continue
        if _rank(lo) <= r <= _rank(hi):
            return country
    return None


# --- Manufacturer table ------------------------------------------------------
# Curated list of WMIs commonly seen in North American and European fleets.
# Deliberately partial: an unmatched WMI yields None rather than a fabricated
# manufacturer name.
WMI_MANUFACTURERS: dict[str, str] = {
    # BMW Group
    "WBA": "BMW AG",
    "WBS": "BMW M GmbH",
    "WBY": "BMW AG",
    "WBX": "BMW AG",
    "5UX": "BMW Manufacturing Co. (Spartanburg, USA)",
    "5UM": "BMW M (Spartanburg, USA)",
    "5YM": "BMW M (Spartanburg, USA)",
    "4US": "BMW Manufacturing Co. (USA)",
    "WMW": "MINI (BMW Group)",
    "WMX": "MINI (BMW Group)",
    # Volkswagen Group
    "WVW": "Volkswagen AG",
    "WV1": "Volkswagen Commercial Vehicles",
    "WV2": "Volkswagen Commercial Vehicles",
    "WVG": "Volkswagen AG",
    "3VW": "Volkswagen de Mexico",
    "1VW": "Volkswagen Group of America",
    "WAU": "Audi AG",
    "WA1": "Audi AG (SUV)",
    "WUA": "Audi Sport GmbH",
    "TRU": "Audi Hungaria",
    "WP0": "Porsche AG",
    "WP1": "Porsche AG (SUV)",
    "VSS": "SEAT S.A.",
    "TMB": "Skoda Auto",
    "ZHW": "Automobili Lamborghini",
    "ZAM": "Maserati S.p.A.",
    # Mercedes-Benz
    "WDD": "Mercedes-Benz AG",
    "WDB": "Mercedes-Benz AG",
    "WDC": "Mercedes-Benz AG (SUV)",
    "WDF": "Mercedes-Benz Vans",
    "W1K": "Mercedes-Benz AG",
    "W1N": "Mercedes-Benz AG (SUV)",
    "4JG": "Mercedes-Benz USA (Tuscaloosa)",
    "55S": "Mercedes-Benz USA",
    # Stellantis / FCA
    "1C3": "Chrysler",
    "1C4": "Chrysler (SUV)",
    "1C6": "Ram Trucks",
    "2C3": "Chrysler Canada",
    "3C4": "Chrysler Mexico",
    "1J4": "Jeep",
    "1J8": "Jeep",
    "ZFA": "Fiat Group",
    "ZAR": "Alfa Romeo",
    "ZFF": "Ferrari S.p.A.",
    # Ford
    "1FA": "Ford Motor Company",
    "1FB": "Ford Motor Company",
    "1FC": "Ford Motor Company",
    "1FD": "Ford Motor Company",
    "1FM": "Ford Motor Company (SUV)",
    "1FT": "Ford Motor Company (Truck)",
    "2FA": "Ford Motor Company of Canada",
    "3FA": "Ford Motor Company Mexico",
    "WF0": "Ford Werke GmbH",
    "5LM": "Lincoln",
    "1LN": "Lincoln",
    # General Motors
    "1G1": "Chevrolet",
    "1G4": "Buick",
    "1G6": "Cadillac",
    "1GC": "Chevrolet Truck",
    "1GK": "GMC (SUV)",
    "1GT": "GMC Truck",
    "1GY": "Cadillac (SUV)",
    "2G1": "General Motors Canada",
    "3G1": "General Motors Mexico",
    "KL4": "Buick (GM Korea)",
    # Toyota / Lexus
    "JTD": "Toyota Motor Corporation",
    "JTE": "Toyota Motor Corporation (SUV)",
    "JTM": "Toyota Motor Corporation",
    "JTN": "Toyota Motor Corporation",
    "JTH": "Lexus",
    "JTJ": "Lexus (SUV)",
    "4T1": "Toyota Motor Manufacturing (USA)",
    "5TD": "Toyota Motor Manufacturing Indiana",
    "2T1": "Toyota Motor Manufacturing Canada",
    # Honda / Acura
    "JHM": "Honda Motor Co.",
    "JHL": "Honda Motor Co. (SUV)",
    "1HG": "Honda of America Mfg.",
    "2HG": "Honda of Canada Mfg.",
    "5FN": "Honda of America Mfg. (SUV)",
    "5J6": "Honda of America Mfg.",
    "19U": "Acura",
    "5J8": "Acura (SUV)",
    "JH4": "Acura",
    # Nissan / Infiniti
    "JN1": "Nissan Motor Co.",
    "JN8": "Nissan Motor Co. (SUV)",
    "1N4": "Nissan North America",
    "5N1": "Nissan North America (SUV)",
    "JNK": "Infiniti",
    "JNR": "Infiniti (SUV)",
    # Korean
    "KMH": "Hyundai Motor Company",
    "KM8": "Hyundai Motor Company (SUV)",
    "5NP": "Hyundai Motor Manufacturing Alabama",
    "KNA": "Kia Corporation",
    "KND": "Kia Corporation (SUV)",
    "5XY": "Kia Georgia",
    "KNM": "Renault Samsung Motors",
    "KL7": "GM Korea",
    "KPT": "SsangYong Motor",
    # Subaru / Mazda / Mitsubishi
    "JF1": "Fuji Heavy Industries (Subaru)",
    "JF2": "Subaru (SUV)",
    "4S3": "Subaru of Indiana Automotive",
    "4S4": "Subaru of Indiana (SUV)",
    "JM1": "Mazda Motor Corporation",
    "JM3": "Mazda Motor Corporation (SUV)",
    "3MZ": "Mazda Motor de Mexico",
    "JA3": "Mitsubishi Motors",
    "JA4": "Mitsubishi Motors (SUV)",
    # Volvo / JLR / specialty
    "YV1": "Volvo Car Corporation",
    "YV4": "Volvo Car Corporation (SUV)",
    "LYV": "Volvo Cars (China)",
    "SAL": "Land Rover",
    "SAJ": "Jaguar Cars",
    "SAD": "Jaguar Land Rover",
    "SCC": "Lotus Cars",
    "SCB": "Bentley Motors",
    "SCA": "Rolls-Royce Motor Cars",
    # EV / newer entrants
    "5YJ": "Tesla, Inc.",
    "7SA": "Tesla, Inc.",
    "LRW": "Tesla (Shanghai)",
    "7FC": "Rivian Automotive",
    "7PD": "Lucid Motors",
}


def decode_manufacturer(vin_or_wmi: str) -> str | None:
    """Manufacturer from the 3-character WMI, or None if not in the table."""
    if len(vin_or_wmi) < 3:
        return None
    return WMI_MANUFACTURERS.get(vin_or_wmi[:3].upper())


def wmi_of(vin: str) -> str | None:
    """The WMI portion of a VIN.

    Manufacturers producing fewer than 1000 vehicles/year use '9' as the third
    character; for those, characters 12-14 complete the identifier.
    """
    if len(vin) < 3:
        return None
    wmi = vin[:3].upper()
    if wmi[2] == "9" and len(vin) >= 17:
        return wmi + vin[11:14].upper()
    return wmi


def is_small_manufacturer(vin: str) -> bool:
    return len(vin) >= 3 and vin[2].upper() == "9"
