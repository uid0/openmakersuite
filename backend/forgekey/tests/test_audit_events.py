"""Tests for ForgeKey audit-event emission hooks."""

from __future__ import annotations

from unittest import mock

from django.urls import reverse

import pytest

from forgekey.audit import record_event
from forgekey.models import AssetAuthorization, DeviceLockout, ForgeKeyAuditEvent
from forgekey.services.firmware_dispatch import publish_firmware_update
from forgekey.services.ota_dispatch import publish_ota_trigger
from forgekey.tests.factories import (
    AssetAuthorizationFactory,
    DeviceFirmwareUpdateFactory,
    DeviceLockoutFactory,
    ESP32DeviceFactory,
    FirmwareVersionFactory,
    UserFactory,
)
from inventory.tests.factories import AssetFactory

pytestmark = pytest.mark.django_db


class TestAuthorizationAuditEvents:
    def test_authorization_grant_records_audit_event(self, api_client):
        actor = UserFactory()
        target_user = UserFactory()
        asset = AssetFactory()
        api_client.force_authenticate(user=actor)

        response = api_client.post(
            reverse("forgekey:asset-authorization-list"),
            {"asset": asset.id, "user": target_user.id, "notes": "approved for shop access"},
            format="json",
        )

        assert response.status_code == 201, response.content
        assert ForgeKeyAuditEvent.objects.count() == 1

        authorization = AssetAuthorization.objects.get(asset=asset, user=target_user)
        event = ForgeKeyAuditEvent.objects.get(action=ForgeKeyAuditEvent.ACTION_AUTHORIZATION_GRANT)
        assert event.actor == actor
        assert event.authorization == authorization
        assert event.asset == asset

    def test_authorization_revoke_records_audit_event(self, api_client):
        actor = UserFactory()
        authorization = AssetAuthorizationFactory(is_active=True)
        api_client.force_authenticate(user=actor)

        response = api_client.post(
            reverse("forgekey:asset-authorization-revoke", kwargs={"pk": authorization.pk}),
            {"notes": "no longer authorized"},
            format="json",
        )

        assert response.status_code == 200, response.content
        authorization.refresh_from_db()
        assert authorization.is_active is False
        assert ForgeKeyAuditEvent.objects.count() == 1

        event = ForgeKeyAuditEvent.objects.get(
            action=ForgeKeyAuditEvent.ACTION_AUTHORIZATION_REVOKE
        )
        assert event.actor == actor
        assert event.authorization == authorization
        assert event.asset == authorization.asset
        assert event.notes == "no longer authorized"

    def test_revoke_already_inactive_returns_400(self, api_client):
        actor = UserFactory()
        authorization = AssetAuthorizationFactory(is_active=False)
        api_client.force_authenticate(user=actor)

        response = api_client.post(
            reverse("forgekey:asset-authorization-revoke", kwargs={"pk": authorization.pk}),
            {"notes": "should not matter"},
            format="json",
        )

        assert response.status_code == 400, response.content
        assert ForgeKeyAuditEvent.objects.count() == 0

    def test_revoke_unauthenticated_returns_401(self, api_client):
        authorization = AssetAuthorizationFactory(is_active=True)

        response = api_client.post(
            reverse("forgekey:asset-authorization-revoke", kwargs={"pk": authorization.pk}),
            {"notes": "anonymous revoke"},
            format="json",
        )

        assert response.status_code in {401, 403}, response.content
        assert ForgeKeyAuditEvent.objects.count() == 0


class TestLockoutAuditEvents:
    def test_lockout_create_records_audit_event(self, api_client):
        actor = UserFactory()
        asset = AssetFactory()
        api_client.force_authenticate(user=actor)

        response = api_client.post(
            reverse("forgekey:device-lockout-list"),
            {"asset": asset.id, "reason": "safety inspection in progress"},
            format="json",
        )

        assert response.status_code == 201, response.content
        assert ForgeKeyAuditEvent.objects.count() == 1

        lockout = DeviceLockout.objects.get(asset=asset)
        event = ForgeKeyAuditEvent.objects.get(action=ForgeKeyAuditEvent.ACTION_LOCKOUT_CREATE)
        assert event.actor == actor
        assert event.lockout == lockout
        assert event.asset == asset
        assert event.metadata["lockout_level"] == lockout.lockout_level

    def test_lockout_unlock_records_audit_event(self, api_client):
        actor = UserFactory()
        lockout = DeviceLockoutFactory(locked_by=actor)
        api_client.force_authenticate(user=actor)

        response = api_client.post(
            reverse("forgekey:device-lockout-unlock", kwargs={"pk": lockout.pk}),
            format="json",
        )

        assert response.status_code == 200, response.content
        lockout.refresh_from_db()
        assert lockout.is_active is False
        assert ForgeKeyAuditEvent.objects.count() == 1

        event = ForgeKeyAuditEvent.objects.get(action=ForgeKeyAuditEvent.ACTION_LOCKOUT_UNLOCK)
        assert event.actor == actor
        assert event.lockout == lockout
        assert event.asset == lockout.asset
        assert event.metadata["lockout_level"] == lockout.lockout_level


class TestFirmwareAuditEvents:
    def test_firmware_request_via_dispatch_records_audit_event(self):
        actor = UserFactory()
        device = ESP32DeviceFactory()
        firmware = FirmwareVersionFactory(device_type=device.device_type, version="9.9.1")
        client = mock.MagicMock()
        client.publish.return_value = mock.MagicMock(rc=0)

        with mock.patch("forgekey.services.firmware_dispatch.get_mqtt_client", return_value=client):
            records = publish_firmware_update(device, firmware, requested_by=actor)

        assert len(records) == 1
        assert ForgeKeyAuditEvent.objects.count() == 1

        event = ForgeKeyAuditEvent.objects.get(action=ForgeKeyAuditEvent.ACTION_FIRMWARE_REQUEST)
        assert event.actor == actor
        assert event.device == device
        assert event.firmware_update == records[0]
        assert event.metadata["dispatch_path"] == "mqtt"
        assert event.metadata["firmware_version"] == firmware.version

    def test_firmware_request_via_ota_records_audit_event(self):
        actor = UserFactory()
        device = ESP32DeviceFactory()
        firmware = FirmwareVersionFactory(device_type=device.device_type, version="9.9.2")
        client = mock.MagicMock()
        client.publish.return_value = mock.MagicMock(rc=0)

        with mock.patch("forgekey.tasks.get_mqtt_client", return_value=client):
            record = publish_ota_trigger(device, firmware, requested_by=actor)

        assert ForgeKeyAuditEvent.objects.count() == 1

        event = ForgeKeyAuditEvent.objects.get(action=ForgeKeyAuditEvent.ACTION_FIRMWARE_REQUEST)
        assert event.actor == actor
        assert event.device == device
        assert event.firmware_update == record
        assert event.metadata["dispatch_path"] == "ota"
        assert event.metadata["firmware_version"] == firmware.version

    def test_record_event_with_anonymous_actor_creates_row_with_null_actor(self):
        update = DeviceFirmwareUpdateFactory(requested_by=None)

        event = record_event(
            action=ForgeKeyAuditEvent.ACTION_FIRMWARE_REQUEST,
            actor=None,
            firmware_update=update,
            device=update.device,
        )

        assert ForgeKeyAuditEvent.objects.count() == 1
        assert event.actor is None
        assert event.firmware_update == update
        assert event.device == update.device

    def test_record_event_derives_asset_from_authorization(self):
        actor = UserFactory()
        authorization = AssetAuthorizationFactory()

        event = record_event(
            action=ForgeKeyAuditEvent.ACTION_AUTHORIZATION_GRANT,
            actor=actor,
            authorization=authorization,
        )

        assert ForgeKeyAuditEvent.objects.count() == 1
        assert event.actor == actor
        assert event.authorization == authorization
        assert event.asset == authorization.asset


class TestAccessLogPermissions:
    """The access log is staff-only (op-2se): ``IsAdminUser`` on
    ``ForgeKeyAuditEventViewSet``. A plain authenticated member must not be able
    to read who was granted or denied access — only staff/admins can."""

    def _event(self, actor=None):
        return record_event(
            action=ForgeKeyAuditEvent.ACTION_ACCESS_GRANTED,
            actor=actor,
            asset=AssetFactory(),
        )

    def test_non_staff_member_forbidden_from_list(self, api_client):
        member = UserFactory(is_staff=False)
        self._event(actor=member)
        api_client.force_authenticate(user=member)

        response = api_client.get(reverse("forgekey:access-log-list"))

        assert response.status_code == 403, response.content

    def test_non_staff_member_forbidden_from_retrieve(self, api_client):
        member = UserFactory(is_staff=False)
        event = self._event(actor=member)
        api_client.force_authenticate(user=member)

        response = api_client.get(reverse("forgekey:access-log-detail", kwargs={"pk": event.pk}))

        assert response.status_code == 403, response.content

    def test_staff_user_can_list(self, api_client):
        staff = UserFactory(is_staff=True)
        self._event(actor=staff)
        api_client.force_authenticate(user=staff)

        response = api_client.get(reverse("forgekey:access-log-list"))

        assert response.status_code == 200, response.content

    def test_staff_user_can_retrieve(self, api_client):
        staff = UserFactory(is_staff=True)
        event = self._event(actor=staff)
        api_client.force_authenticate(user=staff)

        response = api_client.get(reverse("forgekey:access-log-detail", kwargs={"pk": event.pk}))

        assert response.status_code == 200, response.content

    def test_unauthenticated_request_denied(self, api_client):
        self._event()

        response = api_client.get(reverse("forgekey:access-log-list"))

        assert response.status_code in {401, 403}, response.content
