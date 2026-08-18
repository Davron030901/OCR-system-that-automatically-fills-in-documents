"""Provider abstraction for the LLM/VLM layer.

The `allows_real_pii` capability is the most important field in this module.
It is what stops passport data reaching a provider tier whose terms permit
using submitted data for model training.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class LLMTask(StrEnum):
    TEXT_MAPPING = "text_mapping"     # stage L2 — OCR text to canonical schema
    VISION = "vision"                 # stage L3 — image to canonical schema
    NORMALISE = "normalise"           # tidy a single value


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_vision: bool = False
    supports_json_schema: bool = False
    max_input_tokens: int = 128_000

    # --- privacy properties -------------------------------------------------
    allows_real_pii: bool = False
    """Whether real personal data may be sent to this provider/tier.

    False for any free tier whose terms allow training on submitted data.
    Enforced by PIIGate, which cannot be disabled by configuration.
    """
    data_used_for_training: bool = True
    zero_data_retention: bool = False

    # --- cost, from configuration rather than hardcoded ---------------------
    cost_per_1m_input: float = 0.0
    cost_per_1m_output: float = 0.0


@dataclass
class LLMRequest:
    system: str
    user_text: str
    images: list[bytes] = field(default_factory=list)
    json_schema: dict[str, Any] | None = None
    max_tokens: int = 2048
    temperature: float = 0.0
    task: LLMTask = LLMTask.TEXT_MAPPING

    contains_real_pii: bool = True
    """Callers MUST set this honestly.

    It defaults to True so that forgetting to set it fails closed: the request
    is restricted to providers cleared for real personal data rather than
    leaking to a free tier.
    """

    prompt_version: str = "v1"


@dataclass
class LLMResponse:
    content: str
    parsed: dict[str, Any] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    request_id: str = ""
    finish_reason: str = ""
    cache_hit: bool = False


class LLMProvider(Protocol):
    name: str
    model: str
    capabilities: ProviderCapabilities

    async def complete(self, req: LLMRequest) -> LLMResponse: ...
    async def health_check(self) -> bool: ...


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class LLMError(Exception):
    """Base for every failure in this layer."""


class PIIPolicyViolation(LLMError):
    """Raised when a request would send real personal data somewhere unsafe.

    This is a policy failure, never a transient one. It must not be retried,
    routed elsewhere automatically, or downgraded to a warning.
    """


class RateLimited(LLMError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class QuotaExhausted(LLMError):
    pass


class InvalidKey(LLMError):
    pass


class BadRequest(LLMError):
    """A malformed request. Retrying on another key produces the same error."""


class ProviderUnavailable(LLMError):
    pass


class BudgetExceeded(LLMError):
    pass
