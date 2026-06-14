"""Tests for the PR2 pre-conversion endpoints.

Covers:

* ``POST /lookup/`` — identity-only lookup hit / miss / staff gate.
* ``POST /pre-convert/`` — creates a queue row, idempotent on resolved
  username, refuses to clobber an already-converted row.
* ``GET /pre-conversion-queue/`` — lists only ``pre_conversion`` rows.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from maker_boxes.models import MakerBox
from maker_boxes.services.whmcs_client import MemberLookup

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


def _hit(username="ada", *, status_="valid", expires_days=30):
    expires = timezone.now() + timedelta(days=expires_days)
    return MemberLookup(
        status=status_,
        username=username,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.org",
        expires_at=expires,
        days_remaining=expires_days,
    )


# ---------------------------------------------------------------------------
# /lookup/
# ---------------------------------------------------------------------------


def test_lookup_hit_returns_identity(staff_client):
    with patch("maker_boxes.views.resolve_identity", return_value=(_hit(), "whmcs")):
        resp = staff_client.post(reverse("maker-box-lookup"), {"query": "ada"}, format="json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["identity_source"] == "whmcs"
    assert body["username"] == "ada"
    assert body["first_name"] == "Ada"
    assert body["membership_status"] == "valid"
    # No DB write — the lookup endpoint is read-only.
    assert MakerBox.objects.count() == 0


def test_lookup_miss_returns_found_false(staff_client):
    with patch("maker_boxes.views.resolve_identity", return_value=(None, "")):
        resp = staff_client.post(reverse("maker-box-lookup"), {"query": "ghost"}, format="json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is False
    assert body["username"] == ""
    assert body["membership_status"] == ""
    assert body["expires_at"] is None


def test_lookup_rejects_non_staff(member_client):
    resp = member_client.post(reverse("maker-box-lookup"), {"query": "ada"}, format="json")
    assert resp.status_code == 403


def test_lookup_anonymous_rejected():
    client = APIClient()
    resp = client.post(reverse("maker-box-lookup"), {"query": "ada"}, format="json")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# /pre-convert/
# ---------------------------------------------------------------------------


def test_pre_convert_creates_pre_conversion_row(staff_client):
    with patch("maker_boxes.views.resolve_identity", return_value=(_hit(), "whmcs")):
        resp = staff_client.post(reverse("maker-box-pre-convert"), {"query": "ada"}, format="json")

    assert resp.status_code == 201
    body = resp.json()
    assert body["assigned_username"] == "ada"
    assert body["status"] == "pre_conversion"
    assert body["bin_id"] is None
    assert body["identity_source"] == "whmcs"
    assert body["first_name"] == "Ada"

    row = MakerBox.objects.get(assigned_username="ada")
    assert row.status == MakerBox.STATUS_PRE_CONVERSION
    assert row.bin_id is None
    assert row.last_verified_at is not None


def test_pre_convert_idempotent_returns_200_and_refreshes_identity(staff_client):
    # First call creates.
    with patch("maker_boxes.views.resolve_identity", return_value=(_hit(), "whmcs")):
        first = staff_client.post(reverse("maker-box-pre-convert"), {"query": "ada"}, format="json")
    assert first.status_code == 201

    # Second call returns the existing row with refreshed identity.
    refreshed = _hit()
    refreshed.first_name = "Augusta"  # WHMCS now has the formal name.
    refreshed.email = "ada+new@example.org"
    with patch("maker_boxes.views.resolve_identity", return_value=(refreshed, "whmcs")):
        second = staff_client.post(
            reverse("maker-box-pre-convert"), {"query": "ada"}, format="json"
        )

    assert second.status_code == 200
    body = second.json()
    assert body["first_name"] == "Augusta"
    assert body["email"] == "ada+new@example.org"
    assert MakerBox.objects.filter(assigned_username="ada").count() == 1


def test_pre_convert_appends_notes(staff_client):
    with patch("maker_boxes.views.resolve_identity", return_value=(_hit(), "whmcs")):
        staff_client.post(
            reverse("maker-box-pre-convert"),
            {"query": "ada", "notes": "wants the small bin"},
            format="json",
        )
        staff_client.post(
            reverse("maker-box-pre-convert"),
            {"query": "ada", "notes": "also: dropped off keys"},
            format="json",
        )
    row = MakerBox.objects.get(assigned_username="ada")
    assert "wants the small bin" in row.notes
    assert "dropped off keys" in row.notes


def test_pre_convert_miss_returns_404(staff_client):
    with patch("maker_boxes.views.resolve_identity", return_value=(None, "")):
        resp = staff_client.post(
            reverse("maker-box-pre-convert"), {"query": "ghost"}, format="json"
        )
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"
    assert MakerBox.objects.count() == 0


def test_pre_convert_rejects_already_converted_user(staff_client):
    # User has a bin_id and a real status — they're past PR3.
    MakerBox.objects.create(
        bin_id="MBX-007",
        assigned_username="ada",
        status=MakerBox.STATUS_VALID,
        first_name="Ada",
    )
    with patch("maker_boxes.views.resolve_identity", return_value=(_hit(), "whmcs")):
        resp = staff_client.post(reverse("maker-box-pre-convert"), {"query": "ada"}, format="json")
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "already_converted"
    assert body["bin_id"] == "MBX-007"


def test_pre_convert_rejects_non_staff(member_client):
    resp = member_client.post(reverse("maker-box-pre-convert"), {"query": "ada"}, format="json")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# /pre-conversion-queue/
# ---------------------------------------------------------------------------


def test_pre_conversion_queue_lists_only_pre_conversion_rows(staff_client):
    MakerBox.objects.create(
        assigned_username="ada",
        status=MakerBox.STATUS_PRE_CONVERSION,
        first_name="Ada",
    )
    MakerBox.objects.create(
        assigned_username="bob",
        status=MakerBox.STATUS_PRE_CONVERSION,
        first_name="Bob",
    )
    # Already-converted: should NOT appear.
    MakerBox.objects.create(
        bin_id="MBX-001",
        assigned_username="carol",
        status=MakerBox.STATUS_VALID,
        first_name="Carol",
    )
    # Unassigned legacy row: also should NOT appear.
    MakerBox.objects.create(
        bin_id="MBX-002",
        status=MakerBox.STATUS_UNASSIGNED,
    )

    resp = staff_client.get(reverse("maker-box-pre-conversion-queue"))
    assert resp.status_code == 200
    body = resp.json()
    usernames = sorted(row["assigned_username"] for row in body)
    assert usernames == ["ada", "bob"]


def test_pre_conversion_queue_rejects_non_staff(member_client):
    resp = member_client.get(reverse("maker-box-pre-conversion-queue"))
    assert resp.status_code == 403
