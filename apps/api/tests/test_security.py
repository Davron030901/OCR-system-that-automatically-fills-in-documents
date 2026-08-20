"""API security tests: redaction, encryption and upload validation.

These are the acceptance criteria that make the privacy claims in the docs
verifiable rather than aspirational.
"""
import io
import logging

import pytest
from app.security.crypto import (
    CryptoError,
    decrypt,
    decrypt_json,
    encrypt,
    encrypt_json,
    generate_key,
)
from app.security.redaction import (
    RedactionFilter,
    redact_obj,
    redact_text,
)
from app.security.validation import UploadRejected, sniff_mime, validate_upload


class TestRedaction:
    def test_pinfl_removed(self):
        assert "31503950012345" not in redact_text("JSHSHIR 31503950012345")

    def test_document_number_removed(self):
        assert "AA1234567" not in redact_text("Seriya AA1234567 berilgan")

    def test_mrz_removed(self):
        line = "P<UZBALIYEV<<SHOHRUH<<<<<<<<<<<<<<<<<<<<<<<<"
        assert "ALIYEV" not in redact_text(line)

    def test_email_and_phone_removed(self):
        out = redact_text("aliyev@example.com, 998901234567")
        assert "aliyev@example.com" not in out
        assert "998901234567" not in out

    def test_api_keys_removed(self):
        out = redact_text("key=sk-abc123def456ghi789 other=AIzaSyABCDEFGHIJKL")
        assert "sk-abc123def456ghi789" not in out
        assert "AIzaSyABCDEFGHIJKL" not in out

    def test_sensitive_keys_redacted_by_name(self):
        out = redact_obj({"pinfl": "31503950012345", "surname": "Aliyev",
                          "status": "ok"})
        assert out["pinfl"] == "<REDACTED>"
        assert out["surname"] == "<REDACTED>"
        assert out["status"] == "ok"          # non-sensitive survives

    def test_nested_structures_redacted(self):
        out = redact_obj({"person": {"name": {"surname": "Aliyev"},
                                     "notes": ["JSHSHIR 31503950012345"]}})
        assert out["person"]["name"]["surname"] == "<REDACTED>"
        assert "31503950012345" not in str(out)

    def test_logging_filter_scrubs_records(self, caplog):
        logger = logging.getLogger("test-redaction")
        logger.addFilter(RedactionFilter())
        with caplog.at_level(logging.INFO, logger="test-redaction"):
            logger.info("processing JSHSHIR 31503950012345 for AA1234567")
        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert "31503950012345" not in blob
        assert "AA1234567" not in blob

    def test_recursion_is_bounded(self):
        deep = current = {}
        for _ in range(30):
            current["next"] = {}
            current = current["next"]
        assert redact_obj(deep)          # must not blow the stack


class TestCrypto:
    def test_round_trip(self):
        key = generate_key()
        blob = encrypt(b"passport data", key)
        assert decrypt(blob, key) == b"passport data"

    def test_ciphertext_does_not_contain_plaintext(self):
        key = generate_key()
        assert b"31503950012345" not in encrypt(b"31503950012345", key)

    def test_wrong_key_fails_loudly(self):
        blob = encrypt(b"secret", generate_key())
        with pytest.raises(CryptoError):
            decrypt(blob, generate_key())

    def test_tampering_detected(self):
        """AES-GCM is authenticated: altered ciphertext must not decrypt."""
        key = generate_key()
        blob = bytearray(encrypt(b"AA1234567", key))
        blob[-1] ^= 0x01
        with pytest.raises(CryptoError):
            decrypt(bytes(blob), key)

    def test_aad_binds_ciphertext_to_its_job(self):
        """A blob copied to another job's row must not decrypt."""
        key = generate_key()
        blob = encrypt(b"data", key, aad=b"job-1")
        with pytest.raises(CryptoError):
            decrypt(blob, key, aad=b"job-2")

    def test_json_helpers(self):
        key = generate_key()
        obj = {"person": {"pinfl": "31503950012345"}}
        assert decrypt_json(encrypt_json(obj, key), key) == obj

    def test_missing_key_gives_actionable_error(self):
        with pytest.raises(CryptoError) as exc:
            encrypt(b"x", "")
        assert "ENCRYPTION_KEY" in str(exc.value)

    def test_nonce_is_unique_per_call(self):
        key = generate_key()
        assert encrypt(b"same", key) != encrypt(b"same", key)


class TestUploadValidation:
    JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
    PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200

    def test_sniffs_by_magic_bytes(self):
        assert sniff_mime(self.JPEG) == "image/jpeg"
        assert sniff_mime(self.PNG) == "image/png"
        assert sniff_mime(b"%PDF-1.7") == "application/pdf"

    def test_extension_lies_are_irrelevant(self):
        """A file named photo.jpg containing a Word document is not an image."""
        assert sniff_mime(b"PK\x03\x04rest") != "image/jpeg"

    def test_unknown_type_rejected(self):
        with pytest.raises(UploadRejected):
            validate_upload(b"\x00\x01\x02\x03random", 10_000_000)

    def test_oversize_rejected(self):
        with pytest.raises(UploadRejected) as exc:
            validate_upload(self.JPEG + b"\x00" * 5000, 1000)
        assert "katta" in str(exc.value)

    def test_empty_rejected(self):
        with pytest.raises(UploadRejected):
            validate_upload(b"", 1000)

    def test_real_png_accepted(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (100, 100), "white").save(buf, "PNG")
        mime, digest = validate_upload(buf.getvalue(), 10_000_000)
        assert mime == "image/png"
        assert len(digest) == 64


class TestDemoModeExposure:
    """The demo banner is driven by the server, so the endpoint must be honest.

    A frontend that renders "production" while the backend runs on free-tier
    keys is worse than having no banner: it actively tells someone it is safe
    to photograph their passport.

    The endpoint function is awaited directly rather than through TestClient:
    the app's lifespan waits for Postgres, and this behaviour has nothing to do
    with the database.
    """

    @staticmethod
    async def _config(monkeypatch, demo: str | None):
        from app.config import get_settings
        from app.routers.health import client_config
        if demo is None:
            monkeypatch.delenv("DEMO_MODE", raising=False)
        else:
            monkeypatch.setenv("DEMO_MODE", demo)
        get_settings.cache_clear()
        try:
            return await client_config()
        finally:
            get_settings.cache_clear()

    async def test_config_reports_production_when_demo_is_off(self, monkeypatch):
        """Explicitly "false" rather than "unset".

        Settings reads .env, and a developer's local .env legitimately has
        DEMO_MODE=true. Asserting on absence would make this test pass or fail
        depending on whose machine it runs on.
        """
        body = await self._config(monkeypatch, "false")
        assert body["demo_mode"] is False
        assert body["demo_warning"] is None

    async def test_config_returns_a_warning_in_demo_mode(self, monkeypatch):
        body = await self._config(monkeypatch, "true")
        assert body["demo_mode"] is True
        assert body["demo_warning"]
        # The text must name the concrete action, not the abstract mode.
        assert "pasport" in body["demo_warning"].lower()

    async def test_config_exposes_no_secrets(self, monkeypatch):
        body = await self._config(monkeypatch, "true")
        forbidden = {"encryption_key", "jwt_secret", "internal_token",
                     "database_url", "openai_api_key", "storage_secret_key"}
        assert not forbidden & set(body)
        blob = str(body).lower()
        assert "sk-" not in blob and "postgres" not in blob
