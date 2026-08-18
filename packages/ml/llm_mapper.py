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

from packages.llm import prompts
from packages.llm.base import LLMProvider, LLMRequest, LLMTask
from packages.llm.cache import ResponseCache, build_cache
from packages.llm.grounding import verify
from packages.llm.keypool import KeyPool, ManagedKey
from packages.llm.prompts import Prompt
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

# Prompts live in packages/llm/prompts/ as versioned files, not as a constant
# here. A prompt edit changes model behaviour as much as a model swap does, so
# it needs a version number that travels with every response, a pin so a
# rollout can be held on the previous one, and a regression eval set. See
# packages/llm/prompts/registry.py.


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
    # Annotated as the protocol rather than inferred from the first append.
    # Without this mypy infers list[OpenAIProvider] from line one and then
    # rejects every other provider, which is exactly backwards for a list
    # whose entire purpose is holding heterogeneous providers.
    providers: list[LLMProvider] = []
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


def prompt_for(doc_type: str) -> Prompt:
    """Pick the prompt registered for this document type.

    Falls back to the identity-document prompt, which is the conservative
    choice: its rules are the strictest in the registry, so an unrecognised
    document type gets more caution rather than less.
    """
    name = ("diploma_extract" if "diploma" in doc_type.lower()
            else "id_visual_zone")
    return prompts.load(name)


class CascadeMapper:
    """Implements the LLMMapper protocol used by ExtractionPipeline."""

    def __init__(self, router: LLMRouter | None = None,
                 cache: ResponseCache | None = None,
                 tenant: str = "default"):
        self.router = router or build_router()
        # Retries are the common case: a user who dislikes a result uploads the
        # same photo again. Without the cache that second attempt pays full
        # price for a byte-identical request.
        self.cache = cache if cache is not None else build_cache()
        self.tenant = tenant

    async def map_text(self, ocr_text: str, doc_type: str,
                       unresolved: list[str]) -> tuple[dict[str, Any], float]:
        fields = unresolved[:25]
        prompt = prompt_for(doc_type)
        payload = f"{doc_type}\n{json.dumps(fields)}\n{ocr_text}"

        cached = self.cache.get("router", "cascade", prompt.id, payload,
                                tenant=self.tenant)
        if cached is not None:
            # A cache hit still goes through grounding. The stored answer was
            # verified once, but re-verifying costs microseconds and means a
            # grounding fix applies to cached answers too.
            cleaned, _ = verify(cached, ocr_text)
            return {k: v for k, v in cleaned.items() if v is not None}, 0.0

        req = LLMRequest(
            system=prompt.text,
            user_text=(
                f"Document type: {doc_type}\n\n"
                f"Recognised text:\n---\n{ocr_text}\n---\n\n"
                f"Return these fields: {json.dumps(fields)}"),
            json_schema=_schema_for(fields),
            task=LLMTask.TEXT_MAPPING,
            contains_real_pii=True,     # it is a real document; fail closed
            prompt_version=prompt.id,
        )
        resp = await self.router.complete(req)
        raw = resp.parsed or {}
        if raw:
            self.cache.set("router", "cascade", prompt.id, payload, raw,
                           tenant=self.tenant)
        # Nothing the model returns is trusted until it is found in the text.
        cleaned, _report = verify(raw, ocr_text)
        return {k: v for k, v in cleaned.items() if v is not None}, resp.cost_usd

    async def map_image(self, image_bytes: bytes, doc_type: str,
                        unresolved: list[str]) -> tuple[dict[str, Any], float]:
        fields = unresolved[:25]
        prompt = prompts.load("vision_fallback")
        req = LLMRequest(
            system=prompt.text,
            user_text=(f"Document type: {doc_type}\n"
                       f"Return these fields: {json.dumps(fields)}"),
            images=[image_bytes],
            json_schema=_schema_for(fields),
            task=LLMTask.VISION,
            contains_real_pii=True,
            prompt_version=prompt.id,
        )
        resp = await self.router.complete(req)
        # No local text to ground against here, which is exactly why L3 is a
        # last resort and its output is given lower confidence upstream.
        # The vision prompt returns {"values": ..., "legible": ...}; a field
        # the model itself marked illegible is dropped rather than trusted.
        parsed = resp.parsed or {}
        values = parsed.get("values", parsed)
        legible = parsed.get("legible", {})
        return ({k: v for k, v in values.items()
                 if v is not None and legible.get(k, True)}, resp.cost_usd)
