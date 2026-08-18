"""API key pool with health tracking and failover.

WHAT THIS IS FOR, AND WHAT IT IS NOT FOR
----------------------------------------
This pool exists for RELIABILITY ACROSS PROVIDERS: when OpenAI returns 503,
work continues on another provider rather than failing the user's upload.

It is NOT a way to escape one provider's free-tier quota. Free-tier limits are
generally counted per PROJECT, not per key, so ten keys minted inside one
project share a single quota and rotating between them achieves exactly
nothing. Ten keys across ten accounts would be a terms-of-service problem and
a fragile foundation, since all of them can be disabled at once.

The economics make the point better than the policy does. At current
Flash-Lite-class pricing a document costs on the order of $0.001 to process,
so a thousand documents a month is a couple of dollars. Building and
maintaining quota-evasion machinery costs more engineering time than the
inference it saves.

Free keys do have one genuinely good use in this project: running the
synthetic dataset through prompt tuning and evaluation, where no real personal
data is involved. That is what the `allows_real_pii=False` keys are for.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from enum import StrEnum

from packages.llm.base import (
    BadRequest,
    InvalidKey,
    QuotaExhausted,
    RateLimited,
)


class KeyState(StrEnum):
    HEALTHY = "healthy"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    INVALID = "invalid"
    COOLING = "cooling"


@dataclass
class ManagedKey:
    """One API credential. The secret itself is held, never logged."""

    key_id: str                      # a label such as "gemini-free-03"
    provider: str
    secret: str = field(repr=False)  # excluded from repr so it cannot leak
    allows_real_pii: bool = False

    state: KeyState = KeyState.HEALTHY
    available_at: float = 0.0
    consecutive_failures: int = 0
    requests_today: int = 0
    tokens_today: int = 0
    cost_today_usd: float = 0.0
    last_used_at: float = 0.0

    def __str__(self) -> str:                      # never expose the secret
        return f"{self.key_id}({self.state})"

    def is_available(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        if self.state == KeyState.INVALID:
            return False
        if self.state in (KeyState.RATE_LIMITED, KeyState.QUOTA_EXHAUSTED,
                          KeyState.COOLING):
            if now >= self.available_at:
                self.state = KeyState.HEALTHY
                self.consecutive_failures = 0
                return True
            return False
        return True

    def record_success(self, tokens: int = 0, cost: float = 0.0) -> None:
        self.state = KeyState.HEALTHY
        self.consecutive_failures = 0
        self.requests_today += 1
        self.tokens_today += tokens
        self.cost_today_usd += cost
        self.last_used_at = time.time()

    def record_failure(self, exc: Exception, now: float | None = None) -> None:
        """Classify the failure. Getting this wrong causes infinite retry loops."""
        now = now if now is not None else time.time()
        self.consecutive_failures += 1

        if isinstance(exc, InvalidKey):
            self.state = KeyState.INVALID
            self.available_at = float("inf")
        elif isinstance(exc, QuotaExhausted):
            self.state = KeyState.QUOTA_EXHAUSTED
            self.available_at = now + 3600          # retry after an hour
        elif isinstance(exc, RateLimited):
            self.state = KeyState.RATE_LIMITED
            wait = exc.retry_after if exc.retry_after else 30.0
            self.available_at = now + wait
        elif isinstance(exc, BadRequest):
            # The request is wrong, not the key. Do not penalise the key and
            # do NOT retry elsewhere: every key returns the same error and the
            # caller spins forever.
            self.consecutive_failures -= 1
        else:
            backoff = min(2 ** self.consecutive_failures, 300)
            jitter = random.uniform(0, backoff * 0.1)
            self.state = KeyState.COOLING
            self.available_at = now + backoff + jitter


class CircuitBreaker:
    """Trips a whole provider after repeated failures."""

    def __init__(self, threshold: int = 5, reset_after: float = 120.0):
        self.threshold = threshold
        self.reset_after = reset_after
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def is_open(self, provider: str, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        until = self._open_until.get(provider, 0.0)
        if now < until:
            return True
        if until:
            self._open_until.pop(provider, None)
            self._failures[provider] = 0
        return False

    def record_failure(self, provider: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self._failures[provider] = self._failures.get(provider, 0) + 1
        if self._failures[provider] >= self.threshold:
            self._open_until[provider] = now + self.reset_after

    def record_success(self, provider: str) -> None:
        self._failures[provider] = 0
        self._open_until.pop(provider, None)


class KeyPool:
    """Selects keys, tracks their health, and refuses to leak secrets."""

    def __init__(self, keys: list[ManagedKey] | None = None):
        self.keys: list[ManagedKey] = keys or []
        self.breaker = CircuitBreaker()

    def add(self, key: ManagedKey) -> None:
        self.keys.append(key)

    def available(self, provider: str | None = None,
                  require_real_pii: bool = False,
                  now: float | None = None) -> list[ManagedKey]:
        out = []
        for k in self.keys:
            if provider and k.provider != provider:
                continue
            if require_real_pii and not k.allows_real_pii:
                continue
            if self.breaker.is_open(k.provider, now):
                continue
            if k.is_available(now):
                out.append(k)
        return out

    def acquire(self, provider: str | None = None,
                require_real_pii: bool = False,
                now: float | None = None) -> ManagedKey | None:
        """Least-recently-used healthy key, so load spreads evenly."""
        candidates = self.available(provider, require_real_pii, now)
        if not candidates:
            return None
        return min(candidates, key=lambda k: k.last_used_at)

    def report(self) -> dict[str, dict]:
        """Operational snapshot. Contains no secrets, safe to log."""
        return {
            k.key_id: {
                "provider": k.provider,
                "state": str(k.state),
                "requests_today": k.requests_today,
                "cost_today_usd": round(k.cost_today_usd, 4),
                "allows_real_pii": k.allows_real_pii,
            }
            for k in self.keys
        }


def redact_secrets(text: str, pool: KeyPool) -> str:
    """Strip any key material that made it into a log line or exception."""
    out = text
    for k in pool.keys:
        if k.secret and len(k.secret) > 6 and k.secret in out:
            out = out.replace(k.secret, f"<redacted:{k.key_id}>")
    return out
