"""Parse a validated MRZ into canonical schema objects.

Confidence policy implemented here:
  * every check digit passes  -> 0.98 and validated=True
  * a field's own check digit fails -> that field drops to 0.4, validated=False
  * composite check digit fails -> a global warning, other fields keep theirs

Nothing is ever guessed. A field that cannot be read becomes None.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from packages.ml.mrz.correct import (
    FieldCorrection,
    correct_mrz_fields,
    disambiguate_with_composite,
    normalize_line,
)
from packages.schema.models import (
    FieldSource,
    FieldValue,
    IdentityDocument,
    Person,
    PersonName,
)
from packages.schema.translit import (
    latin_to_cyrillic,
    mrz_to_display,
    title_case_name,
)
from packages.schema.validators import (
    parse_mrz_date,
    validate_pinfl,
    validate_td1,
    validate_td3,
)

HIGH = 0.98
LOW = 0.40


@dataclass
class MRZResult:
    found: bool
    format: str | None = None
    reason: str | None = None
    person: Person = field(default_factory=Person)
    document: IdentityDocument | None = None
    warnings: list[str] = field(default_factory=list)
    corrections: list[FieldCorrection] = field(default_factory=list)
    ambiguous_fields: list[str] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)
    all_valid: bool = False


def _fv(value: str | None, valid: bool, bbox: list[int] | None = None,
        correction=None) -> FieldValue:
    """Build a FieldValue from an MRZ field.

    When a repair could not be corroborated we deliberately keep the value the
    recogniser actually READ and offer the repair as an alternative, rather
    than substituting it. Showing a silently altered document number is worse
    than showing a wrong one: the user proof-reads against the physical card,
    and a substituted value looks plausible enough to wave through.
    """
    fv = FieldValue(
        value=value or None,
        confidence=HIGH if valid else LOW,
        source=FieldSource.MRZ,
        validated=valid,
        bbox=bbox,
    )
    if correction is not None and correction.changed and not correction.trustworthy:
        fv.value = mrz_to_display(correction.original) or fv.value
        fv.alternatives = [mrz_to_display(c) or c
                           for c in correction.candidates[:5]]
        fv.confidence = LOW
        fv.validated = False
        fv.note = ("Nazorat raqami mos kelmadi — hujjatdagi qiymat bilan "
                   "solishtiring")
    return fv


def _split_names(mrz_names: str) -> tuple[str, str, str]:
    """'ALIYEV<<SHOHRUH<AKMAL' -> ('ALIYEV', 'SHOHRUH', 'AKMAL')."""
    parts = mrz_names.split("<<", 1)
    surname = mrz_to_display(parts[0]) or ""
    given, patronymic = "", ""
    if len(parts) > 1:
        givens = [g for g in parts[1].split("<") if g]
        if givens:
            given = givens[0]
        if len(givens) > 1:
            patronymic = " ".join(givens[1:])
    return surname, given, patronymic


def _build_name(mrz_names: str) -> PersonName:
    surname, given, patronymic = _split_names(mrz_names)
    name = PersonName()
    for latin_attr, cyr_attr, raw in (
        ("surname_latin", "surname_cyrillic", surname),
        ("given_name_latin", "given_name_cyrillic", given),
        ("patronymic_latin", "patronymic_cyrillic", patronymic),
    ):
        display = title_case_name(raw) if raw else None
        setattr(name, latin_attr, FieldValue(
            value=display, confidence=0.95 if display else 0.0,
            source=FieldSource.MRZ, validated=False,
        ))
        # The Cyrillic form is derived, never read — mark it as such so the
        # review UI can show that it was computed rather than extracted.
        setattr(name, cyr_attr, FieldValue(
            value=latin_to_cyrillic(display) if display else None,
            confidence=0.75 if display else 0.0,
            source=FieldSource.DERIVED, validated=False,
        ))
    return name


def parse_td1(lines: list[str]) -> MRZResult:
    """Parse a TD1 (national ID card) MRZ: 3 lines of 30 characters."""
    norm = [normalize_line(ln, 30) for ln in lines]
    if len(norm) != 3:
        return MRZResult(found=False, reason="TD1 requires 3 lines")

    l1, l2, l3 = norm
    raw_fields = {
        "doc_number": (l1[5:14], l1[14]),
        "birth_date": (l2[0:6], l2[6]),
        "expiry_date": (l2[8:14], l2[14]),
    }
    corrected, logs = correct_mrz_fields(raw_fields)

    # The composite check digit spans several fields at once and is what
    # actually resolves per-field ambiguity. Feed it the candidate sets.
    def _composite_td1(vals: dict[str, str]) -> tuple[str, str]:
        a1 = l1[:5] + vals["doc_number"] + l1[14:]
        a2 = vals["birth_date"] + l2[6:8] + vals["expiry_date"] + l2[14:]
        return a1[5:30] + a2[0:7] + a2[8:15] + a2[18:29], a2[29]

    corrected = disambiguate_with_composite(corrected, _composite_td1)
    logs = [c for c in corrected.values() if c.changed or c.ambiguous]

    l1 = l1[:5] + corrected["doc_number"].value + l1[14:]
    l2 = (corrected["birth_date"].value + l2[6:8]
          + corrected["expiry_date"].value + l2[14:])

    v = validate_td1([l1, l2, l3])
    res = MRZResult(found=True, format="TD1", raw_lines=[l1, l2, l3],
                    corrections=logs, all_valid=v.ok)
    res.ambiguous_fields = [c.name for c in corrected.values() if c.ambiguous]
    for c in corrected.values():
        if c.ambiguous:
            res.warnings.append(
                f"MRZ maydoni '{c.name}' uchun bir nechta variant mos keldi "
                f"({', '.join(c.candidates[:3])}) - qo'lda tekshiring")

    doc = IdentityDocument(doc_type="id_card")
    doc.doc_number = _fv(mrz_to_display(corrected["doc_number"].value),
                         corrected["doc_number"].trustworthy,
                         correction=corrected["doc_number"])

    birth = parse_mrz_date(corrected["birth_date"].value, "birth")
    expiry = parse_mrz_date(corrected["expiry_date"].value, "expiry")
    doc.expiry_date = _fv(expiry.isoformat() if expiry else None,
                          corrected["expiry_date"].trustworthy)

    person = Person(name=_build_name(l3))
    person.birth_date = _fv(birth.isoformat() if birth else None,
                            corrected["birth_date"].trustworthy)
    person.sex = _fv({"M": "M", "F": "F"}.get(l2[7]), l2[7] in ("M", "F"))
    person.nationality = _fv(mrz_to_display(l2[15:18]), True)
    person.citizenship = _fv(mrz_to_display(l2[15:18]), True)

    # Uzbek ID cards normally carry the PINFL in the optional data field.
    optional = l1[15:30].replace("<", "").strip()
    if optional:
        digits = "".join(ch for ch in optional if ch.isdigit())
        if len(digits) == 14:
            ok, info = validate_pinfl(digits)
            # Structure only — see validate_pinfl docstring. Never validated.
            person.pinfl = FieldValue(
                value=digits, confidence=0.9 if ok else 0.4,
                source=FieldSource.MRZ, validated=False,
                note=None if ok else "; ".join(info["errors"]),
            )
            if ok and info["sex"] and person.sex.value \
                    and info["sex"] != person.sex.value:
                res.warnings.append(
                    "JSHSHIR dagi jins MRZ dagi jinsga mos kelmadi")
            if ok and info["birth_date"] and birth \
                    and info["birth_date"] != birth:
                res.warnings.append(
                    "JSHSHIR dagi tug'ilgan sana MRZ dagiga mos kelmadi")

    if not v.fields["composite"].valid:
        res.warnings.append(
            "MRZ umumiy nazorat raqami mos kelmadi — hujjatni qayta suratga oling")

    res.person = person
    res.document = doc
    return res


def parse_td3(lines: list[str]) -> MRZResult:
    """Parse a TD3 (passport) MRZ: 2 lines of 44 characters."""
    norm = [normalize_line(ln, 44) for ln in lines]
    if len(norm) != 2:
        return MRZResult(found=False, reason="TD3 requires 2 lines")

    l1, l2 = norm
    raw_fields = {
        "doc_number": (l2[0:9], l2[9]),
        "birth_date": (l2[13:19], l2[19]),
        "expiry_date": (l2[21:27], l2[27]),
        "personal_number": (l2[28:42], l2[42]),
    }
    corrected, logs = correct_mrz_fields(raw_fields)

    def _composite_td3(vals: dict[str, str]) -> tuple[str, str]:
        a2 = (vals["doc_number"] + l2[9:13] + vals["birth_date"] + l2[19:21]
              + vals["expiry_date"] + l2[27:28] + vals["personal_number"]
              + l2[42:])
        return a2[0:10] + a2[13:20] + a2[21:43], a2[43]

    corrected = disambiguate_with_composite(corrected, _composite_td3)
    logs = [c for c in corrected.values() if c.changed or c.ambiguous]

    l2 = (corrected["doc_number"].value + l2[9:13]
          + corrected["birth_date"].value + l2[19:21]
          + corrected["expiry_date"].value + l2[27:28]
          + corrected["personal_number"].value + l2[42:])

    v = validate_td3([l1, l2])
    res = MRZResult(found=True, format="TD3", raw_lines=[l1, l2],
                    corrections=logs, all_valid=v.ok)
    res.ambiguous_fields = [c.name for c in corrected.values() if c.ambiguous]
    for c in corrected.values():
        if c.ambiguous:
            res.warnings.append(
                f"MRZ maydoni '{c.name}' uchun bir nechta variant mos keldi "
                f"({', '.join(c.candidates[:3])}) - qo'lda tekshiring")

    doc = IdentityDocument(doc_type="passport")
    doc.doc_number = _fv(mrz_to_display(corrected["doc_number"].value),
                         corrected["doc_number"].trustworthy,
                         correction=corrected["doc_number"])

    birth = parse_mrz_date(corrected["birth_date"].value, "birth")
    expiry = parse_mrz_date(corrected["expiry_date"].value, "expiry")
    doc.expiry_date = _fv(expiry.isoformat() if expiry else None,
                          corrected["expiry_date"].trustworthy)

    person = Person(name=_build_name(l1[5:44]))
    person.birth_date = _fv(birth.isoformat() if birth else None,
                            corrected["birth_date"].trustworthy)
    person.sex = _fv({"M": "M", "F": "F"}.get(l2[20]), l2[20] in ("M", "F"))
    person.nationality = _fv(mrz_to_display(l2[10:13]), True)
    person.citizenship = _fv(mrz_to_display(l2[10:13]), True)

    personal = corrected["personal_number"].value.replace("<", "").strip()
    digits = "".join(ch for ch in personal if ch.isdigit())
    if len(digits) == 14:
        ok, info = validate_pinfl(digits)
        person.pinfl = FieldValue(
            value=digits,
            confidence=0.9 if (ok and corrected["personal_number"].trustworthy) else 0.4,
            source=FieldSource.MRZ, validated=False,
            note=None if ok else "; ".join(info["errors"]),
        )

    if not v.fields["composite"].valid:
        res.warnings.append(
            "MRZ umumiy nazorat raqami mos kelmadi — hujjatni qayta suratga oling")

    res.person = person
    res.document = doc
    return res


def parse_mrz(lines: list[str]) -> MRZResult:
    """Dispatch on line count. 3 lines -> TD1, 2 lines -> TD3."""
    cleaned = [s for s in (x.strip() for x in lines) if s]
    if len(cleaned) == 3:
        return parse_td1(cleaned)
    if len(cleaned) == 2:
        return parse_td3(cleaned)
    return MRZResult(
        found=False,
        reason=f"unexpected MRZ line count: {len(cleaned)} (expected 2 or 3)",
    )
