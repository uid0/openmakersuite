"""
Admin-side CA generation: clicking "Add Certificate Authority" in
/admin/forgekey/certificateauthority/ mints a fresh CA server-side
instead of presenting a no-op form (the old behavior — see PR #662 for
context on the readonly-form trap).

Mirrors the test surface around `manage.py forgekey_ca init` but exercises
the admin form + save_model path.
"""

from __future__ import annotations

from django.contrib import admin
from django.test import RequestFactory

import pytest
from cryptography import x509
from cryptography.fernet import Fernet

from forgekey.admin import CertificateAuthorityAdmin, CertificateAuthorityForm
from forgekey.models import CertificateAuthority
from forgekey.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def kek(settings):
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


def _admin() -> CertificateAuthorityAdmin:
    return CertificateAuthorityAdmin(CertificateAuthority, admin.site)


def _add_request(user):
    request = RequestFactory().post("/admin/forgekey/certificateauthority/add/")
    request.user = user
    # Required by Django's MessageMiddleware path that save_model invokes via
    # `messages.success`. We never read these back, but the storage has to
    # exist or `add_message` raises.
    from django.contrib.messages.storage.fallback import FallbackStorage

    setattr(request, "session", {})
    request._messages = FallbackStorage(request)
    return request


# ----- permission gating -----------------------------------------------------


def test_only_superuser_can_add():
    superuser = UserFactory(is_staff=True, is_superuser=True)
    staff = UserFactory(is_staff=True, is_superuser=False)

    admin_cls = _admin()
    su_req = RequestFactory().get("/")
    su_req.user = superuser
    staff_req = RequestFactory().get("/")
    staff_req.user = staff

    assert admin_cls.has_add_permission(su_req) is True
    assert admin_cls.has_add_permission(staff_req) is False


def test_delete_is_blocked_for_superuser():
    admin_cls = _admin()
    request = RequestFactory().get("/")
    request.user = UserFactory(is_staff=True, is_superuser=True)

    assert admin_cls.has_delete_permission(request) is False


# ----- form validation -------------------------------------------------------


def test_form_accepts_first_ca_without_force(kek):
    form = CertificateAuthorityForm(
        data={"name": "forgekey-root", "cn": "ForgeKey", "validity_years": "1"}
    )
    assert form.is_valid(), form.errors


def test_form_requires_force_replace_when_active_ca_exists(kek):
    _generate_active_ca(name="existing")
    form = CertificateAuthorityForm(
        data={"name": "forgekey-root-2", "cn": "ForgeKey", "validity_years": "1"}
    )
    assert not form.is_valid()
    assert any("active CA already exists" in e for e in form.non_field_errors())


def test_form_accepts_replacement_with_force(kek):
    _generate_active_ca(name="existing")
    form = CertificateAuthorityForm(
        data={
            "name": "forgekey-root-2",
            "cn": "ForgeKey",
            "validity_years": "1",
            "force_replace": "on",
        }
    )
    assert form.is_valid(), form.errors


# ----- save_model mints + persists ------------------------------------------


def test_save_model_generates_and_persists_active_ca(kek):
    user = UserFactory(is_staff=True, is_superuser=True)
    request = _add_request(user)
    form = CertificateAuthorityForm(
        data={"name": "forgekey-root", "cn": "ForgeKey", "validity_years": "1"}
    )
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)

    _admin().save_model(request, obj, form, change=False)

    assert CertificateAuthority.objects.filter(is_active=True).count() == 1
    active = CertificateAuthority.get_active()
    assert active.name == "forgekey-root"
    # The cert is a parseable x509 with the requested CN.
    cert = x509.load_pem_x509_certificate(active.cert_pem.encode("utf-8"))
    cn_attr = next(a.value for a in cert.subject if a.oid.dotted_string == "2.5.4.3")
    assert cn_attr == "ForgeKey"
    # not_before/not_after came from the cert itself, not blank.
    assert active.not_before == cert.not_valid_before_utc
    assert active.not_after == cert.not_valid_after_utc
    assert active.key_kid.startswith("forgekey-ca-kek-")
    assert active.encrypted_private_key  # non-empty


def test_save_model_deactivates_prior_active(kek):
    prior = _generate_active_ca(name="prior")
    user = UserFactory(is_staff=True, is_superuser=True)
    request = _add_request(user)
    form = CertificateAuthorityForm(
        data={
            "name": "successor",
            "cn": "ForgeKey",
            "validity_years": "1",
            "force_replace": "on",
        }
    )
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)

    _admin().save_model(request, obj, form, change=False)

    prior.refresh_from_db()
    assert prior.is_active is False
    active = CertificateAuthority.get_active()
    assert active.name == "successor"


def test_save_model_surfaces_missing_kek_via_messages(settings):
    """No KEK ⇒ CaKeyStorageError ⇒ admin message + raise (no IntegrityError 500)."""
    from forgekey.services.ca_key_storage import CaKeyStorageError

    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = ""  # explicitly cleared
    user = UserFactory(is_staff=True, is_superuser=True)
    request = _add_request(user)
    form = CertificateAuthorityForm(
        data={"name": "forgekey-root", "cn": "ForgeKey", "validity_years": "1"}
    )
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)

    with pytest.raises(CaKeyStorageError):
        _admin().save_model(request, obj, form, change=False)

    assert CertificateAuthority.objects.count() == 0
    msgs = [str(m) for m in request._messages]
    assert any("Cannot encrypt CA private key" in m for m in msgs)


# ----- helpers ---------------------------------------------------------------


def _generate_active_ca(*, name: str) -> CertificateAuthority:
    """Use the form+admin path itself so the test fixture matches production."""
    user = UserFactory(is_staff=True, is_superuser=True)
    request = _add_request(user)
    form = CertificateAuthorityForm(data={"name": name, "cn": name, "validity_years": "1"})
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)
    _admin().save_model(request, obj, form, change=False)
    return CertificateAuthority.get_active()
