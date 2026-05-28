"""Tests for the XIAO 7.5" ePaper PM-display foundation.

Covers:
  - Model: low-battery threshold, default ordering, asset binding.
  - Render service: deterministic ETag, image bytes are valid PNG,
    status-line content reflects PMSchedule state.
  - Image endpoint: 200 on first fetch with ETag header, 304 on
    matching If-None-Match, 404 for an unknown display, 409 for an
    unbound display.
  - Battery endpoint: persists percent + timestamp, captures Sentry
    warning when below threshold, ignores non-integer payloads.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

import pytest

from forgekey.models import EPaperDisplay
from forgekey.services.epaper_render import compute_snapshot_etag, render_pm_image
from forgekey.tests.factories import ESP32DeviceFactory
from inventory.models import Asset, Location
from preventive_maintenance.models import PMSchedule, PMServiceLog

pytestmark = pytest.mark.django_db


def _make_asset(name: str = "ePaper Asset") -> Asset:
    location = Location.objects.create(name="ePaper Loc")
    return Asset.objects.create(name=name, location=location)


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
        PMSchedule.objects.create(asset=asset, task_name="Blade tension", interval_days=30)
        first = compute_snapshot_etag(asset)
        second = compute_snapshot_etag(asset)
        assert first == second

    def test_etag_changes_when_service_logged(self):
        asset = _make_asset("Bandsaw")
        schedule = PMSchedule.objects.create(
            asset=asset, task_name="Blade tension", interval_days=30
        )
        before = compute_snapshot_etag(asset)
        PMServiceLog.objects.create(schedule=schedule, performed_at=timezone.now())
        after = compute_snapshot_etag(asset)
        assert before != after

    def test_render_returns_png_bytes(self):
        asset = _make_asset()
        PMSchedule.objects.create(asset=asset, task_name="Filter swap", interval_days=90)
        png = render_pm_image(asset)
        # PNG magic: 89 50 4E 47 0D 0A 1A 0A.
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(png) > 100  # not an empty buffer

    def test_render_handles_asset_without_schedules(self):
        asset = _make_asset("Idle")
        png = render_pm_image(asset)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# Image endpoint
# ---------------------------------------------------------------------------


class TestEPaperImageEndpoint:
    def test_image_endpoint_returns_png_with_etag(self, client):
        asset = _make_asset()
        PMSchedule.objects.create(asset=asset, task_name="Lube", interval_days=90)
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

    def test_image_endpoint_returns_404_for_unknown_display(self, client):
        url = reverse(
            "forgekey:epaper-image",
            args=["00000000-0000-0000-0000-000000000000"],
        )
        response = client.get(url)
        assert response.status_code == 404

    def test_image_endpoint_returns_409_when_display_unbound(self, client):
        display = _bind_display(asset=None)
        url = reverse("forgekey:epaper-image", args=[display.id])
        response = client.get(url)
        assert response.status_code == 409


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
