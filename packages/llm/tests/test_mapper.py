"""Tests for the cascade mapper's key assembly and grounding integration."""
import os
from unittest.mock import patch

import pytest

from packages.llm.base import LLMResponse, PIIPolicyViolation
from packages.llm.keypool import KeyPool
from packages.llm.router import BudgetGuard, LLMRouter
from packages.ml.llm_mapper import CascadeMapper, build_pool


class TestPoolAssembly:
    def test_free_gemini_keys_are_never_pii_cleared(self):
        """Ten free keys stay ten free keys: they cannot be promoted."""
        with patch.dict(os.environ, {"GEMINI_FREE_KEYS": ",".join(
                f"AIzaFAKE{i:03d}" for i in range(10))}, clear=True):
            pool = build_pool()
        assert len(pool.keys) == 10
        assert all(not k.allows_real_pii for k in pool.keys)
        assert pool.available(require_real_pii=True) == []

    def test_free_keys_usable_for_synthetic_work(self):
        with patch.dict(os.environ, {"GEMINI_FREE_KEYS": "AIzaFAKE1,AIzaFAKE2"},
                        clear=True):
            pool = build_pool()
        assert len(pool.available(require_real_pii=False)) == 2

    def test_local_provider_is_pii_cleared(self):
        """A locally hosted model is the migration path for data localisation."""
        with patch.dict(os.environ, {"LOCAL_VLM_URL": "http://localhost:8000/v1"},
                        clear=True):
            pool = build_pool()
        assert pool.keys[0].allows_real_pii is True


class FakeProvider:
    def __init__(self, name, caps, payload):
        self.name, self.capabilities, self.model = name, caps, "fake"
        self.payload = payload

    async def complete(self, req):
        return LLMResponse(content="", parsed=self.payload, input_tokens=100,
                           output_tokens=20, cost_usd=0.001, provider=self.name)

    async def health_check(self):
        return True


class TestMapperGrounding:
    OCR = ("ALIYEV SHOHRUH\nTug'ilgan sanasi: 15.03.1995\n"
           "JSHSHIR 31503950012345\nToshkent shahri")

    def _mapper(self, payload):
        from packages.llm.base import ProviderCapabilities
        from packages.llm.keypool import ManagedKey
        caps = ProviderCapabilities(allows_real_pii=True, supports_json_schema=True)
        pool = KeyPool([ManagedKey("k", "fake", "s", True)])
        router = LLMRouter([FakeProvider("fake", caps, payload)], pool,
                           BudgetGuard(), order=["fake"])
        return CascadeMapper(router)

    @pytest.mark.asyncio
    async def test_grounded_values_kept(self):
        m = self._mapper({"person.birth_place": "Toshkent shahri"})
        out, cost = await m.map_text(self.OCR, "id_front", ["person.birth_place"])
        assert out["person.birth_place"] == "Toshkent shahri"
        assert cost > 0

    @pytest.mark.asyncio
    async def test_hallucinated_value_dropped(self):
        """The model invents a plausible address; grounding must discard it."""
        m = self._mapper({"person.address": "Samarqand viloyati, Urgut tumani"})
        out, _ = await m.map_text(self.OCR, "id_front", ["person.address"])
        assert "person.address" not in out

    @pytest.mark.asyncio
    async def test_altered_pinfl_dropped(self):
        m = self._mapper({"person.pinfl": "31503950012399"})
        out, _ = await m.map_text(self.OCR, "id_front", ["person.pinfl"])
        assert "person.pinfl" not in out

    @pytest.mark.asyncio
    async def test_requests_are_marked_as_real_pii(self):
        """Documents are real data; the flag must fail closed, not open."""
        from packages.llm.base import ProviderCapabilities
        from packages.llm.keypool import ManagedKey
        free = ProviderCapabilities(allows_real_pii=False,
                                    data_used_for_training=True)
        pool = KeyPool([ManagedKey("f", "free", "s", False)])
        router = LLMRouter([FakeProvider("free", free, {})], pool,
                           BudgetGuard(), order=["free"])
        m = CascadeMapper(router)
        with pytest.raises(Exception) as exc:
            await m.map_text(self.OCR, "id_front", ["person.address"])
        assert "provider" in str(exc.value).lower() or isinstance(
            exc.value, PIIPolicyViolation)
