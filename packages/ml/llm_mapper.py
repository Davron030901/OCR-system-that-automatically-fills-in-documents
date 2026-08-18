"""Stages L2 and L3: map OCR text (or, as a last resort, the image) via an LLM.

The key design choice is that L2 sends TEXT, not the image. Three consequences:
the document image never leaves the machine, the token count drops by roughly
5x, and grounding verification becomes trivial because every returned value
must appear in the text we sent.
"""
from __future__ import annotations

import json
import os
from typing import Any

from packages.llm.base import LLMRequest, LLMTask
from packages.llm.grounding import verify
from packages.llm.keypool import KeyPool, ManagedKey
from packages.llm.providers.config import (
    GEMINI_FREE_CAPS,
    GEMINI_FREE_MODEL,
    GEMINI_PAID_CAPS,
    GEMINI_PAID_MODEL,
    LOCAL_BASE_URL,
    LOCAL_CAPS,
    LOCAL_MODEL,
    OPENAI_CAPS,
    OPENAI_MODEL,
)
from packages.llm.providers.http_providers import (
    GeminiProvider,
    LocalProvider,
    OpenAIProvider,
)
from packages.llm.router import BudgetGuard, LLMRouter

SYSTEM_PROMPT = """You map text recognised from Uzbek identity and education \
documents onto a fixed schema.

Rules you must follow exactly:
1. Copy values VERBATIM from the supplied text. Never translate, correct,
   reformat or complete them.
2. If a field is not present in the text, return null. Do NOT infer it from
   context, and do NOT guess. A null is correct; an invention is a defect.
3. Dates must be returned as YYYY-MM-DD, converted only from a date that is
   actually present in the text.
4. Uzbek documents mix Latin and Cyrillic script and Uzbek, Russian and
   English labels. Common labels: Familiya/Фамилия (surname),
   Ismi/Имя (given name), Otasining ismi/Отчество (patronymic),
   Tug'ilgan sanasi/Дата рождения (birth date), JSHSHIR/PINFL (14-digit
   personal number), Seriya/Серия (document number).
5. Return only the requested fields as a single JSON object."""


def build_pool() -> KeyPool:
    """Assemble keys from the environment.

    Free Gemini keys are registered with allows_real_pii=False. They are useful
    for prompt tuning against synthetic data; the PII gate makes sure they can
    never receive a real document.
    """
    pool = KeyPool()
    if key := os.getenv("OPENAI_API_KEY"):
        pool.add(ManagedKey("openai-1", "openai", key,
                            allows_real_pii=OPENAI_CAPS.allows_real_pii))

    for i, key in enumerate(_split(os.getenv("GEMINI_FREE_KEYS", "")), 1):
        pool.add(ManagedKey(f"gemini-free-{i:02d}", "gemini-free", key,
                            allows_real_pii=False))

    for i, key in enumerate(_split(os.getenv("GEMINI_PAID_KEYS", "")), 1):
        pool.add(ManagedKey(f"gemini-paid-{i:02d}", "gemini-paid", key,
                            allows_real_pii=GEMINI_PAID_CAPS.allows_real_pii))

    if os.getenv("LOCAL_VLM_URL"):
        pool.add(ManagedKey("local-1", "local", "n/a", allows_real_pii=True))
    return pool


def _split(raw: str) -> list[str]:
    return [k.strip() for k in raw.replace("\n", ",").split(",") if k.strip()]


def build_router() -> LLMRouter:
    providers = []
    if key := os.getenv("OPENAI_API_KEY"):
        providers.append(OpenAIProvider(key, OPENAI_MODEL, OPENAI_CAPS))
    free = _split(os.getenv("GEMINI_FREE_KEYS", ""))
    if free:
        providers.append(GeminiProvider(free[0], GEMINI_FREE_MODEL,
                                        GEMINI_FREE_CAPS, name="gemini-free"))
    paid = _split(os.getenv("GEMINI_PAID_KEYS", ""))
    if paid:
        providers.append(GeminiProvider(paid[0], GEMINI_PAID_MODEL,
                                        GEMINI_PAID_CAPS, name="gemini-paid"))
    if os.getenv("LOCAL_VLM_URL"):
        providers.append(LocalProvider(LOCAL_BASE_URL, LOCAL_MODEL, LOCAL_CAPS))

    order = os.getenv("LLM_PROVIDER_ORDER",
                      "local,openai,gemini-paid,gemini-free").split(",")
    return LLMRouter(
        providers, build_pool(),
        BudgetGuard(daily_limit_usd=float(os.getenv("LLM_DAILY_BUDGET_USD", 5))),
        order=[o.strip() for o in order],
    )


def _schema_for(fields: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {f: {"type": ["string", "null"]} for f in fields},
        "required": fields,
        "additionalProperties": False,
    }


class CascadeMapper:
    """Implements the LLMMapper protocol used by ExtractionPipeline."""

    def __init__(self, router: LLMRouter | None = None):
        self.router = router or build_router()
        self.prompt_version = "v1"

    async def map_text(self, ocr_text: str, doc_type: str,
                       unresolved: list[str]) -> tuple[dict[str, Any], float]:
        fields = unresolved[:25]
        req = LLMRequest(
            system=SYSTEM_PROMPT,
            user_text=(
                f"Document type: {doc_type}\n\n"
                f"Recognised text:\n---\n{ocr_text}\n---\n\n"
                f"Return these fields: {json.dumps(fields)}"),
            json_schema=_schema_for(fields),
            task=LLMTask.TEXT_MAPPING,
            contains_real_pii=True,     # it is a real document; fail closed
            prompt_version=self.prompt_version,
        )
        resp = await self.router.complete(req)
        raw = resp.parsed or {}
        # Nothing the model returns is trusted until it is found in the text.
        cleaned, _report = verify(raw, ocr_text)
        return {k: v for k, v in cleaned.items() if v is not None}, resp.cost_usd

    async def map_image(self, image_bytes: bytes, doc_type: str,
                        unresolved: list[str]) -> tuple[dict[str, Any], float]:
        fields = unresolved[:25]
        req = LLMRequest(
            system=SYSTEM_PROMPT,
            user_text=(f"Document type: {doc_type}\n"
                       f"Return these fields: {json.dumps(fields)}"),
            images=[image_bytes],
            json_schema=_schema_for(fields),
            task=LLMTask.VISION,
            contains_real_pii=True,
            prompt_version=self.prompt_version,
        )
        resp = await self.router.complete(req)
        # No local text to ground against here, which is exactly why L3 is a
        # last resort and its output is given lower confidence upstream.
        return {k: v for k, v in (resp.parsed or {}).items() if v is not None}, \
            resp.cost_usd
