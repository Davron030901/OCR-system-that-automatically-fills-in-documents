"""Demo mode tests.

The claim being tested is narrow and important: demo mode changes WHERE
requests are routed, and never WHAT is allowed to leave. A real identity
document uploaded to a demo deployment must be refused, not sent to a
training-enabled free tier.

If a future change makes demo mode a way to bypass the PII gate, these tests
fail — which is the point of writing them against real passport-shaped payloads
rather than against the flag.
"""

from __future__ import annotations

from typing import cast

import pytest

from packages.llm.base import (
    LLMProvider,
    LLMRequest,
    LLMTask,
    PIIPolicyViolation,
    ProviderCapabilities,
)
from packages.llm.demo import (
    contains_real_pii,
    is_demo_mode,
    provider_order,
)
from packages.llm.pii_gate import PIIGate

# A payload that looks exactly like what a real upload produces: a 14-digit
# PINFL and an Uzbek document number.
REAL_PAYLOAD = (
    "Familiyasi TOSHMATOV\n"
    "Ismi JASUR\n"
    "JSHSHIR 31206930045612\n"
    "Seriya va raqami AC1928374"
)

# Synthetic text with no identifier in it — what a demo is supposed to carry.
SYNTHETIC_PAYLOAD = (
    "Familiyasi NAMUNAYEV\n"
    "Ismi NAMUNA\n"
    "Tug'ilgan sanasi 01.01.2000"
)

FREE_CAPS = ProviderCapabilities(
    supports_vision=True,
    supports_json_schema=True,
    max_input_tokens=1_000_000,
    allows_real_pii=False,
    data_used_for_training=True,
    zero_data_retention=False,
    cost_per_1m_input=0.0,
    cost_per_1m_output=0.0,
)

PAID_CAPS = ProviderCapabilities(
    supports_vision=True,
    supports_json_schema=True,
    max_input_tokens=128_000,
    allows_real_pii=True,
    data_used_for_training=False,
    zero_data_retention=False,
    cost_per_1m_input=0.15,
    cost_per_1m_output=0.60,
)


class FakeProvider:
    """Only the attributes PIIGate reads.

    The gate inspects name, model and capabilities and never dispatches, so
    implementing complete()/health_check() here would be dead code that
    obscures what is actually under test.
    """

    def __init__(self, name: str, caps: ProviderCapabilities) -> None:
        self.name = name
        self.model = f"{name}-model"
        self.capabilities = caps


def provider(name: str, caps: ProviderCapabilities) -> LLMProvider:
    return cast(LLMProvider, FakeProvider(name, caps))


FREE = provider("gemini-free", FREE_CAPS)
PAID = provider("openai", PAID_CAPS)


def request(text: str, *, demo: bool, images=None) -> LLMRequest:
    return LLMRequest(
        system="map the text",
        user_text=text,
        images=images,
        task=LLMTask.VISION if images else LLMTask.TEXT_MAPPING,
        contains_real_pii=contains_real_pii(demo),
    )


# --- the flag itself -------------------------------------------------------

def test_demo_mode_defaults_to_off(monkeypatch) -> None:
    """A deployment is production unless it says otherwise.

    The opposite default would let a misconfigured instance claim to be safe
    while accepting real documents.
    """
    monkeypatch.delenv("DEMO_MODE", raising=False)
    assert is_demo_mode() is False
    assert contains_real_pii() is True


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", " true "])
def test_demo_mode_accepts_the_usual_spellings(monkeypatch, value: str) -> None:
    monkeypatch.setenv("DEMO_MODE", value)
    assert is_demo_mode() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "", "maybe"])
def test_anything_unclear_means_production(monkeypatch, value: str) -> None:
    monkeypatch.setenv("DEMO_MODE", value)
    assert is_demo_mode() is False


# --- routing ---------------------------------------------------------------

def test_demo_mode_puts_the_free_tier_first(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("LLM_PROVIDER_ORDER", raising=False)
    assert provider_order()[0] == "gemini-free"


def test_production_never_puts_the_free_tier_first(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.delenv("LLM_PROVIDER_ORDER", raising=False)
    order = provider_order()
    assert order[0] != "gemini-free"


def test_explicit_order_overrides_demo_mode(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "openai,local")
    assert provider_order() == ["openai", "local"]


# --- the part that matters -------------------------------------------------

def test_demo_mode_still_refuses_a_real_document() -> None:
    """⭐ The whole design rests on this.

    Demo mode marks the request synthetic. The gate then looks at the actual
    payload, finds a PINFL and a document number, and refuses. The user's
    passport does not reach a training-enabled tier.
    """
    with pytest.raises(PIIPolicyViolation, match="looks like"):
        PIIGate.check(request(REAL_PAYLOAD, demo=True), FREE)


def test_demo_mode_allows_genuinely_synthetic_text() -> None:
    """The mode has to actually work, or nobody uses it and it rots."""
    PIIGate.check(request(SYNTHETIC_PAYLOAD, demo=True), FREE)


def test_production_refuses_real_data_to_a_free_tier() -> None:
    """The original policy check, unchanged by demo mode existing."""
    with pytest.raises(PIIPolicyViolation, match="not cleared"):
        PIIGate.check(request(REAL_PAYLOAD, demo=False), FREE)


def test_production_allows_real_data_to_a_paid_tier() -> None:
    PIIGate.check(request(REAL_PAYLOAD, demo=False), PAID)


def test_images_never_reach_a_free_tier_even_in_demo_mode() -> None:
    """L3 vision is unavailable in demo mode, by construction.

    An image of a document cannot be screened by a text heuristic, so the gate
    treats every image as real personal data regardless of the flag.
    """
    with pytest.raises(PIIPolicyViolation, match="images"):
        PIIGate.check(
            request(SYNTHETIC_PAYLOAD, demo=True, images=[b"\xff\xd8\xff"]),
            FREE,
        )


def test_demo_mode_cannot_be_turned_into_a_bypass() -> None:
    """There is no combination of flags that sends a real PINFL to a free tier.

    Enumerated rather than asserted in prose: if someone later adds a
    "trust the caller" escape hatch, this is what fails.
    """
    for demo in (True, False):
        req = request(REAL_PAYLOAD, demo=demo)
        with pytest.raises(PIIPolicyViolation):
            PIIGate.check(req, FREE)


def test_mapper_marks_requests_according_to_mode() -> None:
    """CascadeMapper reads the mode once, at construction.

    The router is a stub: this asserts on how the mode is recorded, and
    building a real router would require live credentials.
    """
    from packages.llm.router import LLMRouter
    from packages.ml.llm_mapper import CascadeMapper

    class NullRouter:
        """Never dispatched to; only present so __init__ skips build_router()."""

    stub = cast(LLMRouter, NullRouter())
    assert CascadeMapper(router=stub, demo_mode=True).demo_mode is True
    assert CascadeMapper(router=stub, demo_mode=False).demo_mode is False
