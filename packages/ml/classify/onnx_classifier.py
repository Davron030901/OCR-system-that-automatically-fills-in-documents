"""Document-type classifier, ONNX Runtime, CPU only.

WHY A CLASSIFIER AT ALL, GIVEN THE LLM LAYER
--------------------------------------------
The LLM maps fields, so a trained field DETECTOR is no longer on the critical
path. A type classifier still is, for three reasons:

  * routing — a passport goes down the MRZ path, a diploma supplement down the
    table path. Guessing wrong wastes the whole extraction.
  * refusal — the "unknown" class is the point of this model. A photo of a
    receipt must be rejected with a sentence the user can act on, not silently
    processed into a confidently wrong document.
  * cost — one 30 ms CPU inference prevents an LLM round trip on input that was
    never going to work.

It is deliberately small: EfficientNet-B0 or MobileNetV3-Small at 320x320,
about half an hour of fine-tuning on a Colab T4, exported to ONNX and
quantised to int8. See training/01_classifier.ipynb.

OUT-OF-DISTRIBUTION DETECTION
-----------------------------
A softmax always sums to one, so a network shown a photo of a cat still
reports 94% confidence in whichever class it likes most. Two independent
checks are applied instead of trusting that number:

  max softmax  — the winner must clear a probability floor.
  free energy  — -logsumexp(logits). Trained-on inputs produce large logits
                 and therefore low energy; unfamiliar inputs produce small
                 logits across the board and high energy. This catches the
                 confident-but-wrong case that a probability floor misses,
                 because energy reads the SCALE of the logits, which softmax
                 normalises away.

Both thresholds come from configuration and should be recalibrated whenever
the model is retrained — the notebook prints the values measured on its
validation split.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from packages.schema.models import DocumentType

log = logging.getLogger(__name__)

# Order matters: it is the output-index-to-label mapping the model was trained
# with. It is also written to labels.json beside the model, and that file wins
# if present — a retrained model with reordered classes must not silently
# start reporting diplomas as passports.
DEFAULT_CLASSES: tuple[DocumentType, ...] = (
    DocumentType.PASSPORT_BIO,
    DocumentType.ID_FRONT,
    DocumentType.ID_BACK,
    DocumentType.DIPLOMA,
    DocumentType.DIPLOMA_SUPPLEMENT,
    DocumentType.BIRTH_CERTIFICATE,
    DocumentType.UNKNOWN,
)

INPUT_SIZE = 320
# ImageNet statistics, because the backbone is ImageNet-pretrained. Changing
# the normalisation without retraining silently degrades accuracy.
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Classification:
    """A prediction plus everything needed to explain a refusal."""

    doc_type: DocumentType
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)
    energy: float = 0.0
    is_ood: bool = False
    reason: str = ""


class InferenceSession(Protocol):
    """The slice of onnxruntime.InferenceSession this module uses.

    Declared as a protocol so tests can drive the whole decision path with a
    stub, without an ONNX file or a training run.
    """

    def run(self, output_names: Any, input_feed: dict[str, Any]) -> list[Any]: ...

    def get_inputs(self) -> list[Any]: ...


def preprocess(image: np.ndarray, size: int = INPUT_SIZE) -> np.ndarray:
    """BGR uint8 HWC -> normalised float32 NCHW.

    Input arrives as OpenCV BGR because that is what the rest of the ML package
    passes around. Getting the channel order wrong costs several points of
    accuracy and produces no error, so the conversion is explicit here rather
    than assumed at the call site.
    """
    if image.ndim == 2:
        image = np.stack([image] * 3, axis=-1)
    if image.shape[2] == 4:
        image = image[:, :, :3]

    # Local import: OpenCV is a heavy dependency and this module is imported by
    # the API container too, where it must not pull cv2 in unless it is used.
    import cv2

    resized = cv2.resize(image, (size, size), interpolation=cv2.INTER_AREA)
    rgb = resized[:, :, ::-1].astype(np.float32) / 255.0
    normalised = (rgb - MEAN) / STD
    return np.ascontiguousarray(normalised.transpose(2, 0, 1)[None, ...])


def softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max()
    exp = np.exp(shifted)
    return exp / exp.sum()


def free_energy(logits: np.ndarray) -> float:
    """-logsumexp(logits). Low for familiar inputs, high for unfamiliar ones."""
    m = float(logits.max())
    return -float(m + np.log(np.exp(logits - m).sum()))


class DocumentClassifier:
    def __init__(
        self,
        session: InferenceSession,
        classes: tuple[DocumentType, ...] = DEFAULT_CLASSES,
        min_confidence: float | None = None,
        max_energy: float | None = None,
        input_size: int = INPUT_SIZE,
    ) -> None:
        self.session = session
        self.classes = classes
        # Defaults are intentionally cautious. A false "unknown" costs the user
        # one retake; a false "passport" on a diploma costs them a wrong
        # document with real data in it.
        self.min_confidence = (
            min_confidence
            if min_confidence is not None
            else _env_float("CLASSIFIER_MIN_CONFIDENCE", 0.60)
        )
        self.max_energy = (
            max_energy
            if max_energy is not None
            else _env_float("CLASSIFIER_MAX_ENERGY", -2.0)
        )
        self.input_size = input_size
        self._input_name: str | None = None

    @property
    def input_name(self) -> str:
        if self._input_name is None:
            self._input_name = self.session.get_inputs()[0].name
        return self._input_name

    def classify(self, image: np.ndarray) -> Classification:
        tensor = preprocess(image, self.input_size)
        outputs = self.session.run(None, {self.input_name: tensor})
        logits = np.asarray(outputs[0], dtype=np.float32).reshape(-1)

        if logits.size != len(self.classes):
            # A model/label mismatch must be loud. Silently truncating would
            # map every class to the wrong name.
            raise ValueError(
                f"model returned {logits.size} logits but {len(self.classes)} "
                "classes are configured; labels.json and the model are out of sync"
            )

        probs = softmax(logits)
        energy = free_energy(logits)
        index = int(np.argmax(probs))
        predicted = self.classes[index]
        confidence = float(probs[index])
        probabilities = {c.value: float(p) for c, p in zip(self.classes, probs, strict=True)}

        if predicted is DocumentType.UNKNOWN:
            return Classification(
                doc_type=DocumentType.UNKNOWN,
                confidence=confidence,
                probabilities=probabilities,
                energy=energy,
                is_ood=True,
                reason="model predicted the unknown class",
            )
        if confidence < self.min_confidence:
            return Classification(
                doc_type=DocumentType.UNKNOWN,
                confidence=confidence,
                probabilities=probabilities,
                energy=energy,
                is_ood=True,
                reason=(
                    f"top class {predicted.value} scored {confidence:.2f}, "
                    f"below the {self.min_confidence:.2f} floor"
                ),
            )
        if energy > self.max_energy:
            return Classification(
                doc_type=DocumentType.UNKNOWN,
                confidence=confidence,
                probabilities=probabilities,
                energy=energy,
                is_ood=True,
                reason=(
                    f"free energy {energy:.2f} exceeds {self.max_energy:.2f}; "
                    "the image does not resemble the training distribution"
                ),
            )
        return Classification(
            doc_type=predicted,
            confidence=confidence,
            probabilities=probabilities,
            energy=energy,
            is_ood=False,
        )

    def predict(self, image: np.ndarray) -> DocumentType:
        """The interface packages.ml.pipeline expects."""
        return self.classify(image).doc_type


def load_classes(model_path: Path) -> tuple[DocumentType, ...]:
    labels = model_path.with_name("labels.json")
    if not labels.exists():
        return DEFAULT_CLASSES
    raw = json.loads(labels.read_text(encoding="utf-8"))
    names = raw["classes"] if isinstance(raw, dict) else raw
    return tuple(DocumentType(n) for n in names)


def load_classifier(model_path: str | os.PathLike[str] | None = None
                    ) -> DocumentClassifier | None:
    """Build a classifier, or return None if no model is installed.

    Returning None rather than raising is the graceful-degradation rule: with
    no classifier the pipeline runs on the caller's document-type hint and
    still extracts. A missing optional model must never be a 500.
    """
    raw = str(model_path or os.getenv("CLASSIFIER_MODEL_PATH", "")).strip()
    if not raw:
        log.info("classifier disabled: CLASSIFIER_MODEL_PATH is not set")
        return None
    path = Path(raw)
    if not path.exists():
        log.warning("classifier disabled: model file not found at the configured path")
        return None
    try:
        import onnxruntime as ort
    except ImportError:
        log.warning("classifier disabled: onnxruntime is not installed")
        return None

    options = ort.SessionOptions()
    # One thread on purpose. The service runs on a fraction of a vCPU, and
    # letting ORT spawn a thread per core there causes contention, not speed.
    options.intra_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(str(path), options,
                                   providers=["CPUExecutionProvider"])
    return DocumentClassifier(session, classes=load_classes(path))
