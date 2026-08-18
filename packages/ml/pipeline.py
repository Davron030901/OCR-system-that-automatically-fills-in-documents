"""Extraction orchestrator.

Implements the four-stage cascade. Each stage runs only for fields the earlier
ones could not resolve, which is what keeps both cost and data exposure down:

  L0  MRZ, deterministic          free, nothing leaves the machine
  L1  local OCR + rules           free, nothing leaves the machine
  L2  local OCR text -> LLM       cheap, only TEXT leaves
  L3  image -> vision model       expensive, the IMAGE leaves

The ordering is not just an optimisation. By the time L2 runs, the MRZ has
already supplied the document number, dates, sex, nationality and PINFL with
check-digit backing, so the model is only asked for the things it is actually
good at: reading a printed address, or deciding that "Tug'ilgan sanasi" labels
the date next to it.

The pipeline never raises to its caller. Every failure path returns an
ExtractionResult carrying a status and a message the user can act on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from packages.ml.mrz.parse import MRZResult, parse_mrz
from packages.ml.preprocess.pipeline import PreprocessResult, preprocess
from packages.schema.models import (
    DocumentType,
    ExtractionResult,
    ExtractionStatus,
    FieldSource,
    FieldValue,
    StageTiming,
)
from packages.schema.validators import validate_date_logic

CONF_THRESHOLD = 0.75          # below this a field is escalated to the next stage
L3_MIN_UNRESOLVED = 3          # do not pay for vision over one or two fields


class OCREngine(Protocol):
    """Whatever local OCR is wired in (PaddleOCR, docTR, Tesseract)."""

    def read(self, image: np.ndarray) -> OCROutput: ...


@dataclass
class OCRLine:
    text: str
    bbox: list[int]
    confidence: float = 0.0


@dataclass
class OCROutput:
    lines: list[OCRLine] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)

    def mrz_candidates(self) -> list[str]:
        """Lines that look like an MRZ: long, upper-case, filler characters."""
        out = []
        for ln in self.lines:
            s = ln.text.strip().upper().replace(" ", "<")
            if (len(s) >= 28 and s.count("<") >= 2
                    and all(c.isalnum() or c == "<" for c in s)):
                out.append(s)
        return out


class LLMMapper(Protocol):
    """Stage L2/L3 mapper. Implemented in apps/ml-service against packages.llm."""

    async def map_text(self, ocr_text: str, doc_type: str,
                       unresolved: list[str]) -> tuple[dict[str, Any], float]: ...

    async def map_image(self, image_bytes: bytes, doc_type: str,
                        unresolved: list[str]) -> tuple[dict[str, Any], float]: ...


@dataclass
class PipelineConfig:
    enable_l1: bool = True
    enable_l2: bool = True
    enable_l3: bool = False        # off by default: it sends the image away
    confidence_threshold: float = CONF_THRESHOLD
    l3_min_unresolved: int = L3_MIN_UNRESOLVED


# Fields the MRZ can supply. Once L0 validates these, later stages skip them.
MRZ_BACKED = {
    "person.birth_date", "person.sex", "person.nationality",
    "person.citizenship", "person.pinfl",
    "person.name.surname_latin", "person.name.given_name_latin",
    "documents.0.doc_number", "documents.0.expiry_date",
}

# What the visual zone must supply because no MRZ carries it.
VISUAL_ONLY = {
    "person.birth_place", "person.address",
    "person.name.surname_cyrillic", "person.name.given_name_cyrillic",
    "person.name.patronymic_latin", "person.name.patronymic_cyrillic",
    "documents.0.issuing_authority", "documents.0.issue_date",
}


def unresolved_fields(result: ExtractionResult, threshold: float) -> list[str]:
    """Paths that are empty or below the confidence bar."""
    out = []
    for path, fv in result.flatten().items():
        if fv.is_empty or fv.confidence < threshold:
            out.append(path)
    return out


def merge_field(current: FieldValue, candidate: FieldValue) -> FieldValue:
    """Merge policy: a validated value always wins.

    Anything check-digit-backed outranks any model output regardless of the
    confidence the model reports, because model confidence is self-assessed
    and check digits are arithmetic.
    """
    if current.validated:
        return current
    if candidate.validated:
        return candidate
    if candidate.confidence > current.confidence:
        return candidate
    return current


def apply_mapping(result: ExtractionResult, mapping: dict[str, Any],
                  source: FieldSource, confidence: float) -> list[str]:
    """Write a {path: value} map into the result, respecting merge policy."""
    applied = []
    flat = result.flatten()
    for path, value in mapping.items():
        if value is None or path not in flat:
            continue
        candidate = FieldValue(value=str(value), confidence=confidence,
                               source=source, validated=False)
        current = flat[path]
        winner = merge_field(current, candidate)
        if winner is candidate:
            # Mutate in place: flatten() returns the live objects.
            current.value = candidate.value
            current.confidence = candidate.confidence
            current.source = candidate.source
            applied.append(path)
    return applied


class ExtractionPipeline:
    def __init__(self, ocr: OCREngine | None = None,
                 mapper: LLMMapper | None = None,
                 classifier=None,
                 config: PipelineConfig | None = None):
        self.ocr = ocr
        self.mapper = mapper
        self.classifier = classifier
        self.config = config or PipelineConfig()

    # ------------------------------------------------------------------
    async def extract(self, image: np.ndarray, job_id: str,
                      doc_type_hint: DocumentType | None = None
                      ) -> ExtractionResult:
        started = time.time()
        result = ExtractionResult(job_id=job_id)

        # --- preprocessing -------------------------------------------------
        t0 = time.time()
        pre: PreprocessResult = preprocess(image)
        result.quality = pre.quality
        result.timings.append(StageTiming(stage="preprocess", ms=int((time.time() - t0) * 1000)))

        if not pre.quality.is_acceptable:
            # Refusing here is cheaper and more useful than a confident guess.
            result.status = ExtractionStatus.BAD_QUALITY
            result.error_code = "BAD_QUALITY"
            result.error_message = " ".join(pre.quality.reasons)
            result.processing_ms = int((time.time() - started) * 1000)
            return result

        # --- classification ------------------------------------------------
        if doc_type_hint:
            result.doc_type = doc_type_hint
        elif self.classifier is not None:
            t0 = time.time()
            result.doc_type = self.classifier.predict(pre.rectified)
            result.timings.append(StageTiming(stage="classify", ms=int((time.time() - t0) * 1000)))
            if result.doc_type == DocumentType.UNKNOWN:
                result.status = ExtractionStatus.UNKNOWN_DOC_TYPE
                result.error_code = "UNKNOWN_DOC_TYPE"
                result.error_message = (
                    "Hujjat turi aniqlanmadi. Pasport, ID karta yoki diplom "
                    "suratini yuklang.")
                result.processing_ms = int((time.time() - started) * 1000)
                return result

        # --- local OCR ------------------------------------------------------
        ocr_out = OCROutput()
        if self.ocr is not None:
            t0 = time.time()
            ocr_out = self.ocr.read(pre.rectified)
            result.timings.append(StageTiming(stage="ocr", ms=int((time.time() - t0) * 1000)))

        # --- L0: MRZ --------------------------------------------------------
        mrz: MRZResult | None = None
        candidates = ocr_out.mrz_candidates()
        if candidates:
            t0 = time.time()
            mrz = parse_mrz(candidates[-3:] if len(candidates) >= 3
                            else candidates)
            result.timings.append(StageTiming(stage="L0_mrz", ms=int((time.time() - t0) * 1000)))
            if mrz.found:
                result.stages_used.append("L0_mrz")
                result.person = mrz.person
                if mrz.document:
                    result.documents = [mrz.document]
                result.warnings.extend(mrz.warnings)
                for c in mrz.corrections:
                    if c.changed:
                        result.warnings.append(f"MRZ tuzatildi: {c}")

        if not result.documents:
            from packages.schema.models import IdentityDocument
            result.documents = [IdentityDocument()]

        # --- L1: rule-based mapping over the visual zone ---------------------
        if self.config.enable_l1 and ocr_out.lines:
            t0 = time.time()
            from packages.ml.rules.field_rules import map_by_rules
            rule_map = map_by_rules(ocr_out.lines, result.doc_type)
            applied = apply_mapping(result, rule_map, FieldSource.OCR_VISUAL, 0.80)
            if applied:
                result.stages_used.append("L1_rules")
            result.timings.append(StageTiming(stage="L1_rules", ms=int((time.time() - t0) * 1000)))

        # --- L2: LLM over the OCR TEXT, never the image ----------------------
        unresolved = unresolved_fields(result, self.config.confidence_threshold)
        if self.config.enable_l2 and self.mapper and unresolved and ocr_out.lines:
            t0 = time.time()
            try:
                mapping, cost = await self.mapper.map_text(
                    ocr_out.text, str(result.doc_type), unresolved)
                apply_mapping(result, mapping, FieldSource.LLM_TEXT, 0.70)
                result.llm_cost_usd += cost
                result.stages_used.append("L2_llm_text")
            except Exception:                          # noqa: BLE001
                # The LLM layer is an enhancement, never a hard dependency.
                result.warnings.append(
                    "Qo'shimcha tahlil qatlami mavjud emas — natija faqat "
                    "lokal o'qish asosida")
            result.timings.append(StageTiming(stage="L2_llm_text", ms=int((time.time() - t0) * 1000)))

        # --- L3: vision model, last resort -----------------------------------
        unresolved = unresolved_fields(result, self.config.confidence_threshold)
        if (self.config.enable_l3 and self.mapper
                and len(unresolved) >= self.config.l3_min_unresolved):
            t0 = time.time()
            try:
                import cv2
                ok, buf = cv2.imencode(".jpg", pre.rectified)
                if ok:
                    mapping, cost = await self.mapper.map_image(
                        buf.tobytes(), str(result.doc_type), unresolved)
                    apply_mapping(result, mapping, FieldSource.VLM, 0.65)
                    result.llm_cost_usd += cost
                    result.stages_used.append("L3_vlm")
            except Exception:                                  # noqa: BLE001
                result.warnings.append("Tasvir tahlili bajarilmadi")
            result.timings.append(StageTiming(stage="L3_vlm", ms=int((time.time() - t0) * 1000)))

        # --- validation and cross-checks --------------------------------------
        self._validate(result, mrz, ocr_out)

        # --- map boxes back to the uploaded image -----------------------------
        for fv in result.flatten().values():
            if fv.bbox:
                fv.bbox = pre.to_original(fv.bbox)

        result.needs_review = unresolved_fields(result,
                                                self.config.confidence_threshold)
        values = [fv.confidence for fv in result.flatten().values()
                  if not fv.is_empty]
        result.overall_confidence = float(sum(values) / len(values)) if values else 0.0
        result.status = (ExtractionStatus.REVIEW_NEEDED if result.needs_review
                         else ExtractionStatus.OK)
        result.processing_ms = int((time.time() - started) * 1000)
        return result

    # ------------------------------------------------------------------
    def _validate(self, result: ExtractionResult, mrz: MRZResult | None,
                  ocr_out: OCROutput) -> None:
        from datetime import date

        def as_date(fv: FieldValue) -> date | None:
            try:
                return date.fromisoformat(fv.value) if fv.value else None
            except ValueError:
                return None

        doc = result.documents[0] if result.documents else None
        result.warnings.extend(validate_date_logic(
            as_date(result.person.birth_date),
            as_date(doc.issue_date) if doc else None,
            as_date(doc.expiry_date) if doc else None,
        ))

        # MRZ against the visual zone: the strongest tamper signal available.
        if mrz and mrz.found and ocr_out.lines:
            visual_text = ocr_out.text.upper()
            mrz_values = {
                "surname": mrz.person.name.surname_latin.value,
                "doc_number": (mrz.document.doc_number.value
                               if mrz.document else None),
            }
            visual_values = {
                k: (v if v and v.upper() in visual_text else None)
                for k, v in mrz_values.items()
            }
            for key, val in mrz_values.items():
                if val and visual_values.get(key) is None:
                    result.warnings.append(
                        f"MRZ dagi '{key}' qiymati hujjat yuzida topilmadi — "
                        "hujjatni diqqat bilan tekshiring")
