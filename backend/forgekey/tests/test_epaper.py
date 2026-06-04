"""Tests for the XIAO 7.5" ePaper PM-display foundation.

Covers:
  - Model: low-battery threshold, default ordering, asset binding.
  - Render service: deterministic ETag, image bytes are valid PNG,
    status-line content reflects MaintenanceItem state.
  - Image endpoint: 200 on first fetch with ETag header, 304 on
    matching If-None-Match, auto-creates an unbound row on first
    contact (409), 404 for a retired display, 409 for an unbound
    display.
  - Battery endpoint: persists percent + timestamp, captures Sentry
    warning when below threshold, ignores non-integer payloads.
  - Bind endpoint: staff-authenticated, sets asset_id (auto-creates
    display row if needed), rejects unauthenticated callers, rejects
    unknown asset_id, rejects retired displays, supports re-bind.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.urls import reverse
from django.utils import timezone

import pytest

from forgekey.models import EPaperDisplay
from forgekey.services.epaper_render import compute_snapshot_etag, render_pm_image
from forgekey.tests.factories import ESP32DeviceFactory
from inventory.models import Asset, Location, MaintenanceItem

pytestmark = pytest.mark.django_db


def _make_item(
    asset: Asset,
    title: str = "Blade tension",
    interval_days: int = 30,
    last_done_days: int | None = None,
) -> MaintenanceItem:
    """A recurring (= preventive) maintenance item, optionally last completed
    ``last_done_days`` ago. ``last_done_days=None`` leaves it never-completed."""
    item = MaintenanceItem.objects.create(asset=asset, title=title, interval_days=interval_days)
    if last_done_days is not None:
        item.last_completed_at = timezone.now() - timedelta(days=last_done_days)
        item.save(update_fields=["last_completed_at"])
    return item


def _make_asset(name: str = "ePaper Asset") -> Asset:
    # Both Location.name and Asset.asset_tag have unique constraints;
    # the auto-generated asset_tag is deterministic enough that two
    # back-to-back calls in the same test collide. Suffix everything
    # with a uuid fragment to keep tests independent.
    suffix = uuid4().hex[:8].upper()
    location = Location.objects.create(name=f"ePaper Loc {suffix}")
    return Asset.objects.create(
        name=name,
        location=location,
        asset_tag=f"TEST-{suffix}",
    )


def _bind_display(asset: Asset | None = None) -> EPaperDisplay:
    device = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:01")
    return EPaperDisplay.objects.create(device=device, asset=asset)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class TestEPaperDisplayModel:
    def test_unreported_battery_is_not_low(self):
        display = _bind_display(_make_asset())
        assert display.battery_percent is None
        assert display.is_low_battery is False

    def test_battery_below_threshold_is_low(self, settings):
        settings.FORGEKEY_EPAPER_LOW_BATTERY_PERCENT = 20
        display = _bind_display(_make_asset())
        display.battery_percent = 15
        assert display.is_low_battery is True

    def test_battery_at_threshold_is_not_low(self, settings):
        # The threshold is the lower bound for "ok" — at exactly 20%
        # the panel still has runway. Below 20% trips the warning.
        settings.FORGEKEY_EPAPER_LOW_BATTERY_PERCENT = 20
        display = _bind_display(_make_asset())
        display.battery_percent = 20
        assert display.is_low_battery is False


# ---------------------------------------------------------------------------
# Render service
# ---------------------------------------------------------------------------


class TestEPaperRender:
    def test_etag_is_stable_for_same_state(self):
        asset = _make_asset("Bandsaw")
        _make_item(asset, "Blade tension", interval_days=30)
        first = compute_snapshot_etag(asset)
        second = compute_snapshot_etag(asset)
        assert first == second

    def test_etag_changes_when_service_logged(self):
        asset = _make_asset("Bandsaw")
        item = _make_item(asset, "Blade tension", interval_days=30)
        before = compute_snapshot_etag(asset)
        item.last_completed_at = timezone.now()
        item.save(update_fields=["last_completed_at"])
        after = compute_snapshot_etag(asset)
        assert before != after

    def test_render_returns_png_bytes(self):
        asset = _make_asset()
        _make_item(asset, "Filter swap", interval_days=90)
        png = render_pm_image(asset)
        # PNG magic: 89 50 4E 47 0D 0A 1A 0A.
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(png) > 100  # not an empty buffer

    def test_render_handles_asset_without_items(self):
        asset = _make_asset("Idle")
        png = render_pm_image(asset)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_etag_changes_when_training_required_toggles(self):
        # Toggling training_required must invalidate the cached image so
        # a panel showing the unflagged face flips to the flagged face on
        # its next wake. Without this, an operator could stand in front
        # of a panel that says "go ahead" for hours after the asset's
        # training gate was turned on.
        asset = _make_asset("Plasma cutter")
        _make_item(asset, "Consumables check", interval_days=30)
        before = compute_snapshot_etag(asset)
        asset.training_required = True
        asset.save(update_fields=["training_required"])
        after = compute_snapshot_etag(asset)
        assert before != after

    def test_render_with_training_required_still_returns_png(self):
        # The badge is drawn additively over the existing PM face — the
        # render must keep producing a valid PNG with the badge present.
        # Pillow doesn't surface layout errors at render time, so this
        # is a smoke test that the badge codepath doesn't crash.
        asset = _make_asset("Plasma cutter")
        asset.training_required = True
        asset.save(update_fields=["training_required"])
        _make_item(asset, "Consumables check", interval_days=30)
        png = render_pm_image(asset)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(png) > 100


# ---------------------------------------------------------------------------
# Image endpoint
# ---------------------------------------------------------------------------


class TestEPaperImageEndpoint:
    def test_image_endpoint_returns_png_with_etag(self, client):
        asset = _make_asset()
        _make_item(asset, "Lube", interval_days=90)
        display = _bind_display(asset)
        url = reverse("forgekey:epaper-image", args=[display.id])
        response = client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        assert response.has_header("ETag")
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
        display.refresh_from_db()
        assert display.last_image_at is not None
        assert display.last_image_etag != ""

    def test_image_endpoint_returns_304_for_matching_etag(self, client):
        asset = _make_asset()
        display = _bind_display(asset)
        url = reverse("forgekey:epaper-image", args=[display.id])

        first = client.get(url)
        etag_value = first["ETag"].strip('"')
        second = client.get(url, HTTP_IF_NONE_MATCH=etag_value)
        assert second.status_code == 304

    def test_image_endpoint_auto_registers_first_contact(self, client):
        # A panel fresh off the shelf generates its own display_id and
        # hits image.png before any staff has bound it to an asset.
        # The server creates a stub row and returns 409 so the firmware
        # knows to paint the bind QR.
        unknown_id = uuid4()
        assert not EPaperDisplay.objects.filter(pk=unknown_id).exists()
        url = reverse("forgekey:epaper-image", args=[unknown_id])
        response = client.get(url)
        assert response.status_code == 409
        display = EPaperDisplay.objects.get(pk=unknown_id)
        assert display.is_active is True
        assert display.asset_id is None
        assert display.device_id is None

    def test_image_endpoint_returns_404_for_retired_display(self, client):
        display = _bind_display(_make_asset())
        display.is_active = False
        display.save(update_fields=["is_active"])
        url = reverse("forgekey:epaper-image", args=[display.id])
        response = client.get(url)
        assert response.status_code == 404

    def test_image_endpoint_returns_409_when_display_unbound(self, client):
        display = _bind_display(asset=None)
        url = reverse("forgekey:epaper-image", args=[display.id])
        response = client.get(url)
        assert response.status_code == 409


# ---------------------------------------------------------------------------
# Bind endpoint
# ---------------------------------------------------------------------------


class TestEPaperBindEndpoint:
    def _url(self, display_id) -> str:
        return reverse("forgekey:epaper-bind", args=[display_id])

    def test_bind_requires_authentication(self, client):
        display = _bind_display(asset=None)
        response = client.post(
            self._url(display.id),
            data={"asset_id": str(_make_asset().pk)},
            content_type="application/json",
        )
        assert response.status_code in (401, 403)

    def test_bind_sets_asset_id(self, authenticated_client):
        client, _user = authenticated_client
        asset = _make_asset("Bandsaw")
        display = _bind_display(asset=None)
        response = client.post(
            self._url(display.id),
            data={"asset_id": str(asset.pk)},
            format="json",
        )
        assert response.status_code == 200
        display.refresh_from_db()
        assert display.asset_id == asset.pk
        assert response.json()["asset_name"] == "Bandsaw"

    def test_bind_auto_creates_display_on_first_call(self, authenticated_client):
        # The mobile bind page may run before the panel has hit
        # image.png even once, so the bind endpoint can't depend on a
        # pre-existing row.
        client, _user = authenticated_client
        asset = _make_asset("New Asset")
        new_did = uuid4()
        assert not EPaperDisplay.objects.filter(pk=new_did).exists()
        response = client.post(
            self._url(new_did),
            data={"asset_id": str(asset.pk)},
            format="json",
        )
        assert response.status_code == 200
        display = EPaperDisplay.objects.get(pk=new_did)
        assert display.asset_id == asset.pk
        assert display.is_active is True

    def test_bind_rebinds_to_a_different_asset(self, authenticated_client):
        client, _user = authenticated_client
        first = _make_asset("First")
        second = _make_asset("Second")
        display = _bind_display(asset=first)
        response = client.post(
            self._url(display.id),
            data={"asset_id": str(second.pk)},
            format="json",
        )
        assert response.status_code == 200
        display.refresh_from_db()
        assert display.asset_id == second.pk

    def test_bind_missing_asset_id_returns_400(self, authenticated_client):
        client, _user = authenticated_client
        display = _bind_display(asset=None)
        response = client.post(self._url(display.id), data={}, format="json")
        assert response.status_code == 400

    def test_bind_unknown_asset_returns_404(self, authenticated_client):
        client, _user = authenticated_client
        display = _bind_display(asset=None)
        response = client.post(
            self._url(display.id),
            data={"asset_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code == 404

    def test_bind_retired_display_returns_410(self, authenticated_client):
        client, _user = authenticated_client
        display = _bind_display(asset=None)
        display.is_active = False
        display.save(update_fields=["is_active"])
        response = client.post(
            self._url(display.id),
            data={"asset_id": str(_make_asset().pk)},
            format="json",
        )
        assert response.status_code == 410


# ---------------------------------------------------------------------------
# Battery endpoint
# ---------------------------------------------------------------------------


class TestEPaperBatteryEndpoint:
    def _url(self, display: EPaperDisplay) -> str:
        return reverse("forgekey:epaper-battery", args=[display.id])

    def test_battery_endpoint_persists_percent(self, client):
        display = _bind_display(_make_asset())
        url = self._url(display)
        response = client.post(url, data={"percent": 75}, content_type="application/json")
        assert response.status_code == 200
        display.refresh_from_db()
        assert display.battery_percent == 75
        assert display.last_battery_at is not None

    def test_battery_endpoint_rejects_non_integer(self, client):
        display = _bind_display(_make_asset())
        response = client.post(
            self._url(display),
            data={"percent": "banana"},
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_battery_endpoint_rejects_out_of_range(self, client):
        display = _bind_display(_make_asset())
        response = client.post(
            self._url(display),
            data={"percent": 150},
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_battery_endpoint_404s_for_unknown_display(self, client):
        url = reverse(
            "forgekey:epaper-battery",
            args=["00000000-0000-0000-0000-000000000000"],
        )
        response = client.post(url, data={"percent": 90}, content_type="application/json")
        assert response.status_code == 404

    def test_low_battery_captures_sentry_warning(self, client, settings):
        settings.FORGEKEY_EPAPER_LOW_BATTERY_PERCENT = 20
        asset = _make_asset("Low Asset")
        display = _bind_display(asset)
        url = self._url(display)
        with patch("forgekey.views.sentry_sdk", create=True) as mock_sentry:
            # `new_scope` is a context manager — give the mock a
            # working __enter__/__exit__ so the `with` in the view
            # doesn't blow up.
            mock_scope = mock_sentry.new_scope.return_value
            mock_scope.__enter__.return_value = mock_scope
            mock_scope.__exit__.return_value = False
            response = client.post(url, data={"percent": 5}, content_type="application/json")
        assert response.status_code == 200
        assert mock_sentry.capture_message.called
        msg = mock_sentry.capture_message.call_args.args[0]
        assert "5%" in msg
        assert "Low Asset" in msg

    def test_battery_at_threshold_does_not_alert(self, client, settings):
        settings.FORGEKEY_EPAPER_LOW_BATTERY_PERCENT = 20
        display = _bind_display(_make_asset())
        with patch("forgekey.views.sentry_sdk", create=True) as mock_sentry:
            client.post(self._url(display), data={"percent": 20}, content_type="application/json")
        assert not mock_sentry.capture_message.called

    def test_battery_value_old_enough_still_replaced(self, client):
        """A subsequent report overwrites the previous battery value.

        Pin the upsert semantics — operators rely on the freshest
        reading, not a max() or average across reports.
        """
        display = _bind_display(_make_asset())
        display.battery_percent = 80
        display.last_battery_at = timezone.now() - timedelta(hours=1)
        display.save()

        client.post(self._url(display), data={"percent": 40}, content_type="application/json")
        display.refresh_from_db()
        assert display.battery_percent == 40


# ---------------------------------------------------------------------------
# Health endpoint (full wake-cycle envelope)
# ---------------------------------------------------------------------------


class TestEPaperHealthEndpoint:
    """Wake-cycle health POST: same battery handling as /battery/ but
    also persists firmware_version + last_image_etag from the wider
    payload the firmware actually sends."""

    def _url(self, display: EPaperDisplay) -> str:
        return reverse("forgekey:epaper-health", args=[display.id])

    def _payload(self, **overrides) -> dict:
        body = {
            "schema_version": "epaper_v1",
            "firmware_version": "0.1.0-abc1234",
            "last_image_etag": "deadbeefcafef00d",
            "unchanged_count": 0,
            "failure_count": 0,
            "wake_interval_min": 60,
            "configured_wake_min": 60,
            "render_status": "rendered",
            "cycle_result": "ok",
            "last_http_status": 200,
            "retired": False,
            "power": {"battery": {"available": True, "percent": 73}},
        }
        body.update(overrides)
        return body

    def test_health_persists_battery_firmware_and_etag(self, client):
        display = _bind_display(_make_asset())
        resp = client.post(
            self._url(display), data=self._payload(), content_type="application/json"
        )
        assert resp.status_code == 200
        display.refresh_from_db()
        assert display.battery_percent == 73
        assert display.last_battery_at is not None
        assert display.firmware_version == "0.1.0-abc1234"
        assert display.last_image_etag == "deadbeefcafef00d"
        # battery.available=true claim is mirrored so the dashboard can
        # distinguish "panel has a sensor" from "never reported".
        assert display.battery_available is True
        assert display.battery_unavailable_reason == ""
        assert display.last_health_at is not None

    def test_health_without_battery_block_still_succeeds(self, client):
        """Stock SKU-6416 panels report power={} (battery not wired).

        Firmware still POSTs health every wake; we just don't update
        battery_percent. Reject would break the wake cycle.
        """
        display = _bind_display(_make_asset())
        resp = client.post(
            self._url(display),
            data=self._payload(power={}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        display.refresh_from_db()
        assert display.battery_percent is None
        assert display.last_battery_at is None
        # …but firmware_version still landed.
        assert display.firmware_version == "0.1.0-abc1234"
        # …and the wake itself is stamped, even though no battery fields
        # changed — operators can see the panel is alive.
        assert display.last_health_at is not None
        # Missing `available` doesn't touch the sensor-presence flag —
        # null means "we still don't know whether this panel has one".
        assert display.battery_available is None

    def test_health_records_no_sensor_state(self, client):
        """SKU-6416 firmware reports power.battery.available=false.

        Backend should persist that plus the firmware's reason so the
        dashboard can render "No sensor (battery_adc_not_configured)"
        instead of an ambiguous "—".
        """
        display = _bind_display(_make_asset())
        resp = client.post(
            self._url(display),
            data=self._payload(
                power={
                    "battery": {
                        "available": False,
                        "source": "unsupported",
                        "reason": "battery_adc_not_configured",
                    }
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        display.refresh_from_db()
        assert display.battery_percent is None
        assert display.battery_available is False
        assert display.battery_unavailable_reason == "battery_adc_not_configured"
        assert display.last_health_at is not None

    def test_health_clears_unavailable_reason_when_sensor_returns(self, client):
        """If a panel previously reported no sensor and is later modded
        (or replaced) to report a percent, the stale reason text must
        not linger — it would mislead an operator triaging the row."""
        display = _bind_display(_make_asset())
        display.battery_available = False
        display.battery_unavailable_reason = "battery_adc_not_configured"
        display.save(update_fields=["battery_available", "battery_unavailable_reason"])

        resp = client.post(
            self._url(display),
            data=self._payload(power={"battery": {"available": True, "percent": 64}}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        display.refresh_from_db()
        assert display.battery_percent == 64
        assert display.battery_available is True
        assert display.battery_unavailable_reason == ""

    def test_health_rejects_out_of_range_battery(self, client):
        display = _bind_display(_make_asset())
        resp = client.post(
            self._url(display),
            data=self._payload(power={"battery": {"percent": 250}}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_health_404s_for_unknown_display(self, client):
        url = reverse("forgekey:epaper-health", args=["00000000-0000-0000-0000-000000000000"])
        resp = client.post(url, data=self._payload(), content_type="application/json")
        assert resp.status_code == 404

    def test_low_battery_alerts_sentry(self, client, settings):
        settings.FORGEKEY_EPAPER_LOW_BATTERY_PERCENT = 20
        asset = _make_asset("Lonely Panel")
        display = _bind_display(asset)
        with patch("forgekey.views.sentry_sdk", create=True) as mock_sentry:
            scope = mock_sentry.new_scope.return_value
            scope.__enter__.return_value = scope
            scope.__exit__.return_value = False
            resp = client.post(
                self._url(display),
                data=self._payload(power={"battery": {"percent": 5}}),
                content_type="application/json",
            )
        assert resp.status_code == 200
        assert mock_sentry.capture_message.called
        msg = mock_sentry.capture_message.call_args.args[0]
        assert "5%" in msg
        assert "Lonely Panel" in msg

    def test_health_works_without_device_fk(self, client):
        """HTTPS-only panels don't have an enrolled ESP32Device.

        Sentry tag for device_mac is guarded — a low-battery report
        from a deviceless display must not crash with AttributeError.
        """
        display = EPaperDisplay.objects.create(asset=_make_asset(), device=None)
        with patch("forgekey.views.sentry_sdk", create=True) as mock_sentry:
            scope = mock_sentry.new_scope.return_value
            scope.__enter__.return_value = scope
            scope.__exit__.return_value = False
            resp = client.post(
                self._url(display),
                data=self._payload(power={"battery": {"percent": 5}}),
                content_type="application/json",
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Desired-state endpoint
# ---------------------------------------------------------------------------


class TestEPaperDesiredEndpoint:
    def _url(self, display: EPaperDisplay) -> str:
        return reverse("forgekey:epaper-desired", args=[display.id])

    def test_desired_returns_wake_min_from_setting(self, client, settings):
        settings.FORGEKEY_EPAPER_DEFAULT_WAKE_MIN = 45
        display = _bind_display(_make_asset())
        resp = client.get(self._url(display))
        assert resp.status_code == 200
        body = resp.json()
        assert body["desired"]["wake_min"] == 45

    def test_desired_defaults_to_60_when_setting_absent(self, client, settings):
        # No FORGEKEY_EPAPER_DEFAULT_WAKE_MIN configured → fall back to 60.
        if hasattr(settings, "FORGEKEY_EPAPER_DEFAULT_WAKE_MIN"):
            del settings.FORGEKEY_EPAPER_DEFAULT_WAKE_MIN
        display = _bind_display(_make_asset())
        resp = client.get(self._url(display))
        assert resp.json()["desired"]["wake_min"] == 60

    def test_desired_404s_for_unknown_display(self, client):
        url = reverse("forgekey:epaper-desired", args=["00000000-0000-0000-0000-000000000000"])
        assert client.get(url).status_code == 404


# ---------------------------------------------------------------------------
# Command / firmware status ack endpoints (no-ops)
# ---------------------------------------------------------------------------


class TestEPaperStatusAckEndpoints:
    """Both ack endpoints exist only so the firmware's POSTs stop
    404-ing in the serial log. They 204 on a known display, 404 on
    an unknown one. Until OMS actually issues commands or surfaces
    firmware-OTA progress, that's all the contract they need."""

    def test_command_status_ack_204s_for_known_display(self, client):
        display = _bind_display(_make_asset())
        url = reverse("forgekey:epaper-command-status", args=[display.id])
        resp = client.post(url, data={"any": "shape"}, content_type="application/json")
        assert resp.status_code == 204

    def test_command_status_404s_for_unknown(self, client):
        url = reverse(
            "forgekey:epaper-command-status",
            args=["00000000-0000-0000-0000-000000000000"],
        )
        assert client.post(url, data={}, content_type="application/json").status_code == 404

    def test_firmware_status_ack_204s_for_known_display(self, client):
        display = _bind_display(_make_asset())
        url = reverse("forgekey:epaper-firmware-status", args=[display.id])
        resp = client.post(url, data={"phase": "downloading"}, content_type="application/json")
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Scan-to-log service endpoints (front-end "complete this PM" page)
# ---------------------------------------------------------------------------


class TestEPaperServiceInfo:
    def _url(self, display_id) -> str:
        return reverse("forgekey:epaper-service-info", args=[display_id])

    def test_info_is_public_and_lists_due_task(self, client):
        asset = _make_asset("Bandsaw")
        item = _make_item(asset, "Blade tension", interval_days=30, last_done_days=41)
        display = _bind_display(asset)
        response = client.get(self._url(display.id))
        assert response.status_code == 200
        body = response.json()
        assert body["bound"] is True
        assert body["asset"]["name"] == "Bandsaw"
        assert body["primary_item_id"] == str(item.pk)
        assert body["items"][0]["title"] == "Blade tension"
        assert body["items"][0]["status"] == "overdue"

    def test_info_excludes_non_recurring_items(self, client):
        # One-off items (no interval) are not "preventive" → off the panel.
        asset = _make_asset("Bandsaw")
        MaintenanceItem.objects.create(asset=asset, title="One-off fix", interval_days=None)
        display = _bind_display(asset)
        body = client.get(self._url(display.id)).json()
        assert body["items"] == []
        assert body["primary_item_id"] is None

    def test_info_includes_work_order_detail(self, client):
        from decimal import Decimal

        from inventory.models import MaintenanceMaterial, MaintenanceTask
        from loto.models import AssetEnergySource, LOTODevice

        asset = _make_asset("Mill")
        asset.lockout_instructions = "Verify zero energy at the panel."
        asset.save(update_fields=["lockout_instructions"])

        item = _make_item(asset, "Lube", interval_days=30, last_done_days=5)
        item.instructions = "Use way oil only."
        item.estimated_time_minutes = 20
        item.save(update_fields=["instructions", "estimated_time_minutes"])
        MaintenanceTask.objects.create(
            maintenance_item=item, order=1, title="Wipe ways", is_required=True
        )
        MaintenanceTask.objects.create(maintenance_item=item, order=2, title="Apply oil")
        MaintenanceMaterial.objects.create(
            maintenance_item=item, name="Way oil", quantity=Decimal("50"), unit="ml"
        )

        source = AssetEnergySource.objects.create(
            asset=asset,
            source_type=AssetEnergySource.SOURCE_ELECTRICAL,
            magnitude="240V",
            isolation_point="Panel A breaker 12",
        )
        device = LOTODevice.objects.create(
            device_type=LOTODevice.DEVICE_TYPE_CHOICES[0][0], label="PAD-001"
        )
        source.required_devices.add(device)

        display = _bind_display(asset)
        body = client.get(self._url(display.id)).json()

        # Asset-level lockout/tagout
        assert body["loto"]["instructions"] == "Verify zero energy at the panel."
        es = body["loto"]["energy_sources"][0]
        assert es["magnitude"] == "240V"
        assert es["isolation_point"] == "Panel A breaker 12"
        assert es["devices"][0]["label"] == "PAD-001"

        # Per-task work-order detail
        work_order = body["items"][0]
        assert work_order["estimated_time_minutes"] == 20
        assert work_order["instructions"] == "Use way oil only."
        assert [s["title"] for s in work_order["steps"]] == ["Wipe ways", "Apply oil"]
        assert work_order["steps"][0]["is_required"] is True
        assert work_order["materials"][0]["name"] == "Way oil"

    def test_info_surfaces_tools_locations_and_power(self, client):
        """The work order resolves where tools/consumables live + how much is
        on hand, and where the asset's power is — the data a maintainer needs
        before walking up to the machine."""
        from decimal import Decimal

        from electrical_circuits.models import PowerBreaker, PowerPanel
        from inventory.models import MaintenanceMaterial, MaintenanceTool
        from inventory.tests.factories import InventoryItemFactory

        asset = _make_asset("Lathe")
        # Free-text power fields + a structured breaker→panel→location chain.
        asset.suite = "East wing"
        asset.electrical_box = "East enclosure"
        asset.breaker_location = "Panel A, Breaker 12"
        panel = PowerPanel.objects.create(location=asset.location, name="Panel A")
        asset.breaker = PowerBreaker.objects.create(
            panel=panel, position="12", amperage=20, label="Lathe feed"
        )
        asset.save(update_fields=["suite", "electrical_box", "breaker_location", "breaker"])

        item = _make_item(asset, "Lube", interval_days=30, last_done_days=5)

        # A consumable stocked in inventory → location + on-hand resolve.
        oil_loc = Location.objects.create(name="Oil cabinet")
        oil = InventoryItemFactory(name="Way oil", location=oil_loc, current_stock=7)
        MaintenanceMaterial.objects.create(
            maintenance_item=item,
            name="Way oil",
            quantity=Decimal("50"),
            unit="ml",
            inventory_item=oil,
        )

        # A tool tracked in inventory (location auto-resolves) ...
        crib = Location.objects.create(name="Tool crib")
        wrench = InventoryItemFactory(name="17mm wrench", location=crib, current_stock=3)
        MaintenanceTool.objects.create(
            maintenance_item=item, name="17mm wrench", inventory_item=wrench
        )
        # ... and a tool that's only a free-text hint.
        MaintenanceTool.objects.create(
            maintenance_item=item,
            name="Torque wrench",
            location_hint="Calibration shelf, bay 2",
        )

        display = _bind_display(asset)
        body = client.get(self._url(display.id)).json()

        power = body["power"]
        assert power["suite"] == "East wing"
        assert power["electrical_box"] == "East enclosure"
        assert power["breaker_location"] == "Panel A, Breaker 12"
        assert power["breaker"]["panel"] == "Panel A"
        assert power["breaker"]["panel_location"] == asset.location.name
        assert power["breaker"]["position"] == "12"

        work_order = body["items"][0]
        material = work_order["materials"][0]
        assert material["name"] == "Way oil"
        assert material["location"] == "Oil cabinet"
        assert material["on_hand"] == 7

        tools = {t["name"]: t for t in work_order["tools"]}
        assert tools["17mm wrench"]["location"] == "Tool crib"
        assert tools["17mm wrench"]["on_hand"] == 3
        assert tools["17mm wrench"]["is_required"] is True
        assert tools["Torque wrench"]["location"] == "Calibration shelf, bay 2"
        assert tools["Torque wrench"]["on_hand"] is None

    def test_info_409_when_unbound(self, client):
        display = _bind_display(asset=None)
        response = client.get(self._url(display.id))
        assert response.status_code == 409
        assert response.json()["bound"] is False

    def test_info_404_when_retired(self, client):
        display = _bind_display(_make_asset())
        display.is_active = False
        display.save(update_fields=["is_active"])
        response = client.get(self._url(display.id))
        assert response.status_code == 404


class TestEPaperServiceComplete:
    def _url(self, display_id) -> str:
        return reverse("forgekey:epaper-complete", args=[display_id])

    def test_complete_requires_authentication(self, client):
        display = _bind_display(_make_asset())
        _make_item(display.asset, "Lube", interval_days=30)
        response = client.post(self._url(display.id), data={}, content_type="application/json")
        assert response.status_code in (401, 403)

    def test_complete_logs_attributable_service_and_resets_status(self, authenticated_client):
        client, user = authenticated_client
        asset = _make_asset("Bandsaw")
        item = _make_item(asset, "Blade tension", interval_days=30, last_done_days=41)
        assert item.is_overdue is True
        display = _bind_display(asset)

        response = client.post(
            self._url(display.id), data={"notes": "swapped blade"}, format="json"
        )
        assert response.status_code == 201
        body = response.json()
        assert body["ok"] is True
        assert body["status"] == "ok"

        latest = item.logs.order_by("-completed_at").first()
        assert latest.completed_by_id == user.pk
        assert latest.notes == "swapped blade"
        item.refresh_from_db()
        assert item.is_overdue is False

    def test_complete_honours_explicit_item_id(self, authenticated_client):
        client, _user = authenticated_client
        asset = _make_asset("Mill")
        lube = _make_item(asset, "Lube", interval_days=30)
        belt = _make_item(asset, "Belt", interval_days=60)
        display = _bind_display(asset)

        response = client.post(self._url(display.id), data={"item_id": str(belt.pk)}, format="json")
        assert response.status_code == 201
        assert belt.logs.exists() is True
        assert lube.logs.exists() is False

    def test_complete_400_when_asset_has_no_schedule(self, authenticated_client):
        client, _user = authenticated_client
        display = _bind_display(_make_asset("Idle"))
        response = client.post(self._url(display.id), data={}, format="json")
        assert response.status_code == 400

    def test_complete_409_when_unbound(self, authenticated_client):
        client, _user = authenticated_client
        display = _bind_display(asset=None)
        response = client.post(self._url(display.id), data={}, format="json")
        assert response.status_code == 409

    def test_complete_stamps_location_and_attaches_photo(self, authenticated_client):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile

        from PIL import Image

        client, user = authenticated_client
        asset = _make_asset("Lathe")  # _make_asset gives the asset a Location
        item = _make_item(asset, "Lube", interval_days=30, last_done_days=5)
        display = _bind_display(asset)

        buf = BytesIO()
        Image.new("RGB", (8, 8), "blue").save(buf, format="JPEG")
        buf.seek(0)
        photo = SimpleUploadedFile("work.jpg", buf.read(), content_type="image/jpeg")

        response = client.post(
            self._url(display.id),
            data={"item_id": str(item.pk), "photo": photo},
            format="multipart",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["photo_attached"] is True
        # Location defaults to the asset's location.
        assert body["location"] == asset.location.name

        log = item.logs.order_by("-completed_at").first()
        assert log.location_id == asset.location_id
        assert log.photos.count() == 1
        assert log.photos.first().uploaded_by_id == user.pk

    def test_complete_honours_explicit_location(self, authenticated_client):
        client, _user = authenticated_client
        asset = _make_asset("Drill")
        item = _make_item(asset, "Oil", interval_days=30)
        display = _bind_display(asset)
        other = Location.objects.create(name="Annex bench")

        response = client.post(
            self._url(display.id),
            data={"item_id": str(item.pk), "location_id": str(other.pk)},
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["location"] == "Annex bench"
        assert item.logs.order_by("-completed_at").first().location_id == other.pk
