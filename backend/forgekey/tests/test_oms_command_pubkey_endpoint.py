"""
Tests for ``GET /api/forgekey/oms-command-public-key.pem`` (oms-d2axqu).

The endpoint must return exactly the PEM produced by
:func:`forgekey.services.jwt_signing.get_jwt_public_key_pem` so the firmware
build can bake it into ``oms_command_pubkey.h``.
"""

from __future__ import annotations

from django.urls import reverse
import pytest

from forgekey.services.jwt_signing import get_jwt_public_key_pem


pytestmark = pytest.mark.django_db


def test_returns_matching_pem(api_client):
    url = reverse("forgekey:oms-command-public-key")
    resp = api_client.get(url)
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("application/x-pem-file")
    body = resp.content.decode("ascii")
    assert body.strip() == get_jwt_public_key_pem().strip()


def test_returns_404_when_signing_key_missing(api_client, settings):
    settings.FORGEKEY_JWT_SIGNING_KEY = ""
    url = reverse("forgekey:oms-command-public-key")
    resp = api_client.get(url)
    assert resp.status_code == 404


def test_requires_no_auth(api_client):
    """No provisioning token / no session — public key endpoints are by definition public."""
    url = reverse("forgekey:oms-command-public-key")
    resp = api_client.get(url)
    assert resp.status_code == 200
