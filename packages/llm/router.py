"""Budget control, provider selection and dispatch.

The router is the only place that calls a provider, which makes it the only
place the PII gate has to be wired in. Nothing bypasses it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from packages.llm.base import (
    BadRequest,
    BudgetExceeded,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMTask,
    PIIPolicyViolation,
    ProviderUnavailable,
)
from packages.llm.keypool import KeyPool
from packages.llm.pii_gate import PIIGate


@dataclass
class BudgetGuard:
    """Hard spend ceiling.

    Warn at 80%, stop at 100%. Stopping is not a degraded mode: the pipeline
    falls back to its local stages and flags the result for review, which is
    strictly better than an unbounded bill.
    """

    daily_limit_usd: float = 5.0
    monthly_limit_usd: float = 100.0
    spent_today: float = 0.0
    spent_month: float = 0.0
    _day: str = field(default_factory=lambda: time.strftime("%Y-%m-%d"))
    _month: str = field(default_factory=lambda: time.strftime("%Y-%m"))

    def _roll(self) -> None:
        today, month = time.strftime("%Y-%m-%d"), time.strftime("%Y-%m")
        if today != self._day:
            self._day, self.spent_today = today, 0.0
        if month != self._month:
            self._month, self.spent_month = month, 0.0

    def remaining(self) -> float:
        self._roll()
        return min(self.daily_limit_usd - self.spent_today,
                   self.monthly_limit_usd - self.spent_month)

    def check(self) -> None:
        if self.remaining() <= 0:
            raise BudgetExceeded(
                f"LLM budget exhausted (daily {self.spent_today:.2f}/"
                f"{self.daily_limit_usd:.2f} USD). Falling back to local stages."
            )

    def record(self, cost_usd: float) -> None:
        self._roll()
        self.spent_today += cost_usd
        self.spent_month += cost_usd

    @property
    def near_limit(self) -> bool:
        self._roll()
        return self.spent_today >= 0.8 * self.daily_limit_usd


@dataclass
class UsageRecord:
    """Metrics only. Prompt and response CONTENT is never recorded: it is PII."""

    provider: str
    model: str
    task: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    cache_hit: bool = False
    prompt_version: str = ""


class LLMRouter:
    """Selects a provider, enforces policy and budget, and dispatches."""

    def __init__(self, providers: list[LLMProvider], pool: KeyPool,
                 budget: BudgetGuard | None = None,
                 order: list[str] | None = None):
        self.providers = {p.name: p for p in providers}
        self.pool = pool
        self.budget = budget or BudgetGuard()
        self.order = order or list(self.providers)
        self.usage: list[UsageRecord] = []

    def candidates(self, req: LLMRequest) -> list[LLMProvider]:
        """Providers eligible for this request, in preference order."""
        out = []
        for name in self.order:
            p = self.providers.get(name)
            if p is None:
                continue
            if req.contains_real_pii and not p.capabilities.allows_real_pii:
                continue
            if req.images and not p.capabilities.supports_vision:
                continue
            if req.task == LLMTask.VISION and not p.capabilities.supports_vision:
                continue
            if self.pool.breaker.is_open(p.name):
                continue
            out.append(p)
        return out

    async def complete(self, req: LLMRequest) -> LLMResponse:
        """Dispatch with failover. Raises only when nothing can serve it."""
        self.budget.check()

        eligible = self.candidates(req)
        if not eligible:
            raise ProviderUnavailable(
                "No provider is eligible for this request. If it carries real "
                "personal data, configure a paid tier or a local model."
            )

        last_error: Exception | None = None
        for provider in eligible:
            # Policy check happens per provider, before any network call.
            PIIGate.check(req, provider)

            key = self.pool.acquire(
                provider=provider.name,
                require_real_pii=req.contains_real_pii,
            )
            if key is None:
                continue

            started = time.time()
            try:
                resp = await provider.complete(req)
            except BadRequest as exc:
                # Malformed request: every other key returns the same thing.
                # Retrying is an infinite loop, so surface it immediately.
                key.record_failure(exc)
                raise
            except PIIPolicyViolation:
                raise
            except Exception as exc:                     # noqa: BLE001
                last_error = exc
                key.record_failure(exc)
                self.pool.breaker.record_failure(provider.name)
                continue

            resp.latency_ms = int((time.time() - started) * 1000)
            key.record_success(resp.input_tokens + resp.output_tokens,
                               resp.cost_usd)
            self.pool.breaker.record_success(provider.name)
            self.budget.record(resp.cost_usd)
            self.usage.append(UsageRecord(
                provider=resp.provider, model=resp.model, task=str(req.task),
                input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
                cost_usd=resp.cost_usd, latency_ms=resp.latency_ms,
                cache_hit=resp.cache_hit, prompt_version=req.prompt_version,
            ))
            return resp

        raise ProviderUnavailable(
            f"All eligible providers failed or are rate limited. "
            f"Last error: {type(last_error).__name__ if last_error else 'no keys'}"
        )

    def total_cost(self) -> float:
        return sum(u.cost_usd for u in self.usage)
