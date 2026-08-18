"""Template analysis and rendering.

Two things here are load-bearing for safety:

  * The Jinja environment is sandboxed. Templates are uploaded by users and are
    therefore untrusted code. An unsandboxed environment turns a template
    upload form into remote code execution.
  * Analysis reports what it could not understand. A template that silently
    renders a placeholder as literal text is worse than one that refuses to
    publish, because the defect surfaces in a document someone has already
    signed.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime

from docxtpl import DocxTemplate
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel
from rapidfuzz import process as fuzz_process

from packages.docgen.normalize_runs import coalesce_runs, extract_text
from packages.schema.models import ExtractionResult, FieldValue
from packages.schema.translit import (
    cyrillic_to_latin,
    latin_to_cyrillic,
    normalize_apostrophes,
)

# --------------------------------------------------------------------------
# Uzbek-aware Jinja filters
# --------------------------------------------------------------------------

_MONTHS_UZ = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul",
              "avgust", "sentabr", "oktabr", "noyabr", "dekabr"]
_MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]

_ONES = ["", "bir", "ikki", "uch", "to'rt", "besh", "olti", "yetti",
         "sakkiz", "to'qqiz"]
_TENS = ["", "o'n", "yigirma", "o'ttiz", "qirq", "ellik", "oltmish",
         "yetmish", "sakson", "to'qson"]


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    s = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def f_date_uz(value) -> str:
    d = _as_date(value)
    return f"{d.day}-{_MONTHS_UZ[d.month - 1]}, {d.year}-yil" if d else str(value or "")


def f_date_uz_short(value) -> str:
    d = _as_date(value)
    return d.strftime("%d.%m.%Y") if d else str(value or "")


def f_date_ru(value) -> str:
    d = _as_date(value)
    return f"{d.day} {_MONTHS_RU[d.month - 1]} {d.year} г." if d else str(value or "")


def f_date_iso(value) -> str:
    d = _as_date(value)
    return d.isoformat() if d else str(value or "")


def f_upper_uz(value) -> str:
    """Upper-case without destroying the Oʻ / Gʻ digraph marker."""
    s = normalize_apostrophes(str(value or "")) or ""
    return "".join(ch if ch == "\u02BB" else ch.upper() for ch in s)


def f_lower_uz(value) -> str:
    s = normalize_apostrophes(str(value or "")) or ""
    return "".join(ch if ch == "\u02BB" else ch.lower() for ch in s)


def f_pinfl_spaced(value) -> str:
    s = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(s) != 14:
        return str(value or "")
    return f"{s[0]} {s[1:5]} {s[5:9]} {s[9:13]} {s[13]}"


def _three_digits_to_words(n: int) -> str:
    parts: list[str] = []
    if n >= 100:
        parts.append(f"{_ONES[n // 100]} yuz" if n // 100 > 1 else "yuz")
        n %= 100
    if n >= 10:
        parts.append(_TENS[n // 10])
        n %= 10
    if n:
        parts.append(_ONES[n])
    return " ".join(p for p in parts if p)


def f_amount_words(value) -> str:
    """Render an integer in Uzbek words. Contracts require the written form."""
    try:
        n = int(float(str(value).replace(" ", "").replace(",", "")))
    except (TypeError, ValueError):
        return str(value or "")
    if n == 0:
        return "nol"
    scales = [(10**9, "milliard"), (10**6, "million"), (1000, "ming"), (1, "")]
    out: list[str] = []
    for size, label in scales:
        if n >= size:
            count, n = divmod(n, size)
            chunk = _three_digits_to_words(count)
            if size == 1000 and count == 1:
                chunk = ""
            out.append(f"{chunk} {label}".strip())
    return " ".join(p for p in out if p)


def f_initials(value) -> str:
    parts = str(value or "").split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return parts[0] + " " + ".".join(p[0].upper() for p in parts[1:]) + "."


FILTERS = {
    "date_uz": f_date_uz,
    "date_uz_short": f_date_uz_short,
    "date_ru": f_date_ru,
    "date_iso": f_date_iso,
    "upper_uz": f_upper_uz,
    "lower_uz": f_lower_uz,
    "cyrillic": lambda v: latin_to_cyrillic(str(v or "")) or "",
    "latin": lambda v: cyrillic_to_latin(str(v or "")) or "",
    "pinfl_spaced": f_pinfl_spaced,
    "amount_words": f_amount_words,
    "initials": f_initials,
}


def make_environment() -> SandboxedEnvironment:
    """A sandboxed Jinja environment.

    Templates are user-uploaded, so this must never be a plain Environment.
    The sandbox blocks attribute traversal into `__class__`, `__globals__`,
    `__subclasses__` and friends, which is the standard route from "upload a
    Word file" to "execute arbitrary code on the server".
    """
    env = SandboxedEnvironment(autoescape=False)
    env.filters.update(FILTERS)
    return env


# --------------------------------------------------------------------------
# Sanitisation
# --------------------------------------------------------------------------

MAX_TEMPLATE_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_ENTRIES = 2000

_DANGEROUS_PARTS = re.compile(
    r"(vbaProject\.bin|vbaData\.xml|activeX|oleObject)", re.I)
_DANGEROUS_FIELDS = re.compile(rb"(DDEAUTO|DDE\s|INCLUDETEXT|INCLUDEPICTURE)", re.I)


class TemplateRejected(Exception):
    """The uploaded file is not safe or not supported. Never auto-retried."""


# Identifiers that appear in server-side template injection payloads and in no
# legitimate document template. The sandbox already blocks these at render
# time, but rejecting them statically is defence in depth: it fails loudly at
# upload with a clear message instead of silently rendering an empty string,
# and it keeps hostile templates out of storage entirely.
_SSTI_PATTERNS = re.compile(
    r"(__class__|__mro__|__subclasses__|__globals__|__builtins__|__base__|"
    r"__bases__|__init__|__import__|__getattribute__|__reduce__|"
    r"\bself\b|\bconfig\b|\brequest\b|\bcycler\b|\bjoiner\b|"
    r"\bnamespace\b|\blipsum\b)"
)


def check_for_injection(text: str) -> list[str]:
    """Dangerous identifiers found inside template expressions."""
    hits: set[str] = set()
    for expr in re.findall(r"\{\{(.*?)\}\}|\{%(.*?)%\}", text, re.S):
        blob = "".join(part for part in expr if part)
        hits |= set(m.group(0) for m in _SSTI_PATTERNS.finditer(blob))
    return sorted(hits)


@dataclass
class SanitizationReport:
    removed: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def as_user_message(self) -> list[str]:
        """Plain-language summary. Users should not be shown XML internals."""
        msgs = []
        if any("external" in f for f in self.findings):
            msgs.append("Shablonda tashqi havola topildi va olib tashlandi")
        if any("DDE" in f or "INCLUDE" in f for f in self.findings):
            msgs.append("Shablonda avtomatik buyruq maydoni topildi va olib tashlandi")
        return msgs


def detect_format(data: bytes) -> str:
    """Identify by magic bytes. Extensions lie: .doc files are often RTF."""
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "doc"
    if data[:4] == b"PK\x03\x04":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            names = z.namelist()
            if any(n.startswith("word/") for n in names):
                return "docm" if _DANGEROUS_PARTS.search(" ".join(names)) else "docx"
            return "zip"
        except zipfile.BadZipFile:
            return "unknown"
    if data[:5] == b"{\\rtf":
        return "rtf"
    head = data[:200].lstrip().lower()
    if head.startswith(b"<html") or head.startswith(b"<?xml"):
        return "html"
    return "unknown"


def sanitize_office(data: bytes) -> tuple[bytes, SanitizationReport]:
    """Reject or strip anything in a .docx that can execute or phone home."""
    report = SanitizationReport()

    if len(data) > MAX_TEMPLATE_BYTES:
        raise TemplateRejected(
            f"Fayl juda katta ({len(data) // 1024 // 1024} MB). "
            f"Chegara: {MAX_TEMPLATE_BYTES // 1024 // 1024} MB")

    fmt = detect_format(data)
    if fmt == "docm":
        raise TemplateRejected(
            "Makrosli hujjat (.docm) qabul qilinmaydi. Faylni .docx sifatida "
            "saqlab qayta yuklang.")
    if fmt != "docx":
        raise TemplateRejected(
            f"Qo'llab-quvvatlanmaydigan format: {fmt}. .docx yoki .doc yuklang.")

    try:
        src = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise TemplateRejected("Fayl buzilgan yoki .docx emas") from exc

    infos = src.infolist()
    if len(infos) > MAX_ENTRIES:
        raise TemplateRejected("Shablon ichida juda ko'p element bor")

    expanded = sum(i.file_size for i in infos)
    if expanded > MAX_EXPANDED_BYTES:
        raise TemplateRejected("Shablon ochilganda juda katta hajmga yoyiladi")
    compressed = max(sum(i.compress_size for i in infos), 1)
    if expanded / compressed > MAX_COMPRESSION_RATIO:
        raise TemplateRejected("Shablon siqilish nisbati shubhali (zip bomba)")

    for i in infos:
        name = i.filename
        if name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
            raise TemplateRejected("Shablon ichida xavfli fayl yo'li bor")
        if _DANGEROUS_PARTS.search(name):
            raise TemplateRejected(
                "Shablonda makros yoki ActiveX komponenti bor — qabul qilinmaydi")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for i in infos:
            payload = src.read(i.filename)
            if i.filename.endswith(".rels"):
                before = payload
                payload = re.sub(
                    rb'\s+TargetMode="External"', b"", payload)
                if payload != before:
                    report.findings.append("external relationship")
                    report.removed.append(i.filename)
            if i.filename.endswith(".xml") and _DANGEROUS_FIELDS.search(payload):
                payload = _DANGEROUS_FIELDS.sub(b"REMOVED_FIELD", payload)
                report.findings.append("DDE/INCLUDE field")
                report.removed.append(i.filename)
            dst.writestr(i, payload)
    src.close()
    return out.getvalue(), report


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

_VAR_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
_FOR_RE = re.compile(r"\{%\s*(?:tr\s+)?for\s+(\w+)\s+in\s+([\w.\[\]]+)")
_IF_RE = re.compile(r"\{%\s*if\s+(.+?)\s*%\}")


@dataclass
class TemplateVariable:
    raw: str
    field_path: str
    filters: list[str] = field(default_factory=list)
    required: bool = True


@dataclass
class TemplateSpec:
    name: str = ""
    variables: list[TemplateVariable] = field(default_factory=list)
    loops: list[str] = field(default_factory=list)
    conditionals: list[str] = field(default_factory=list)
    required_fields: list[str] = field(default_factory=list)
    optional_fields: list[str] = field(default_factory=list)
    unknown_variables: list[tuple[str, str | None]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    repaired_tags: list[str] = field(default_factory=list)
    runs_merged: int = 0


def known_field_paths() -> list[str]:
    """Every dotted path the canonical schema can supply."""
    sample = ExtractionResult(job_id="_")
    sample.documents.append(__import__(
        "packages.schema.models", fromlist=["IdentityDocument"]
    ).IdentityDocument())
    from packages.schema.models import Education
    sample.education = Education()
    paths = list(sample.flatten())
    paths += [p.replace("documents.0", "documents[0]") for p in paths
              if p.startswith("documents.0")]
    return sorted(set(paths))


def _suggest_path(path: str, known: set[str]) -> str | None:
    """Suggest the canonical path an author probably meant.

    Authors usually get the LEAF name almost right ("middle_name" for
    "patronymic_latin") while the prefix is fine, so match on leaves first and
    only accept a confident hit. A wrong suggestion is worse than none: it
    invites the author to wire passport data into the wrong field.
    """
    leaf = path.split(".")[-1].split("[")[0]
    synonyms = {
        "middle_name": "patronymic", "father_name": "patronymic",
        "otchestvo": "patronymic", "last_name": "surname",
        "first_name": "given_name", "familiya": "surname",
        "ism": "given_name", "dob": "birth_date", "jshshir": "pinfl",
    }
    target = synonyms.get(leaf, leaf)

    by_leaf = {p.split(".")[-1]: p for p in known}
    best = fuzz_process.extractOne(target, list(by_leaf), score_cutoff=70)
    if best:
        return by_leaf[best[0]].replace("documents.0", "documents[0]")

    best_full = fuzz_process.extractOne(path, sorted(known), score_cutoff=80)
    return best_full[0].replace("documents.0", "documents[0]") if best_full else None


def analyze(docx_bytes: bytes, name: str = "") -> tuple[bytes, TemplateSpec]:
    """Coalesce, then report exactly what the template asks for."""
    fixed, coalesce_report = coalesce_runs(docx_bytes)
    text = extract_text(fixed)

    spec = TemplateSpec(name=name,
                        repaired_tags=coalesce_report.repaired_tags,
                        runs_merged=coalesce_report.runs_merged)

    injection = check_for_injection(text)
    if injection:
        raise TemplateRejected(
            "Shablonda xavfli ifoda topildi: "
            + ", ".join(injection)
            + ". Hujjat shablonida bunday konstruksiya ishlatilmaydi.")

    # Unbalanced braces are the most common authoring mistake, and docxtpl
    # reports them poorly, so catch them here with a usable message.
    if text.count("{{") != text.count("}}"):
        spec.errors.append(
            f"Yopilmagan teg bor: '{{{{' {text.count('{{')} marta, "
            f"'}}}}' {text.count('}}')} marta uchradi")

    loop_vars: set[str] = set()
    for alias, source in _FOR_RE.findall(text):
        spec.loops.append(source)
        loop_vars.add(alias)

    spec.conditionals = _IF_RE.findall(text)
    known = set(known_field_paths())
    # Values the renderer injects rather than the schema supplying. Reporting
    # these as unknown -- and worse, suggesting "education.graduation_year"
    # for "today.year" -- would train authors to ignore the warnings.
    context_builtins = {"today", "doc_type", "now"}

    for raw in _VAR_RE.findall(text):
        parts = [p.strip() for p in raw.split("|")]
        path, filters = parts[0], parts[1:]
        if not path or path.startswith(("%", "#")):
            continue

        var = TemplateVariable(raw=raw, field_path=path, filters=filters,
                               required="default" not in " ".join(filters))
        spec.variables.append(var)

        root = path.split(".")[0].split("[")[0]
        if root in loop_vars or root in context_builtins:
            continue                      # loop alias or renderer-provided value

        normalised = path.replace("[0]", ".0")
        if normalised not in known and path not in known:
            suggestion = _suggest_path(path, known)
            spec.unknown_variables.append((path, suggestion))
        elif var.required:
            spec.required_fields.append(path)
        else:
            spec.optional_fields.append(path)

        for f in filters:
            fname = f.split("(")[0].strip()
            if fname and fname not in FILTERS and fname != "default":
                spec.errors.append(f"Noma'lum filtr: '{fname}'")

    spec.required_fields = sorted(set(spec.required_fields))
    spec.optional_fields = sorted(set(spec.optional_fields))
    return fixed, spec


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


@dataclass
class FillResult:
    content: bytes
    format: str = "docx"
    filled_fields: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class _Blank(str):
    """Renders as a fill-in line while remaining attribute-safe in templates."""

    def __getattr__(self, item):
        return self


def build_context(result: ExtractionResult, extra: dict | None = None,
                  blank: str = "____________") -> tuple[dict, list[str]]:
    """Flatten FieldValues so templates can write {{ person.pinfl }}.

    Returns the context and the list of paths that had no value, so the caller
    can tell the user which fields still need filling in by hand.
    """
    missing: list[str] = []

    def convert(obj):
        if isinstance(obj, FieldValue):
            if obj.is_empty:
                return _Blank(blank)
            return normalize_apostrophes(obj.value)
        if isinstance(obj, BaseModel):
            return {k: convert(getattr(obj, k))
                    for k in type(obj).model_fields}
        if isinstance(obj, list):
            return [convert(x) for x in obj]
        return obj

    for path, fv in result.flatten().items():
        if fv.is_empty:
            missing.append(path)

    ctx = {
        "person": convert(result.person),
        "documents": convert(result.documents),
        "education": convert(result.education) if result.education else None,
        "today": date.today(),
        "doc_type": str(result.doc_type),
    }
    if extra:
        ctx.update(extra)
    return ctx, missing


def render_docx(template_bytes: bytes, result: ExtractionResult,
                extra: dict | None = None) -> FillResult:
    """Render a template against extracted data."""
    fixed, spec = analyze(template_bytes)
    if spec.errors:
        raise TemplateRejected("; ".join(spec.errors))

    ctx, missing_all = build_context(result, extra)

    tpl = DocxTemplate(io.BytesIO(fixed))
    tpl.render(ctx, make_environment())

    buf = io.BytesIO()
    tpl.save(buf)

    required = set(spec.required_fields)
    missing_required = sorted(
        p for p in missing_all
        if p in required or p.replace("documents.0", "documents[0]") in required
    )

    warnings = []
    if missing_required:
        warnings.append(
            f"{len(missing_required)} ta majburiy maydon bo'sh qoldi — "
            "hujjatni yuborishdan oldin to'ldiring")

    return FillResult(
        content=buf.getvalue(),
        filled_fields=sorted(set(spec.required_fields) - set(missing_required)),
        missing_fields=missing_required,
        warnings=warnings,
    )
