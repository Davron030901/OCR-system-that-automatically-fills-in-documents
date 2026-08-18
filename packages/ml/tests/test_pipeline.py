"""Pipeline and rule-mapping tests."""
import asyncio

import numpy as np

from packages.ml.pipeline import (
    ExtractionPipeline,
    OCRLine,
    OCROutput,
    PipelineConfig,
    apply_mapping,
    merge_field,
    unresolved_fields,
)
from packages.ml.rules.field_rules import map_by_rules
from packages.schema.models import (
    DocumentType,
    ExtractionResult,
    ExtractionStatus,
    FieldSource,
    FieldValue,
    IdentityDocument,
)

UZ_ID_LINES = [
    "O'ZBEKISTON RESPUBLIKASI",
    "Familiyasi: ALIYEV",
    "Ismi: SHOHRUH",
    "Otasining ismi: AKMAL O'G'LI",
    "Tug'ilgan sanasi: 15.03.1995",
    "Tug'ilgan joyi: Toshkent shahri",
    "JSHSHIR 31503950012345",
    "Seriya AA1234567",
    "Berilgan sana: 10.01.2020",
    "Amal qilish muddati: 10.01.2030",
    "Jinsi: M",
]


def lines(texts):
    return [OCRLine(text=t, bbox=[0, i * 20, 200, i * 20 + 18], confidence=0.9)
            for i, t in enumerate(texts)]


class TestRuleMapping:
    def test_extracts_labelled_fields(self):
        out = map_by_rules(lines(UZ_ID_LINES), DocumentType.ID_FRONT)
        assert out["person.name.surname_latin"] == "ALIYEV"
        assert out["person.birth_date"] == "1995-03-15"
        assert out["person.pinfl"] == "31503950012345"
        assert out["documents.0.doc_number"] == "AA1234567"
        assert out["person.sex"] == "M"

    def test_dates_normalised_to_iso(self):
        out = map_by_rules(lines(UZ_ID_LINES), DocumentType.ID_FRONT)
        assert out["documents.0.issue_date"] == "2020-01-10"
        assert out["documents.0.expiry_date"] == "2030-01-10"

    def test_russian_labels(self):
        out = map_by_rules(lines([
            "Фамилия: КАРИМОВ", "Дата рождения: 01.02.1980",
        ]), DocumentType.ID_FRONT)
        assert out["person.name.surname_latin"] == "КАРИМОВ"
        assert out["person.birth_date"] == "1980-02-01"

    def test_value_on_next_line(self):
        out = map_by_rules(lines(["Familiyasi", "ALIYEV"]), DocumentType.ID_FRONT)
        assert out["person.name.surname_latin"] == "ALIYEV"

    def test_pinfl_found_without_label(self):
        out = map_by_rules(lines(["random", "31503950012345"]),
                           DocumentType.ID_FRONT)
        assert out["person.pinfl"] == "31503950012345"

    def test_garbage_produces_nothing_rather_than_noise(self):
        out = map_by_rules(lines(["####", "?????", "   "]), DocumentType.ID_FRONT)
        assert out == {}


class TestMergePolicy:
    def test_validated_always_wins(self):
        validated = FieldValue(value="AA1234567", confidence=0.5,
                               source=FieldSource.MRZ, validated=True)
        confident_guess = FieldValue(value="AA7654321", confidence=0.99,
                                     source=FieldSource.VLM, validated=False)
        assert merge_field(validated, confident_guess) is validated

    def test_higher_confidence_wins_when_neither_validated(self):
        a = FieldValue(value="x", confidence=0.5)
        b = FieldValue(value="y", confidence=0.8)
        assert merge_field(a, b) is b

    def test_mapping_does_not_overwrite_validated_field(self):
        r = ExtractionResult(job_id="t")
        r.documents = [IdentityDocument()]
        r.documents[0].doc_number = FieldValue(
            value="AA1234567", confidence=0.98,
            source=FieldSource.MRZ, validated=True)
        apply_mapping(r, {"documents.0.doc_number": "WRONG"},
                      FieldSource.LLM_TEXT, 0.9)
        assert r.documents[0].doc_number.value == "AA1234567"


class TestUnresolved:
    def test_lists_empty_and_low_confidence(self):
        r = ExtractionResult(job_id="t")
        r.person.pinfl = FieldValue(value="31503950012345", confidence=0.95)
        r.person.address = FieldValue(value=None, confidence=0.0)
        out = unresolved_fields(r, 0.75)
        assert "person.address" in out
        assert "person.pinfl" not in out


class FakeOCR:
    def __init__(self, texts):
        self.out = OCROutput(lines=lines(texts))

    def read(self, image):
        return self.out


class TestPipeline:
    def _image(self):
        """A synthetic card with enough printed detail to pass quality gating."""
        import cv2
        bg = np.full((1100, 1600, 3), 45, np.uint8)
        card = np.full((520, 830, 3), 205, np.uint8)
        for i, line in enumerate(UZ_ID_LINES[:9]):
            cv2.putText(card, line[:34], (24, 55 + i * 48),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (15, 15, 15), 2)
        cv2.rectangle(card, (2, 2), (827, 517), (60, 60, 60), 3)
        bg[280:800, 380:1210] = card
        return bg

    def test_bad_quality_short_circuits(self):
        blank = np.full((100, 100, 3), 255, np.uint8)   # blurry, tiny, blown out
        p = ExtractionPipeline()
        r = asyncio.run(p.extract(blank, "job-1"))
        assert r.status == ExtractionStatus.BAD_QUALITY
        assert r.error_message                          # actionable, in Uzbek

    def test_runs_without_ocr_or_llm(self):
        """Every dependency is optional; the pipeline degrades, never crashes."""
        p = ExtractionPipeline()
        r = asyncio.run(p.extract(self._image(), "job-2"))
        assert isinstance(r, ExtractionResult)
        assert r.processing_ms >= 0

    def test_l1_fills_fields_from_ocr(self):
        p = ExtractionPipeline(ocr=FakeOCR(UZ_ID_LINES),
                               config=PipelineConfig(enable_l2=False))
        r = asyncio.run(p.extract(self._image(), "job-3",
                                  doc_type_hint=DocumentType.ID_FRONT))
        assert r.person.pinfl.value == "31503950012345"
        assert "L1_rules" in r.stages_used

    def test_l2_failure_does_not_break_pipeline(self):
        class BrokenMapper:
            async def map_text(self, *a, **k):
                raise RuntimeError("provider down")

            async def map_image(self, *a, **k):
                raise RuntimeError("provider down")

        p = ExtractionPipeline(ocr=FakeOCR(UZ_ID_LINES), mapper=BrokenMapper())
        r = asyncio.run(p.extract(self._image(), "job-4",
                                  doc_type_hint=DocumentType.ID_FRONT))
        assert r.person.pinfl.value == "31503950012345"   # L1 result survives
        assert any("lokal o'qish" in w for w in r.warnings)

    def test_l3_disabled_by_default(self):
        """Sending the image away must be an explicit decision."""
        assert PipelineConfig().enable_l3 is False
