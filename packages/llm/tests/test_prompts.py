"""Prompt registry and evaluation-set tests.

The most valuable assertion here is the LAST one. Every non-null value in every
evaluation fixture must be findable in that fixture's input text by the same
grounding code that runs in production. That makes it impossible to write an
eval set which teaches the model to invent a value: an expected answer that is
not in the input fails the suite.
"""

from __future__ import annotations

import json

import pytest

from packages.llm import prompts
from packages.llm.grounding import verify_value

PROMPT_NAMES = ["id_visual_zone", "diploma_extract",
                "diploma_supplement_table", "vision_fallback"]


def eval_rows(name: str) -> list[dict]:
    path = prompts.PROMPT_DIR / "eval" / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_all_expected_prompts_exist() -> None:
    found = prompts.available()
    for name in PROMPT_NAMES:
        assert name in found, f"prompt {name} is missing"
        assert found[name], f"prompt {name} has no versions"


def test_load_returns_highest_version_by_default() -> None:
    p = prompts.load("id_visual_zone")
    assert p.name == "id_visual_zone"
    assert p.version.startswith("v")
    assert p.id == f"id_visual_zone.{p.version}"
    assert len(p.text) > 500


def test_version_can_be_pinned_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("PROMPT_PIN_ID_VISUAL_ZONE", "1")
    assert prompts.load("id_visual_zone").version == "v1"


def test_unknown_prompt_and_unknown_version_raise() -> None:
    with pytest.raises(KeyError, match="no prompt named"):
        prompts.load("does_not_exist")
    with pytest.raises(KeyError, match="has no v99"):
        prompts.load("id_visual_zone", version=99)


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_every_prompt_forbids_guessing(name: str) -> None:
    """The single instruction this system cannot ship without."""
    text = prompts.load(name).text.lower()
    assert prompts.REQUIRED_INSTRUCTION in text
    assert "guess" in text, f"{name} does not tell the model not to guess"


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_prompts_carry_no_real_looking_pinfl(name: str) -> None:
    """Few-shot examples must be synthetic.

    A prompt is copied into logs, bug reports and provider request bodies, so
    a real 14-digit personal number embedded in one leaks everywhere at once.
    The synthetic examples here are checked structurally: any 14-digit run must
    come from the fixture generator's reserved test ranges.
    """
    import re

    text = prompts.load(name).text
    for match in re.finditer(r"\b\d{14}\b", text):
        pinfl = match.group()
        assert pinfl.startswith(("3", "5")) and pinfl.endswith("12345"), (
            f"{name} contains a 14-digit number that is not from the synthetic "
            f"range: {pinfl[:2]}...{pinfl[-2:]}"
        )


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_every_prompt_has_an_eval_set(name: str) -> None:
    rows = eval_rows(name)
    assert len(rows) >= 3, f"{name} needs at least 3 evaluation cases"
    for row in rows:
        assert row["id"], "every case needs an id"
        assert row["input_text"].strip(), f"{row['id']} has no input"
        assert "expected" in row, f"{row['id']} has no expected output"


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_eval_sets_include_a_null_case(name: str) -> None:
    """At least one case must have a correct answer of "nothing".

    An eval set made only of documents where every field is present rewards a
    model that always answers, which is the failure mode this project cares
    about most.
    """
    rows = eval_rows(name)
    has_null = any(
        _contains_null(row["expected"]) or _is_empty_list(row["expected"])
        for row in rows
    )
    assert has_null, f"{name} has no case whose correct answer is null or empty"


def _contains_null(expected: dict) -> bool:
    return any(v is None for v in expected.values())


def _is_empty_list(expected: dict) -> bool:
    return any(isinstance(v, list) and not v for v in expected.values())


def _flat_values(expected: dict):
    """Yield (path, value) for scalars, descending into subject-row lists."""
    for path, value in expected.items():
        if isinstance(value, list):
            for i, row in enumerate(value):
                for k, v in row.items():
                    yield f"{path}.{i}.{k}", v
        else:
            yield path, value


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_every_expected_value_is_grounded_in_its_input(name: str) -> None:
    """The eval sets cannot encode a hallucination.

    Uses the production grounding check, so this also acts as a regression test
    on grounding itself: if a change there stops recognising Uzbek dates or
    apostrophe variants, these fixtures start failing.
    """
    for row in eval_rows(name):
        text = row["input_text"]
        for path, value in _flat_values(row["expected"]):
            if value is None:
                continue
            ok, reason = verify_value(path, str(value), text)
            assert ok, (
                f"{name}/{row['id']}: expected {path}={value!r} is not present "
                f"in the input text ({reason}). Either the fixture is wrong or "
                f"it is teaching the model to invent values."
            )
