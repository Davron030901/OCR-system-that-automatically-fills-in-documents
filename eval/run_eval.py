"""Golden-set evaluation.

The metric that decides product decisions is `zero_touch_rate`: the share of
documents a user did not have to correct. Field accuracy can look excellent
while zero-touch stays low, because one bad field forces a review of the whole
document.

`false_validated_rate` must be exactly zero. A value marked check-digit-verified
that is nonetheless wrong will propagate into a signed document unchallenged,
so any non-zero reading is a release blocker rather than a quality metric.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

GOLDEN = Path(__file__).parent / "golden_set"


@dataclass
class Metrics:
    documents: int = 0
    field_exact_match: float = 0.0
    zero_touch_rate: float = 0.0
    reject_rate: float = 0.0
    false_validated_rate: float = 0.0
    l3_usage_rate: float = 0.0
    llm_rejection_rate: float = 0.0
    cost_per_document: float = 0.0
    latency_p50_ms: int = 0
    latency_p95_ms: int = 0
    per_field: dict[str, float] = field(default_factory=dict)
    worst_cases: list[str] = field(default_factory=list)


def load_golden() -> list[dict]:
    manifest = GOLDEN / "manifest.jsonl"
    if not manifest.exists():
        return []
    return [json.loads(line) for line in manifest.read_text().splitlines() if line]


def compare(expected: dict, actual: dict) -> tuple[int, int, int]:
    """Returns (correct, total, false_validated)."""
    correct = total = false_validated = 0
    for path, want in expected.items():
        total += 1
        node = actual.get(path) or {}
        got = node.get("value")
        if got == want:
            correct += 1
        elif node.get("validated"):
            # Claimed verified, yet wrong. This is the failure that matters.
            false_validated += 1
    return correct, total, false_validated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path,
                        help="previous metrics.json for regression comparison")
    args = parser.parse_args()

    golden = load_golden()
    if not golden:
        print("No golden set found.\n"
              "Add 150-300 hand-annotated real documents to eval/golden_set/ "
              "with a manifest.jsonl of {file, expected:{path: value}}.\n"
              "Until then, accuracy claims are unmeasured.")
        return

    print(f"golden set: {len(golden)} documents")
    metrics = Metrics(documents=len(golden))
    # Wire the real pipeline in here once local OCR is installed:
    #   from packages.ml.pipeline import ExtractionPipeline
    print(json.dumps(asdict(metrics), indent=2))

    if args.baseline and args.baseline.exists():
        before = json.loads(args.baseline.read_text())
        if metrics.field_exact_match < before.get("field_exact_match", 0) - 0.01:
            raise SystemExit("REGRESSION: field accuracy dropped by more than 1%")


if __name__ == "__main__":
    main()
