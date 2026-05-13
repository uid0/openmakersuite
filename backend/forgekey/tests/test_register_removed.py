"""
Guard test: ``/api/forgekey/devices/register/`` was retired in oms-d2axqu
when the device-identity trust foundation landed. Refer to the new
``/devices/enroll/`` endpoint (covered in :mod:`test_enrollment`).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def test_register_view_is_deleted_from_views_module():
    from forgekey import views

    assert not hasattr(views, "ForgeKeyDeviceRegisterView")


def test_register_url_name_is_gone():
    from django.urls import NoReverseMatch, reverse

    with pytest.raises(NoReverseMatch):
        reverse("forgekey:device-register")


def test_register_path_not_a_named_pattern(api_client, admin_user):
    """``/devices/register/`` no longer routes to a registration view.

    The DRF ``devices`` router still owns the ``/devices/<pk>/`` prefix, so a
    request with ``register`` as the pk falls through to the detail handler.
    With an authenticated admin client the detail handler answers with 404
    (no such device) — i.e., it is no longer the enrollment endpoint.
    """
    api_client.force_authenticate(user=admin_user)
    resp = api_client.post("/api/forgekey/devices/register/")
    assert resp.status_code in (404, 405)
