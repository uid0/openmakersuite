"""Tests for the maker box API endpoints (AC3, AC5)."""

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


def _scan(client, **payload):
    return client.post(reverse("maker-box-scan"), payload, format="json")


def test_scan_creates_assignment_row_for_active_member(staff_client):
    expires = timezone.now() + timedelta(days=15)
    fake = MemberLookup(
        status="valid",
        username="ada",
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.org",
        expires_at=expires,
        days_remaining=15,
    )
    with patch("maker_boxes.views.lookup_member", return_value=fake):
        response = _scan(staff_client, bin_id="PSB-007", username="ada")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "valid"
    assert body["first_name"] == "Ada"
    assert body["bin_id"] == "PSB-007"

    box = MakerBox.objects.get(bin_id="PSB-007")
    assert box.assigned_username == "ada"
    assert box.status == "valid"
    assert box.last_verified_at is not None


def test_scan_unknown_user_returns_unknown_status(staff_client):
    with patch("maker_boxes.views.lookup_member", return_value=None):
        response = _scan(staff_client, bin_id="PSB-008", username="ghost")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unknown"
    box = MakerBox.objects.get(bin_id="PSB-008")
    assert box.status == "unknown"


def test_email_pickup_sends_message(staff_client, mailoutbox):
    box = MakerBox.objects.create(
        bin_id="PSB-009",
        assigned_username="ghost",
        first_name="Ghost",
        last_name="Member",
        email="ghost@example.org",
        status="expired",
    )
    url = reverse("maker-box-email-pickup", args=[box.pk])
    response = staff_client.post(url, {}, format="json")
    assert response.status_code == 200
    assert len(mailoutbox) == 1
    assert "ghost@example.org" in mailoutbox[0].to
    assert "PSB-009" in mailoutbox[0].body


# ---------------------------------------------------------------------------
# print-sheet (Avery 5371 bulk multi-up)
# ---------------------------------------------------------------------------


@pytest.fixture
def member_client():
    """Plain authenticated user with no Logistics / staff role."""
    user = User.objects.create_user(username="rando", password="x")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _print_sheet(client, bin_ids):
    return client.post(
        reverse("maker-box-print-sheet"),
        {"bin_ids": bin_ids},
        format="json",
    )


def test_print_sheet_anonymous_rejected():
    client = APIClient()
    resp = client.post(
        reverse("maker-box-print-sheet"),
        {"bin_ids": ["PSB-100"]},
        format="json",
    )
    assert resp.status_code in (401, 403)


def test_print_sheet_member_rejected(member_client):
    MakerBox.objects.create(bin_id="PSB-100")
    resp = _print_sheet(member_client, ["PSB-100"])
    assert resp.status_code == 403


def test_print_sheet_staff_can_render(staff_client):
    MakerBox.objects.create(
        bin_id="PSB-100", assigned_username="ada", first_name="Ada", last_name="L"
    )
    MakerBox.objects.create(
        bin_id="PSB-101", assigned_username="bob", first_name="Bob", last_name="P"
    )
    resp = _print_sheet(staff_client, ["PSB-100", "PSB-101"])
    assert resp.status_code == 200, resp.content
    assert resp["Content-Type"] == "image/png"
    # PNG magic bytes
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_print_sheet_empty_bin_ids_400(staff_client):
    resp = _print_sheet(staff_client, [])
    assert resp.status_code == 400


def test_print_sheet_non_list_400(staff_client):
    resp = staff_client.post(
        reverse("maker-box-print-sheet"),
        {"bin_ids": "PSB-100"},
        format="json",
    )
    assert resp.status_code == 400


def test_print_sheet_all_unknown_404(staff_client):
    resp = _print_sheet(staff_client, ["PSB-NOPE1", "PSB-NOPE2"])
    assert resp.status_code == 404


def test_print_sheet_caps_at_ten(staff_client):
    # 12 supplied → silently caps to 10. Validates by returning a
    # single PNG (no error, no pagination header — v1).
    for i in range(12):
        MakerBox.objects.create(
            bin_id=f"PSB-{200+i:03d}",
            assigned_username=f"u{i}",
            first_name=f"F{i}",
        )
    resp = _print_sheet(staff_client, [f"PSB-{200+i:03d}" for i in range(12)])
    assert resp.status_code == 200
    assert resp["Content-Type"] == "image/png"
