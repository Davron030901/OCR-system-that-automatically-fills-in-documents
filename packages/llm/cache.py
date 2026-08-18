"""Response cache for the LLM layer.

WHY CACHE AT ALL
----------------
Users retry. They upload a photo, the result looks off, they hit the button
again. Without a cache that second attempt pays full price for a byte-identical
request. Retries and re-runs of the evaluation harness are the bulk of repeated
traffic, and both are exactly cache-shaped.

WHY THIS FILE IS LONGER THAN A DICT
-----------------------------------
The cached value is the model's answer about somebody's passport. That makes
this cache a store of personal data, and it inherits every rule that applies to
the database:

  * encrypted at rest — Redis persists to disk and shows up in backups. A
    plaintext cache is a PII leak with a TTL.
  * strict TTL — cached PII must expire on its own. Seven days by default.
  * tenant isolation — the tenant is part of the key, so one tenant can never
    be served another's cached answer even on an identical document.
  * key derived from CONTENT, never from a user identifier — the key is a hash
    of provider, model, prompt version and normalised input, so it reveals
    nothing and changes the moment any of those change.

The prompt version is in the key on purpose. Editing a prompt must invalidate
every answer it produced; otherwise a prompt fix appears to do nothing because
the old answers keep being served.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 7 * 24 * 3600
NAMESPACE = "llmcache:v1"

_WHITESPACE = re.compile(r"\s+")


class CacheBackend(Protocol):
    def get(self, key: str) -> bytes | None: ...
    def set(self, key: str, value: bytes, ttl: int) -> None: ...
    def delete(self, key: str) -> None: ...


@dataclass
class _Entry:
    value: bytes
    expires_at: float


class MemoryBackend:
    """In-process backend. The default for tests, single-worker runs and CI.

    Expiry is checked on read rather than swept on a timer: a background sweeper
    would be one more thing to shut down cleanly, and an entry nobody reads
    costs nothing but memory that the process gives back on exit.
    """

    def __init__(self, max_entries: int = 2048) -> None:
        self._data: dict[str, _Entry] = {}
        self.max_entries = max_entries

    def get(self, key: str) -> bytes | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.time():
            del self._data[key]
            return None
        return entry.value

    def set(self, key: str, value: bytes, ttl: int) -> None:
        if len(self._data) >= self.max_entries:
            # Drop whatever expires soonest. Approximate, and adequate: this
            # backend exists for tests and single-process deployments.
            oldest = min(self._data, key=lambda k: self._data[k].expires_at)
            del self._data[oldest]
        self._data[key] = _Entry(value, time.time() + ttl)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def __len__(self) -> int:
        return len(self._data)


class RedisBackend:
    """Redis backend. TTL is enforced by Redis itself, not by application code."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def get(self, key: str) -> bytes | None:
        # The redis client is typed as Any (it is an optional import), so the
        # cast keeps the protocol's contract honest at this boundary.
        value = self.client.get(key)
        return value if value is None else bytes(value)

    def set(self, key: str, value: bytes, ttl: int) -> None:
        self.client.setex(key, ttl, value)

    def delete(self, key: str) -> None:
        self.client.delete(key)


def normalize_input(text: str) -> str:
    """Collapse whitespace so trivial OCR jitter still hits the same entry.

    Deliberately conservative: case and characters are preserved, because two
    documents differing only in case are two different documents.
    """
    return _WHITESPACE.sub(" ", text).strip()


def make_key(provider: str, model: str, prompt_version: str, payload: str,
             tenant: str = "default") -> str:
    """Content-addressed key. Contains no identifier, only a digest."""
    digest = hashlib.sha256(
        "\x00".join([provider, model, prompt_version, tenant,
                     normalize_input(payload)]).encode("utf-8")
    ).hexdigest()
    return f"{NAMESPACE}:{tenant}:{digest}"


class _NullCipher:
    """Used only when no key is configured, and only outside production."""

    def encrypt(self, data: bytes) -> bytes:
        return data

    def decrypt(self, data: bytes) -> bytes:
        return data


def _build_cipher(secret: str | None):
    """Fernet over the configured key, or refuse to run unencrypted in prod."""
    secret = secret or os.getenv("LLM_CACHE_KEY") or os.getenv("ENCRYPTION_KEY")
    if not secret:
        if os.getenv("ENVIRONMENT", "development").lower() == "production":
            raise RuntimeError(
                "LLM cache requires LLM_CACHE_KEY or ENCRYPTION_KEY in "
                "production: cached model output contains personal data"
            )
        log.warning("LLM cache running unencrypted (no key configured)")
        return _NullCipher()
    from base64 import urlsafe_b64encode

    from cryptography.fernet import Fernet

    # Fernet wants 32 url-safe base64 bytes. Hashing accepts a key of any
    # shape without weakening a key that is already 32 random bytes.
    material = urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(material)


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    stores: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class ResponseCache:
    def __init__(self, backend: CacheBackend | None = None,
                 ttl: int | None = None, secret: str | None = None,
                 enabled: bool | None = None) -> None:
        # `backend or MemoryBackend()` would be wrong: MemoryBackend defines
        # __len__, so an empty one is falsy and would be silently swapped for a
        # fresh instance. The caller's backend must be used exactly as given.
        self.backend = backend if backend is not None else MemoryBackend()
        self.ttl = ttl if ttl is not None else int(
            os.getenv("LLM_CACHE_TTL_SECONDS", DEFAULT_TTL_SECONDS))
        self.enabled = (enabled if enabled is not None
                        else os.getenv("LLM_CACHE_ENABLED", "true").lower() == "true")
        self._cipher = _build_cipher(secret)
        self.stats = CacheStats()

    def get(self, provider: str, model: str, prompt_version: str, payload: str,
            tenant: str = "default") -> dict[str, Any] | None:
        if not self.enabled:
            return None
        key = make_key(provider, model, prompt_version, payload, tenant)
        raw = self.backend.get(key)
        if raw is None:
            self.stats.misses += 1
            return None
        try:
            decoded: dict[str, Any] = json.loads(
                self._cipher.decrypt(raw).decode("utf-8"))
        except Exception:                                        # noqa: BLE001
            # A corrupt or undecryptable entry (key rotation, truncated write)
            # is a miss, never an error. Drop it and let the caller re-ask.
            log.warning("discarding an unreadable cache entry")
            self.backend.delete(key)
            self.stats.misses += 1
            return None
        self.stats.hits += 1
        return decoded

    def set(self, provider: str, model: str, prompt_version: str, payload: str,
            value: dict[str, Any], tenant: str = "default") -> None:
        if not self.enabled:
            return
        key = make_key(provider, model, prompt_version, payload, tenant)
        blob = self._cipher.encrypt(json.dumps(value, ensure_ascii=False).encode())
        self.backend.set(key, blob, self.ttl)
        self.stats.stores += 1


def build_cache() -> ResponseCache:
    """Redis when REDIS_URL is set, memory otherwise. Never fails hard."""
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return ResponseCache()
    try:
        import redis

        client = redis.Redis.from_url(url)
        client.ping()
        return ResponseCache(backend=RedisBackend(client))
    except Exception as exc:                                     # noqa: BLE001
        log.warning("redis cache unavailable (%s); using in-process cache",
                    type(exc).__name__)
        return ResponseCache()
