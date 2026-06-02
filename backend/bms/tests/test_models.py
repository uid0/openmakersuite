"""Token encrypt/decrypt roundtrip + set_tokens semantics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bms.models import BmsConfig, _decrypt, _encrypt

pytestmark = pytest.mark.django_db


def test_encrypt_decrypt_roundtrip():
    plain = "a-resideo-access-token-abc123"
    cipher = _encrypt(plain)
    assert cipher != plain.encode("utf-8")
    assert _decrypt(cipher) == plain


def test_empty_token_roundtrip():
    assert _encrypt("") == b""
    assert _decrypt(b"") == ""


def test_set_tokens_persists_both_and_updates_expiry():
    cfg = BmsConfig.objects.create(name="x", adapter_type=BmsConfig.ADAPTER_MOCK)
    expires = datetime.now(timezone.utc) + timedelta(minutes=29)
    cfg.set_tokens(access_token="A1", refresh_token="R1", expires_at=expires)
    cfg.refresh_from_db()
    assert cfg.access_token() == "A1"
    assert cfg.refresh_token() == "R1"
    assert cfg.access_token_expires_at == expires


def test_set_tokens_preserves_refresh_when_none_passed():
    cfg = BmsConfig.objects.create(name="x", adapter_type=BmsConfig.ADAPTER_MOCK)
    expires = datetime.now(timezone.utc) + timedelta(minutes=29)
    cfg.set_tokens(access_token="A1", refresh_token="R1", expires_at=expires)

    # Subsequent refresh that returns only an access token must NOT wipe
    # the existing refresh — that would force a full re-auth dance.
    new_expires = datetime.now(timezone.utc) + timedelta(minutes=29)
    cfg.set_tokens(access_token="A2", refresh_token=None, expires_at=new_expires)
    cfg.refresh_from_db()
    assert cfg.access_token() == "A2"
    assert cfg.refresh_token() == "R1"
