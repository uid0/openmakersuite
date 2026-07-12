"""Tests for the asset meter framework (EAM bead-1).

Covers the models, the shared ``apply_reading`` helper (absolute vs delta),
the ``SessionRuntimeAdapter`` runtime rollup (only ended sessions, exactly-once
via the watermark, watermark advances), the hours_used dual-write, the manual
record-reading / adjust API actions, the permission gates, and the nested
``meters`` on the asset-detail payload.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from forgekey.models import DeviceUsage
from inventory.models import AssetMeter, AssetMeterReading
from inventory.services.meter_sources import ReadingSpec, apply_reading, run_rollup
from inventory.tests.factories import AssetFactory
from membership.models import SIGAdmin

User = get_user_model()
pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _user(username, **flags):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=get_random_string(24),
        **flags,
    )


def _client(user=None):
    client = APIClient()
    if user:
        client.force_authenticate(user=user)
    return client


def _meter(asset, **kwargs):
    kwargs.setdefault("name", "Runtime")
    kwargs.setdefault("meter_type", AssetMeter.MeterType.RUNTIME_HOURS)
    kwargs.setdefault("source", AssetMeter.Source.MANUAL)
    kwargs.setdefault("unit", "hours")
    return AssetMeter.objects.create(asset=asset, **kwargs)


def _ended_session(asset, seconds, ended_at):
    """An ended DeviceUsage session (duration set, as end_session() would)."""
    return DeviceUsage.objects.create(asset=asset, ended_at=ended_at, duration_seconds=seconds)


def _record_url(meter):
    return f"/api/inventory/asset-meters/{meter.id}/record-reading/"


def _adjust_url(meter):
    return f"/api/inventory/asset-meters/{meter.id}/adjust/"


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
class TestAssetMeterModels:
    def test_create_meter_defaults(self):
        asset = AssetFactory()
        meter = _meter(asset, name="Spindle", meter_type=AssetMeter.MeterType.RUNTIME_HOURS)
        assert meter.current_value == Decimal("0")
        assert meter.current_is_estimated is False
        assert meter.rollup_watermark_at is None
        assert meter.is_active is True
        assert meter.source == AssetMeter.Source.MANUAL

    def test_unique_together_asset_name(self):
        asset = AssetFactory()
        _meter(asset, name="Runtime")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                _meter(asset, name="Runtime")

    def test_same_name_different_assets_allowed(self):
        a1, a2 = AssetFactory(), AssetFactory()
        _meter(a1, name="Runtime")
        _meter(a2, name="Runtime")  # no collision across assets
        assert AssetMeter.objects.filter(name="Runtime").count() == 2

    def test_reading_ledger_ordering(self):
        asset = AssetFactory()
        meter = _meter(asset)
        apply_reading(
            meter,
            ReadingSpec(
                source=AssetMeterReading.Source.MANUAL,
                observed_at=timezone.now(),
                absolute=Decimal("5"),
            ),
        )
        apply_reading(
            meter,
            ReadingSpec(
                source=AssetMeterReading.Source.MANUAL,
                observed_at=timezone.now(),
                delta=Decimal("3"),
            ),
        )
        readings = list(meter.readings.all())  # ordering = [-recorded_at]
        assert readings[0].value_after == Decimal("8")


# --------------------------------------------------------------------------- #
# apply_reading — absolute vs delta collapse
# --------------------------------------------------------------------------- #
class TestApplyReading:
    def test_absolute_reading_sets_value_and_delta(self):
        asset = AssetFactory()
        meter = _meter(asset, meter_type=AssetMeter.MeterType.VOLUME_GALLONS, unit="gallons")
        reading = apply_reading(
            meter,
            ReadingSpec(
                source=AssetMeterReading.Source.MANUAL,
                observed_at=timezone.now(),
                absolute=Decimal("100"),
            ),
        )
        assert reading.delta == Decimal("100")
        assert reading.value_after == Decimal("100")
        meter.refresh_from_db()
        assert meter.current_value == Decimal("100")

    def test_delta_reading_adds_to_current(self):
        asset = AssetFactory()
        meter = _meter(asset, meter_type=AssetMeter.MeterType.VOLUME_GALLONS, unit="gallons")
        meter.current_value = Decimal("100")
        meter.save(update_fields=["current_value"])
        reading = apply_reading(
            meter,
            ReadingSpec(
                source=AssetMeterReading.Source.MANUAL,
                observed_at=timezone.now(),
                delta=Decimal("25"),
            ),
        )
        assert reading.value_after == Decimal("125")
        meter.refresh_from_db()
        assert meter.current_value == Decimal("125")

    def test_absolute_then_delta_compose(self):
        asset = AssetFactory()
        meter = _meter(asset, meter_type=AssetMeter.MeterType.CYCLES, unit="cycles")
        apply_reading(
            meter,
            ReadingSpec(
                source=AssetMeterReading.Source.MANUAL,
                observed_at=timezone.now(),
                absolute=Decimal("40"),
            ),
        )
        r2 = apply_reading(
            meter,
            ReadingSpec(
                source=AssetMeterReading.Source.MANUAL,
                observed_at=timezone.now(),
                delta=Decimal("-10"),
            ),
        )
        assert r2.delta == Decimal("-10")
        assert r2.value_after == Decimal("30")

    def test_estimated_flag_propagates_to_meter(self):
        asset = AssetFactory()
        meter = _meter(asset, meter_type=AssetMeter.MeterType.VOLUME_GALLONS, unit="gallons")
        apply_reading(
            meter,
            ReadingSpec(
                source=AssetMeterReading.Source.MANUAL,
                observed_at=timezone.now(),
                absolute=Decimal("12"),
                is_estimated=True,
            ),
        )
        meter.refresh_from_db()
        assert meter.current_is_estimated is True

    def test_spec_requires_exactly_one_of_delta_absolute(self):
        with pytest.raises(ValueError):
            ReadingSpec(source=AssetMeterReading.Source.MANUAL, observed_at=timezone.now())
        with pytest.raises(ValueError):
            ReadingSpec(
                source=AssetMeterReading.Source.MANUAL,
                observed_at=timezone.now(),
                delta=Decimal("1"),
                absolute=Decimal("1"),
            )


# --------------------------------------------------------------------------- #
# SessionRuntimeAdapter rollup
# --------------------------------------------------------------------------- #
class TestSessionRuntimeRollup:
    def test_sums_ended_sessions_into_hours(self):
        asset = AssetFactory(hours_used=0)
        meter = _meter(asset, source=AssetMeter.Source.AUTO_SESSION)
        now = timezone.now()
        _ended_session(asset, 3600, now - timedelta(minutes=30))  # 1.0h
        _ended_session(asset, 1800, now - timedelta(minutes=10))  # 0.5h

        run_rollup(now)

        meter.refresh_from_db()
        assert meter.current_value == Decimal("1.5000")
        readings = meter.readings.all()
        assert readings.count() == 1
        reading = readings.first()
        assert reading.source == AssetMeterReading.Source.AUTO_SESSION
        assert reading.is_estimated is False
        assert reading.delta == Decimal("1.5000")
        assert "device_usage x2" in reading.source_ref

    def test_ignores_in_flight_sessions(self):
        asset = AssetFactory()
        meter = _meter(asset, source=AssetMeter.Source.AUTO_SESSION)
        now = timezone.now()
        _ended_session(asset, 3600, now - timedelta(minutes=5))
        # In-flight: ended_at + duration not set (as before end_session()).
        DeviceUsage.objects.create(asset=asset, ended_at=None, duration_seconds=None)

        run_rollup(now)

        meter.refresh_from_db()
        assert meter.current_value == Decimal("1.0000")  # only the ended one

    def test_watermark_advances_to_latest_ended(self):
        asset = AssetFactory()
        meter = _meter(asset, source=AssetMeter.Source.AUTO_SESSION)
        now = timezone.now()
        latest = now - timedelta(minutes=1)
        _ended_session(asset, 600, now - timedelta(minutes=20))
        _ended_session(asset, 600, latest)

        run_rollup(now)

        meter.refresh_from_db()
        assert meter.rollup_watermark_at == latest

    def test_rollup_is_idempotent(self):
        asset = AssetFactory(hours_used=0)
        meter = _meter(asset, source=AssetMeter.Source.AUTO_SESSION)
        now = timezone.now()
        _ended_session(asset, 7200, now - timedelta(minutes=15))  # 2.0h

        run_rollup(now)
        run_rollup(now)  # re-run must not double-count

        meter.refresh_from_db()
        asset.refresh_from_db()
        assert meter.current_value == Decimal("2.0000")
        assert meter.readings.count() == 1
        assert asset.hours_used == 2

    def test_only_new_sessions_after_watermark_are_counted(self):
        asset = AssetFactory()
        meter = _meter(asset, source=AssetMeter.Source.AUTO_SESSION)
        now = timezone.now()
        _ended_session(asset, 3600, now - timedelta(minutes=30))
        run_rollup(now)
        meter.refresh_from_db()
        assert meter.current_value == Decimal("1.0000")

        # A new session ends AFTER the watermark; the next rollup adds only it.
        _ended_session(asset, 1800, now - timedelta(minutes=5))
        run_rollup(now)

        meter.refresh_from_db()
        assert meter.current_value == Decimal("1.5000")
        assert meter.readings.count() == 2

    def test_no_sessions_writes_no_reading(self):
        asset = AssetFactory()
        meter = _meter(asset, source=AssetMeter.Source.AUTO_SESSION)
        run_rollup(timezone.now())
        meter.refresh_from_db()
        assert meter.readings.count() == 0
        assert meter.rollup_watermark_at is None

    def test_hours_used_dual_write_increments(self):
        asset = AssetFactory(hours_used=10)
        _meter(
            asset,
            source=AssetMeter.Source.AUTO_SESSION,
            meter_type=AssetMeter.MeterType.RUNTIME_HOURS,
        )
        now = timezone.now()
        _ended_session(asset, 7200, now - timedelta(minutes=5))  # 2.0h

        run_rollup(now)

        asset.refresh_from_db()
        assert asset.hours_used == 12  # 10 + int(2.0)

    def test_dual_write_only_for_runtime_meters(self):
        asset = AssetFactory(hours_used=5)
        _meter(
            asset,
            source=AssetMeter.Source.AUTO_SESSION,
            meter_type=AssetMeter.MeterType.VOLUME_GALLONS,
            unit="gallons",
        )
        # A gallons meter should never touch hours_used, even from a session.
        run_rollup(timezone.now())
        asset.refresh_from_db()
        assert asset.hours_used == 5

    def test_zero_duration_advances_watermark_without_delta(self):
        # A sub-second session ends with duration_seconds=0. It adds nothing but
        # must still advance the watermark so it isn't rescanned forever.
        asset = AssetFactory(hours_used=0)
        meter = _meter(asset, source=AssetMeter.Source.AUTO_SESSION)
        ended = timezone.now() - timedelta(minutes=1)
        _ended_session(asset, 0, ended)

        run_rollup(timezone.now())

        meter.refresh_from_db()
        asset.refresh_from_db()
        assert meter.current_value == Decimal("0")
        assert meter.rollup_watermark_at == ended
        assert meter.readings.count() == 1
        assert meter.readings.first().delta == Decimal("0")
        assert asset.hours_used == 0


# --------------------------------------------------------------------------- #
# run_rollup dispatch
# --------------------------------------------------------------------------- #
class TestRunRollupDispatch:
    def test_skips_inactive_meters(self):
        asset = AssetFactory()
        meter = _meter(asset, source=AssetMeter.Source.AUTO_SESSION, is_active=False)
        _ended_session(asset, 3600, timezone.now())
        run_rollup(timezone.now())
        meter.refresh_from_db()
        assert meter.current_value == Decimal("0")
        assert meter.readings.count() == 0

    def test_manual_and_telemetry_are_push_no_ops(self):
        asset = AssetFactory()
        manual = _meter(asset, name="Manual", source=AssetMeter.Source.MANUAL)
        telem = _meter(asset, name="Telemetry", source=AssetMeter.Source.AUTO_TELEMETRY)
        # Sessions exist, but only auto_session pulls them.
        _ended_session(asset, 3600, timezone.now())

        stats = run_rollup(timezone.now())

        manual.refresh_from_db()
        telem.refresh_from_db()
        assert manual.readings.count() == 0
        assert telem.readings.count() == 0
        assert stats["readings_applied"] == 0

    def test_unknown_source_is_skipped(self):
        # A meter whose source has no registered adapter is skipped (logged),
        # not fatal — the sweep keeps going.
        asset = AssetFactory()
        # choices aren't DB-enforced; force an unregistered source.
        AssetMeter.objects.create(asset=asset, name="Mystery", source="bogus_source")
        stats = run_rollup(timezone.now())
        assert stats["readings_applied"] == 0


# --------------------------------------------------------------------------- #
# manual entry API — record-reading + adjust
# --------------------------------------------------------------------------- #
class TestManualEntryAPI:
    def setup_method(self):
        self.staff = _user("meter_admin", is_staff=True)
        self.client = _client(self.staff)

    def test_record_reading_absolute(self):
        asset = AssetFactory()
        meter = _meter(asset, meter_type=AssetMeter.MeterType.VOLUME_GALLONS, unit="gallons")
        resp = self.client.post(
            _record_url(meter),
            data={"value": 500, "is_absolute": True},
            format="json",
        )
        assert resp.status_code == 201
        body = resp.json()
        assert Decimal(body["meter"]["current_value"]) == Decimal("500")
        assert body["reading"]["source"] == AssetMeterReading.Source.MANUAL
        meter.refresh_from_db()
        assert meter.current_value == Decimal("500")
        assert meter.readings.first().recorded_by == self.staff

    def test_record_reading_delta(self):
        asset = AssetFactory()
        meter = _meter(asset, meter_type=AssetMeter.MeterType.VOLUME_GALLONS, unit="gallons")
        meter.current_value = Decimal("500")
        meter.save(update_fields=["current_value"])
        resp = self.client.post(
            _record_url(meter),
            data={"value": 50, "is_absolute": False},
            format="json",
        )
        assert resp.status_code == 201
        assert Decimal(resp.json()["meter"]["current_value"]) == Decimal("550")

    def test_record_reading_estimated(self):
        asset = AssetFactory()
        meter = _meter(asset, meter_type=AssetMeter.MeterType.VOLUME_GALLONS, unit="gallons")
        resp = self.client.post(
            _record_url(meter),
            data={"value": 42, "is_absolute": True, "is_estimated": True},
            format="json",
        )
        assert resp.status_code == 201
        assert resp.json()["reading"]["is_estimated"] is True
        meter.refresh_from_db()
        assert meter.current_is_estimated is True

    def test_record_reading_rejects_non_number(self):
        asset = AssetFactory()
        meter = _meter(asset)
        resp = self.client.post(_record_url(meter), data={"value": "lots"}, format="json")
        assert resp.status_code == 400

    def test_record_reading_on_runtime_meter_dual_writes(self):
        asset = AssetFactory(hours_used=0)
        meter = _meter(asset, meter_type=AssetMeter.MeterType.RUNTIME_HOURS, unit="hours")
        resp = self.client.post(
            _record_url(meter),
            data={"value": 8, "is_absolute": True},
            format="json",
        )
        assert resp.status_code == 201
        asset.refresh_from_db()
        assert asset.hours_used == 8  # manual runtime entry feeds the forecast too

    def test_adjust_sets_target_with_reason(self):
        asset = AssetFactory()
        meter = _meter(asset, meter_type=AssetMeter.MeterType.VOLUME_GALLONS, unit="gallons")
        meter.current_value = Decimal("100")
        meter.save(update_fields=["current_value"])
        resp = self.client.post(
            _adjust_url(meter),
            data={"target": 90, "reason": "physical recount"},
            format="json",
        )
        assert resp.status_code == 201
        body = resp.json()
        assert Decimal(body["meter"]["current_value"]) == Decimal("90")
        assert body["reading"]["source"] == AssetMeterReading.Source.MANUAL_ADJUST
        assert body["reading"]["notes"] == "physical recount"
        assert Decimal(body["reading"]["delta"]) == Decimal("-10")

    def test_adjust_requires_reason(self):
        asset = AssetFactory()
        meter = _meter(asset)
        resp = self.client.post(_adjust_url(meter), data={"target": 5}, format="json")
        assert resp.status_code == 400

    def test_adjust_rejects_non_number_target(self):
        asset = AssetFactory()
        meter = _meter(asset)
        resp = self.client.post(
            _adjust_url(meter),
            data={"target": "nope", "reason": "x"},
            format="json",
        )
        assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# permissions
# --------------------------------------------------------------------------- #
class TestMeterPermissions:
    def test_record_reading_anonymous_denied(self):
        meter = _meter(AssetFactory())
        resp = _client().post(_record_url(meter), data={"value": 1}, format="json")
        assert resp.status_code in (401, 403)

    def test_record_reading_volunteer_denied(self):
        meter = _meter(AssetFactory())
        resp = _client(_user("vol")).post(_record_url(meter), data={"value": 1}, format="json")
        assert resp.status_code == 403

    def test_record_reading_staff_allowed(self):
        meter = _meter(AssetFactory())
        resp = _client(_user("staff", is_staff=True)).post(
            _record_url(meter), data={"value": 1}, format="json"
        )
        assert resp.status_code == 201

    def test_sig_admin_can_record_on_their_asset(self):
        user = _user("sig_lead")
        sig = Group.objects.create(name="Wood SIG")
        SIGAdmin.objects.create(user=user, group=sig)
        asset = AssetFactory(owning_group=sig)
        meter = _meter(asset)
        resp = _client(user).post(_record_url(meter), data={"value": 3}, format="json")
        assert resp.status_code == 201

    def test_meter_list_requires_staff(self):
        _meter(AssetFactory())
        assert _client(_user("vol2")).get("/api/inventory/asset-meters/").status_code == 403
        assert (
            _client(_user("staff2", is_staff=True)).get("/api/inventory/asset-meters/").status_code
            == 200
        )

    def test_meter_create_requires_staff(self):
        asset = AssetFactory()
        payload = {"asset": str(asset.id), "name": "New", "meter_type": AssetMeter.MeterType.CYCLES}
        assert (
            _client(_user("vol3"))
            .post("/api/inventory/asset-meters/", data=payload, format="json")
            .status_code
            == 403
        )
        assert (
            _client(_user("staff3", is_staff=True))
            .post("/api/inventory/asset-meters/", data=payload, format="json")
            .status_code
            == 201
        )

    def test_readings_get_requires_authentication(self):
        meter = _meter(AssetFactory())
        apply_reading(
            meter,
            ReadingSpec(
                source=AssetMeterReading.Source.MANUAL,
                observed_at=timezone.now(),
                absolute=Decimal("1"),
            ),
        )
        url = f"/api/inventory/asset-meter-readings/?meter={meter.id}"
        assert _client().get(url).status_code in (401, 403)
        resp = _client(_user("reader")).get(url)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1


# --------------------------------------------------------------------------- #
# nested serializer surface
# --------------------------------------------------------------------------- #
class TestAssetDetailEmbedsMeters:
    def test_asset_detail_includes_meters(self):
        asset = AssetFactory()
        _meter(asset, name="Runtime", meter_type=AssetMeter.MeterType.RUNTIME_HOURS, unit="hours")
        resp = _client(_user("viewer")).get(f"/api/inventory/assets/{asset.id}/")
        assert resp.status_code == 200
        meters = resp.json()["meters"]
        assert len(meters) == 1
        assert meters[0]["name"] == "Runtime"
        assert meters[0]["meter_type_display"] == "Runtime hours"
        assert "current_value" in meters[0]
