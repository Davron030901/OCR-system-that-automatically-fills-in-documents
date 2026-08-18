"""Stage L1: rule-based field mapping over the visual zone.

Anchoring on printed labels is unglamorous and it resolves a large share of
fields for free, on hardware you control, with no model and no API call. Every
field resolved here is one the LLM stage never sees, which is why this module
is worth more than its size suggests.

Labels are matched in Uzbek Latin, Uzbek Cyrillic, Russian and English,
because Uzbek documents mix all four.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from packages.schema.models import DocumentType
from packages.schema.translit import normalize_apostrophes

if TYPE_CHECKING:
    from packages.ml.pipeline import OCRLine

# path -> label variants that introduce that value
LABELS: dict[str, list[str]] = {
    "person.name.surname_latin": [
        "familiya", "familiyasi", "фамилия", "surname", "last name"],
    "person.name.given_name_latin": [
        "ism", "ismi", "имя", "given name", "first name", "name"],
    "person.name.patronymic_latin": [
        "otasining ismi", "sharifi", "отчество", "patronymic"],
    "person.birth_date": [
        "tug'ilgan sana", "tugilgan sana", "tug'ilgan sanasi",
        "дата рождения", "date of birth", "born"],
    "person.birth_place": [
        "tug'ilgan joy", "tugilgan joy", "tug'ilgan joyi",
        "место рождения", "place of birth"],
    "person.pinfl": [
        "jshshir", "jshshr", "pinfl", "пинфл", "жшшир",
        "personal number", "shaxsiy raqam"],
    "person.address": [
        "manzil", "yashash manzili", "адрес", "address", "residence"],
    "person.sex": ["jinsi", "jins", "пол", "sex", "gender"],
    "person.nationality": ["millati", "национальность", "nationality"],
    "person.citizenship": ["fuqaroligi", "гражданство", "citizenship"],
    "documents.0.doc_number": [
        "seriya", "raqam", "seriya raqami", "номер", "серия",
        "document no", "passport no", "no"],
    "documents.0.issue_date": [
        "berilgan sana", "berilgan", "дата выдачи", "date of issue"],
    "documents.0.expiry_date": [
        "amal qilish muddati", "amal qiladi", "срок действия",
        "date of expiry", "valid until"],
    "documents.0.issuing_authority": [
        "kim tomonidan berilgan", "bergan organ", "кем выдан",
        "issuing authority", "authority"],
    "education.institution": [
        "oliy ta'lim muassasasi", "universitet", "institut",
        "учреждение", "institution", "university"],
    "education.speciality": [
        "mutaxassislik", "yo'nalish", "специальность", "speciality", "major"],
    "education.degree": [
        "daraja", "malaka", "bakalavr", "magistr", "степень", "degree"],
    "education.diploma_number": [
        "diplom raqami", "reg. №", "registratsiya raqami", "diploma no"],
    "education.graduation_year": [
        "bitirgan yili", "tamomlagan", "год окончания", "graduation year"],
}

_DATE_RE = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\b")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_PINFL_RE = re.compile(r"\b(\d{14})\b")
_DOCNO_RE = re.compile(r"\b([A-Z]{2}\s?\d{6,8})\b")
_SEX_RE = re.compile(r"\b([MFМЖ])\b")


def _norm(text: str) -> str:
    return (normalize_apostrophes(text) or "").lower().strip()


def _value_after_label(text: str, label: str) -> str | None:
    """Take what follows the label on the same line."""
    low = _norm(text)
    idx = low.find(_norm(label))
    if idx < 0:
        return None
    tail = text[idx + len(label):].lstrip(" :;.-\u2014\t")
    return tail.strip() or None


def _coerce(path: str, raw: str) -> str | None:
    """Turn a raw fragment into the canonical representation for that field."""
    raw = raw.strip()
    if not raw:
        return None

    if path.endswith("_date") or path == "person.birth_date":
        m = _ISO_RE.search(raw)
        if m:
            return m.group(0)
        m = _DATE_RE.search(raw)
        if m:
            d, mo, y = m.groups()
            return f"{y}-{int(mo):02d}-{int(d):02d}"
        return None

    if path == "person.pinfl":
        m = _PINFL_RE.search(raw.replace(" ", ""))
        return m.group(1) if m else None

    if path == "documents.0.doc_number":
        m = _DOCNO_RE.search(raw.upper())
        return m.group(1).replace(" ", "") if m else None

    if path == "person.sex":
        m = _SEX_RE.search(raw.upper())
        if not m:
            return None
        return {"М": "M", "Ж": "F"}.get(m.group(1), m.group(1))

    if path == "education.graduation_year":
        m = re.search(r"\b(19|20)\d{2}\b", raw)
        return m.group(0) if m else None

    # Free text: strip trailing label fragments and stray punctuation.
    cleaned = re.sub(r"\s{2,}", " ", raw).strip(" :;.-")
    return normalize_apostrophes(cleaned) if len(cleaned) > 1 else None


def map_by_rules(lines: list[OCRLine], doc_type: DocumentType
                 ) -> dict[str, str]:
    """Extract fields by anchoring on printed labels.

    Two passes: value on the same line as the label, then value on the line
    below it, which is how most Uzbek ID layouts are set.
    """
    out: dict[str, str] = {}
    texts = [ln.text for ln in lines]

    # Longest label first. Otherwise "familiya" matches inside "Familiyasi:"
    # and the value comes back as "si: ALIYEV".
    ordered = {p: sorted(v, key=len, reverse=True) for p, v in LABELS.items()}

    for i, text in enumerate(texts):
        low = _norm(text)
        for path, labels in ordered.items():
            if path in out:
                continue
            for label in labels:
                if _norm(label) not in low:
                    continue
                raw = _value_after_label(text, label)
                if not raw and i + 1 < len(texts):
                    raw = texts[i + 1]          # label above, value below
                if raw:
                    value = _coerce(path, raw)
                    if value:
                        out[path] = value
                break

    # Standalone patterns that need no label at all.
    joined = "\n".join(texts)
    if "person.pinfl" not in out:
        m = _PINFL_RE.search(joined.replace(" ", ""))
        if m:
            out["person.pinfl"] = m.group(1)
    if "documents.0.doc_number" not in out:
        m = _DOCNO_RE.search(joined.upper())
        if m:
            out["documents.0.doc_number"] = m.group(1).replace(" ", "")

    return out
