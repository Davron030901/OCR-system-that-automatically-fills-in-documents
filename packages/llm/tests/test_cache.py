"""Response cache tests.

Two things are being protected. The obvious one is that a repeat request does
not pay twice. The important one is that a cache holding model output about a
passport behaves like every other store of personal data in this system:
encrypted, expiring, and isolated per tenant.
"""

from __future__ import annotations

import json

import pytest

from packages.llm.cache import (
    MemoryBackend,
    ResponseCache,
    make_key,
    normalize_input,
)

ARGS = ("openai", "gpt-4o-mini", "id_visual_zone.v1")
PAYLOAD = "Familiyasi\nTOSHMATOV\nSeriya va raqami AC1928374"
VALUE = {"person.name.surname_latin": "TOSHMATOV",
         "documents.0.doc_number": "AC1928374"}

SECRET = "test-only-cache-key"


def cache(**kwargs) -> ResponseCache:
    kwargs.setdefault("secret", SECRET)
    kwargs.setdefault("backend", MemoryBackend())
    return ResponseCache(**kwargs)


def test_miss_then_hit() -> None:
    c = cache()
    assert c.get(*ARGS, PAYLOAD) is None
    c.set(*ARGS, PAYLOAD, VALUE)
    assert c.get(*ARGS, PAYLOAD) == VALUE
    assert c.stats.hits == 1 and c.stats.misses == 1
    assert c.stats.hit_rate == pytest.approx(0.5)


def test_whitespace_jitter_still_hits() -> None:
    c = cache()
    c.set(*ARGS, PAYLOAD, VALUE)
    assert c.get(*ARGS, PAYLOAD.replace("\n", "  \n ")) == VALUE


def test_case_change_is_a_different_document() -> None:
    """Normalisation is deliberately conservative: case is meaning here."""
    c = cache()
    c.set(*ARGS, PAYLOAD, VALUE)
    assert c.get(*ARGS, PAYLOAD.lower()) is None


def test_prompt_version_change_invalidates() -> None:
    """A prompt edit must not keep serving answers the old prompt produced."""
    c = cache()
    c.set("openai", "gpt-4o-mini", "id_visual_zone.v1", PAYLOAD, VALUE)
    assert c.get("openai", "gpt-4o-mini", "id_visual_zone.v2", PAYLOAD) is None


def test_model_change_invalidates() -> None:
    c = cache()
    c.set(*ARGS, PAYLOAD, VALUE)
    assert c.get("openai", "some-other-model", ARGS[2], PAYLOAD) is None


def test_tenants_are_isolated() -> None:
    """The same document uploaded by two tenants must not cross over."""
    c = cache()
    c.set(*ARGS, PAYLOAD, VALUE, tenant="acme")
    assert c.get(*ARGS, PAYLOAD, tenant="acme") == VALUE
    assert c.get(*ARGS, PAYLOAD, tenant="globex") is None


def test_stored_bytes_do_not_contain_the_plaintext() -> None:
    """The point of encrypting: a Redis dump or backup must not leak PII."""
    backend = MemoryBackend()
    c = cache(backend=backend)
    c.set(*ARGS, PAYLOAD, VALUE)
    blob = next(iter(backend._data.values())).value
    assert b"TOSHMATOV" not in blob
    assert b"AC1928374" not in blob
    with pytest.raises(ValueError):
        # Not merely unreadable as a whole: it is not JSON at all.
        json.loads(blob.decode("utf-8", errors="ignore"))


def test_key_contains_no_payload_or_tenant_secret() -> None:
    key = make_key(*ARGS, PAYLOAD, tenant="acme")
    assert "TOSHMATOV" not in key and "AC1928374" not in key
    assert key.startswith("llmcache:v1:acme:")
    assert len(key.rsplit(":", 1)[1]) == 64          # sha256 hex


def test_entries_expire() -> None:
    c = cache(ttl=0)
    c.set(*ARGS, PAYLOAD, VALUE)
    assert c.get(*ARGS, PAYLOAD) is None


def test_corrupt_entry_is_a_miss_not_an_error() -> None:
    """Key rotation or a truncated write must degrade to a cache miss."""
    backend = MemoryBackend()
    c = cache(backend=backend)
    c.set(*ARGS, PAYLOAD, VALUE)
    key = make_key(*ARGS, PAYLOAD)
    backend.set(key, b"not-a-valid-token", 60)
    assert c.get(*ARGS, PAYLOAD) is None
    assert backend.get(key) is None                  # and it was evicted


def test_disabled_cache_stores_nothing() -> None:
    c = cache(enabled=False)
    c.set(*ARGS, PAYLOAD, VALUE)
    assert c.get(*ARGS, PAYLOAD) is None


def test_production_refuses_to_run_without_a_key(monkeypatch) -> None:
    """Silently caching PII in plaintext in production is not an option."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("LLM_CACHE_KEY", raising=False)
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="personal data"):
        ResponseCache(backend=MemoryBackend())


def test_memory_backend_bounds_its_size() -> None:
    backend = MemoryBackend(max_entries=4)
    for i in range(20):
        backend.set(f"k{i}", b"v", 60 + i)
    assert len(backend) <= 4


def test_normalize_input_collapses_whitespace() -> None:
    assert normalize_input("  a \n\t b  ") == "a b"
