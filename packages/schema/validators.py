"""Deterministic validators.

These are what allow the system to say `validated=True` about a value. No
model output ever earns that flag; only arithmetic does.

The MRZ check-digit machinery here is the single highest-leverage piece of
code in the project: it turns OCR from "probably right" into "provably right,
or known to be wrong".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from rapidfuzz import fuzz

# --------------------------------------------------------------------------
# ICAO 9303 check digits
# --------------------------------------------------------------------------

_WEIGHTS = (7, 3, 1)


def char_value(ch: str) -> int:
    """Map an MRZ character to its numeric value.

    Digits map to themselves, A-Z map to 10-35, filler '<' maps to 0.
    """
    if ch.isdigit():
        return int(ch)
    if ch == "<":
        return 0
    up = ch.upper()
    if "A" <= up <= "Z":
        return ord(up) - 55  # 'A' -> 10
    raise ValueError(f"invalid MRZ character: {ch!r}")


def check_digit(s: str) -> int:
    """ICAO 9303 mod-10 check digit with repeating 7-3-1 weights."""
    total = 0
    for i, ch in enumerate(s):
        total += char_value(ch) * _WEIGHTS[i % 3]
    return total % 10


def verify_check_digit(field_value: str, digit: str) -> bool:
    """True when `digit` is the correct check digit for `field_value`.

    A '<' in the check-digit position means "not used" and is treated as
    valid only when the field itself is entirely filler.
    """
    if digit == "<":
        return set(field_value) <= {"<"}
    if not digit.isdigit():
        return False
    try:
        return check_digit(field_value) == int(digit)
    except ValueError:
        return False


# --------------------------------------------------------------------------
# MRZ parsing and validation
# --------------------------------------------------------------------------


@dataclass
class MRZField:
    name: str
    raw: str
    check_digit: str | None = None
    valid: bool = True          # True when there is no check digit to fail


@dataclass
class MRZValidation:
    format: str                                   # "TD1" | "TD3"
    ok: bool
    fields: dict[str, MRZField] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def all_check_digits_pass(self) -> bool:
        return all(f.valid for f in self.fields.values())


def _norm_lines(lines: list[str], count: int, width: int) -> list[str] | None:
    """Pad/trim lines to the exact TD shape. Returns None if unusable."""
    if len(lines) != count:
        return None
    out = []
    for ln in lines:
        ln = ln.strip().upper().replace(" ", "<")
        if len(ln) < width:
            ln = ln.ljust(width, "<")
        out.append(ln[:width])
    return out


def validate_td1(lines: list[str]) -> MRZValidation:
    """TD1: national ID card. 3 lines of 30 characters.

    Line 1: [0:2] doc code, [2:5] issuing state, [5:14] doc number,
            [14] check digit, [15:30] optional data (PINFL in Uzbek cards)
    Line 2: [0:6] birth YYMMDD, [6] CD, [7] sex, [8:14] expiry YYMMDD,
            [14] CD, [15:18] nationality, [18:29] optional data 2,
            [29] composite CD
    Line 3: SURNAME<<GIVEN<PATRONYMIC
    """
    norm = _norm_lines(lines, 3, 30)
    v = MRZValidation(format="TD1", ok=False)
    if norm is None:
        v.errors.append("TD1 requires exactly 3 lines")
        return v
    l1, l2, l3 = norm

    doc_number, doc_cd = l1[5:14], l1[14]
    optional1 = l1[15:30]
    birth, birth_cd = l2[0:6], l2[6]
    sex = l2[7]
    expiry, expiry_cd = l2[8:14], l2[14]
    nationality = l2[15:18]
    optional2 = l2[18:29]
    composite_cd = l2[29]

    v.fields = {
        "doc_code": MRZField("doc_code", l1[0:2]),
        "issuing_state": MRZField("issuing_state", l1[2:5]),
        "doc_number": MRZField("doc_number", doc_number, doc_cd,
                               verify_check_digit(doc_number, doc_cd)),
        "optional1": MRZField("optional1", optional1),
        "birth_date": MRZField("birth_date", birth, birth_cd,
                               verify_check_digit(birth, birth_cd)),
        "sex": MRZField("sex", sex),
        "expiry_date": MRZField("expiry_date", expiry, expiry_cd,
                                verify_check_digit(expiry, expiry_cd)),
        "nationality": MRZField("nationality", nationality),
        "optional2": MRZField("optional2", optional2),
        "names": MRZField("names", l3),
    }

    # Composite covers positions 6-30 of line 1 and 1-7, 9-15, 19-29 of line 2.
    composite_src = l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]
    composite_ok = verify_check_digit(composite_src, composite_cd)
    v.fields["composite"] = MRZField("composite", composite_src,
                                     composite_cd, composite_ok)

    for f in v.fields.values():
        if not f.valid:
            v.errors.append(f"check digit failed: {f.name}")
    v.ok = not v.errors
    return v


def validate_td3(lines: list[str]) -> MRZValidation:
    """TD3: passport. 2 lines of 44 characters.

    Line 2: [0:9] doc number, [9] CD, [10:13] nationality,
            [13:19] birth YYMMDD, [19] CD, [20] sex, [21:27] expiry, [27] CD,
            [28:42] personal number, [42] CD, [43] composite CD
    """
    norm = _norm_lines(lines, 2, 44)
    v = MRZValidation(format="TD3", ok=False)
    if norm is None:
        v.errors.append("TD3 requires exactly 2 lines")
        return v
    l1, l2 = norm

    doc_number, doc_cd = l2[0:9], l2[9]
    nationality = l2[10:13]
    birth, birth_cd = l2[13:19], l2[19]
    sex = l2[20]
    expiry, expiry_cd = l2[21:27], l2[27]
    personal, personal_cd = l2[28:42], l2[42]
    composite_cd = l2[43]

    v.fields = {
        "doc_code": MRZField("doc_code", l1[0:2]),
        "issuing_state": MRZField("issuing_state", l1[2:5]),
        "names": MRZField("names", l1[5:44]),
        "doc_number": MRZField("doc_number", doc_number, doc_cd,
                               verify_check_digit(doc_number, doc_cd)),
        "nationality": MRZField("nationality", nationality),
        "birth_date": MRZField("birth_date", birth, birth_cd,
                               verify_check_digit(birth, birth_cd)),
        "sex": MRZField("sex", sex),
        "expiry_date": MRZField("expiry_date", expiry, expiry_cd,
                                verify_check_digit(expiry, expiry_cd)),
        "personal_number": MRZField("personal_number", personal, personal_cd,
                                    verify_check_digit(personal, personal_cd)),
    }

    composite_src = l2[0:10] + l2[13:20] + l2[21:43]
    composite_ok = verify_check_digit(composite_src, composite_cd)
    v.fields["composite"] = MRZField("composite", composite_src,
                                     composite_cd, composite_ok)

    for f in v.fields.values():
        if not f.valid:
            v.errors.append(f"check digit failed: {f.name}")
    v.ok = not v.errors
    return v


# --------------------------------------------------------------------------
# PINFL (JSHSHIR)
# --------------------------------------------------------------------------

_PINFL_RE = re.compile(r"^\d{14}$")

# First digit encodes century + sex.
_PINFL_CENTURY_SEX = {
    "1": (1800, "M"), "2": (1800, "F"),
    "3": (1900, "M"), "4": (1900, "F"),
    "5": (2000, "M"), "6": (2000, "F"),
}


def validate_pinfl(pinfl: str) -> tuple[bool, dict]:
    """Structural validation of a 14-digit Uzbek personal identification number.

    Layout in common use:
        [0]      century + sex marker
        [1:7]    birth date DDMMYY
        [7:9]    region code
        [9:13]   sequence
        [13]     control digit

    IMPORTANT — READ BEFORE TRUSTING THIS FUNCTION
    ----------------------------------------------
    The structural decoding above is well attested, but I could not verify the
    official CONTROL DIGIT algorithm from an authoritative source. Implementing
    a guessed checksum would be worse than none: it would reject valid numbers
    and give false confidence about invalid ones.

    So: structure is checked, the control digit is NOT. `checksum_verified` is
    always False and the caller must never set FieldValue.validated=True on the
    basis of this function alone.

    To close this TODO, collect 50-100 known-valid PINFLs and brute-force the
    weight vector: for w in itertools.product(range(1,10), repeat=13), test
    whether sum(d_i * w_i) % 10 (and % 11) reproduces d_13 for all samples.
    A single consistent vector across 50 samples is almost certainly the real
    algorithm. Then implement it here and flip `checksum_verified`.
    """
    info: dict = {
        "structure_valid": False,
        "checksum_verified": False,   # deliberately never True — see docstring
        "birth_date": None,
        "sex": None,
        "region_code": None,
        "errors": [],
    }

    if not pinfl or not _PINFL_RE.match(pinfl):
        info["errors"].append("PINFL must be exactly 14 digits")
        return False, info

    marker = pinfl[0]
    if marker not in _PINFL_CENTURY_SEX:
        info["errors"].append(f"unknown century/sex marker: {marker}")
        return False, info
    century, sex = _PINFL_CENTURY_SEX[marker]
    info["sex"] = sex

    try:
        dd, mm, yy = int(pinfl[1:3]), int(pinfl[3:5]), int(pinfl[5:7])
        info["birth_date"] = date(century + yy, mm, dd)
    except ValueError:
        info["errors"].append("invalid birth date encoded in PINFL")
        return False, info

    info["region_code"] = pinfl[7:9]
    info["structure_valid"] = True
    return True, info


# --------------------------------------------------------------------------
# Cross-field logic
# --------------------------------------------------------------------------


def validate_date_logic(
    birth: date | None, issue: date | None, expiry: date | None,
    today: date | None = None,
) -> list[str]:
    """Return human-readable warnings (Uzbek) about implausible dates."""
    today = today or date.today()
    w: list[str] = []
    if birth and birth.year < 1900:
        w.append("Tug'ilgan sana 1900-yildan oldin — o'qishda xato bo'lishi mumkin")
    if birth and birth > today:
        w.append("Tug'ilgan sana kelajakda")
    if birth and issue and issue < birth:
        w.append("Hujjat berilgan sana tug'ilgan sanadan oldin")
    if issue and expiry and expiry <= issue:
        w.append("Amal qilish muddati berilgan sanadan oldin yoki teng")
    if expiry and expiry < today:
        w.append("Hujjat muddati tugagan")
    return w


def _norm_for_compare(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def cross_check(mrz: dict[str, str | None],
                visual: dict[str, str | None],
                threshold: int = 85) -> list[str]:
    """Compare MRZ against the visual zone.

    A mismatch between these two zones is the strongest single fraud signal
    available without specialist equipment, so every discrepancy surfaces as a
    warning rather than being silently reconciled.
    """
    warnings: list[str] = []
    for key in set(mrz) & set(visual):
        a, b = mrz.get(key), visual.get(key)
        if not a or not b:
            continue
        na, nb = _norm_for_compare(a), _norm_for_compare(b)
        if not na or not nb:
            continue
        if na == nb:
            continue
        # Numbers must match exactly; one digit apart is a different person.
        if na.isdigit() or nb.isdigit():
            warnings.append(
                f"MRZ va vizual zona mos kelmadi ({key}): '{a}' / '{b}'")
            continue
        if fuzz.ratio(na, nb) < threshold:
            warnings.append(
                f"MRZ va vizual zona mos kelmadi ({key}): '{a}' / '{b}'")
    return warnings


def parse_mrz_date(yymmdd: str, kind: str = "birth",
                   today: date | None = None) -> date | None:
    """Convert a 6-digit MRZ date to a real date.

    Century inference: expiry dates are always in the 2000s; birth dates use
    the pivot rule (a YY greater than the current 2-digit year means 19xx).
    """
    if len(yymmdd) != 6 or not yymmdd.isdigit():
        return None
    today = today or date.today()
    yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    if kind == "expiry":
        year = 2000 + yy
    else:
        year = 1900 + yy if yy > (today.year % 100) else 2000 + yy
    try:
        return date(year, mm, dd)
    except ValueError:
        return None
