"""The PII gate.

Every provider call passes through here. There is deliberately no
configuration flag to switch it off: a setting that disables a safety control
eventually gets set in production by someone debugging at 2am.

Two checks run:

1. Policy. If the request is marked as carrying real personal data and the
   target provider is not cleared for it, the call is refused outright.

2. Heuristic. If the request is marked as synthetic but the payload looks like
   real personal data, the call is also refused. Callers mislabel requests --
   usually by reusing a synthetic-data code path with real input -- and that
   mistake is exactly the one with irreversible consequences, because data
   sent to a training-enabled tier cannot be recalled.
"""

from __future__ import annotations

import re

from packages.llm.base import LLMProvider, LLMRequest, PIIPolicyViolation

# Patterns that indicate real Uzbek identity data rather than a test fixture.
_PINFL_RE = re.compile(r"\b\d{14}\b")
_DOC_NUMBER_RE = re.compile(r"\b[A-Z]{2}\s?\d{7}\b")
_MRZ_LINE_RE = re.compile(r"^[A-Z0-9<]{28,44}$", re.MULTILINE)
_PASSPORT_MRZ_RE = re.compile(r"P<[A-Z]{3}[A-Z<]{10,}")

# Values that appear in this project's own synthetic fixtures and public ICAO
# specimens. They must not trip the heuristic or the test suite becomes
# unrunnable against free-tier providers, which is the whole point of having
# synthetic data.
_KNOWN_SPECIMENS = {
    "L898902C3",          # ICAO 9303 TD3 worked example
    "D23145890",          # ICAO 9303 TD1 worked example
    "ERIKSSON",
    "UTO",                # the fictional "Utopia" issuing state
}


def looks_like_real_pii(text: str) -> list[str]:
    """Heuristic signals that a payload contains real personal data."""
    if not text:
        return []
    if any(spec in text for spec in _KNOWN_SPECIMENS):
        return []

    signals: list[str] = []
    if _PINFL_RE.search(text):
        signals.append("14-digit identifier (PINFL pattern)")
    if _DOC_NUMBER_RE.search(text):
        signals.append("document number pattern")
    if _PASSPORT_MRZ_RE.search(text):
        signals.append("passport MRZ header")
    elif len(_MRZ_LINE_RE.findall(text)) >= 2:
        signals.append("multiple MRZ-shaped lines")
    return signals


class PIIGate:
    """Enforces the personal-data routing policy. Not configurable by design."""

    @staticmethod
    def check(request: LLMRequest, provider: LLMProvider) -> None:
        caps = provider.capabilities

        if request.contains_real_pii and not caps.allows_real_pii:
            reason = (
                "provider tier is not cleared for real personal data"
                + (" (submitted data may be used for model training)"
                   if caps.data_used_for_training else "")
            )
            raise PIIPolicyViolation(
                f"Refusing to send real personal data to '{provider.name}' "
                f"({provider.model}): {reason}. Use a paid tier with a data "
                f"processing agreement, or a locally hosted model."
            )

        if request.images and not caps.allows_real_pii:
            raise PIIPolicyViolation(
                f"Refusing to send document images to '{provider.name}': "
                "images of identity documents are always treated as real "
                "personal data regardless of the contains_real_pii flag."
            )

        if not request.contains_real_pii:
            signals = looks_like_real_pii(
                f"{request.system}\n{request.user_text}")
            if signals and not caps.allows_real_pii:
                raise PIIPolicyViolation(
                    "Request is marked synthetic but the payload looks like "
                    f"real personal data ({'; '.join(signals)}). Refusing to "
                    f"send it to '{provider.name}'. If this really is test "
                    "data, adjust the fixture; if it is not, set "
                    "contains_real_pii=True and route to a cleared provider."
                )


def assert_safe(request: LLMRequest, provider: LLMProvider) -> None:
    """Convenience wrapper used by the router before every dispatch."""
    PIIGate.check(request, provider)
