"""Concrete providers.

Error mapping is the part worth reading. Turning an HTTP status into the right
exception class is what makes the key pool behave: a 400 mapped to a generic
error causes the router to try every key in turn and fail identically on each.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from packages.llm.base import (
    BadRequest,
    InvalidKey,
    LLMRequest,
    LLMResponse,
    ProviderCapabilities,
    ProviderUnavailable,
    QuotaExhausted,
    RateLimited,
)
from packages.llm.providers.config import estimate_cost

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return None


def _raise_for_status(resp: httpx.Response, provider: str) -> None:
    if resp.status_code < 400:
        return
    body = resp.text[:500]
    if resp.status_code in (401, 403):
        raise InvalidKey(f"{provider}: credential rejected ({resp.status_code})")
    if resp.status_code == 429:
        # Providers signal "out of quota for the period" and "too fast right
        # now" through the same status. They need different backoff.
        if re.search(r"quota|exhaust|exceeded", body, re.I):
            raise QuotaExhausted(f"{provider}: quota exhausted")
        raise RateLimited(f"{provider}: rate limited", _retry_after(resp))
    if resp.status_code == 400:
        raise BadRequest(f"{provider}: bad request - {body}")
    if resp.status_code >= 500:
        raise ProviderUnavailable(f"{provider}: upstream {resp.status_code}")
    raise ProviderUnavailable(f"{provider}: unexpected {resp.status_code}")


def _extract_json(text: str) -> dict[str, Any] | None:
    """Parse a JSON object out of a model response, fences and all."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(),
                     flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if 0 <= start < end:
            try:
                parsed = json.loads(cleaned[start:end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str, caps: ProviderCapabilities,
                 base_url: str = "https://api.openai.com/v1"):
        self._key = api_key
        self.model = model
        self.capabilities = caps
        self._base = base_url.rstrip("/")

    async def complete(self, req: LLMRequest) -> LLMResponse:
        content: list[dict[str, Any]] = [{"type": "text", "text": req.user_text}]
        for img in req.images:
            import base64
            b64 = base64.b64encode(img).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": content},
            ],
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        if req.json_schema and self.capabilities.supports_json_schema:
            # Enforce the schema at the API level. Asking for JSON in the
            # prompt is not enough; models drift under long inputs.
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "strict": True,
                                "schema": req.json_schema},
            }

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                f"{self._base}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json=payload,
            )
            _raise_for_status(r, self.name)
            data = r.json()

        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {})
        tin = usage.get("prompt_tokens", 0)
        tout = usage.get("completion_tokens", 0)
        return LLMResponse(
            content=text, parsed=_extract_json(text),
            input_tokens=tin, output_tokens=tout,
            cost_usd=estimate_cost(self.capabilities, tin, tout),
            provider=self.name, model=self.model,
            finish_reason=data["choices"][0].get("finish_reason", ""),
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                r = await c.get(f"{self._base}/models",
                                headers={"Authorization": f"Bearer {self._key}"})
                return r.status_code < 400
        except Exception:
            return False


class GeminiProvider:
    """Google Gemini via the Generative Language API."""

    def __init__(self, api_key: str, model: str, caps: ProviderCapabilities,
                 name: str = "gemini"):
        self._key = api_key
        self.model = model
        self.capabilities = caps
        self.name = name
        self._base = "https://generativelanguage.googleapis.com/v1beta"

    async def complete(self, req: LLMRequest) -> LLMResponse:
        parts: list[dict[str, Any]] = [{"text": req.user_text}]
        for img in req.images:
            import base64
            parts.append({"inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(img).decode(),
            }})

        gen_config: dict[str, Any] = {
            "temperature": req.temperature,
            "maxOutputTokens": req.max_tokens,
        }
        if req.json_schema and self.capabilities.supports_json_schema:
            gen_config["responseMimeType"] = "application/json"
            gen_config["responseSchema"] = req.json_schema

        payload = {
            "systemInstruction": {"parts": [{"text": req.system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": gen_config,
        }

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                f"{self._base}/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self._key},
                json=payload,
            )
            _raise_for_status(r, self.name)
            data = r.json()

        candidates = data.get("candidates") or []
        text = ""
        if candidates:
            for p in candidates[0].get("content", {}).get("parts", []):
                text += p.get("text", "")
        usage = data.get("usageMetadata", {})
        tin = usage.get("promptTokenCount", 0)
        tout = usage.get("candidatesTokenCount", 0)
        return LLMResponse(
            content=text, parsed=_extract_json(text),
            input_tokens=tin, output_tokens=tout,
            cost_usd=estimate_cost(self.capabilities, tin, tout),
            provider=self.name, model=self.model,
            finish_reason=(candidates[0].get("finishReason", "")
                           if candidates else ""),
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                r = await c.get(f"{self._base}/models",
                                headers={"x-goog-api-key": self._key})
                return r.status_code < 400
        except Exception:
            return False


class LocalProvider:
    """A locally hosted vision model behind an OpenAI-compatible endpoint.

    vLLM, Ollama and llama.cpp all expose this shape. Because the model runs on
    your own infrastructure, `allows_real_pii` is True and this is the route
    that satisfies a strict data-localisation requirement without redesigning
    anything above it.
    """

    name = "local"

    def __init__(self, base_url: str, model: str, caps: ProviderCapabilities):
        self._base = base_url.rstrip("/")
        self.model = model
        self.capabilities = caps

    async def complete(self, req: LLMRequest) -> LLMResponse:
        system = req.system
        if req.json_schema:
            # No server-side schema enforcement here, so state the contract
            # explicitly and validate the reply.
            system += ("\n\nRespond with a single JSON object matching this "
                       f"schema and nothing else:\n{json.dumps(req.json_schema)}")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": req.user_text},
            ],
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            r = await client.post(f"{self._base}/chat/completions", json=payload)
            _raise_for_status(r, self.name)
            data = r.json()

        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {})
        return LLMResponse(
            content=text, parsed=_extract_json(text),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cost_usd=0.0, provider=self.name, model=self.model,
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                return (await c.get(f"{self._base}/models")).status_code < 400
        except Exception:
            return False
