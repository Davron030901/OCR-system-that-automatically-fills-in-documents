"""Tests for the LLM layer's safety properties.

These are the tests that matter most in this package. A regression in the PII
gate or the grounding guard is not a bug that degrades quality -- it is a bug
that leaks passport data to a training-enabled endpoint, or writes an invented
identifier into a legal document.
"""

import asyncio
import time

import pytest

from packages.llm.base import (
    BadRequest,
    BudgetExceeded,
    InvalidKey,
    LLMRequest,
    LLMResponse,
    PIIPolicyViolation,
    ProviderCapabilities,
    ProviderUnavailable,
    QuotaExhausted,
    RateLimited,
)
from packages.llm.grounding import verify, verify_value
from packages.llm.keypool import KeyPool, KeyState, ManagedKey, redact_secrets
from packages.llm.pii_gate import PIIGate, looks_like_real_pii
from packages.llm.router import BudgetGuard, LLMRouter

FREE_CAPS = ProviderCapabilities(
    supports_vision=True, allows_real_pii=False, data_used_for_training=True)
PAID_CAPS = ProviderCapabilities(
    supports_vision=True, allows_real_pii=True, data_used_for_training=False,
    cost_per_1m_input=0.1, cost_per_1m_output=0.4)


class FakeProvider:
    def __init__(self, name, caps, fail_with=None, model="fake-1"):
        self.name, self.capabilities, self.model = name, caps, model
        self.fail_with = fail_with
        self.calls = 0

    async def complete(self, req):
        self.calls += 1
        if self.fail_with:
            raise self.fail_with
        return LLMResponse(content='{"ok": true}', parsed={"ok": True},
                           input_tokens=100, output_tokens=20, cost_usd=0.001,
                           provider=self.name, model=self.model)

    async def health_check(self):
        return True


def make_req(**kw):
    base = dict(system="s", user_text="u", contains_real_pii=True)
    base.update(kw)
    return LLMRequest(**base)


class TestPIIGate:
    def test_real_pii_to_free_tier_always_raises(self):
        p = FakeProvider("gemini-free", FREE_CAPS)
        with pytest.raises(PIIPolicyViolation) as exc:
            PIIGate.check(make_req(contains_real_pii=True), p)
        assert "training" in str(exc.value).lower()

    def test_real_pii_to_paid_tier_allowed(self):
        PIIGate.check(make_req(contains_real_pii=True),
                      FakeProvider("openai", PAID_CAPS))

    def test_synthetic_to_free_tier_allowed(self):
        PIIGate.check(make_req(contains_real_pii=False, user_text="ALIYEV test"),
                      FakeProvider("gemini-free", FREE_CAPS))

    def test_images_never_go_to_free_tier(self):
        """Document images are personal data whatever the flag claims."""
        p = FakeProvider("gemini-free", FREE_CAPS)
        with pytest.raises(PIIPolicyViolation):
            PIIGate.check(make_req(contains_real_pii=False, images=[b"jpegdata"]), p)

    def test_mislabelled_pinfl_is_caught(self):
        """The caller says synthetic, but the payload says otherwise."""
        p = FakeProvider("gemini-free", FREE_CAPS)
        req = make_req(contains_real_pii=False,
                       user_text="JSHSHIR: 31503950012345")
        with pytest.raises(PIIPolicyViolation) as exc:
            PIIGate.check(req, p)
        assert "marked synthetic" in str(exc.value)

    def test_mislabelled_document_number_is_caught(self):
        p = FakeProvider("gemini-free", FREE_CAPS)
        req = make_req(contains_real_pii=False, user_text="Passport AB 1234567")
        with pytest.raises(PIIPolicyViolation):
            PIIGate.check(req, p)

    def test_icao_specimens_do_not_trip_the_heuristic(self):
        """Public specimen data must remain usable with free keys."""
        assert looks_like_real_pii(
            "L898902C36UTO7408122F1204159ZE184226B<<<<<10") == []

    def test_default_fails_closed(self):
        """Forgetting to set the flag must not leak to a free tier."""
        req = LLMRequest(system="s", user_text="u")
        assert req.contains_real_pii is True
        with pytest.raises(PIIPolicyViolation):
            PIIGate.check(req, FakeProvider("gemini-free", FREE_CAPS))


class TestKeyPool:
    def _pool(self):
        return KeyPool([
            ManagedKey("free-1", "gemini", "AIzaSyFAKEKEY0001", False),
            ManagedKey("free-2", "gemini", "AIzaSyFAKEKEY0002", False),
            ManagedKey("paid-1", "openai", "sk-FAKEKEY0003", True),
        ])

    def test_requires_real_pii_filters_free_keys(self):
        pool = self._pool()
        got = pool.available(require_real_pii=True)
        assert [k.key_id for k in got] == ["paid-1"]

    def test_rate_limit_uses_retry_after(self):
        pool = self._pool()
        k = pool.keys[0]
        k.record_failure(RateLimited("slow down", retry_after=45), now=1000.0)
        assert k.state == KeyState.RATE_LIMITED
        assert not k.is_available(now=1010.0)
        assert k.is_available(now=1050.0)

    def test_quota_exhausted_waits_longer(self):
        pool = self._pool()
        k = pool.keys[0]
        k.record_failure(QuotaExhausted("daily quota"), now=0.0)
        assert k.state == KeyState.QUOTA_EXHAUSTED
        assert not k.is_available(now=1800.0)
        assert k.is_available(now=3700.0)

    def test_invalid_key_removed_permanently(self):
        pool = self._pool()
        k = pool.keys[0]
        k.record_failure(InvalidKey("revoked"))
        assert k.state == KeyState.INVALID
        assert not k.is_available(now=time.time() + 10**9)

    def test_bad_request_does_not_penalise_key(self):
        """A 400 is the request's fault; blaming the key causes retry storms."""
        pool = self._pool()
        k = pool.keys[0]
        before = k.state
        k.record_failure(BadRequest("malformed schema"))
        assert k.state == before
        assert k.consecutive_failures == 0

    def test_least_recently_used_selection(self):
        pool = self._pool()
        pool.keys[0].last_used_at = 100
        pool.keys[1].last_used_at = 50
        assert pool.acquire(provider="gemini").key_id == "free-2"

    def test_secrets_never_in_repr(self):
        k = ManagedKey("free-1", "gemini", "AIzaSySUPERSECRET", False)
        assert "SUPERSECRET" not in repr(k)
        assert "SUPERSECRET" not in str(k)

    def test_report_contains_no_secrets(self):
        pool = self._pool()
        blob = str(pool.report())
        for k in pool.keys:
            assert k.secret not in blob

    def test_redaction_of_leaked_secret(self):
        pool = self._pool()
        leaked = f"error: key {pool.keys[2].secret} was rejected"
        assert pool.keys[2].secret not in redact_secrets(leaked, pool)

    def test_circuit_breaker_trips_and_resets(self):
        pool = self._pool()
        for _ in range(5):
            pool.breaker.record_failure("gemini", now=0.0)
        assert pool.breaker.is_open("gemini", now=1.0)
        assert not pool.breaker.is_open("gemini", now=200.0)


class TestGrounding:
    OCR = ("O'ZBEKISTON RESPUBLIKASI\nALIYEV SHOHRUH AKMAL O'G'LI\n"
           "Tug'ilgan sanasi: 15.03.1995\nJSHSHIR 31503950012345\n"
           "Seriya AA 1234567\nToshkent shahri")

    def test_present_value_accepted(self):
        ok, _ = verify_value("person.name.surname_latin", "Aliyev", self.OCR)
        assert ok

    def test_invented_name_rejected(self):
        ok, reason = verify_value("person.name.surname_latin", "Karimov", self.OCR)
        assert not ok and reason

    def test_pinfl_exact_match_required(self):
        ok, _ = verify_value("person.pinfl", "31503950012345", self.OCR)
        assert ok
        # One digit different is a different person, so fuzzy must not save it.
        ok2, _ = verify_value("person.pinfl", "31503950012346", self.OCR)
        assert not ok2

    def test_date_matched_across_formats(self):
        ok, _ = verify_value("person.birth_date", "1995-03-15", self.OCR)
        assert ok

    def test_invented_date_rejected(self):
        ok, _ = verify_value("person.birth_date", "1996-03-15", self.OCR)
        assert not ok

    def test_apostrophe_variants_do_not_cause_false_rejection(self):
        ok, _ = verify_value("person.birth_place", "Toshkent", self.OCR)
        assert ok

    def test_verify_filters_and_reports(self):
        out, report = verify({
            "person.name.surname_latin": "Aliyev",
            "person.pinfl": "31503950012345",
            "person.address": "Samarqand viloyati",     # not in the text
        }, self.OCR)

        assert out["person.name.surname_latin"] == "Aliyev"
        assert out["person.address"] is None
        assert "person.address" in report.rejected
        assert report.rejection_rate == pytest.approx(1 / 3)

    def test_none_values_pass_through(self):
        out, report = verify({"person.address": None}, self.OCR)
        assert out["person.address"] is None
        assert not report.rejected


class TestRouterAndBudget:
    def test_budget_stops_at_limit(self):
        b = BudgetGuard(daily_limit_usd=1.0)
        b.record(1.0)
        with pytest.raises(BudgetExceeded):
            b.check()

    def test_budget_warns_at_80_percent(self):
        b = BudgetGuard(daily_limit_usd=1.0)
        b.record(0.85)
        assert b.near_limit

    def test_router_refuses_real_pii_without_cleared_provider(self):
        router = LLMRouter([FakeProvider("gemini-free", FREE_CAPS)],
                           KeyPool([ManagedKey("f1", "gemini-free", "k", False)]))
        with pytest.raises(ProviderUnavailable):
            asyncio.run(router.complete(make_req(contains_real_pii=True)))

    def test_router_fails_over_to_second_provider(self):
        bad = FakeProvider("openai", PAID_CAPS, fail_with=ProviderUnavailable("503"))
        good = FakeProvider("local", PAID_CAPS)
        pool = KeyPool([ManagedKey("k1", "openai", "s1", True),
                        ManagedKey("k2", "local", "s2", True)])
        router = LLMRouter([bad, good], pool, order=["openai", "local"])
        resp = asyncio.run(router.complete(make_req()))
        assert resp.provider == "local"
        assert bad.calls == 1 and good.calls == 1

    def test_bad_request_is_not_retried_elsewhere(self):
        """A 400 on one key is a 400 on all of them; retrying loops forever."""
        bad = FakeProvider("openai", PAID_CAPS, fail_with=BadRequest("schema"))
        other = FakeProvider("local", PAID_CAPS)
        pool = KeyPool([ManagedKey("k1", "openai", "s1", True),
                        ManagedKey("k2", "local", "s2", True)])
        router = LLMRouter([bad, other], pool, order=["openai", "local"])
        with pytest.raises(BadRequest):
            asyncio.run(router.complete(make_req()))
        assert other.calls == 0

    def test_usage_records_carry_no_content(self):
        p = FakeProvider("openai", PAID_CAPS)
        pool = KeyPool([ManagedKey("k1", "openai", "s1", True)])
        router = LLMRouter([p], pool)
        asyncio.run(router.complete(make_req(user_text="JSHSHIR 31503950012345")))
        blob = str(router.usage[0].__dict__)
        assert "31503950012345" not in blob
        assert router.usage[0].cost_usd > 0
