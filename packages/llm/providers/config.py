"""Provider definitions.

Model names and prices live here rather than in code paths because provider
line-ups change fast: models get deprecated and replaced on a few months'
notice, and a hardcoded model string becomes a production outage. Override any
of this from environment variables.

Prices are USD per million tokens and reflect published rates at the time of
writing. VERIFY THEM before relying on cost projections.
"""

from __future__ import annotations

import os

from packages.llm.base import ProviderCapabilities


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# --- OpenAI: paid key, data not used for training -------------------------
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_CAPS = ProviderCapabilities(
    supports_vision=True,
    supports_json_schema=True,
    max_input_tokens=128_000,
    allows_real_pii=os.getenv("OPENAI_TIER", "paid") == "paid",
    data_used_for_training=False,
    zero_data_retention=os.getenv("OPENAI_ZDR", "false").lower() == "true",
    cost_per_1m_input=_f("OPENAI_COST_IN", 0.15),
    cost_per_1m_output=_f("OPENAI_COST_OUT", 0.60),
)

# --- Gemini FREE tier: synthetic data only --------------------------------
# Free-tier submissions may be used to improve the provider's models, so this
# tier must never receive real personal data. The PII gate enforces it.
GEMINI_FREE_MODEL = os.getenv("GEMINI_FREE_MODEL", "gemini-2.5-flash-lite")
GEMINI_FREE_CAPS = ProviderCapabilities(
    supports_vision=True,
    supports_json_schema=True,
    max_input_tokens=1_000_000,
    allows_real_pii=False,          # deliberately hardcoded, not configurable
    data_used_for_training=True,
    zero_data_retention=False,
    cost_per_1m_input=0.0,
    cost_per_1m_output=0.0,
)

# --- Gemini PAID tier: billing enabled, no training on submissions --------
GEMINI_PAID_MODEL = os.getenv("GEMINI_PAID_MODEL", "gemini-2.5-flash-lite")
GEMINI_PAID_CAPS = ProviderCapabilities(
    supports_vision=True,
    supports_json_schema=True,
    max_input_tokens=1_000_000,
    allows_real_pii=os.getenv("GEMINI_PAID_ENABLED", "false").lower() == "true",
    data_used_for_training=False,
    zero_data_retention=False,
    cost_per_1m_input=_f("GEMINI_COST_IN", 0.10),
    cost_per_1m_output=_f("GEMINI_COST_OUT", 0.40),
)

# --- Local model: nothing leaves the premises -----------------------------
# This is the migration path if the data-localisation requirement is enforced
# strictly. Swapping to it is a configuration change, not a rewrite.
LOCAL_MODEL = os.getenv("LOCAL_VLM_MODEL", "qwen2.5-vl-7b")
LOCAL_BASE_URL = os.getenv("LOCAL_VLM_URL", "http://localhost:8000/v1")
LOCAL_CAPS = ProviderCapabilities(
    supports_vision=True,
    supports_json_schema=False,     # enforced by re-prompting instead
    max_input_tokens=32_000,
    allows_real_pii=True,           # data never leaves your infrastructure
    data_used_for_training=False,
    zero_data_retention=True,
    cost_per_1m_input=0.0,
    cost_per_1m_output=0.0,
)


def estimate_cost(caps: ProviderCapabilities, tokens_in: int,
                  tokens_out: int) -> float:
    return (tokens_in / 1_000_000 * caps.cost_per_1m_input
            + tokens_out / 1_000_000 * caps.cost_per_1m_output)
