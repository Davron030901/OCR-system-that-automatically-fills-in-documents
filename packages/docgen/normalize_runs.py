"""Run coalescing for .docx templates.

THE PROBLEM THIS SOLVES
-----------------------
Word stores a paragraph as a sequence of runs (`<w:r>`), and it splits them
freely: a spell-check marker, a stray language attribute, or simply where the
cursor happened to rest while typing. A template author sees

    {{ person.pinfl }}

but the file contains

    <w:r><w:t>{{ person.</w:t></w:r>
    <w:r><w:rPr><w:noProof/></w:rPr><w:t>pinfl</w:t></w:r>
    <w:r><w:t> }}</w:t></w:r>

docxtpl looks for the literal tag, does not find it, and renders the template
with the placeholder left as-is. No exception, no warning. The user reports
"my template does not work" and there is nothing in the logs.

This module merges adjacent runs whose formatting is semantically identical,
so the tag becomes a contiguous string again. Merging identically-formatted
runs cannot change how the document looks, which is what makes it safe to do
unconditionally.

Attributes deliberately ignored when comparing formatting -- they carry
editing metadata, not appearance:
    w:rsid*        revision save IDs
    w:noProof      "skip spell check"
    w:lang         language tagging
    w:spellStart / w:spellEnd / w:grammarStart / w:grammarEnd
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}

# Parts that can carry template tags. Missing headers and footers here is the
# most common defect in this kind of code.
TEXT_PARTS = re.compile(
    r"word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml$"
)

# Ignored when deciding whether two runs are formatted the same.
_IGNORED_RPR_TAGS = {
    f"{{{W}}}noProof", f"{{{W}}}lang", f"{{{W}}}spellStart", f"{{{W}}}spellEnd",
    f"{{{W}}}grammarStart", f"{{{W}}}grammarEnd",
}
_IGNORED_ATTR = re.compile(r"rsid", re.I)

# Content that makes a run unmergeable: merging would move or drop it.
_UNMERGEABLE = {
    f"{{{W}}}br", f"{{{W}}}tab", f"{{{W}}}drawing", f"{{{W}}}pict",
    f"{{{W}}}footnoteReference", f"{{{W}}}endnoteReference",
    f"{{{W}}}commentReference", f"{{{W}}}fldChar", f"{{{W}}}instrText",
    f"{{{W}}}object", f"{{{W}}}ruby",
}


@dataclass
class CoalesceReport:
    runs_merged: int = 0
    paragraphs_touched: int = 0
    parts_touched: list[str] = field(default_factory=list)
    repaired_tags: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.runs_merged > 0


def _rpr_signature(run: etree._Element) -> str:
    """A canonical string for a run's appearance-relevant formatting."""
    rpr = run.find(f"{{{W}}}rPr")
    if rpr is None:
        return ""
    parts: list[str] = []
    for child in rpr:
        if child.tag in _IGNORED_RPR_TAGS:
            continue
        attrs = sorted(
            f"{k}={v}" for k, v in child.attrib.items()
            if not _IGNORED_ATTR.search(str(k))
        )
        parts.append(f"{child.tag}[{','.join(attrs)}]")
    return "|".join(sorted(parts))


def _is_mergeable(run: etree._Element) -> bool:
    return not any(child.tag in _UNMERGEABLE for child in run)


def _run_text(run: etree._Element) -> str:
    return "".join(t.text or "" for t in run.findall(f"{{{W}}}t"))


def _set_run_text(run: etree._Element, text: str) -> None:
    """Collapse a run down to a single <w:t> carrying `text`."""
    for t in run.findall(f"{{{W}}}t"):
        run.remove(t)
    el = etree.SubElement(run, f"{{{W}}}t")
    el.text = text
    # Without this, Word silently eats leading and trailing spaces.
    el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def coalesce_paragraph(para: etree._Element) -> int:
    """Merge adjacent identically-formatted runs in one paragraph."""
    runs = para.findall(f"{{{W}}}r")
    if len(runs) < 2:
        return 0

    merged = 0
    i = 0
    while i < len(runs) - 1:
        a, b = runs[i], runs[i + 1]
        if (_is_mergeable(a) and _is_mergeable(b)
                and _rpr_signature(a) == _rpr_signature(b)):
            _set_run_text(a, _run_text(a) + _run_text(b))
            para.remove(b)
            runs.pop(i + 1)
            merged += 1
            continue
        i += 1
    return merged


def coalesce_xml(xml_bytes: bytes) -> tuple[bytes, int, int]:
    """Coalesce every paragraph in one document part."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True,
                             huge_tree=False)
    root = etree.fromstring(xml_bytes, parser)

    merged = touched = 0
    for para in root.iter(f"{{{W}}}p"):
        n = coalesce_paragraph(para)
        if n:
            merged += n
            touched += 1

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                          standalone=True), merged, touched


_TAG_RE = re.compile(r"\{\{\s*([\w.\[\]|'\"() -]+?)\s*\}\}")


def _tags_in_single_runs(xml_bytes: bytes) -> set[str]:
    """Tags that are contiguous inside one <w:t>, i.e. visible to docxtpl."""
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        root = etree.fromstring(xml_bytes, parser)
    except etree.XMLSyntaxError:
        return set()
    found: set[str] = set()
    for t in root.iter(f"{{{W}}}t"):
        if t.text:
            found |= set(_TAG_RE.findall(t.text))
    return found


def coalesce_runs(docx_bytes: bytes) -> tuple[bytes, CoalesceReport]:
    """Repair fragmented runs across every text-bearing part of a .docx.

    Returns the rewritten archive and a report naming the tags that were only
    findable after repair, so the UI can tell the author what was fixed.
    """
    report = CoalesceReport()
    src = zipfile.ZipFile(io.BytesIO(docx_bytes))

    tags_before: set[str] = set()
    tags_after: set[str] = set()
    out_buf = io.BytesIO()

    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if TEXT_PARTS.match(item.filename):
                try:
                    # A tag is only visible to the template engine when it sits
                    # inside a SINGLE <w:t>. Comparing paragraph-level text
                    # would hide exactly the defect we are fixing.
                    tags_before |= _tags_in_single_runs(data)

                    data, merged, touched = coalesce_xml(data)

                    tags_after |= _tags_in_single_runs(data)

                    if merged:
                        report.runs_merged += merged
                        report.paragraphs_touched += touched
                        report.parts_touched.append(item.filename)
                except etree.XMLSyntaxError:
                    # An unparseable part is left byte-identical rather than
                    # dropped: better a template that renders imperfectly than
                    # one silently missing a header.
                    pass
            dst.writestr(item, data)

    src.close()
    report.repaired_tags = sorted(tags_after - tags_before)
    return out_buf.getvalue(), report


def extract_text(docx_bytes: bytes) -> str:
    """Visible text from every part, used for tag discovery and diagnostics."""
    z = zipfile.ZipFile(io.BytesIO(docx_bytes))
    chunks: list[str] = []
    for name in z.namelist():
        if not TEXT_PARTS.match(name):
            continue
        try:
            parser = etree.XMLParser(resolve_entities=False, no_network=True)
            root = etree.fromstring(z.read(name), parser)
        except etree.XMLSyntaxError:
            continue
        for para in root.iter(f"{{{W}}}p"):
            line = "".join(t.text or "" for t in para.iter(f"{{{W}}}t"))
            if line.strip():
                chunks.append(line)
    z.close()
    return "\n".join(chunks)
