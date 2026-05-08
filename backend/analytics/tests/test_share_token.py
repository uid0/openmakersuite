"""Unit tests for the signed-URL share token utility."""

from __future__ import annotations



import pytest
from freezegun import freeze_time

from analytics.services.share_token import (
    MAX_TTL_DAYS,
    InvalidShareToken,
    mint_share_token,
    verify_share_token,
)


class TestMintAndVerify:
    def test_round_trip(self):
        token = mint_share_token(ttl_days=7)
        assert verify_share_token(token) == 7

    def test_default_ttl(self):
        token = mint_share_token()
        assert verify_share_token(token) == 30

    def test_zero_ttl_rejected(self):
        with pytest.raises(ValueError):
            mint_share_token(ttl_days=0)

    def test_negative_ttl_rejected(self):
        with pytest.raises(ValueError):
            mint_share_token(ttl_days=-5)

    def test_clamped_at_max(self):
        token = mint_share_token(ttl_days=MAX_TTL_DAYS + 100)
        # Stored TTL should be MAX_TTL_DAYS, not the requested value.
        assert verify_share_token(token) == MAX_TTL_DAYS


class TestVerifyRejectsBadTokens:
    def test_garbage_string(self):
        with pytest.raises(InvalidShareToken):
            verify_share_token("not-a-token")

    def test_tampered_signature(self):
        token = mint_share_token(ttl_days=7)
        # Flip a character in the signature segment.
        if token.endswith("a"):
            tampered = token[:-1] + "b"
        else:
            tampered = token[:-1] + "a"
        with pytest.raises(InvalidShareToken):
            verify_share_token(tampered)

    def test_empty_token(self):
        with pytest.raises(InvalidShareToken):
            verify_share_token("")


class TestExpiry:
    def test_expired_after_embedded_ttl(self):
        with freeze_time("2026-01-01 12:00:00"):
            token = mint_share_token(ttl_days=2)
        # 3 days later: signature still valid (under MAX_TTL_DAYS) but
        # embedded TTL has elapsed.
        with freeze_time("2026-01-04 13:00:00"):
            with pytest.raises(InvalidShareToken):
                verify_share_token(token)

    def test_still_valid_within_ttl(self):
        with freeze_time("2026-01-01 12:00:00"):
            token = mint_share_token(ttl_days=7)
        with freeze_time("2026-01-05 12:00:00"):
            assert verify_share_token(token) == 7

    def test_signature_expired_past_max(self):
        with freeze_time("2024-01-01"):
            token = mint_share_token(ttl_days=MAX_TTL_DAYS)
        # MAX_TTL_DAYS (365) + a buffer past the absolute cap.
        with freeze_time("2026-01-02"):
            with pytest.raises(InvalidShareToken):
                verify_share_token(token)


class TestSaltRotation:
    def test_changing_salt_invalidates_existing_tokens(self, settings):
        token = mint_share_token(ttl_days=7)
        assert verify_share_token(token) == 7
        settings.ANALYTICS_SHARE_SALT = "rotated-v2"
        with pytest.raises(InvalidShareToken):
            verify_share_token(token)
