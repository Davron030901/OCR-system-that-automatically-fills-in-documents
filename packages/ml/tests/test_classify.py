"""Classifier tests.

The point of these is the REFUSAL path. A classifier that is 96% accurate on
the six document classes and cannot say "I do not recognise this" is worse than
useless here, because every unrecognised photo becomes a confidently wrong
extraction carrying somebody's real passport number.

The ONNX session is stubbed. That keeps the decision logic — thresholds,
energy, label mapping — under test without a trained model or a GPU, and it
lets the tests assert on logits chosen to sit exactly on the interesting side
of each boundary.
"""

from __future__ import annotations

import numpy as np
import pytest

from packages.ml.classify import (
    DEFAULT_CLASSES,
    DocumentClassifier,
    free_energy,
    preprocess,
    softmax,
)
from packages.schema.models import DocumentType


class StubInput:
    name = "input"


class StubSession:
    """Returns fixed logits, ignoring the image."""

    def __init__(self, logits: list[float]) -> None:
        self.logits = np.array([logits], dtype=np.float32)
        self.calls: list[tuple[int, ...]] = []

    def run(self, output_names, input_feed):  # noqa: ANN001, ARG002
        tensor = next(iter(input_feed.values()))
        self.calls.append(tuple(tensor.shape))
        return [self.logits]

    def get_inputs(self):
        return [StubInput()]


def blank_image() -> np.ndarray:
    return np.full((640, 400, 3), 200, dtype=np.uint8)


def test_preprocess_shape_and_range() -> None:
    tensor = preprocess(blank_image())
    assert tensor.shape == (1, 3, 320, 320)
    assert tensor.dtype == np.float32
    # Normalised with ImageNet statistics, so values land roughly in [-2.2, 2.7].
    assert float(tensor.min()) > -3.0 and float(tensor.max()) < 3.0


def test_preprocess_accepts_grayscale_and_alpha() -> None:
    assert preprocess(np.zeros((100, 100), dtype=np.uint8)).shape == (1, 3, 320, 320)
    assert preprocess(np.zeros((100, 100, 4), dtype=np.uint8)).shape == (1, 3, 320, 320)


def test_softmax_is_a_distribution() -> None:
    probs = softmax(np.array([2.0, 1.0, 0.1], dtype=np.float32))
    assert float(probs.sum()) == pytest.approx(1.0)
    assert probs[0] > probs[1] > probs[2]


def test_energy_separates_confident_from_flat_logits() -> None:
    """Energy reads logit SCALE, which softmax normalises away.

    Both vectors below produce the same softmax. Only energy can tell them
    apart, and that difference is the whole out-of-distribution signal.
    """
    confident = np.array([8.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype=np.float32)
    flat = confident * 0.05
    assert softmax(confident)[0] > softmax(flat)[0]
    assert free_energy(confident) < free_energy(flat)


def test_confident_passport_is_accepted() -> None:
    clf = DocumentClassifier(
        StubSession([9.0, 1.0, 0.5, 0.2, 0.1, 0.1, 0.0]), max_energy=-2.0
    )
    result = clf.classify(blank_image())
    assert result.doc_type is DocumentType.PASSPORT_BIO
    assert result.is_ood is False
    assert result.confidence > 0.9
    assert result.probabilities["passport_bio"] == pytest.approx(result.confidence)


def test_low_confidence_is_refused_not_guessed() -> None:
    """Two classes neck and neck must produce a refusal, not a coin flip."""
    clf = DocumentClassifier(
        StubSession([4.0, 3.95, 0.1, 0.1, 0.1, 0.1, 0.1]), min_confidence=0.60
    )
    result = clf.classify(blank_image())
    assert result.doc_type is DocumentType.UNKNOWN
    assert result.is_ood is True
    assert "floor" in result.reason


def test_high_energy_is_refused_even_when_softmax_is_confident() -> None:
    """The confidently-wrong case: a cat photo that scores 0.95 on something."""
    clf = DocumentClassifier(
        StubSession([0.30, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01]),
        min_confidence=0.10,
        max_energy=-2.5,
    )
    result = clf.classify(blank_image())
    assert result.doc_type is DocumentType.UNKNOWN
    assert "energy" in result.reason


def test_explicit_unknown_class_wins() -> None:
    clf = DocumentClassifier(StubSession([0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 9.0]))
    result = clf.classify(blank_image())
    assert result.doc_type is DocumentType.UNKNOWN
    assert result.is_ood is True


def test_label_count_mismatch_raises_rather_than_mislabels() -> None:
    clf = DocumentClassifier(StubSession([1.0, 2.0, 3.0]))
    with pytest.raises(ValueError, match="out of sync"):
        clf.classify(blank_image())


def test_predict_returns_a_document_type() -> None:
    clf = DocumentClassifier(StubSession([9.0, 1.0, 0.5, 0.2, 0.1, 0.1, 0.0]))
    assert clf.predict(blank_image()) in set(DEFAULT_CLASSES)


def test_missing_model_disables_the_classifier_instead_of_raising() -> None:
    """Graceful degradation: no model means no classification, not a crash."""
    from packages.ml.classify import load_classifier

    assert load_classifier("") is None
    assert load_classifier("/nonexistent/model.onnx") is None
