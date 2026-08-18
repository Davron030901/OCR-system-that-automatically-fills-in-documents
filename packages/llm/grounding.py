"""Hallucination guard.

An LLM asked to map OCR text into a schema will occasionally produce a
plausible value that appears nowhere in the input. For a chat product that is
an annoyance. For a system that fills legal documents with passport numbers it
is unacceptable.

The rule enforced here: every value the model returns must be traceable to the
OCR text. Anything that is not gets discarded and flagged, never quietly kept.

Numbers are held to a stricter standard than names. A name may survive fuzzy
matching because OCR routinely confuses similar glyphs and the meaning
survives. An identifier may not: a PINFL one digit away from the text is a
different human being, so only exact matches pass.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from packages.schema.translit import normalize_apostrophes

DEFAULT_THRESHOLD = 90

# Fields where a near miss means a different person or document.
EXACT_MATCH_FIELDS = {
    "person.pinfl",
    "documents.0.doc_number",
    "documents.0.doc_series",
    "education.diploma_number",
}


@dataclass
class GroundingReport:
    confirmed: dict[str, str] = field(default_factory=dict)
    rejected: dict[str, str] = field(default_factory=dict)   # path -> value
    warnings: list[str] = field(default_factory=list)

    @property
    def rejection_rate(self) -> float:
        total = len(self.confirmed) + len(self.rejected)
        return len(self.rejected) / total if total else 0.0


def _normalize(text: str) -> str:
    """Aggressive normalisation for comparison only, never for storage."""
    s = normalize_apostrophes(text) or ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.upper()
    return re.sub(r"[^A-Z0-9\u0400-\u04FF]", "", s)


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


_DATE_PATTERNS = [
    re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})"),   # ISO-ish
    re.compile(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})"),   # DD.MM.YYYY
]


def _date_tokens(text: str) -> set[tuple[int, int, int]]:
    """Every (y, m, d) triple appearing in the text, format-agnostic."""
    found: set[tuple[int, int, int]] = set()
    for i, pat in enumerate(_DATE_PATTERNS):
        for m in pat.finditer(text):
            a, b, c = (int(g) for g in m.groups())
            found.add((a, b, c) if i == 0 else (c, b, a))
    # Bare YYMMDD / YYYYMMDD runs, as they appear in an MRZ.
    for m in re.finditer(r"\b(\d{8})\b", text):
        s = m.group(1)
        found.add((int(s[:4]), int(s[4:6]), int(s[6:8])))
    return found


def _is_date(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()))


def verify_value(path: str, value: str, ocr_text: str,
                 threshold: int = DEFAULT_THRESHOLD) -> tuple[bool, str | None]:
    """Check a single value against the OCR text.

    Returns (accepted, reason_if_rejected).
    """
    if value is None or not str(value).strip():
        return True, None                      # nothing asserted, nothing to check

    value = str(value).strip()
    hay_norm = _normalize(ocr_text)

    if _is_date(value):
        y, m, d = (int(x) for x in value.split("-"))
        if (y, m, d) in _date_tokens(ocr_text):
            return True, None
        # Fall back to digits appearing together, e.g. '150395' from an MRZ.
        compact = f"{d:02d}{m:02d}{y % 100:02d}"
        if compact in _digits(ocr_text) or f"{y%100:02d}{m:02d}{d:02d}" in _digits(ocr_text):
            return True, None
        return False, "date not present in the recognised text"

    if path in EXACT_MATCH_FIELDS or value.isdigit():
        needle = _digits(value) or _normalize(value)
        hay = _digits(ocr_text) if value.isdigit() else hay_norm
        if needle and needle in hay:
            return True, None
        return False, "identifier not found verbatim in the recognised text"

    needle = _normalize(value)
    if not needle:
        return True, None
    if needle in hay_norm:
        return True, None

    # Names may be split across OCR lines, so compare against a sliding window.
    if fuzz.partial_ratio(needle, hay_norm) >= threshold:
        return True, None

    return False, "value not supported by the recognised text"


def verify(llm_output: dict[str, str | None], ocr_text: str,
           threshold: int = DEFAULT_THRESHOLD) -> tuple[dict[str, str | None],
                                                        GroundingReport]:
    """Filter an LLM's field map down to what the OCR text actually supports.

    Args:
        llm_output: {dotted_field_path: value}
        ocr_text: everything the local OCR produced for this document.

    Returns the filtered map (rejected entries become None) and a report.
    """
    report = GroundingReport()
    cleaned: dict[str, str | None] = {}

    for path, value in llm_output.items():
        if value is None:
            cleaned[path] = None
            continue
        ok, reason = verify_value(path, str(value), ocr_text, threshold)
        if ok:
            cleaned[path] = value
            report.confirmed[path] = str(value)
        else:
            cleaned[path] = None
            report.rejected[path] = str(value)
            report.warnings.append(
                f"'{path}' qiymati rad etildi ({reason}) - model tasdiqlanmagan "
                "ma'lumot qaytardi"
            )

    return cleaned, report
