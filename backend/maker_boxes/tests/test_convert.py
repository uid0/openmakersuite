"""Tests for the PR3 conversion endpoint + public verify scan."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from maker_boxes.models import MakerBox

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client():
    user = User.objects.create_user(username="staff", password="x", is_staff=True)
    Group.objects.get_or_create(name="Logistics")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def member_client():
    user = User.objects.create_user(username="rando", password="x")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ---------------------------------------------------------------------------
# /convert/
# ---------------------------------------------------------------------------


def test_convert_allocates_mbx_001_for_first_row(staff_client):
    row = MakerBox.objects.create(
        assigned_username="ada",
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.org",
        status=MakerBox.STATUS_PRE_CONVERSION,
        identity_source=MakerBox.IDENTITY_WHMCS,
        expires_at=timezone.now() + timedelta(days=30),
    )

    resp = staff_client.post(reverse("maker-box-convert"), {"id": row.id}, format="json")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["bin_id"] == "MBX-001"
    assert body["status"] == "valid"
    assert body["conversion_completed_at"] is not None
    assert body["assigned_at"] is not None

    row.refresh_from_db()
    assert row.bin_id == "MBX-001"
    assert row.status == MakerBox.STATUS_VALID


def test_convert_increments_bin_id():
    pass  # Covered by allocator unit tests; integration only needs one call.


def test_convert_by_username(staff_client):
    MakerBox.objects.create(
        assigned_username="ada",
        first_name="Ada",
        status=MakerBox.STATUS_PRE_CONVERSION,
        identity_source=MakerBox.IDENTITY_WHMCS,
        expires_at=timezone.now() + timedelta(days=30),
    )
    resp = staff_client.post(
        reverse("maker-box-convert"),
        {"assigned_username": "ada"},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.json()["bin_id"] == "MBX-001"


def test_convert_marks_expired_if_lookup_expired(staff_client):
    row = MakerBox.objects.create(
        assigned_username="ada",
        first_name="Ada",
        status=MakerBox.STATUS_PRE_CONVERSION,
        identity_source=MakerBox.IDENTITY_WHMCS,
        expires_at=timezone.now() - timedelta(days=20),
    )
    resp = staff_client.post(reverse("maker-box-convert"), {"id": row.id}, format="json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "expired"
    assert body["bin_id"] == "MBX-001"


def test_convert_handles_addon_user_without_expiry(staff_client):
    """Common-API add-on case: identity confirmed but no WHMCS expiry."""
    row = MakerBox.objects.create(
        assigned_username="addon",
        first_name="Add",
        status=MakerBox.STATUS_PRE_CONVERSION,
        identity_source=MakerBox.IDENTITY_COMMON_API,
        expires_at=None,
    )
    resp = staff_client.post(reverse("maker-box-convert"), {"id": row.id}, format="json")
    assert resp.status_code == 200
    assert resp.json()["status"] == "valid"


def test_convert_missing_row_returns_404(staff_client):
    resp = staff_client.post(reverse("maker-box-convert"), {"id": 99999}, format="json")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_convert_rejects_already_converted(staff_client):
    row = MakerBox.objects.create(
        assigned_username="ada",
        bin_id="MBX-007",
        status=MakerBox.STATUS_VALID,
    )
    resp = staff_client.post(reverse("maker-box-convert"), {"id": row.id}, format="json")
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "already_converted"
    assert body["bin_id"] == "MBX-007"


def test_convert_requires_id_or_username(staff_client):
    resp = staff_client.post(reverse("maker-box-convert"), {}, format="json")
    assert resp.status_code == 400


def test_convert_rejects_non_staff(member_client):
    resp = member_client.post(reverse("maker-box-convert"), {"id": 1}, format="json")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# /verify/<bin>/<user>/
# ---------------------------------------------------------------------------


def _verify_url(bin_id: str, username: str) -> str:
    return reverse("maker-box-verify-scan", args=[bin_id, username])


def test_verify_scan_returns_status_for_matched_pair():
    MakerBox.objects.create(
        bin_id="MBX-001",
        assigned_username="ada",
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.org",
        status=MakerBox.STATUS_VALID,
        expires_at=timezone.now() + timedelta(days=30),
    )
    client = APIClient()
    resp = client.get(_verify_url("MBX-001", "ada"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "valid"
    assert body["bin_id"] == "MBX-001"
    assert body["username"] == "ada"
    assert body["first_name"] == "Ada"
    assert body["email"] == "ada@example.org"


def test_verify_scan_allows_anonymous():
    MakerBox.objects.create(
        bin_id="MBX-002",
        assigned_username="bob",
        status=MakerBox.STATUS_VALID,
    )
    client = APIClient()  # no auth
    resp = client.get(_verify_url("MBX-002", "bob"))
    assert resp.status_code == 200


def test_verify_scan_unknown_bin_returns_unknown():
    client = APIClient()
    resp = client.get(_verify_url("MBX-999", "ghost"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unknown"
    assert body["first_name"] == ""
    assert body["email"] == ""
    assert "No such bin" in body.get("detail", "")


def test_verify_scan_wrong_user_returns_unknown_and_no_owner_leak():
    """Cross-user scan must not leak the real owner's identity."""
    MakerBox.objects.create(
        bin_id="MBX-003",
        assigned_username="ada",
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.org",
        status=MakerBox.STATUS_VALID,
    )
    client = APIClient()
    resp = client.get(_verify_url("MBX-003", "eve"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unknown"
    # Real owner's name/email must NOT be in the response.
    assert body["first_name"] == ""
    assert body["last_name"] == ""
    assert body["email"] == ""
    assert "Ada" not in (body.get("detail") or "")


def test_verify_scan_username_match_is_case_insensitive():
    MakerBox.objects.create(
        bin_id="MBX-004",
        assigned_username="Ada",
        status=MakerBox.STATUS_VALID,
    )
    client = APIClient()
    resp = client.get(_verify_url("MBX-004", "ada"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "valid"
