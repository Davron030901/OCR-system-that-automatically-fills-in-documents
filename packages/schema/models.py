"""Canonical data schema.

Every extractor writes into these models and every template reads from them.
This decoupling is the architectural keystone of the project: OCR internals
can change completely without touching a single document template.

Design rules enforced here:
  * A field is never a bare string. It is a FieldValue carrying confidence,
    provenance and a bounding box, because the review UI needs all three.
  * `validated=True` means a DETERMINISTIC check passed (MRZ check digit,
    checksum). It never means "the model was confident".
  * A missing value is None. Nothing in this system guesses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FieldSource(StrEnum):
    """Where a value came from. Drives trust, cost accounting and audit."""

    MRZ = "mrz"                 # ICAO 9303 machine readable zone, check-digit protected
    OCR_VISUAL = "ocr_visual"   # printed human-readable zone, local OCR
    LLM_TEXT = "llm_text"       # stage L2: local OCR text mapped by an LLM
    VLM = "vlm"                 # stage L3: image sent to a vision model
    BARCODE = "barcode"         # PDF417 / QR, where present
    MANUAL = "manual"           # corrected by a human in the review UI
    DERIVED = "derived"         # computed from other fields (e.g. transliteration)


class DocumentType(StrEnum):
    PASSPORT_BIO = "passport_bio"
    ID_FRONT = "id_front"
    ID_BACK = "id_back"
    DIPLOMA = "diploma"
    DIPLOMA_SUPPLEMENT = "diploma_supplement"
    BIRTH_CERTIFICATE = "birth_certificate"
    UNKNOWN = "unknown"


class ExtractionStatus(StrEnum):
    OK = "ok"
    REVIEW_NEEDED = "review_needed"
    BAD_QUALITY = "bad_quality"
    UNKNOWN_DOC_TYPE = "unknown_doc_type"
    FAILED = "failed"


class FieldValue(BaseModel):
    """A single extracted field.

    `bbox` is in ORIGINAL uploaded-image coordinates, not preprocessed
    coordinates. The preprocessing stage is responsible for mapping back via
    its transform matrix, because the review UI overlays boxes on the photo
    the user actually took.
    """

    model_config = ConfigDict(frozen=False)

    value: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: FieldSource = FieldSource.OCR_VISUAL
    bbox: list[int] | None = None          # [x1, y1, x2, y2]
    validated: bool = False                # deterministic check passed
    alternatives: list[str] = Field(default_factory=list)
    note: str | None = None                # why it was rejected / corrected

    @property
    def is_empty(self) -> bool:
        return self.value is None or self.value.strip() == ""

    def __str__(self) -> str:              # templates render {{ person.pinfl }}
        return self.value or ""


def empty_field(source: FieldSource = FieldSource.OCR_VISUAL) -> FieldValue:
    return FieldValue(value=None, confidence=0.0, source=source)


class PersonName(BaseModel):
    """Names in both scripts.

    Uzbek documents print the visual zone in Latin and often Cyrillic, while
    the MRZ carries a transliterated Latin form only. We keep both so
    templates can request whichever the receiving institution expects.
    """

    surname_latin: FieldValue = Field(default_factory=empty_field)
    given_name_latin: FieldValue = Field(default_factory=empty_field)
    patronymic_latin: FieldValue = Field(default_factory=empty_field)
    surname_cyrillic: FieldValue = Field(default_factory=empty_field)
    given_name_cyrillic: FieldValue = Field(default_factory=empty_field)
    patronymic_cyrillic: FieldValue = Field(default_factory=empty_field)

    def full_latin(self) -> str:
        parts = [
            self.surname_latin.value,
            self.given_name_latin.value,
            self.patronymic_latin.value,
        ]
        return " ".join(p for p in parts if p)


class Person(BaseModel):
    name: PersonName = Field(default_factory=PersonName)
    birth_date: FieldValue = Field(default_factory=empty_field)      # ISO YYYY-MM-DD
    birth_place: FieldValue = Field(default_factory=empty_field)
    sex: FieldValue = Field(default_factory=empty_field)             # "M" | "F"
    nationality: FieldValue = Field(default_factory=empty_field)     # ISO-3, e.g. UZB
    citizenship: FieldValue = Field(default_factory=empty_field)
    pinfl: FieldValue = Field(default_factory=empty_field)           # 14 digits
    address: FieldValue = Field(default_factory=empty_field)
    phone: FieldValue = Field(default_factory=empty_field)


class IdentityDocument(BaseModel):
    doc_type: Literal["passport", "id_card", "birth_certificate"] = "id_card"
    doc_number: FieldValue = Field(default_factory=empty_field)
    doc_series: FieldValue = Field(default_factory=empty_field)
    issuing_authority: FieldValue = Field(default_factory=empty_field)
    issue_date: FieldValue = Field(default_factory=empty_field)
    expiry_date: FieldValue = Field(default_factory=empty_field)


class Subject(BaseModel):
    """One row of a diploma supplement subject table."""

    name: FieldValue = Field(default_factory=empty_field)
    hours: FieldValue = Field(default_factory=empty_field)
    credits: FieldValue = Field(default_factory=empty_field)
    grade: FieldValue = Field(default_factory=empty_field)


class Education(BaseModel):
    institution: FieldValue = Field(default_factory=empty_field)
    degree: FieldValue = Field(default_factory=empty_field)          # bakalavr / magistr
    speciality: FieldValue = Field(default_factory=empty_field)
    speciality_code: FieldValue = Field(default_factory=empty_field)
    graduation_year: FieldValue = Field(default_factory=empty_field)
    diploma_number: FieldValue = Field(default_factory=empty_field)
    gpa: FieldValue = Field(default_factory=empty_field)
    subjects: list[Subject] = Field(default_factory=list)


class QualityReport(BaseModel):
    """Why we did or did not accept the uploaded image."""

    blur_score: float = 0.0
    brightness: float = 0.0
    contrast: float = 0.0
    estimated_dpi: int = 0
    glare_regions: list[list[int]] = Field(default_factory=list)
    moire_detected: bool = False
    is_acceptable: bool = True
    reasons: list[str] = Field(default_factory=list)   # user-facing, Uzbek


class StageTiming(BaseModel):
    stage: str
    ms: int
    used: bool = True


class ExtractionResult(BaseModel):
    """The single object the whole system passes around.

    The ML pipeline produces it, the API stores it (encrypted), the review UI
    edits it, and the document generator consumes it.
    """

    job_id: str
    status: ExtractionStatus = ExtractionStatus.OK
    doc_type: DocumentType = DocumentType.UNKNOWN

    person: Person = Field(default_factory=Person)
    documents: list[IdentityDocument] = Field(default_factory=list)
    education: Education | None = None

    overall_confidence: float = 0.0
    needs_review: list[str] = Field(default_factory=list)   # dotted field paths
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None                        # user-facing, Uzbek

    quality: QualityReport | None = None
    stages_used: list[str] = Field(default_factory=list)    # L0_mrz, L1_rules, ...
    timings: list[StageTiming] = Field(default_factory=list)
    model_versions: dict[str, str] = Field(default_factory=dict)
    llm_cost_usd: float = 0.0
    processing_ms: int = 0
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC))

    def get_field(self, path: str) -> FieldValue | None:
        """Resolve a dotted path such as 'person.name.surname_latin'."""
        node: object = self
        for part in path.split("."):
            if isinstance(node, list):
                try:
                    node = node[int(part)]
                    continue
                except (ValueError, IndexError):
                    return None
            node = getattr(node, part, None)
            if node is None:
                return None
        return node if isinstance(node, FieldValue) else None

    def flatten(self) -> dict[str, FieldValue]:
        """All FieldValues keyed by dotted path. Used by review UI and docgen."""
        out: dict[str, FieldValue] = {}

        def walk(obj: object, prefix: str) -> None:
            if isinstance(obj, FieldValue):
                out[prefix] = obj
                return
            if isinstance(obj, BaseModel):
                for key in type(obj).model_fields:
                    walk(getattr(obj, key), f"{prefix}.{key}" if prefix else key)
                return
            if isinstance(obj, list):
                for i, item in enumerate(obj):
                    walk(item, f"{prefix}.{i}")

        walk(self.person, "person")
        walk(self.documents, "documents")
        if self.education is not None:
            walk(self.education, "education")
        return out
