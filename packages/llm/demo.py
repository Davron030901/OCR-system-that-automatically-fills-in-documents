"""Demo mode.

WHAT THIS IS
------------
An MVP has to be shown to people before there is a paid provider contract, a
data processing agreement, or a legal opinion on cross-border transfer. Demo
mode is the honest version of that situation: the deployment declares that it
is NOT accepting real documents, and the whole LLM layer is routed to free-tier
keys accordingly.

WHAT THIS IS NOT
----------------
It is not a way to send real passports to a free tier. That remains impossible,
and this module makes it impossible on purpose rather than by convention.

The mechanism is worth understanding, because it is the opposite of the usual
"safety flag" design:

  * Demo mode marks outgoing requests as synthetic (contains_real_pii=False).
  * PIIGate's heuristic then inspects the actual payload. A real PINFL, a real
    document number or an MRZ block trips it, and the request is REFUSED.

So a user who ignores the warning and uploads a real passport does not leak it.
The request fails, the cascade falls back to its local stages, and the result
comes back flagged for review. The failure mode of demo mode is a refusal, not
a disclosure — and that property comes from the gate, which has no off switch,
not from anything here.

The local stages keep working throughout: MRZ parsing (L0) and rule-based
mapping (L1) run entirely on the server and never touch a provider. A demo can
therefore read a passport's machine readable zone perfectly well with no paid
key at all. Only the semantic mapping of the visual zone (L2) is unavailable
for a real document in demo mode.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# The warning shown to end users. Deliberately concrete: "demo mode" means
# nothing to someone holding their passport up to a camera.
DEMO_WARNING_UZ = (
    "Bu demo tizim. Haqiqiy pasport yoki ID karta yuklamang — namuna "
    "hujjatlardan foydalaning. Demo rejimida hujjat ma'lumoti tahlil uchun "
    "bepul xizmatga yuborilmaydi va tizim uni qabul qilmaydi."
)

DEMO_REFUSAL_UZ = (
    "Demo rejimida haqiqiy hujjat qabul qilinmaydi. Mashina o'qiydigan zona "
    "(MRZ) lokal o'qildi, lekin qolgan maydonlarni tahlil qilish uchun "
    "pullik rejim kerak."
)


def is_demo_mode() -> bool:
    """True when this deployment is a demo and must not process real documents.

    Defaults to False. A deployment is production unless it says otherwise —
    the opposite default would mean a misconfigured instance silently claims to
    be safe while accepting real documents.
    """
    return os.getenv("DEMO_MODE", "false").strip().lower() in {"1", "true", "yes"}


def provider_order(default: str = "openai,gemini-paid") -> list[str]:
    """Preference order for provider selection.

    In demo mode the free tier goes first: it is what the mode exists to use.
    A paid provider stays in the list behind it, so a deployment that has both
    still works if the free quota runs out.
    """
    raw = os.getenv("LLM_PROVIDER_ORDER", "").strip()
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    if is_demo_mode():
        return ["gemini-free", "openai", "gemini-paid"]
    return [p.strip() for p in default.split(",") if p.strip()]


def contains_real_pii(demo: bool | None = None) -> bool:
    """What to put in LLMRequest.contains_real_pii.

    False in demo mode is a CLAIM about the deployment, not about the specific
    payload — and PIIGate is what verifies that claim per request. If the claim
    turns out to be false for a given document, the gate refuses the call.
    """
    demo = is_demo_mode() if demo is None else demo
    return not demo


def log_startup_banner() -> None:
    """Say plainly, once, which mode the process is in.

    A deployment silently running in the wrong mode is the failure worth
    spending three log lines to prevent.
    """
    if is_demo_mode():
        log.warning(
            "DEMO MODE: free-tier providers only. Real identity documents "
            "will be REFUSED by the PII gate, not processed. Set "
            "DEMO_MODE=false with a paid key for production."
        )
    else:
        log.info("production mode: real documents routed to cleared providers")
