"""Tests for the InviteCode self-signup workflow.

Staff mint a code → invitee POSTs to /invite/redeem/ with their
desired credentials → a fresh User is created and added to the
intended Django groups. Single-use, anonymous, expires.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from membership.models import InviteCode

pytestmark = pytest.mark.django_db

User = get_user_model()

VALID_PASSWORD = "Correct-horse-battery-staple-7"


def _staff(username="staff") -> User:
    return User.objects.create_user(username=username, email=f"{username}@x.com", is_staff=True)


def _open_invite(**kwargs) -> InviteCode:
    return InviteCode.objects.create(
        code=kwargs.pop("code", InviteCode.generate_code()),
        intended_label=kwargs.pop("intended_label", "Board Member"),
        expires_at=kwargs.pop("expires_at", timezone.now() + timedelta(days=7)),
        **kwargs,
    )


class TestInviteCodeModel:
    def test_generate_code_is_url_safe(self):
        code = InviteCode.generate_code()
        # token_urlsafe(16) → 22 chars, base64url alphabet
        assert len(code) >= 16
        assert all(c.isalnum() or c in "-_" for c in code)

    def test_is_redeemable_open(self):
        assert _open_invite().is_redeemable()

    def test_not_redeemable_when_inactive(self):
        invite = _open_invite()
        invite.is_active = False
        invite.save()
        assert not invite.is_redeemable()

    def test_not_redeemable_after_redemption(self):
        invite = _open_invite()
        invite.redeemed = True
        invite.save()
        assert not invite.is_redeemable()

    def test_not_redeemable_after_expiry(self):
        invite = _open_invite(expires_at=timezone.now() - timedelta(seconds=1))
        assert not invite.is_redeemable()


class TestStaffMintEndpoints:
    def test_anonymous_cannot_create(self, api_client):
        url = reverse("invite-code-list")
        response = api_client.post(
            url, data={"intended_label": "X", "expires_at": "2026-12-31T00:00:00Z"}
        )
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_non_staff_cannot_create(self, api_client):
        non_staff = User.objects.create_user(username="member", email="m@x.com")
        api_client.force_authenticate(user=non_staff)
        url = reverse("invite-code-list")
        response = api_client.post(
            url, data={"intended_label": "X", "expires_at": "2026-12-31T00:00:00Z"}
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_staff_can_create_and_code_is_server_generated(self, api_client):
        staff = _staff()
        api_client.force_authenticate(user=staff)
        board = Group.objects.create(name="Board")
        url = reverse("invite-code-list")
        response = api_client.post(
            url,
            data={
                "intended_label": "Board Member — Q3 2026",
                "intended_groups": [board.id],
                "expires_at": (timezone.now() + timedelta(days=14)).isoformat(),
                "notes": "For incoming members.",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        invite = InviteCode.objects.get(pk=response.data["id"])
        # Code is server-set; ignored if client tries to set it
        assert invite.code  # not empty
        assert invite.code == response.data["code"]
        assert invite.created_by_id == staff.id
        assert invite.intended_groups.filter(id=board.id).exists()

    def test_revoke_action(self, api_client):
        staff = _staff()
        api_client.force_authenticate(user=staff)
        invite = _open_invite()
        url = reverse("invite-code-revoke", kwargs={"pk": invite.pk})
        response = api_client.post(url)
        assert response.status_code == status.HTTP_200_OK
        invite.refresh_from_db()
        assert invite.is_active is False
        assert response.data["state"] == "revoked"

    def test_revoke_already_redeemed_is_rejected(self, api_client):
        staff = _staff()
        api_client.force_authenticate(user=staff)
        invite = _open_invite()
        invite.redeemed = True
        invite.save()
        url = reverse("invite-code-revoke", kwargs={"pk": invite.pk})
        response = api_client.post(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestAnonymousPreview:
    def test_preview_returns_intended_label_and_groups(self, api_client):
        board = Group.objects.create(name="Board")
        invite = _open_invite(intended_label="Board Member")
        invite.intended_groups.add(board)
        url = reverse("invite-preview") + f"?code={invite.code}"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["intended_label"] == "Board Member"
        assert response.data["intended_group_names"] == ["Board"]

    def test_preview_invalid_code_returns_404(self, api_client):
        url = reverse("invite-preview") + "?code=NOPE"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_preview_expired_code_returns_404(self, api_client):
        invite = _open_invite(expires_at=timezone.now() - timedelta(hours=1))
        url = reverse("invite-preview") + f"?code={invite.code}"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestAnonymousRedeem:
    def test_happy_path(self, api_client):
        board = Group.objects.create(name="Board")
        invite = _open_invite()
        invite.intended_groups.add(board)
        url = reverse("invite-redeem")
        response = api_client.post(
            url,
            data={
                "code": invite.code,
                "username": "newboard",
                "email": "newboard@example.com",
                "password": VALID_PASSWORD,
                "first_name": "Jane",
                "last_name": "Doe",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        user = User.objects.get(username="newboard")
        assert user.email == "newboard@example.com"
        assert user.groups.filter(name="Board").exists()
        invite.refresh_from_db()
        assert invite.redeemed is True
        assert invite.redeemed_by_id == user.id
        assert invite.redeemed_at is not None

    def test_invalid_code_rejected(self, api_client):
        response = api_client.post(
            reverse("invite-redeem"),
            data={
                "code": "totally-fake",
                "username": "x",
                "email": "x@x.com",
                "password": VALID_PASSWORD,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_expired_code_rejected(self, api_client):
        invite = _open_invite(expires_at=timezone.now() - timedelta(seconds=1))
        response = api_client.post(
            reverse("invite-redeem"),
            data={
                "code": invite.code,
                "username": "x",
                "email": "x@x.com",
                "password": VALID_PASSWORD,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        invite.refresh_from_db()
        assert invite.redeemed is False

    def test_single_use_second_redeem_rejected(self, api_client):
        invite = _open_invite()
        for username in ("first", "second"):
            response = api_client.post(
                reverse("invite-redeem"),
                data={
                    "code": invite.code,
                    "username": username,
                    "email": f"{username}@x.com",
                    "password": VALID_PASSWORD,
                },
                format="json",
            )
            if username == "first":
                assert response.status_code == status.HTTP_201_CREATED
            else:
                assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert User.objects.filter(username="second").count() == 0

    def test_weak_password_rejected(self, api_client):
        invite = _open_invite()
        response = api_client.post(
            reverse("invite-redeem"),
            data={
                "code": invite.code,
                "username": "x",
                "email": "x@x.com",
                "password": "short",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        invite.refresh_from_db()
        assert invite.redeemed is False
        assert User.objects.filter(username="x").count() == 0

    def test_duplicate_username_rejected_atomically(self, api_client):
        # Pre-existing user with the same username; redeem must fail and
        # leave the invite un-redeemed.
        User.objects.create_user(username="taken", email="taken@x.com")
        invite = _open_invite()
        response = api_client.post(
            reverse("invite-redeem"),
            data={
                "code": invite.code,
                "username": "taken",
                "email": "taken2@x.com",
                "password": VALID_PASSWORD,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        invite.refresh_from_db()
        assert invite.redeemed is False
