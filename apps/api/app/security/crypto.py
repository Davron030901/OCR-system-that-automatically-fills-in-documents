"""Application-level encryption for extracted personal data.

Extraction results are stored encrypted rather than as plain JSONB columns.
Database backups, replicas and support queries all touch that table, and none
of them need to see a passport number.

AES-GCM gives authenticated encryption: a tampered ciphertext fails to decrypt
rather than silently returning altered data.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
KEY_VERSION = b"\x01"   # prefix byte enables key rotation without a migration


class CryptoError(Exception):
    pass


def generate_key() -> str:
    """Generate a new key. Store it in a secret manager, never in the repo."""
    return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode()


def _load_key(key_b64: str) -> bytes:
    if not key_b64:
        raise CryptoError(
            "ENCRYPTION_KEY is not set. Generate one with "
            "`python -c 'from app.security.crypto import generate_key; "
            "print(generate_key())'` and store it in your secret manager."
        )
    try:
        key = base64.urlsafe_b64decode(key_b64)
    except Exception as exc:
        raise CryptoError("ENCRYPTION_KEY is not valid base64") from exc
    if len(key) != 32:
        raise CryptoError("ENCRYPTION_KEY must decode to 32 bytes")
    return key


def encrypt(plaintext: bytes, key_b64: str, aad: bytes = b"") -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    ct = AESGCM(_load_key(key_b64)).encrypt(nonce, plaintext, aad)
    return KEY_VERSION + nonce + ct


def decrypt(blob: bytes, key_b64: str, aad: bytes = b"") -> bytes:
    if not blob or blob[:1] != KEY_VERSION:
        raise CryptoError("unknown ciphertext version")
    nonce, ct = blob[1:1 + NONCE_BYTES], blob[1 + NONCE_BYTES:]
    try:
        return AESGCM(_load_key(key_b64)).decrypt(nonce, ct, aad)
    except Exception as exc:
        raise CryptoError("decryption failed (wrong key or tampered data)") from exc


def encrypt_json(obj: Any, key_b64: str, aad: bytes = b"") -> bytes:
    return encrypt(json.dumps(obj, default=str).encode(), key_b64, aad)


def decrypt_json(blob: bytes, key_b64: str, aad: bytes = b"") -> Any:
    return json.loads(decrypt(blob, key_b64, aad).decode())
