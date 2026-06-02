"""
Admin-side device certificate signing: paste a CSR for a known
DeviceIdentity, server signs via the same `sign_csr` path the
/enroll/ endpoint uses, prior active cert for that device is
revoked atomically, the PEM is returned as a download.
"""

from __future__ import annotations

from django.contrib import admin
from django.test import RequestFactory

import pytest
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from forgekey.admin import DeviceCertificateAdmin, DeviceCertificateForm
from forgekey.models import CertificateAuthority, DeviceCertificate, DeviceIdentity
from forgekey.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# ----- fixtures --------------------------------------------------------------


@pytest.fixture
def kek(settings):
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


@pytest.fixture
def active_ca(kek):
    from django.core.management import call_command

    call_command("forgekey_ca", "init", "--validity-years", "1")
    return CertificateAuthority.get_active()


@pytest.fixture
def device():
    return DeviceIdentity.objects.create(device_id="chip-aabbccddeeff")


def _build_csr(mac: str = "aabbccddeeff") -> str:
    """Build a valid CSR matching the firmware contract: EC P-256 / SHA-256 /
    CommonName = ``forgekey-<12-hex-mac>``. (``validate_csr`` rejects anything
    else, same gate the /enroll/ endpoint runs.)"""
    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"forgekey-{mac}")])
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .sign(private_key=private_key, algorithm=hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _admin() -> DeviceCertificateAdmin:
    return DeviceCertificateAdmin(DeviceCertificate, admin.site)


def _add_request(user):
    request = RequestFactory().post("/admin/forgekey/devicecertificate/add/")
    request.user = user
    from django.contrib.messages.storage.fallback import FallbackStorage

    request.session = {}
    request._messages = FallbackStorage(request)
    return request


# ----- permission gating -----------------------------------------------------


def test_only_superuser_can_sign_via_admin():
    request_su = RequestFactory().get("/")
    request_su.user = UserFactory(is_staff=True, is_superuser=True)
    request_staff = RequestFactory().get("/")
    request_staff.user = UserFactory(is_staff=True, is_superuser=False)

    assert _admin().has_add_permission(request_su) is True
    assert _admin().has_add_permission(request_staff) is False


# ----- form validation -------------------------------------------------------


def test_form_rejects_garbage_csr(device):
    form = DeviceCertificateForm(data={"device": device.pk, "csr_pem": "not a CSR"})
    assert not form.is_valid()
    assert any("Invalid CSR" in e for e in form.non_field_errors())


def test_form_rejects_decommissioned_device():
    decom = DeviceIdentity.objects.create(
        device_id="chip-dead", status=DeviceIdentity.STATUS_DECOMMISSIONED
    )
    form = DeviceCertificateForm(data={"device": decom.pk, "csr_pem": _build_csr()})
    assert not form.is_valid()
    assert any("decommissioned" in e for e in form.non_field_errors())


def test_form_accepts_well_formed_csr(device):
    form = DeviceCertificateForm(data={"device": device.pk, "csr_pem": _build_csr()})
    assert form.is_valid(), form.errors


# ----- save_model signs + persists + revokes --------------------------------


def test_save_model_signs_and_persists(active_ca, device):
    user = UserFactory(is_staff=True, is_superuser=True)
    request = _add_request(user)
    form = DeviceCertificateForm(data={"device": device.pk, "csr_pem": _build_csr()})
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)

    _admin().save_model(request, obj, form, change=False)

    assert DeviceCertificate.objects.filter(device=device).count() == 1
    cert = DeviceCertificate.objects.get(device=device)
    assert cert.serial
    assert cert.fingerprint_sha256
    assert cert.issued_by == active_ca.name
    assert cert.revoked_at is None
    # PEM is stashed on the request for response_add to deliver.
    assert request._signed_cert_pem.startswith("-----BEGIN CERTIFICATE-----")
    assert request._signed_cert_serial == cert.serial


def test_save_model_revokes_prior_active_cert(active_ca, device):
    """Re-signing replaces the device's previous active cert."""
    user = UserFactory(is_staff=True, is_superuser=True)

    # First signing
    request = _add_request(user)
    form = DeviceCertificateForm(data={"device": device.pk, "csr_pem": _build_csr()})
    assert form.is_valid(), form.errors
    _admin().save_model(request, form.save(commit=False), form, change=False)
    first = DeviceCertificate.objects.get(device=device, revoked_at__isnull=True)

    # Second signing — first should now be revoked.
    request2 = _add_request(user)
    form2 = DeviceCertificateForm(data={"device": device.pk, "csr_pem": _build_csr()})
    assert form2.is_valid(), form2.errors
    _admin().save_model(request2, form2.save(commit=False), form2, change=False)

    first.refresh_from_db()
    assert first.revoked_at is not None
    active_now = DeviceCertificate.objects.filter(device=device, revoked_at__isnull=True)
    assert active_now.count() == 1
    assert active_now.first().pk != first.pk


def test_save_model_surfaces_no_active_ca(device):
    """No active CA ⇒ CsrSigningError ⇒ admin message + raise (no 500)."""
    from forgekey.services.csr_signing import CsrSigningError

    user = UserFactory(is_staff=True, is_superuser=True)
    request = _add_request(user)
    form = DeviceCertificateForm(data={"device": device.pk, "csr_pem": _build_csr()})
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)

    with pytest.raises(CsrSigningError):
        _admin().save_model(request, obj, form, change=False)

    assert DeviceCertificate.objects.count() == 0
    msgs = [str(m) for m in request._messages]
    assert any("CA unavailable" in m for m in msgs)


# ----- response_add delivers the PEM as a download --------------------------


def test_response_add_returns_pem_as_attachment(active_ca, device):
    user = UserFactory(is_staff=True, is_superuser=True)
    request = _add_request(user)
    form = DeviceCertificateForm(data={"device": device.pk, "csr_pem": _build_csr()})
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)
    _admin().save_model(request, obj, form, change=False)

    response = _admin().response_add(request, obj)

    assert response.status_code == 200
    assert response["Content-Type"] == "application/x-pem-file"
    assert "attachment" in response["Content-Disposition"]
    assert f"cert-{obj.serial}.pem" in response["Content-Disposition"]
    body = response.content.decode("ascii")
    assert body.startswith("-----BEGIN CERTIFICATE-----")
    # PEM body is a valid x509 cert with the right device_id in the SAN URI.
    cert = x509.load_pem_x509_certificate(body.encode("ascii"))
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    uris = [u.value for u in san.value if isinstance(u, x509.UniformResourceIdentifier)]
    assert any(device.device_id in u for u in uris)
