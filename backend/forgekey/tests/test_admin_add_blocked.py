"""
View-only admins for CSR-issued artifacts must refuse hand-creation.

Without `has_add_permission = False`, Django still renders an Add form even
when every model field is in `readonly_fields` — submitting the form POSTs
empty values and 500s on NOT NULL columns. Sentry BACKEND-D was exactly
that: clicking "Add Device Certificate" produced an IntegrityError on
`not_before`. These admins exist purely for revoke / approve actions on
rows that the enrollment pipeline already created.
"""

from __future__ import annotations

from django.contrib import admin
from django.test import Client

import pytest

from forgekey.admin import DeviceCertificateAdmin, DeviceEnrollmentAdmin
from forgekey.models import DeviceCertificate, DeviceEnrollment
from forgekey.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# CertificateAuthorityAdmin had this same guard in the original BACKEND-D
# fix, but now exposes an Add path that generates the CA server-side. See
# test_admin_ca_generate.py for the generation flow's coverage.
VIEW_ONLY_ADMINS = [
    (DeviceCertificateAdmin, DeviceCertificate, "devicecertificate"),
    (DeviceEnrollmentAdmin, DeviceEnrollment, "deviceenrollment"),
]


@pytest.mark.parametrize("admin_cls,model,_url", VIEW_ONLY_ADMINS)
def test_has_add_permission_is_false(admin_cls, model, _url):
    assert admin_cls(model, admin.site).has_add_permission(None) is False


@pytest.mark.parametrize("_admin_cls,_model,url_segment", VIEW_ONLY_ADMINS)
def test_admin_add_url_is_forbidden_for_superuser(_admin_cls, _model, url_segment):
    """Even a superuser navigating to /admin/.../add/ must not be able to create.

    Pre-fix behavior: the GET rendered an Add form (200), then POST 500'd on
    NOT NULL constraints. With `has_add_permission=False`, Django raises
    PermissionDenied → 403.
    """
    superuser = UserFactory(is_staff=True, is_superuser=True)
    client = Client()
    client.force_login(superuser)

    response = client.get(f"/admin/forgekey/{url_segment}/add/")

    assert response.status_code == 403
