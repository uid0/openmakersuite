"""Tests for the device-management API + "this wasn't me" revoke-all (FP3, oms-ltqs3).

Covers the three acceptance areas from the bead:

* **device list / forget** — owner-scoped read, ``is_current`` flagging, sensitive
  fields hidden, per-device forget (and that a user cannot touch another's device);
* **revoke-all** — deletes the user's Django sessions and invalidates every
  outstanding JWT (access *and* refresh) by advancing ``User.tokens_valid_after``,
  records an audit event, and only affects the calling user;
* **token-revocation enforcement** in ``config.tokens`` — a token issued before the
  cutoff is rejected, one issued after is accepted, and ``None`` means no change.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core import signing
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from config.tokens import CustomRefreshToken
from notifications.device_login import DEVICE_COOKIE_NAME, DEVICE_COOKIE_SALT
from notifications.models import AccountSecurityAuditEvent, KnownDevice

User = get_user_model()

LIST_URL = "account-device-list"
DETAIL_URL = "account-device-detail"
REVOKE_URL = "account-device-revoke-all"
REFRESH_URL = "refresh"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _user(username, **extra):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="device-secret-123",
        **extra,
    )


def _access_token(user, iat=None):
    """Encoded access token; optionally back/forward-date its ``iat`` claim."""
    access = CustomRefreshToken.for_user(user).access_token
    if iat is not None:
        access.payload["iat"] = int(iat.timestamp())
    return str(access)


def _refresh_token(user, iat=None):
    refresh = CustomRefreshToken.for_user(user)
    if iat is not None:
        refresh.payload["iat"] = int(iat.timestamp())
    return str(refresh)


def _auth(client, user, iat=None):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {_access_token(user, iat=iat)}")


def _device(user, token, *, user_agent="UA/1.0", ip="203.0.113.5"):
    return KnownDevice.objects.create(
        user=user, device_token=token, user_agent=user_agent, ip_address=ip
    )


def _set_device_cookie(client, token):
    signer = signing.get_cookie_signer(salt=DEVICE_COOKIE_NAME + DEVICE_COOKIE_SALT)
    client.cookies[DEVICE_COOKIE_NAME] = signer.sign(token)


def _make_session(user):
    store = SessionStore()
    store["_auth_user_id"] = str(user.pk)
    store.create()
    return store.session_key


def _results(response):
    data = response.json()
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return data


# --------------------------------------------------------------------------- #
# Device list + forget (owner-scoped)
# --------------------------------------------------------------------------- #
@pytest.mark.integration
@pytest.mark.django_db
class TestDeviceListAndForget:
    def test_list_requires_authentication(self, api_client):
        assert api_client.get(reverse(LIST_URL)).status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_returns_only_own_devices(self, api_client):
        owner = _user("owner", is_staff=True)
        other = _user("intruder", is_staff=True)
        _device(owner, "tok-a")
        _device(owner, "tok-b")
        _device(other, "tok-c")

        _auth(api_client, owner)
        resp = api_client.get(reverse(LIST_URL))

        assert resp.status_code == status.HTTP_200_OK
        results = _results(resp)
        assert len(results) == 2

    def test_list_marks_the_requesting_browser_as_current(self, api_client):
        owner = _user("currentdev", is_staff=True)
        here = _device(owner, "tok-here")
        elsewhere = _device(owner, "tok-elsewhere")

        _auth(api_client, owner)
        _set_device_cookie(api_client, "tok-here")
        resp = api_client.get(reverse(LIST_URL))

        by_id = {row["id"]: row for row in _results(resp)}
        assert by_id[here.id]["is_current"] is True
        assert by_id[elsewhere.id]["is_current"] is False

    def test_no_device_is_current_without_a_cookie(self, api_client):
        owner = _user("nocookie", is_staff=True)
        _device(owner, "tok-x")
        _auth(api_client, owner)
        resp = api_client.get(reverse(LIST_URL))
        assert all(row["is_current"] is False for row in _results(resp))

    def test_sensitive_fields_are_never_serialized(self, api_client):
        owner = _user("secrets", is_staff=True)
        _device(owner, "super-secret-token")
        _auth(api_client, owner)
        resp = api_client.get(reverse(LIST_URL))

        row = _results(resp)[0]
        assert "device_token" not in row
        assert "fingerprint_hash" not in row
        assert {
            "id",
            "user_agent",
            "ip_address",
            "label",
            "first_seen",
            "last_seen",
            "is_current",
        } == set(row)

    def test_forget_own_device_deletes_and_audits(self, api_client):
        owner = _user("forgetter", is_staff=True)
        device = _device(owner, "tok-forget")

        _auth(api_client, owner)
        resp = api_client.delete(reverse(DETAIL_URL, kwargs={"pk": device.pk}))

        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not KnownDevice.objects.filter(pk=device.pk).exists()
        event = AccountSecurityAuditEvent.objects.get(
            action=AccountSecurityAuditEvent.ACTION_FORGET_DEVICE
        )
        assert event.actor == owner
        assert event.metadata["device_id"] == device.pk

    def test_cannot_forget_another_users_device(self, api_client):
        owner = _user("a", is_staff=True)
        other = _user("b", is_staff=True)
        device = _device(other, "tok-b")

        _auth(api_client, owner)
        resp = api_client.delete(reverse(DETAIL_URL, kwargs={"pk": device.pk}))

        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert KnownDevice.objects.filter(pk=device.pk).exists()

    def test_cannot_retrieve_another_users_device(self, api_client):
        owner = _user("c", is_staff=True)
        other = _user("d", is_staff=True)
        device = _device(other, "tok-d")

        _auth(api_client, owner)
        resp = api_client.get(reverse(DETAIL_URL, kwargs={"pk": device.pk}))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# --------------------------------------------------------------------------- #
# Revoke-all ("this wasn't me")
# --------------------------------------------------------------------------- #
@pytest.mark.integration
@pytest.mark.django_db
class TestRevokeAll:
    def test_requires_authentication(self, api_client):
        assert api_client.post(reverse(REVOKE_URL)).status_code == status.HTTP_401_UNAUTHORIZED

    def test_deletes_only_the_callers_sessions(self, api_client):
        owner = _user("sessowner", is_staff=True)
        other = _user("sessother", is_staff=True)
        s1 = _make_session(owner)
        s2 = _make_session(owner)
        s_other = _make_session(other)

        _auth(api_client, owner)
        resp = api_client.post(reverse(REVOKE_URL))

        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["sessions_deleted"] == 2
        assert not Session.objects.filter(session_key__in=[s1, s2]).exists()
        assert Session.objects.filter(session_key=s_other).exists()

    def test_invalidates_existing_access_token(self, api_client):
        owner = _user("revoker", is_staff=True)
        # Token issued 5s ago — unambiguously before the revoke cutoff.
        old_token = _access_token(owner, iat=timezone.now() - timedelta(seconds=5))

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {old_token}")
        assert api_client.get(reverse(LIST_URL)).status_code == status.HTTP_200_OK

        # The same token authenticates revoke-all, then is invalidated by it.
        assert api_client.post(reverse(REVOKE_URL)).status_code == status.HTTP_200_OK
        assert api_client.get(reverse(LIST_URL)).status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalidates_existing_refresh_token(self, api_client):
        owner = _user("refresher", is_staff=True)
        old_refresh = _refresh_token(owner, iat=timezone.now() - timedelta(seconds=5))

        owner.tokens_valid_after = timezone.now()
        owner.save(update_fields=["tokens_valid_after"])

        resp = api_client.post(reverse(REFRESH_URL), {"refresh": old_refresh})
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_records_an_audit_event(self, api_client):
        owner = _user("auditor", is_staff=True)
        _make_session(owner)

        _auth(api_client, owner)
        api_client.post(reverse(REVOKE_URL))

        event = AccountSecurityAuditEvent.objects.get(
            action=AccountSecurityAuditEvent.ACTION_REVOKE_ALL
        )
        assert event.actor == owner
        assert event.metadata["sessions_deleted"] == 1
        assert "tokens_valid_after" in event.metadata

    def test_only_affects_the_calling_user(self, api_client):
        owner = _user("caller", is_staff=True)
        bystander = _user("bystander", is_staff=True)
        bystander_token = _access_token(bystander)

        _auth(api_client, owner)
        api_client.post(reverse(REVOKE_URL))

        bystander.refresh_from_db()
        assert bystander.tokens_valid_after is None

        other_client = APIClient()
        other_client.credentials(HTTP_AUTHORIZATION=f"Bearer {bystander_token}")
        assert other_client.get(reverse(LIST_URL)).status_code == status.HTTP_200_OK


# --------------------------------------------------------------------------- #
# Token-revocation enforcement in config.tokens
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.django_db
class TestTokenRevocationEnforcement:
    def test_token_without_revocation_is_accepted(self, api_client):
        owner = _user("clean")
        assert owner.tokens_valid_after is None
        _auth(api_client, owner)
        assert api_client.get(reverse(LIST_URL)).status_code == status.HTTP_200_OK

    def test_token_issued_before_cutoff_is_rejected(self, api_client):
        owner = _user("stale")
        owner.tokens_valid_after = timezone.now()
        owner.save(update_fields=["tokens_valid_after"])

        # iat 5s in the past — strictly before the cutoff.
        token = _access_token(owner, iat=timezone.now() - timedelta(seconds=5))
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        assert api_client.get(reverse(LIST_URL)).status_code == status.HTTP_401_UNAUTHORIZED

    def test_token_issued_after_cutoff_is_accepted(self, api_client):
        owner = _user("fresh")
        owner.tokens_valid_after = timezone.now() - timedelta(seconds=30)
        owner.save(update_fields=["tokens_valid_after"])

        # Minted now → iat is well after the cutoff.
        _auth(api_client, owner)
        assert api_client.get(reverse(LIST_URL)).status_code == status.HTTP_200_OK

    def test_refresh_before_revocation_still_mints_access(self, api_client):
        owner = _user("refreshok")
        resp = api_client.post(reverse(REFRESH_URL), {"refresh": _refresh_token(owner)})
        assert resp.status_code == status.HTTP_200_OK
        assert "access" in resp.json()


@pytest.mark.unit
def test_reject_if_revoked_is_noop_without_claims():
    """A token missing user_id/iat is left alone (no DB hit, no raise)."""
    from config.tokens import _reject_if_revoked

    class _Bare:
        payload = {}

    _reject_if_revoked(_Bare())  # must not raise


@pytest.mark.unit
@pytest.mark.django_db
def test_record_event_drops_unauthenticated_actor():
    from notifications.account_security import record_event

    event = record_event(action=AccountSecurityAuditEvent.ACTION_REVOKE_ALL, actor=None)
    assert event.actor is None
