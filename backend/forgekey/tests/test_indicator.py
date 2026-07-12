"""Tests for the indicator status → presentation backend (epic ga-72l / op-1n8).

Covers:
  - the status → presentation mapping (exact ga-72l payloads)
  - ``derive_asset_status`` precedence for every status
  - ``derive_room_status`` direct mapping
  - ``sync_indicator`` dispatch + debounce + audit
  - binding asset-XOR-room + indicator-device validation (model, DB, API)
  - bind / unbind / sync / room-mode / indicator-test endpoints
  - signal-triggered reactive dispatch
  - device online/offline transition re-sync (consumer + webhook task)
"""

from __future__ import annotations

import json
from unittest.mock import patch

from django.db import IntegrityError, transaction

import pytest
from rest_framework.test import APIClient

from forgekey.management.commands.mqtt_consumer import handle_status_message
from forgekey.models import (
    DeviceCommand,
    ForgeKeyAuditEvent,
    IndicatorBinding,
    IndicatorStatus,
    OperationalMode,
    RoomOperationalMode,
)
from forgekey.services.indicator import (
    build_payload,
    build_test_payload,
    derive_asset_status,
    derive_room_status,
    presentation_for_status,
    status_for_binding,
    sync_bindings_for_device,
    sync_indicator,
)
from forgekey.tasks import process_mqtt_status_message
from forgekey.tests.factories import (
    AssetDeviceFactory,
    DeviceLockoutFactory,
    DeviceUsageFactory,
    ESP32DeviceFactory,
    IndicatorBindingFactory,
    IndicatorDeviceFactory,
    OperationalModeFactory,
    RoomOperationalModeFactory,
)
from forgekey.utils import get_mqtt_status_topic
from inventory.models import Asset
from inventory.tests.factories import AssetFactory, LocationFactory

pytestmark = pytest.mark.django_db


# Canonical ga-72l payloads — one per status.
EXPECTED_PAYLOADS = {
    IndicatorStatus.AVAILABLE: {
        "cmd": "set_indicator",
        "color": "green",
        "brightness": "low",
        "pattern": "solid",
        "indicator": "green",
    },
    IndicatorStatus.IN_USE: {
        "cmd": "set_indicator",
        "color": "green",
        "brightness": "high",
        "pattern": "solid",
        "indicator": "green",
    },
    IndicatorStatus.UNAVAILABLE: {
        "cmd": "set_indicator",
        "color": "red",
        "brightness": "low",
        "pattern": "solid",
        "indicator": "red",
    },
    IndicatorStatus.LOCKED_OUT: {
        "cmd": "set_indicator",
        "pattern": "off",
        "indicator": "off",
    },
    IndicatorStatus.CLASSROOM: {
        "cmd": "set_indicator",
        "color": "purple",
        "brightness": "high",
        "pattern": "slow_blink",
        "period_ms": 1500,
        "indicator": "blue",
    },
}


@pytest.fixture(autouse=True)
def mock_publish():
    """Stub the broker publish for every test so no real MQTT I/O happens.

    Patches the name used by ``dispatch_command`` (sync + test paths). Tests
    that assert dispatch take this fixture as a parameter.
    """
    with patch(
        "forgekey.services.device_commands.publish_command",
        return_value="forgekey/x/command",
    ) as mocked:
        yield mocked


@pytest.fixture
def admin_api_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


def _binding_url(suffix=""):
    return f"/api/forgekey/indicator-bindings/{suffix}"


def _last_payload(mock_publish):
    """The set_indicator payload from the most recent dispatch."""
    _device, payload = mock_publish.call_args.args
    return payload


# ---------------------------------------------------------------------------
# Presentation mapping
# ---------------------------------------------------------------------------
class TestPresentationMapping:
    @pytest.mark.parametrize("status,expected", list(EXPECTED_PAYLOADS.items()))
    def test_build_payload_matches_canonical(self, status, expected):
        assert build_payload(status) == expected

    def test_presentation_for_status_keys(self):
        presentation = presentation_for_status(IndicatorStatus.CLASSROOM)
        assert presentation == {
            "color": "purple",
            "brightness": "high",
            "pattern": "slow_blink",
            "period_ms": 1500,
        }

    def test_presentation_for_status_returns_copy(self):
        presentation_for_status(IndicatorStatus.AVAILABLE)["color"] = "tampered"
        assert presentation_for_status(IndicatorStatus.AVAILABLE)["color"] == "green"

    def test_unknown_status_raises(self):
        with pytest.raises(ValueError):
            presentation_for_status("nonsense")

    def test_locked_out_omits_color_and_brightness(self):
        payload = build_payload(IndicatorStatus.LOCKED_OUT)
        assert "color" not in payload and "brightness" not in payload
        assert payload["pattern"] == "off"


# ---------------------------------------------------------------------------
# Asset status derivation + precedence
# ---------------------------------------------------------------------------
class TestDeriveAssetStatus:
    def test_default_active_asset_is_available(self):
        asset = AssetFactory(status=Asset.Status.ACTIVE)
        assert derive_asset_status(asset) == IndicatorStatus.AVAILABLE

    def test_open_usage_is_in_use(self):
        asset = AssetFactory()
        DeviceUsageFactory(asset=asset, ended_at=None)
        assert derive_asset_status(asset) == IndicatorStatus.IN_USE

    def test_locked_out_mode(self):
        asset = AssetFactory()
        OperationalModeFactory(asset=asset, mode=OperationalMode.MODE_LOCKED_OUT)
        assert derive_asset_status(asset) == IndicatorStatus.LOCKED_OUT

    def test_active_device_lockout(self):
        asset = AssetFactory()
        DeviceLockoutFactory(asset=asset, is_active=True)
        assert derive_asset_status(asset) == IndicatorStatus.LOCKED_OUT

    def test_inactive_lockout_does_not_lock(self):
        asset = AssetFactory()
        DeviceLockoutFactory(asset=asset, is_active=False)
        assert derive_asset_status(asset) == IndicatorStatus.AVAILABLE

    def test_classroom_mode(self):
        asset = AssetFactory()
        OperationalModeFactory(asset=asset, mode=OperationalMode.MODE_CLASSROOM)
        assert derive_asset_status(asset) == IndicatorStatus.CLASSROOM

    def test_maintenance_mode_is_unavailable(self):
        asset = AssetFactory()
        OperationalModeFactory(asset=asset, mode=OperationalMode.MODE_MAINTENANCE)
        assert derive_asset_status(asset) == IndicatorStatus.UNAVAILABLE

    def test_non_active_asset_is_unavailable(self):
        asset = AssetFactory(status=Asset.Status.MAINTENANCE)
        assert derive_asset_status(asset) == IndicatorStatus.UNAVAILABLE

    def test_offline_primary_device_is_unavailable(self):
        asset = AssetFactory(status=Asset.Status.ACTIVE)
        offline = ESP32DeviceFactory(is_online=False)
        AssetDeviceFactory(asset=asset, device=offline, is_primary=True)
        assert derive_asset_status(asset) == IndicatorStatus.UNAVAILABLE

    def test_online_primary_device_stays_available(self):
        asset = AssetFactory(status=Asset.Status.ACTIVE)
        online = ESP32DeviceFactory(is_online=True)
        AssetDeviceFactory(asset=asset, device=online, is_primary=True)
        assert derive_asset_status(asset) == IndicatorStatus.AVAILABLE

    def test_precedence_lockout_beats_classroom(self):
        asset = AssetFactory()
        OperationalModeFactory(asset=asset, mode=OperationalMode.MODE_LOCKED_OUT)
        DeviceUsageFactory(asset=asset, ended_at=None)
        assert derive_asset_status(asset) == IndicatorStatus.LOCKED_OUT

    def test_precedence_active_lockout_beats_usage(self):
        asset = AssetFactory()
        DeviceLockoutFactory(asset=asset, is_active=True)
        DeviceUsageFactory(asset=asset, ended_at=None)
        assert derive_asset_status(asset) == IndicatorStatus.LOCKED_OUT

    def test_precedence_classroom_beats_usage(self):
        asset = AssetFactory()
        OperationalModeFactory(asset=asset, mode=OperationalMode.MODE_CLASSROOM)
        DeviceUsageFactory(asset=asset, ended_at=None)
        assert derive_asset_status(asset) == IndicatorStatus.CLASSROOM

    def test_precedence_usage_beats_unavailable(self):
        # in_use (step 3) wins over a non-ACTIVE status (step 4).
        asset = AssetFactory(status=Asset.Status.MAINTENANCE)
        DeviceUsageFactory(asset=asset, ended_at=None)
        assert derive_asset_status(asset) == IndicatorStatus.IN_USE

    def test_closed_usage_is_not_in_use(self):
        from django.utils import timezone

        asset = AssetFactory()
        DeviceUsageFactory(asset=asset, ended_at=timezone.now())
        assert derive_asset_status(asset) == IndicatorStatus.AVAILABLE


# ---------------------------------------------------------------------------
# Room status derivation
# ---------------------------------------------------------------------------
class TestDeriveRoomStatus:
    def test_unset_room_defaults_available(self):
        location = LocationFactory()
        assert derive_room_status(location) == IndicatorStatus.AVAILABLE

    @pytest.mark.parametrize(
        "mode",
        [
            IndicatorStatus.AVAILABLE,
            IndicatorStatus.IN_USE,
            IndicatorStatus.CLASSROOM,
            IndicatorStatus.LOCKED_OUT,
            IndicatorStatus.UNAVAILABLE,
        ],
    )
    def test_room_mode_maps_directly(self, mode):
        location = LocationFactory()
        RoomOperationalModeFactory(location=location, mode=mode)
        assert derive_room_status(location) == mode


# ---------------------------------------------------------------------------
# sync_indicator
# ---------------------------------------------------------------------------
class TestSyncIndicator:
    def test_sync_dispatches_available_payload(self, mock_publish):
        binding = IndicatorBindingFactory()
        record = sync_indicator(binding)

        assert record is not None
        payload = _last_payload(mock_publish)
        assert payload["cmd"] == "set_indicator"
        assert payload["color"] == "green"
        assert payload["brightness"] == "low"
        assert payload["pattern"] == "solid"
        binding.refresh_from_db()
        assert binding.last_status == IndicatorStatus.AVAILABLE
        assert binding.last_presentation["pattern"] == "solid"
        assert binding.last_synced_at is not None

    def test_sync_creates_device_command_row(self, mock_publish):
        binding = IndicatorBindingFactory()
        sync_indicator(binding)
        assert DeviceCommand.objects.filter(device=binding.device, command="set_indicator").exists()

    def test_sync_records_audit_event(self, mock_publish):
        binding = IndicatorBindingFactory()
        sync_indicator(binding)
        event = ForgeKeyAuditEvent.objects.get(action=ForgeKeyAuditEvent.ACTION_INDICATOR_SYNC)
        assert event.device_id == binding.device_id
        assert event.metadata["status"] == IndicatorStatus.AVAILABLE

    def test_debounce_skips_unchanged(self, mock_publish):
        binding = IndicatorBindingFactory()
        sync_indicator(binding)
        assert mock_publish.call_count == 1
        # No status change → second sync is a no-op.
        assert sync_indicator(binding) is None
        assert mock_publish.call_count == 1

    def test_force_overrides_debounce(self, mock_publish):
        binding = IndicatorBindingFactory()
        sync_indicator(binding)
        assert sync_indicator(binding, force=True) is not None
        assert mock_publish.call_count == 2

    def test_room_binding_uses_room_mode(self, mock_publish):
        location = LocationFactory()
        RoomOperationalModeFactory(location=location, mode=IndicatorStatus.CLASSROOM)
        binding = IndicatorBindingFactory(asset=None, location=location)
        sync_indicator(binding)
        payload = _last_payload(mock_publish)
        assert payload["color"] == "purple"
        assert payload["pattern"] == "slow_blink"
        assert payload["period_ms"] == 1500

    def test_status_for_binding_room(self):
        location = LocationFactory()
        RoomOperationalModeFactory(location=location, mode=IndicatorStatus.UNAVAILABLE)
        binding = IndicatorBindingFactory(asset=None, location=location)
        assert status_for_binding(binding) == IndicatorStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# Binding validation (model + DB + API)
# ---------------------------------------------------------------------------
class TestBindingValidation:
    def test_model_clean_rejects_both_targets(self):
        from django.core.exceptions import ValidationError

        binding = IndicatorBinding(
            device=IndicatorDeviceFactory(), asset=AssetFactory(), location=LocationFactory()
        )
        with pytest.raises(ValidationError):
            binding.clean()

    def test_model_clean_rejects_neither_target(self):
        from django.core.exceptions import ValidationError

        binding = IndicatorBinding(device=IndicatorDeviceFactory())
        with pytest.raises(ValidationError):
            binding.clean()

    def test_model_clean_rejects_non_indicator_device(self):
        from django.core.exceptions import ValidationError

        binding = IndicatorBinding(device=ESP32DeviceFactory(), asset=AssetFactory())
        with pytest.raises(ValidationError):
            binding.clean()

    def test_db_constraint_rejects_both_targets(self):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                IndicatorBinding.objects.create(
                    device=IndicatorDeviceFactory(),
                    asset=AssetFactory(),
                    location=LocationFactory(),
                )


# ---------------------------------------------------------------------------
# Binding API
# ---------------------------------------------------------------------------
class TestBindingAPI:
    def test_create_asset_binding_syncs(self, admin_api_client, mock_publish):
        device = IndicatorDeviceFactory()
        asset = AssetFactory()
        response = admin_api_client.post(
            _binding_url(),
            data={"device": str(device.id), "asset": str(asset.id)},
            format="json",
        )
        assert response.status_code == 201, response.data
        assert IndicatorBinding.objects.filter(device=device, asset=asset).exists()
        # Bind pushes the initial state + records an audit event.
        assert mock_publish.called
        assert ForgeKeyAuditEvent.objects.filter(
            action=ForgeKeyAuditEvent.ACTION_INDICATOR_BIND
        ).exists()

    def test_create_room_binding(self, admin_api_client):
        device = IndicatorDeviceFactory()
        location = LocationFactory()
        response = admin_api_client.post(
            _binding_url(),
            data={"device": str(device.id), "location": location.id},
            format="json",
        )
        assert response.status_code == 201, response.data

    def test_create_rejects_both_targets(self, admin_api_client):
        response = admin_api_client.post(
            _binding_url(),
            data={
                "device": str(IndicatorDeviceFactory().id),
                "asset": str(AssetFactory().id),
                "location": LocationFactory().id,
            },
            format="json",
        )
        assert response.status_code == 400

    def test_create_rejects_neither_target(self, admin_api_client):
        response = admin_api_client.post(
            _binding_url(),
            data={"device": str(IndicatorDeviceFactory().id)},
            format="json",
        )
        assert response.status_code == 400

    def test_create_rejects_non_indicator_device(self, admin_api_client):
        response = admin_api_client.post(
            _binding_url(),
            data={"device": str(ESP32DeviceFactory().id), "asset": str(AssetFactory().id)},
            format="json",
        )
        assert response.status_code == 400

    def test_list_filter_by_device(self, admin_api_client):
        binding = IndicatorBindingFactory()
        IndicatorBindingFactory()
        response = admin_api_client.get(_binding_url(), {"device": str(binding.device_id)})
        assert response.status_code == 200
        results = response.json()
        results = results["results"] if isinstance(results, dict) else results
        assert len(results) == 1
        assert results[0]["device"] == str(binding.device_id)

    def test_sync_action_dispatches(self, admin_api_client, mock_publish):
        binding = IndicatorBindingFactory()
        response = admin_api_client.post(_binding_url(f"{binding.id}/sync/"))
        assert response.status_code == 200, response.data
        assert response.json()["status"] == IndicatorStatus.AVAILABLE
        assert mock_publish.called

    def test_unbind_records_audit(self, admin_api_client):
        binding = IndicatorBindingFactory()
        device_id = binding.device_id
        response = admin_api_client.delete(_binding_url(f"{binding.id}/"))
        assert response.status_code == 204
        assert not IndicatorBinding.objects.filter(id=binding.id).exists()
        assert ForgeKeyAuditEvent.objects.filter(
            action=ForgeKeyAuditEvent.ACTION_INDICATOR_UNBIND, device_id=device_id
        ).exists()


# ---------------------------------------------------------------------------
# Indicator-test endpoint (explicit preview, device-keyed)
# ---------------------------------------------------------------------------
class TestIndicatorTestEndpoint:
    def _url(self, device):
        return f"/api/forgekey/devices/{device.id}/indicator/test/"

    def test_explicit_preview_dispatches(self, admin_api_client, mock_publish):
        device = IndicatorDeviceFactory()
        response = admin_api_client.post(
            self._url(device),
            data={"color": "purple", "brightness": "high", "pattern": "slow_blink"},
            format="json",
        )
        assert response.status_code == 200, response.data
        payload = _last_payload(mock_publish)
        assert payload["color"] == "purple"
        assert payload["brightness"] == "high"
        assert payload["pattern"] == "slow_blink"
        assert payload["cmd"] == "set_indicator"

    def test_period_ms_and_rgb_color(self, admin_api_client, mock_publish):
        device = IndicatorDeviceFactory()
        response = admin_api_client.post(
            self._url(device),
            data={"color": [255, 0, 255], "pattern": "blink", "period_ms": 500},
            format="json",
        )
        assert response.status_code == 200, response.data
        payload = _last_payload(mock_publish)
        assert payload["color"] == [255, 0, 255]
        assert payload["period_ms"] == 500

    def test_records_audit_event(self, admin_api_client):
        device = IndicatorDeviceFactory()
        admin_api_client.post(self._url(device), data={"pattern": "off"}, format="json")
        assert ForgeKeyAuditEvent.objects.filter(
            action=ForgeKeyAuditEvent.ACTION_INDICATOR_TEST, device=device
        ).exists()

    def test_invalid_pattern_is_400(self, admin_api_client):
        device = IndicatorDeviceFactory()
        response = admin_api_client.post(
            self._url(device), data={"pattern": "strobe"}, format="json"
        )
        assert response.status_code == 400

    def test_invalid_brightness_is_400(self, admin_api_client):
        device = IndicatorDeviceFactory()
        response = admin_api_client.post(
            self._url(device), data={"brightness": "blinding"}, format="json"
        )
        assert response.status_code == 400

    def test_empty_preview_is_400(self, admin_api_client):
        device = IndicatorDeviceFactory()
        response = admin_api_client.post(self._url(device), data={}, format="json")
        assert response.status_code == 400

    def test_requires_admin(self, authenticated_client):
        client, _user = authenticated_client
        device = IndicatorDeviceFactory()
        response = client.post(self._url(device), data={"pattern": "off"}, format="json")
        assert response.status_code == 403


class TestBuildTestPayload:
    def test_brightness_word(self):
        assert build_test_payload(brightness="high")["brightness"] == "high"

    def test_brightness_int(self):
        assert build_test_payload(brightness=200)["brightness"] == 200

    def test_brightness_out_of_range(self):
        with pytest.raises(ValueError):
            build_test_payload(brightness=999)

    def test_rgb_wrong_length(self):
        with pytest.raises(ValueError):
            build_test_payload(color=[1, 2])

    def test_rgb_component_out_of_range(self):
        with pytest.raises(ValueError):
            build_test_payload(color=[0, 0, 300])

    def test_numeric_string_brightness(self):
        assert build_test_payload(brightness="128")["brightness"] == 128

    def test_bool_brightness_rejected(self):
        with pytest.raises(ValueError):
            build_test_payload(brightness=True)

    def test_empty_color_string_rejected(self):
        with pytest.raises(ValueError):
            build_test_payload(color="   ")

    def test_invalid_color_type_rejected(self):
        with pytest.raises(ValueError):
            build_test_payload(color=123)

    def test_non_int_period_ms_rejected(self):
        with pytest.raises(ValueError):
            build_test_payload(pattern="blink", period_ms="abc")

    def test_bool_period_ms_rejected(self):
        with pytest.raises(ValueError):
            build_test_payload(pattern="blink", period_ms=True)

    def test_requires_at_least_one_field(self):
        with pytest.raises(ValueError):
            build_test_payload()


# ---------------------------------------------------------------------------
# Room operational mode API
# ---------------------------------------------------------------------------
class TestRoomOperationalModeAPI:
    def test_create_room_mode(self, admin_api_client):
        location = LocationFactory()
        response = admin_api_client.post(
            "/api/forgekey/room-operational-modes/",
            data={"location": location.id, "mode": IndicatorStatus.CLASSROOM},
            format="json",
        )
        assert response.status_code == 201, response.data
        mode = RoomOperationalMode.objects.get(location=location)
        assert mode.mode == IndicatorStatus.CLASSROOM

    def test_update_sets_updated_by(self, admin_api_client, admin_user):
        mode = RoomOperationalModeFactory(mode=IndicatorStatus.AVAILABLE)
        response = admin_api_client.patch(
            f"/api/forgekey/room-operational-modes/{mode.id}/",
            data={"mode": IndicatorStatus.LOCKED_OUT},
            format="json",
        )
        assert response.status_code == 200, response.data
        mode.refresh_from_db()
        assert mode.mode == IndicatorStatus.LOCKED_OUT
        assert mode.updated_by_id == admin_user.id

    def test_filter_by_location(self, admin_api_client):
        mode = RoomOperationalModeFactory()
        RoomOperationalModeFactory()
        response = admin_api_client.get(
            "/api/forgekey/room-operational-modes/", {"location": mode.location_id}
        )
        assert response.status_code == 200
        results = response.json()
        results = results["results"] if isinstance(results, dict) else results
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Signal-triggered reactive dispatch
# ---------------------------------------------------------------------------
class TestSignals:
    def test_operational_mode_change_pushes(self, mock_publish):
        binding = IndicatorBindingFactory()
        mock_publish.reset_mock()
        OperationalModeFactory(asset=binding.asset, mode=OperationalMode.MODE_LOCKED_OUT)
        assert mock_publish.called
        assert _last_payload(mock_publish)["pattern"] == "off"

    def test_device_lockout_pushes(self, mock_publish):
        binding = IndicatorBindingFactory()
        mock_publish.reset_mock()
        DeviceLockoutFactory(asset=binding.asset, is_active=True)
        assert mock_publish.called
        assert _last_payload(mock_publish)["pattern"] == "off"

    def test_usage_start_and_end_push(self, mock_publish):
        binding = IndicatorBindingFactory()
        mock_publish.reset_mock()
        usage = DeviceUsageFactory(asset=binding.asset, ended_at=None)
        assert _last_payload(mock_publish)["brightness"] == "high"  # in use

        mock_publish.reset_mock()
        usage.end_session()
        # Back to available (low green).
        assert _last_payload(mock_publish)["brightness"] == "low"

    def test_asset_status_change_pushes(self, mock_publish):
        binding = IndicatorBindingFactory()
        sync_indicator(binding, force=True)  # prime last_presentation = available
        mock_publish.reset_mock()
        binding.asset.status = Asset.Status.MAINTENANCE
        binding.asset.save()
        assert mock_publish.called
        assert _last_payload(mock_publish)["color"] == "red"  # unavailable

    def test_room_mode_change_pushes(self, mock_publish):
        location = LocationFactory()
        IndicatorBindingFactory(asset=None, location=location)
        mock_publish.reset_mock()
        RoomOperationalModeFactory(location=location, mode=IndicatorStatus.LOCKED_OUT)
        assert mock_publish.called
        assert _last_payload(mock_publish)["pattern"] == "off"

    def test_no_binding_no_dispatch(self, mock_publish):
        # A status-source change on an unbound asset must not dispatch anything.
        asset = AssetFactory()
        OperationalModeFactory(asset=asset, mode=OperationalMode.MODE_LOCKED_OUT)
        assert not mock_publish.called

    def test_broker_outage_does_not_break_save(self):
        # A signal-triggered sync must swallow broker errors so the triggering
        # save still succeeds (_safe_sync). The binding stays un-synced.
        from forgekey.services.device_commands import DeviceCommandError

        binding = IndicatorBindingFactory()
        with patch(
            "forgekey.services.device_commands.publish_command",
            side_effect=DeviceCommandError("broker down"),
        ):
            OperationalModeFactory(asset=binding.asset, mode=OperationalMode.MODE_LOCKED_OUT)
        binding.refresh_from_db()
        assert binding.last_status == ""  # sync raised before persisting


# ---------------------------------------------------------------------------
# Device online/offline transition re-sync
# ---------------------------------------------------------------------------
class TestDeviceTransition:
    def test_sync_bindings_for_device_targets_asset(self, mock_publish):
        asset = AssetFactory(status=Asset.Status.ACTIVE)
        control = ESP32DeviceFactory(is_online=False)
        AssetDeviceFactory(asset=asset, device=control, is_primary=True)
        binding = IndicatorBindingFactory(asset=asset)
        sync_indicator(binding, force=True)  # offline control → unavailable
        assert binding.last_status == IndicatorStatus.UNAVAILABLE

        # Control device comes online → asset becomes available.
        control.is_online = True
        control.save(update_fields=["is_online"])
        mock_publish.reset_mock()
        sync_bindings_for_device(control)
        binding.refresh_from_db()
        assert binding.last_status == IndicatorStatus.AVAILABLE
        assert mock_publish.called

    def test_webhook_task_transition_resyncs(self, mock_publish):
        asset = AssetFactory(status=Asset.Status.ACTIVE)
        control = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:01", is_online=False)
        AssetDeviceFactory(asset=asset, device=control, is_primary=True)
        binding = IndicatorBindingFactory(asset=asset)
        sync_indicator(binding, force=True)  # unavailable

        mock_publish.reset_mock()
        process_mqtt_status_message(control.mac_address, {"online": True})
        binding.refresh_from_db()
        assert binding.last_status == IndicatorStatus.AVAILABLE

    def test_consumer_transition_resyncs(self, mock_publish):
        asset = AssetFactory(status=Asset.Status.ACTIVE)
        control = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:02", is_online=False)
        AssetDeviceFactory(asset=asset, device=control, is_primary=True)
        binding = IndicatorBindingFactory(asset=asset)
        sync_indicator(binding, force=True)  # unavailable

        mock_publish.reset_mock()
        topic = get_mqtt_status_topic(control.mac_address)
        handle_status_message(topic, json.dumps({"online": True}).encode("utf-8"))
        binding.refresh_from_db()
        assert binding.last_status == IndicatorStatus.AVAILABLE
