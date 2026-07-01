"""Tests for `make_command_jwt` (gh ForgeKey expansion, Phase 1).

The function signs ECDSA-P256 JWTs that ride on `forgekey/<mac>/cmd`
MQTT messages so locker / door / electronic-device firmware can verify
the command before acting on it. The keys are the same ones EMQX uses
to verify device-auth JWTs, so firmware only carries one trust root.
"""

from __future__ import annotations

import base64
import json
import time

from django.test import override_settings

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    encode_dss_signature,
)

from forgekey.services.jwt_signing import (
    JwtSigningError,
    generate_jwt_signing_keypair,
    make_command_jwt,
)

pytestmark = pytest.mark.unit


def _b64url_decode(seg: str) -> bytes:
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + pad)


def _verify_es256(token: str, public_pem: str) -> dict:
    """Helper: verify an ES256 JWT against `public_pem`. Returns the
    parsed payload on success, or raises ValueError on signature
    failure.
    """
    header_b64, payload_b64, signature_b64 = token.split(".")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = _b64url_decode(signature_b64)
    if len(signature) != 64:
        raise ValueError("ES256 signature must be 64 bytes (r || s)")
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    der = encode_dss_signature(r, s)
    pub = serialization.load_pem_public_key(public_pem.encode("ascii"))
    pub.verify(der, signing_input, ec.ECDSA(hashes.SHA256()))  # raises on fail
    return json.loads(_b64url_decode(payload_b64))


@pytest.fixture
def keypair():
    return generate_jwt_signing_keypair()


def _settings_with(private_pem: str):
    return override_settings(
        FORGEKEY_JWT_SIGNING_KEY=private_pem,
        FORGEKEY_JWT_KEY_ID="test-kid",
    )


def test_command_jwt_roundtrips_and_payload_is_correct(keypair):
    private_pem, public_pem = keypair
    with _settings_with(private_pem):
        before = int(time.time())
        token = make_command_jwt(mac="AA:BB:CC:11:22:33", cmd="unlock", exp_seconds=60)
        after = int(time.time())

    payload = _verify_es256(token, public_pem)
    assert payload["mac"] == "AA:BB:CC:11:22:33"
    assert payload["cmd"] == "unlock"
    assert before + 60 <= payload["exp"] <= after + 60


def test_command_jwt_header_carries_alg_typ_kid(keypair):
    private_pem, _ = keypair
    with _settings_with(private_pem):
        token = make_command_jwt(mac="AA:BB:CC:11:22:33", cmd="unlock")
    header_b64 = token.split(".")[0]
    header = json.loads(_b64url_decode(header_b64))
    assert header["alg"] == "ES256"
    assert header["typ"] == "JWT"
    assert header["kid"] == "test-kid"


def test_command_jwt_uses_short_default_ttl(keypair):
    private_pem, public_pem = keypair
    with _settings_with(private_pem):
        token = make_command_jwt(mac="AA:BB:CC:11:22:33", cmd="unlock")
    payload = _verify_es256(token, public_pem)
    # Default is 60 s.
    assert payload["exp"] - int(time.time()) <= 60


def test_command_jwt_rejects_zero_ttl(keypair):
    private_pem, _ = keypair
    with _settings_with(private_pem):
        with pytest.raises(ValueError):
            make_command_jwt(mac="AA:BB:CC:11:22:33", cmd="unlock", exp_seconds=0)


def test_command_jwt_rejects_negative_ttl(keypair):
    private_pem, _ = keypair
    with _settings_with(private_pem):
        with pytest.raises(ValueError):
            make_command_jwt(mac="AA:BB:CC:11:22:33", cmd="unlock", exp_seconds=-5)


def test_command_jwt_signature_is_invalid_against_other_key(keypair):
    from cryptography.exceptions import InvalidSignature

    private_pem, _ = keypair
    _, other_public = generate_jwt_signing_keypair()
    with _settings_with(private_pem):
        token = make_command_jwt(mac="AA:BB:CC:11:22:33", cmd="unlock")
    with pytest.raises(InvalidSignature):
        _verify_es256(token, other_public)


def test_command_jwt_raises_when_signing_key_missing():
    with override_settings(FORGEKEY_JWT_SIGNING_KEY=""):
        with pytest.raises(JwtSigningError):
            make_command_jwt(mac="AA:BB:CC:11:22:33", cmd="unlock")


def test_command_jwt_binds_envelope_claims_when_provided(keypair):
    """The full ``validate()`` envelope path (set_indicator, relay, …) passes
    command_id/nonce/actor so the firmware's validateJwt() claim checks match
    the top-level envelope fields."""
    private_pem, public_pem = keypair
    with _settings_with(private_pem):
        token = make_command_jwt(
            mac="AA:BB:CC:11:22:33",
            cmd="set_indicator",
            command_id="cmd-123",
            nonce="nonce-abc",
            actor="alice",
        )
    payload = _verify_es256(token, public_pem)
    assert payload["cmd"] == "set_indicator"
    assert payload["command_id"] == "cmd-123"
    assert payload["nonce"] == "nonce-abc"
    assert payload["actor"] == "alice"


def test_command_jwt_omits_envelope_claims_when_absent(keypair):
    """Locker / unlock callers pass only mac+cmd; the JWT stays minimal so the
    jwt-only path keeps validating (no spurious claim binding)."""
    private_pem, public_pem = keypair
    with _settings_with(private_pem):
        token = make_command_jwt(mac="AA:BB:CC:11:22:33", cmd="unlock")
    payload = _verify_es256(token, public_pem)
    assert "command_id" not in payload
    assert "nonce" not in payload
    assert "actor" not in payload
