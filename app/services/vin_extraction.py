"""Extract VINs from spreadsheet cells and free text.

Real-world vehicle spreadsheets do not have a tidy VIN column. VINs arrive
buried in remarks fields, prefixed with "VIN:", "CH#" or "Chassis No.", split
by hyphens or spaces, and mixed several to a cell.

The extraction runs in two stages, deliberately:

1. **Find candidates permissively** - any 17-character alphanumeric run,
   tolerating separators. This is intentionally wider than the VIN charset.
2. **Classify each candidate, and say why.** A token containing the letter O is
   almost always a mistyped zero; silently skipping it loses a real vehicle and
   tells the operator nothing. Rejected candidates are returned with a reason
   rather than discarded.

That two-stage split is what stops the extractor from either inventing VINs out
of prose or quietly dropping ones that need a human eye.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.vin.validate import VIN_ALLOWED, analyze_vin

#: Labels that introduce a VIN in a spreadsheet cell. `CHASSIS` needs its own
#: alternative because `\bCH\b` will not match inside the longer word.
IDENTIFIER_PATTERN = re.compile(
    r"\b(?:VIN|CH|CHASSIS|CHASIS)\b\s*(?:N[O°]\.?|NUM(?:BER)?|#)?\s*[#:.\-]?\s*",
    re.IGNORECASE,
)

#: A 17-character alphanumeric run, allowing up to two separator characters
#: between adjacent characters so "JTM AB3 FV00 D123456" and
#: "JTMAB3-FV00-D123456" both match.
#:
#: Two details carry weight here:
#:
#: * The `{0,2}` bound. With an unbounded `[\s-]*` the pattern happily stitches
#:   17 letters out of a sentence, turning "THE RED CAR WAS PARKED" into a VIN.
#: * The lookaround anchors. Without them an 18-character run matches its first
#:   17 characters, silently inventing a plausible-looking VIN out of a token
#:   that was never one.
CANDIDATE_PATTERN = re.compile(
    r"(?<![A-Z0-9])[A-Z0-9](?:[\s\-]{0,2}[A-Z0-9]){16}(?![A-Z0-9])"
)

#: Letters that never appear in a VIN; they exist as digits instead.
FORBIDDEN_LETTERS = "IOQ"


#: A field label carries punctuation or a "No./Number" token: "VIN:", "CH#",
#: "Chassis No.". The bare word inside a sentence - "no vin recorded" - does not,
#: and must not be treated as a label with a missing value.
_EXPLICIT_LABEL = re.compile(r"[#:.\-]|\bN[O°]\b|\bNUM", re.IGNORECASE)


def _is_explicit_label(matched: str) -> bool:
    return bool(_EXPLICIT_LABEL.search(matched))


def _normalise_label(raw: str) -> str | None:
    """Reduce a matched prefix like "Chassis No. " to a clean label."""
    letters = re.sub(r"[^A-Z]", "", raw.upper())
    if not letters:
        return None
    if letters.startswith("CHAS"):
        return "CHASSIS"
    if letters.startswith("VIN"):
        return "VIN"
    if letters.startswith("CH"):
        return "CH"
    return letters[:8]


class Verdict(str, Enum):
    """How much the extractor trusts a candidate."""

    CONFIRMED = "CONFIRMED"      # valid charset and the check digit validates
    UNVERIFIED = "UNVERIFIED"    # structurally fine, check digit does not match
    REJECTED = "REJECTED"        # not a VIN, with a stated reason


@dataclass(slots=True)
class Candidate:
    """One 17-character token found in a cell."""

    vin: str
    raw: str                      # as it appeared, separators intact
    verdict: Verdict
    reason: str | None = None
    label: str | None = None      # "VIN", "CH", "CHASSIS", or None if unlabelled
    check_digit_valid: bool | None = None
    #: Ordinary prose that merely happened to contain 17 letters. Rejected and
    #: not worth an operator's attention, so it is dropped rather than listed -
    #: otherwise every remarks column would fill the excluded report with noise.
    noise: bool = False

    @property
    def usable(self) -> bool:
        return self.verdict is not Verdict.REJECTED

    @property
    def worth_reporting(self) -> bool:
        return self.usable or not self.noise


@dataclass(slots=True)
class Occurrence:
    """A candidate together with where in the workbook it was found."""

    vin: str
    sheet: str
    row: int                      # 1-based Excel row, header included
    column: str
    original_text: str
    label: str | None
    verdict: Verdict
    reason: str | None
    check_digit_valid: bool | None

    def to_row(self) -> dict[str, Any]:
        return {
            "Sheet": self.sheet,
            "Excel Row": self.row,
            "Column": self.column,
            "VIN": self.vin or "(not found)",
            "Label": self.label or "(unlabelled)",
            "Status": self.verdict.value,
            "Check Digit": (
                "OK" if self.check_digit_valid
                else ("FAILED" if self.check_digit_valid is False else "n/a")
            ),
            "Note": self.reason or "",
            "Original Text": self.original_text,
        }


@dataclass
class ExtractionResult:
    occurrences: list[Occurrence] = field(default_factory=list)
    rejected: list[Occurrence] = field(default_factory=list)
    sheets_scanned: list[str] = field(default_factory=list)
    cells_scanned: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def unique_vins(self) -> list[str]:
        """Distinct usable VINs, in first-seen order."""
        seen: dict[str, None] = {}
        for occ in self.occurrences:
            seen.setdefault(occ.vin, None)
        return list(seen)

    @property
    def confirmed_count(self) -> int:
        return sum(1 for o in self.occurrences if o.verdict is Verdict.CONFIRMED)

    @property
    def unverified_count(self) -> int:
        return sum(1 for o in self.occurrences if o.verdict is Verdict.UNVERIFIED)


# --- Candidate classification ----------------------------------------------

def classify(raw: str, *, label: str | None = None) -> Candidate:
    """Turn a raw 17-character run into a classified candidate."""
    vin = re.sub(r"[\s\-]", "", raw).upper()
    digits = sum(1 for c in vin if c.isdigit())

    # Decided up front, because prose is usually rejected by the I/O/Q check
    # long before the digit test would ever run. Every real VIN carries a
    # check digit in position 9, so a 17-character run with no digits at all
    # and no "VIN:"/"CH:" label in front of it is a sentence, not a vehicle.
    prose = label is None and digits == 0

    if len(vin) != 17:
        return Candidate(vin, raw, Verdict.REJECTED,
                         f"{len(vin)} characters after removing separators, not 17.",
                         label, noise=prose)

    bad_letters = sorted({c for c in vin if c in FORBIDDEN_LETTERS})
    if bad_letters:
        # Very often a transcription slip: O for 0, I for 1 - worth surfacing.
        #
        # But unlabelled prose reaches this branch too, because English is full
        # of I and O: "duplicate of INV-001" collapses to DUPLICATEOFINV001.
        # A real VIN is digit-rich (17 characters carrying a serial number);
        # a sentence is letter-rich. That ratio separates them cleanly.
        word_like = label is None and digits <= 3
        return Candidate(
            vin, raw, Verdict.REJECTED,
            f"Contains {', '.join(bad_letters)}, which never appear in a VIN "
            f"(likely a mistyped 0 or 1).",
            label, noise=prose or word_like,
        )

    if any(c not in VIN_ALLOWED for c in vin):
        return Candidate(vin, raw, Verdict.REJECTED,
                         "Contains characters outside the VIN charset.",
                         label, noise=prose)

    analysis = analyze_vin(vin)
    check_ok = analysis.check_digit_valid

    if check_ok:
        return Candidate(vin, raw, Verdict.CONFIRMED, None, label, True)

    # The check digit did not validate. That is normal for vehicles not built to
    # the North American standard, so it is not disqualifying on its own - but a
    # token with no digits at all is prose, not a VIN.
    if digits == 0:
        return Candidate(
            vin, raw, Verdict.REJECTED,
            "No digits and the check digit does not validate; this is almost "
            "certainly ordinary text, not a VIN.",
            label, False,
            # An unlabelled digit-free run is just a sentence. A labelled one
            # means a "VIN:" was found without a usable VIN after it, which the
            # operator does want to see.
            noise=label is None,
        )
    if digits < 2 and label is None:
        return Candidate(
            vin, raw, Verdict.REJECTED,
            "Unlabelled, nearly all letters, and the check digit does not "
            "validate; treated as text rather than a VIN.",
            label, False, noise=True,
        )

    return Candidate(
        vin, raw, Verdict.UNVERIFIED,
        f"Check digit does not validate (expected "
        f"'{analysis.expected_check_digit}', found '{analysis.actual_check_digit}'). "
        f"Common for imported vehicles; verify against the document.",
        label, False,
    )


def extract_from_text(text: Any, *, require_label: bool = False) -> list[Candidate]:
    """Find every VIN candidate in a single cell.

    When ``require_label`` is true, only runs introduced by VIN/CH/CHASSIS are
    considered. Otherwise a bare 17-character token also counts - which is what
    a dedicated VIN column looks like.
    """
    if text is None:
        return []
    raw_text = str(text)
    if not raw_text.strip():
        return []
    upper = raw_text.upper()

    found: list[Candidate] = []
    seen: set[str] = set()
    covered: list[tuple[int, int]] = []

    # --- Pass 1: labelled candidates ---------------------------------------
    matches = list(IDENTIFIER_PATTERN.finditer(upper))
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(upper)
        section = upper[start:end]

        label = _normalise_label(match.group(0))

        # Only the FIRST run after the label belongs to it. "VIN: <vin>. Awaiting
        # inspection at port." must not hand the label to the trailing sentence
        # and report "AWAITINGINSPECTIO" as a VIN. Anything genuinely further
        # along is picked up unlabelled by pass 2, where the prose filter applies.
        candidate_match = CANDIDATE_PATTERN.search(section)
        if candidate_match is None:
            # The cell says "VIN:" or "CH:" but nothing usable follows - a
            # truncated or mistyped entry. Silently skipping it loses a vehicle
            # without telling anyone, so it is surfaced for review instead.
            #
            # Only for an explicit field label though: the bare word in
            # "no vin recorded" is prose, not an empty field.
            if not _is_explicit_label(match.group(0)):
                continue
            found.append(Candidate(
                vin="", raw=section.strip()[:40], verdict=Verdict.REJECTED,
                reason=f"A '{label}' label was found but no 17-character VIN "
                       f"follows it; the entry may be truncated.",
                label=label,
            ))
            continue

        candidate = classify(candidate_match.group(0), label=label)
        covered.append((start + candidate_match.start(), start + candidate_match.end()))
        if candidate.vin in seen:
            continue
        seen.add(candidate.vin)
        found.append(candidate)

    if require_label:
        return found

    # --- Pass 2: bare candidates, skipping regions already consumed --------
    for candidate_match in CANDIDATE_PATTERN.finditer(upper):
        span = candidate_match.span()
        if any(span[0] < c_end and c_start < span[1] for c_start, c_end in covered):
            continue
        candidate = classify(candidate_match.group(0), label=None)
        if candidate.vin in seen:
            continue
        seen.add(candidate.vin)
        found.append(candidate)

    return found


# --- Workbook scanning ------------------------------------------------------

def extract_from_dataframe(
    frame, sheet_name: str = "Sheet1", *, require_label: bool = False,
    header_offset: int = 2,
) -> ExtractionResult:
    """Scan every cell of a DataFrame.

    ``header_offset`` converts a 0-based DataFrame index to the row number the
    user sees in Excel: 2 accounts for the header row plus 1-based numbering.
    """
    result = ExtractionResult(sheets_scanned=[sheet_name])

    for row_index, row in frame.iterrows():
        for column in frame.columns:
            value = row[column]
            result.cells_scanned += 1
            for candidate in extract_from_text(value, require_label=require_label):
                occurrence = Occurrence(
                    vin=candidate.vin,
                    sheet=sheet_name,
                    row=(row_index if isinstance(row_index, int) else 0) + header_offset,
                    column=str(column),
                    original_text=str(value),
                    label=candidate.label,
                    verdict=candidate.verdict,
                    reason=candidate.reason,
                    check_digit_valid=candidate.check_digit_valid,
                )
                if candidate.usable:
                    result.occurrences.append(occurrence)
                elif candidate.worth_reporting:
                    result.rejected.append(occurrence)

    return result


def extract_from_workbook(source, *, require_label: bool = False) -> ExtractionResult:
    """Scan every sheet of a workbook (or a CSV).

    ``source`` is a path or a file-like object - Streamlit's uploaded file works
    directly. Every sheet is scanned, not just the first: VINs hide in the
    second tab more often than anyone would like.
    """
    import pandas as pd

    combined = ExtractionResult()

    name = getattr(source, "name", str(source))
    try:
        if str(name).lower().endswith((".csv", ".txt", ".tsv")):
            separator = "\t" if str(name).lower().endswith(".tsv") else None
            frames = {
                "CSV": pd.read_csv(source, dtype=str, sep=separator,
                                   engine="python", keep_default_na=False)
            }
        else:
            # sheet_name=None reads every sheet. dtype=str stops pandas turning
            # a numeric-looking VIN into a float and losing leading zeros.
            frames = pd.read_excel(source, sheet_name=None, dtype=str)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, not raised
        combined.errors.append(f"Could not read the file: {exc}")
        return combined

    if not frames:
        combined.errors.append("The file contains no readable sheets.")
        return combined

    for sheet_name, frame in frames.items():
        if frame is None or frame.empty:
            combined.sheets_scanned.append(str(sheet_name))
            continue
        part = extract_from_dataframe(
            frame, sheet_name=str(sheet_name), require_label=require_label
        )
        combined.occurrences.extend(part.occurrences)
        combined.rejected.extend(part.rejected)
        combined.sheets_scanned.extend(part.sheets_scanned)
        combined.cells_scanned += part.cells_scanned

    return combined


# --- Export -----------------------------------------------------------------

def to_workbook_bytes(result: ExtractionResult) -> bytes:
    """Build the two-sheet Excel export: every occurrence, and unique VINs."""
    import io

    import pandas as pd

    columns = ["Sheet", "Excel Row", "Column", "VIN", "Label", "Status",
               "Check Digit", "Note", "Original Text"]

    all_rows = [o.to_row() for o in result.occurrences]
    all_frame = pd.DataFrame(all_rows, columns=columns) if all_rows else pd.DataFrame(columns=columns)
    unique_frame = all_frame.drop_duplicates(subset=["VIN"]).copy()

    rejected_rows = [o.to_row() for o in result.rejected]
    rejected_frame = (
        pd.DataFrame(rejected_rows, columns=columns) if rejected_rows
        else pd.DataFrame([{**{c: "" for c in columns},
                            "Note": "No candidates were rejected."}], columns=columns)
    )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        all_frame.to_excel(writer, sheet_name="All VINs", index=False)
        unique_frame.to_excel(writer, sheet_name="Unique VINs", index=False)
        rejected_frame.to_excel(writer, sheet_name="Excluded", index=False)
    return buffer.getvalue()
