"""Tests for the RFID-KeyMaster interlock integration (Phase 1, backend).

Maps to the acceptance points in op-9pj:
* model + encryption round-trip
* enqueue via enable / disable / status
* command-queue REQUIRES auth (401 without token) and returns decrypted creds
* report updates command + interlock state
* SSH password is write-only (never returned in GET)
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from interlocks.models import Interlock, InterlockCommand
from inventory.models import Asset

User = get_user_model()

DAEMON_TOKEN = "test-interlock-daemon-token"


@pytest.fixture
def asset(db):
    return Asset.objects.create(name="Laser cutter")


@pytest.fixture
def staff_client(db):
    user = User.objects.create_user(
        username="staffer",
        email="staff@example.com",
        password=get_random_string(24),
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


@pytest.fixture
def interlock(asset):
    il = Interlock.objects.create(
        label="Laser cutter interlock",
        asset=asset,
        host="10.0.0.5",
        ssh_username="root",
        service_name="keymaster.service",
        relay_pin=17,
        relay_interface=Interlock.INTERFACE_PIGPIO,
    )
    il.set_ssh_password("s3cr3t-pass")
    il.save()
    return il


# --------------------------------------------------------------------------
# Encryption round-trip
# --------------------------------------------------------------------------
@pytest.mark.django_db
def test_ssh_password_encryption_round_trip(asset):
    il = Interlock.objects.create(label="X", asset=asset, host="10.0.0.9")
    assert il.has_credentials is False

    il.set_ssh_password("hunter2")
    il.save()

    stored = bytes(Interlock.objects.get(pk=il.pk).encrypted_ssh_password)
    # Ciphertext is neither empty nor the plaintext.
    assert stored != b""
    assert b"hunter2" not in stored
    # ...but decrypts back to the original.
    assert Interlock.objects.get(pk=il.pk).get_ssh_password() == "hunter2"
    assert Interlock.objects.get(pk=il.pk).has_credentials is True


# --------------------------------------------------------------------------
# Operator CRUD: password write-only
# --------------------------------------------------------------------------
@pytest.mark.django_db
def test_create_interlock_password_is_write_only(staff_client, asset):
    client, _ = staff_client
    resp = client.post(
        "/api/interlocks/",
        {
            "label": "Mill interlock",
            "asset": str(asset.pk),
            "host": "10.0.0.7",
            "ssh_username": "root",
            "ssh_password": "topsecret",
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    # The plaintext must never come back; a boolean flag is exposed instead.
    assert "ssh_password" not in body
    assert body["has_credentials"] is True

    # And the credential was actually stored (encrypted) + decrypts.
    il = Interlock.objects.get(pk=body["id"])
    assert il.get_ssh_password() == "topsecret"


@pytest.mark.django_db
def test_get_interlock_never_returns_password(staff_client, interlock):
    client, _ = staff_client
    resp = client.get(f"/api/interlocks/{interlock.pk}/")
    assert resp.status_code == 200
    body = resp.json()
    assert "ssh_password" not in body
    assert "encrypted_ssh_password" not in body
    assert body["has_credentials"] is True


@pytest.mark.django_db
def test_update_without_password_keeps_existing(staff_client, interlock):
    client, _ = staff_client
    resp = client.patch(
        f"/api/interlocks/{interlock.pk}/",
        {"label": "Renamed"},
        format="json",
    )
    assert resp.status_code == 200
    interlock.refresh_from_db()
    assert interlock.label == "Renamed"
    # Password left untouched because none was supplied.
    assert interlock.get_ssh_password() == "s3cr3t-pass"


@pytest.mark.django_db
def test_non_staff_cannot_manage_interlocks(db, interlock):
    user = User.objects.create_user(
        username="member", email="m@example.com", password=get_random_string(24)
    )
    client = APIClient()
    client.force_authenticate(user=user)
    resp = client.get("/api/interlocks/")
    assert resp.status_code in (401, 403)


# --------------------------------------------------------------------------
# enable / disable / status enqueue commands
# --------------------------------------------------------------------------
@pytest.mark.django_db
def test_enable_sets_desired_state_and_enqueues(staff_client, interlock):
    client, user = staff_client
    interlock.desired_state = Interlock.STATE_DISABLED
    interlock.save(update_fields=["desired_state"])

    resp = client.post(f"/api/interlocks/{interlock.pk}/enable/")
    assert resp.status_code == 201, resp.content

    interlock.refresh_from_db()
    assert interlock.desired_state == Interlock.STATE_ENABLED
    cmd = InterlockCommand.objects.get(interlock=interlock)
    assert cmd.action == InterlockCommand.ACTION_ENABLE
    assert cmd.state == InterlockCommand.STATE_PENDING
    assert cmd.requested_by == user


@pytest.mark.django_db
def test_disable_sets_desired_state_and_enqueues(staff_client, interlock):
    client, _ = staff_client
    resp = client.post(f"/api/interlocks/{interlock.pk}/disable/")
    assert resp.status_code == 201, resp.content

    interlock.refresh_from_db()
    assert interlock.desired_state == Interlock.STATE_DISABLED
    cmd = InterlockCommand.objects.get(interlock=interlock)
    assert cmd.action == InterlockCommand.ACTION_DISABLE


@pytest.mark.django_db
def test_status_enqueues_without_changing_desired_state(staff_client, interlock):
    client, _ = staff_client
    original = interlock.desired_state
    resp = client.post(f"/api/interlocks/{interlock.pk}/status/")
    assert resp.status_code == 201, resp.content

    interlock.refresh_from_db()
    assert interlock.desired_state == original
    cmd = InterlockCommand.objects.get(interlock=interlock)
    assert cmd.action == InterlockCommand.ACTION_STATUS


# --------------------------------------------------------------------------
# Pi-executor command-queue: auth required + returns decrypted creds
# --------------------------------------------------------------------------
@pytest.mark.django_db
def test_command_queue_requires_auth(api_client, interlock):
    InterlockCommand.objects.create(interlock=interlock, action=InterlockCommand.ACTION_ENABLE)
    resp = api_client.get("/api/interlocks/command-queue/")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_command_queue_rejects_wrong_token(api_client, interlock, settings):
    settings.INTERLOCK_DAEMON_TOKEN = DAEMON_TOKEN
    InterlockCommand.objects.create(interlock=interlock, action=InterlockCommand.ACTION_ENABLE)
    resp = api_client.get("/api/interlocks/command-queue/", HTTP_X_INTERLOCK_TOKEN="wrong")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_command_queue_returns_creds_and_marks_claimed(api_client, interlock, settings):
    settings.INTERLOCK_DAEMON_TOKEN = DAEMON_TOKEN
    cmd = InterlockCommand.objects.create(
        interlock=interlock, action=InterlockCommand.ACTION_DISABLE
    )
    resp = api_client.get("/api/interlocks/command-queue/", HTTP_X_INTERLOCK_TOKEN=DAEMON_TOKEN)
    assert resp.status_code == 200, resp.content
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["command_id"] == cmd.id
    assert row["action"] == "disable"
    assert row["host"] == "10.0.0.5"
    assert row["port"] == 22
    assert row["username"] == "root"
    assert row["password"] == "s3cr3t-pass"  # decrypted only for the daemon
    assert row["service_name"] == "keymaster.service"
    assert row["relay_pin"] == 17
    assert row["relay_interface"] == "pigpio"

    # Handed out => marked claimed so a second poll won't re-run it.
    cmd.refresh_from_db()
    assert cmd.state == InterlockCommand.STATE_CLAIMED
    assert cmd.claimed_at is not None

    second = api_client.get("/api/interlocks/command-queue/", HTTP_X_INTERLOCK_TOKEN=DAEMON_TOKEN)
    assert second.json() == []


@pytest.mark.django_db
def test_command_queue_fail_closed_when_token_unset(api_client, interlock, settings):
    settings.INTERLOCK_DAEMON_TOKEN = ""
    resp = api_client.get("/api/interlocks/command-queue/", HTTP_X_INTERLOCK_TOKEN="anything")
    assert resp.status_code == 401


# --------------------------------------------------------------------------
# Pi-executor report: updates command + interlock telemetry
# --------------------------------------------------------------------------
@pytest.mark.django_db
def test_report_updates_command_and_interlock(api_client, interlock, settings):
    settings.INTERLOCK_DAEMON_TOKEN = DAEMON_TOKEN
    cmd = InterlockCommand.objects.create(
        interlock=interlock,
        action=InterlockCommand.ACTION_DISABLE,
        state=InterlockCommand.STATE_CLAIMED,
    )
    resp = api_client.post(
        f"/api/interlocks/commands/{cmd.pk}/report/",
        {
            "success": True,
            "result_text": "relay forced off; service stopped",
            "state": "disabled",
            "in_use": False,
            "online": True,
        },
        format="json",
        HTTP_X_INTERLOCK_TOKEN=DAEMON_TOKEN,
    )
    assert resp.status_code == 200, resp.content

    cmd.refresh_from_db()
    assert cmd.state == InterlockCommand.STATE_DONE
    assert cmd.success is True
    assert cmd.result_text == "relay forced off; service stopped"
    assert cmd.completed_at is not None

    interlock.refresh_from_db()
    assert interlock.last_reported_state == "disabled"
    assert interlock.in_use is False
    assert interlock.online is True
    assert interlock.last_seen_at is not None


@pytest.mark.django_db
def test_report_failure_marks_failed(api_client, interlock, settings):
    settings.INTERLOCK_DAEMON_TOKEN = DAEMON_TOKEN
    cmd = InterlockCommand.objects.create(
        interlock=interlock,
        action=InterlockCommand.ACTION_ENABLE,
        state=InterlockCommand.STATE_CLAIMED,
    )
    resp = api_client.post(
        f"/api/interlocks/commands/{cmd.pk}/report/",
        {"success": False, "result_text": "ssh timeout"},
        format="json",
        HTTP_X_INTERLOCK_TOKEN=DAEMON_TOKEN,
    )
    assert resp.status_code == 200, resp.content

    cmd.refresh_from_db()
    assert cmd.state == InterlockCommand.STATE_FAILED
    assert cmd.success is False
    interlock.refresh_from_db()
    # No explicit online flag + failure => device treated as offline.
    assert interlock.online is False


@pytest.mark.django_db
def test_report_requires_auth(api_client, interlock):
    cmd = InterlockCommand.objects.create(
        interlock=interlock, action=InterlockCommand.ACTION_STATUS
    )
    resp = api_client.post(
        f"/api/interlocks/commands/{cmd.pk}/report/",
        {"success": True},
        format="json",
    )
    assert resp.status_code == 401
