"""
Tests for :mod:`forgekey.services.ca_key_storage`.

Covers:
  * Encrypt/decrypt roundtrip with a valid KEK.
  * Missing KEK fails loud (no silent plaintext fallback).
  * Wrong-length / non-base64 KEK fails loud.
  * Ciphertext tagged with a different kid refuses to decrypt.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.fernet import Fernet

from forgekey.services import ca_key_storage


VALID_KEK = Fernet.generate_key().decode("ascii")
ALT_KEK = Fernet.generate_key().decode("ascii")


@pytest.fixture
def kek(settings):
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = VALID_KEK
    return VALID_KEK


def test_encrypt_decrypt_roundtrip(kek):
    pem = b"-----BEGIN PRIVATE KEY-----\nABC\n-----END PRIVATE KEY-----\n"
    ciphertext, kid = ca_key_storage.encrypt_ca_key(pem)
    assert ciphertext != pem
    assert kid.startswith("forgekey-ca-kek-")
    assert ca_key_storage.decrypt_ca_key(ciphertext, kid) == pem


def test_decrypt_without_kid_succeeds_against_active_kek(kek):
    ciphertext, _kid = ca_key_storage.encrypt_ca_key(b"hello")
    assert ca_key_storage.decrypt_ca_key(ciphertext, None) == b"hello"


def test_missing_kek_fails_loud(settings):
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = ""
    with pytest.raises(ca_key_storage.CaKeyStorageError):
        ca_key_storage.encrypt_ca_key(b"data")


def test_non_base64_kek_fails_loud(settings):
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = "not base64 ##"
    with pytest.raises(ca_key_storage.CaKeyStorageError):
        ca_key_storage.encrypt_ca_key(b"data")


def test_wrong_length_kek_fails_loud(settings):
    # 16 bytes (not 32) — Fernet requires exactly 32-byte material.
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = base64.urlsafe_b64encode(b"\x00" * 16).decode(
        "ascii"
    )
    with pytest.raises(ca_key_storage.CaKeyStorageError):
        ca_key_storage.encrypt_ca_key(b"data")


def test_decrypt_refuses_wrong_kid(kek, settings):
    ciphertext, kid = ca_key_storage.encrypt_ca_key(b"data")
    # Rotate to a different KEK — old kid no longer matches.
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = ALT_KEK
    with pytest.raises(ca_key_storage.CaKeyStorageError):
        ca_key_storage.decrypt_ca_key(ciphertext, kid)


def test_decrypt_after_kek_rotation_without_kid_check_fails(kek, settings):
    ciphertext, _kid = ca_key_storage.encrypt_ca_key(b"data")
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = ALT_KEK
    with pytest.raises(ca_key_storage.CaKeyStorageError):
        ca_key_storage.decrypt_ca_key(ciphertext, None)
