"""
CA-signed firmware-signing leaf cert: admin path issues the leaf, the
dispatch payload ships it, and legacy keys (no leaf) still dispatch
unchanged for back-compat with devices not yet upgraded.
"""

from __future__ import annotations

from io import StringIO
from unittest import mock

from django.contrib import admin
from django.core.management import call_command
from django.test import RequestFactory

import pytest
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID

from forgekey.admin import FirmwareSigningKeyAdmin, FirmwareSigningKeyForm
from forgekey.models import CertificateAuthority, FirmwareSigningKey, FirmwareVersion
from forgekey.services.firmware_dispatch import _build_payload
from forgekey.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# ----- fixtures --------------------------------------------------------------


@pytest.fixture
def kek(settings):
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


@pytest.fixture
def active_ca(kek):
    out = StringIO()
    call_command("forgekey_ca", "init", "--validity-years", "1", stdout=out)
    return CertificateAuthority.get_active()


def _admin():
    return FirmwareSigningKeyAdmin(FirmwareSigningKey, admin.site)


def _add_request(user):
    request = RequestFactory().post("/admin/forgekey/firmwaresigningkey/add/")
    request.user = user
    from django.contrib.messages.storage.fallback import FallbackStorage

    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _stub_secret_key(settings):
    # FirmwareSigningKey.encrypt_private_pem derives the at-rest KEK from
    # settings.SECRET_KEY; a fixed value keeps the tests deterministic.
    settings.SECRET_KEY = "test-secret-key-for-firmware-signing"


# ----- generate + sign with CA -----------------------------------------------


def test_generate_and_sign_with_ca_persists_leaf_cert(active_ca, settings):
    _stub_secret_key(settings)
    user = UserFactory(is_staff=True, is_superuser=True)
    request = _add_request(user)

    form = FirmwareSigningKeyForm(
        data={
            "label": "prod-2026-q2",
            "description": "",
            "is_active": "on",
            "generate_new": "on",
            "sign_with_ca": "on",
        }
    )
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)

    _admin().save_model(request, obj, form, change=False)

    saved = FirmwareSigningKey.objects.get(pk=obj.pk)
    assert saved.public_key_pem.startswith("-----BEGIN PUBLIC KEY-----")
    assert saved.cert_pem.startswith("-----BEGIN CERTIFICATE-----")
    assert saved.signed_by_ca_id == active_ca.id

    # The leaf cert is a real x509 with the right EKU + SAN.
    leaf = x509.load_pem_x509_certificate(saved.cert_pem.encode("ascii"))
    eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    assert ExtendedKeyUsageOID.CODE_SIGNING in list(eku.value)
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    uris = [u.value for u in san.value if isinstance(u, x509.UniformResourceIdentifier)]
    assert any("firmware-signers/prod-2026-q2" in u for u in uris)
    # Leaf public key matches the row's public PEM.
    leaf_pub = leaf.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    assert leaf_pub.decode("ascii").strip() == saved.public_key_pem.strip()


def test_generate_without_sign_with_ca_skips_leaf(active_ca, settings):
    _stub_secret_key(settings)
    user = UserFactory(is_staff=True, is_superuser=True)
    request = _add_request(user)

    form = FirmwareSigningKeyForm(
        data={
            "label": "legacy-style",
            "description": "",
            "is_active": "on",
            "generate_new": "on",
            # sign_with_ca omitted → unchecked
        }
    )
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)
    _admin().save_model(request, obj, form, change=False)

    saved = FirmwareSigningKey.objects.get(pk=obj.pk)
    assert saved.cert_pem == ""
    assert saved.signed_by_ca is None


def test_sign_with_ca_without_active_ca_errors(settings):
    _stub_secret_key(settings)
    form = FirmwareSigningKeyForm(
        data={
            "label": "prod",
            "description": "",
            "is_active": "on",
            "generate_new": "on",
            "sign_with_ca": "on",
        }
    )
    assert not form.is_valid()
    assert any("no active CA" in e for e in form.non_field_errors())


def test_sign_with_ca_without_kek_errors(active_ca, settings):
    """KEK was set by the active_ca fixture; clear it after the CA is
    bootstrapped to simulate a misconfigured deploy that has an existing CA
    but lost its KEK. sign_firmware_signing_csr needs KEK to unwrap the CA
    private key."""
    _stub_secret_key(settings)
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = ""

    form = FirmwareSigningKeyForm(
        data={
            "label": "prod",
            "description": "",
            "is_active": "on",
            "generate_new": "on",
            "sign_with_ca": "on",
        }
    )
    assert not form.is_valid()
    assert any("Cannot CA-sign keypair" in e for e in form.non_field_errors())


def test_sign_with_ca_requires_label(active_ca, settings):
    _stub_secret_key(settings)
    form = FirmwareSigningKeyForm(
        data={
            "label": "",
            "description": "",
            "is_active": "on",
            "generate_new": "on",
            "sign_with_ca": "on",
        }
    )
    assert not form.is_valid()
    assert any("non-empty label" in e for e in form.non_field_errors())


# ----- dispatch payload includes leaf cert ----------------------------------


def test_dispatch_payload_includes_signing_cert_when_key_has_one(active_ca, settings):
    _stub_secret_key(settings)
    user = UserFactory(is_staff=True, is_superuser=True)
    request = _add_request(user)
    form = FirmwareSigningKeyForm(
        data={
            "label": "prod-ca",
            "description": "",
            "is_active": "on",
            "generate_new": "on",
            "sign_with_ca": "on",
        }
    )
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)
    _admin().save_model(request, obj, form, change=False)

    firmware = mock.Mock(spec=FirmwareVersion)
    firmware.effective_binary_url = "https://example.com/fw.bin"
    firmware.sha256 = "abc123"
    firmware.signature = "sig=="
    firmware.version = "2.0.0"
    firmware.mandatory = False

    payload = _build_payload(firmware)
    assert "signing_cert" in payload
    assert payload["signing_cert"].startswith("-----BEGIN CERTIFICATE-----")
    # The cert in the payload matches the saved row.
    assert payload["signing_cert"] == FirmwareSigningKey.objects.get(pk=obj.pk).cert_pem


def test_dispatch_payload_omits_signing_cert_for_legacy_key(settings):
    _stub_secret_key(settings)
    # Build a legacy key by hand: no leaf cert, no signed_by_ca.
    FirmwareSigningKey.objects.create(
        label="legacy",
        public_key_pem="-----BEGIN PUBLIC KEY-----\nMG==\n-----END PUBLIC KEY-----",
        private_key_pem_encrypted=b"x",
        is_active=True,
    )

    firmware = mock.Mock(spec=FirmwareVersion)
    firmware.effective_binary_url = "https://example.com/fw.bin"
    firmware.sha256 = "abc123"
    firmware.signature = "sig=="
    firmware.version = "1.0.0"
    firmware.mandatory = False

    payload = _build_payload(firmware)
    assert "signing_cert" not in payload


def test_dispatch_payload_omits_signing_cert_when_no_active_key(settings):
    _stub_secret_key(settings)
    firmware = mock.Mock(spec=FirmwareVersion)
    firmware.effective_binary_url = "https://example.com/fw.bin"
    firmware.sha256 = "abc"
    firmware.signature = ""
    firmware.version = "0.0.1"
    firmware.mandatory = False

    payload = _build_payload(firmware)
    assert "signing_cert" not in payload
