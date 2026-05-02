"""
Tests for the public JWKS endpoint that EMQX consumes (oms-6h1).
"""

import base64

from django.conf import settings
from django.urls import reverse

import pytest

from forgekey.services.jwt_signing import get_jwt_public_key_pem


@pytest.mark.unit
@pytest.mark.django_db
class TestJWKSEndpoint:
    URL_NAME = "forgekey:device-jwks"

    def test_returns_jwk_set_with_active_key(self, api_client):
        url = reverse(self.URL_NAME)
        resp = api_client.get(url)
        assert resp.status_code == 200
        body = resp.json()
        assert "keys" in body
        assert len(body["keys"]) == 1
        key = body["keys"][0]
        assert key["kty"] == "EC"
        assert key["crv"] == "P-256"
        assert key["alg"] == "ES256"
        assert key["use"] == "sig"
        assert key["kid"] == settings.FORGEKEY_JWT_KEY_ID
        # x and y are base64url-encoded 32-byte coordinates (no padding).
        for coord in (key["x"], key["y"]):
            decoded = base64.urlsafe_b64decode(coord + "=" * (-len(coord) % 4))
            assert len(decoded) == 32

    def test_advertises_correct_public_key(self, api_client):
        from cryptography.hazmat.primitives import serialization

        url = reverse(self.URL_NAME)
        body = api_client.get(url).json()
        key = body["keys"][0]
        x = int.from_bytes(base64.urlsafe_b64decode(key["x"] + "=" * (-len(key["x"]) % 4)), "big")
        y = int.from_bytes(base64.urlsafe_b64decode(key["y"] + "=" * (-len(key["y"]) % 4)), "big")
        loaded = serialization.load_pem_public_key(get_jwt_public_key_pem().encode())
        numbers = loaded.public_numbers()
        assert numbers.x == x
        assert numbers.y == y

    def test_sets_cache_and_jwks_content_headers(self, api_client):
        resp = api_client.get(reverse(self.URL_NAME))
        assert resp["Cache-Control"] == "public, max-age=3600"
        assert resp["Content-Type"] == "application/jwk-set+json"

    def test_no_authentication_required(self, api_client):
        # Endpoint must be reachable without any credentials — EMQX fetches
        # it unauthenticated.
        resp = api_client.get(reverse(self.URL_NAME))
        assert resp.status_code == 200

    def test_returns_404_when_signing_key_unconfigured(self, api_client, settings):
        settings.FORGEKEY_JWT_SIGNING_KEY = ""
        resp = api_client.get(reverse(self.URL_NAME))
        assert resp.status_code == 404
