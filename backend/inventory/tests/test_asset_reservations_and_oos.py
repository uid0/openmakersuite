"""API tests for AssetReservationViewSet + AssetOutOfServiceViewSet.

Covers the staff / SIG admin gate, the overlap-blocking on
reservations, the single-open-at-a-time invariant on out-of-service
events, and the soft-cancel + restore flows.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from inventory.models import AssetOutOfService, AssetReservation
from inventory.tests.factories import AssetFactory
from membership.models import SIGAdmin

User = get_user_model()
pytestmark = pytest.mark.django_db

RES_URL = "/api/inventory/asset-reservations/"
OOS_URL = "/api/inventory/asset-out-of-service/"


def _user(username, **flags):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=get_random_string(24),
        **flags,
    )


def _client(user=None):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user=user)
    return c


@pytest.fixture
def staff():
    return _user("staffer", is_staff=True)


@pytest.fixture
def member():
    return _user("member")


@pytest.fixture
def sig_group():
    return Group.objects.create(name="Welding SIG")


@pytest.fixture
def sig_admin(sig_group):
    user = _user("welder-admin")
    SIGAdmin.objects.create(user=user, group=sig_group, is_active=True)
    return user


@pytest.fixture
def sig_asset(sig_group):
    return AssetFactory(owning_group=sig_group)


@pytest.fixture
def asset():
    return AssetFactory()


def _reservation_payload(asset, *, hours_from_now=1, hours_long=2, **overrides):
    starts = timezone.now() + timedelta(hours=hours_from_now)
    ends = starts + timedelta(hours=hours_long)
    data = {
        "asset": str(asset.id),
        "title": "Welding Class — Jane Doe",
        "starts_at": starts.isoformat(),
        "ends_at": ends.isoformat(),
        "notes": "Bay 3, beginner curriculum",
    }
    data.update(overrides)
    return data


def _oos_payload(asset, **overrides):
    data = {
        "asset": str(asset.id),
        "reason": "Spindle bearing seized; needs replacement",
        "expected_return_at": (timezone.now() + timedelta(days=7)).isoformat(),
    }
    data.update(overrides)
    return data


class TestReservationCreate:
    def test_anonymous_rejected(self, asset):
        resp = _client().post(RES_URL, _reservation_payload(asset), format="json")
        assert resp.status_code in (401, 403)

    def test_volunteer_rejected(self, asset, member):
        resp = _client(member).post(RES_URL, _reservation_payload(asset), format="json")
        assert resp.status_code == 403

    def test_staff_can_reserve(self, asset, staff):
        resp = _client(staff).post(RES_URL, _reservation_payload(asset), format="json")
        assert resp.status_code == 201, resp.data
        assert resp.data["reserved_by_username"] == staff.username
        assert resp.data["is_active"] is True

    def test_sig_admin_can_reserve_own_sig_asset(self, sig_asset, sig_admin):
        resp = _client(sig_admin).post(RES_URL, _reservation_payload(sig_asset), format="json")
        assert resp.status_code == 201, resp.data

    def test_sig_admin_blocked_on_other_sig_asset(self, asset, sig_admin):
        resp = _client(sig_admin).post(RES_URL, _reservation_payload(asset), format="json")
        assert resp.status_code == 403

    def test_ends_before_starts_rejected(self, asset, staff):
        starts = timezone.now() + timedelta(hours=2)
        ends = starts - timedelta(hours=1)
        resp = _client(staff).post(
            RES_URL,
            _reservation_payload(asset, starts_at=starts.isoformat(), ends_at=ends.isoformat()),
            format="json",
        )
        assert resp.status_code == 400
        # Errors are wrapped by the project-wide envelope:
        # {error: {code, message, details: {field: [msg]}}}
        details = resp.data.get("error", {}).get("details", resp.data)
        assert "ends_at" in details

    def test_overlap_blocked(self, asset, staff):
        first = _client(staff).post(RES_URL, _reservation_payload(asset), format="json")
        assert first.status_code == 201
        # Same window — should collide.
        dup = _client(staff).post(RES_URL, _reservation_payload(asset), format="json")
        assert dup.status_code == 400
        details = dup.data.get("error", {}).get("details", dup.data)
        assert "starts_at" in details

    def test_back_to_back_allowed(self, asset, staff):
        starts = timezone.now() + timedelta(hours=1)
        mid = starts + timedelta(hours=2)
        end = mid + timedelta(hours=2)
        first = _client(staff).post(
            RES_URL,
            _reservation_payload(asset, starts_at=starts.isoformat(), ends_at=mid.isoformat()),
            format="json",
        )
        second = _client(staff).post(
            RES_URL,
            _reservation_payload(asset, starts_at=mid.isoformat(), ends_at=end.isoformat()),
            format="json",
        )
        assert first.status_code == 201
        assert second.status_code == 201


class TestReservationCancel:
    def test_cancel_marks_cancelled_at(self, asset, staff):
        created = _client(staff).post(RES_URL, _reservation_payload(asset), format="json")
        rid = created.data["id"]
        resp = _client(staff).delete(f"{RES_URL}{rid}/")
        assert resp.status_code == 200, resp.data
        assert resp.data["cancelled_at"] is not None
        assert resp.data["is_active"] is False

    def test_cancelled_does_not_block_new_overlap(self, asset, staff):
        first = _client(staff).post(RES_URL, _reservation_payload(asset), format="json")
        rid = first.data["id"]
        _client(staff).delete(f"{RES_URL}{rid}/")
        # Same window as the cancelled one should now succeed.
        retry = _client(staff).post(RES_URL, _reservation_payload(asset), format="json")
        assert retry.status_code == 201


class TestReservationList:
    def test_filter_by_asset(self, asset, staff):
        other = AssetFactory()
        _client(staff).post(RES_URL, _reservation_payload(asset), format="json")
        _client(staff).post(RES_URL, _reservation_payload(other), format="json")
        resp = _client(staff).get(f"{RES_URL}?asset={asset.id}")
        assert resp.status_code == 200
        rows = resp.data["results"] if "results" in resp.data else resp.data
        assert len(rows) == 1, rows
        # resp.data is pre-JSON Python; FK ids surface as UUID instances.
        assert str(rows[0]["asset"]) == str(asset.id)

    def test_active_filter_excludes_cancelled_and_past(self, asset, staff):
        # past
        past_start = timezone.now() - timedelta(days=2)
        past_end = past_start + timedelta(hours=1)
        AssetReservation.objects.create(
            asset=asset,
            title="past",
            reserved_by=staff,
            starts_at=past_start,
            ends_at=past_end,
        )
        # cancelled
        cancelled = AssetReservation.objects.create(
            asset=asset,
            title="cancelled",
            reserved_by=staff,
            starts_at=timezone.now() + timedelta(hours=4),
            ends_at=timezone.now() + timedelta(hours=5),
        )
        cancelled.cancelled_at = timezone.now()
        cancelled.save()
        # future active
        AssetReservation.objects.create(
            asset=asset,
            title="future",
            reserved_by=staff,
            starts_at=timezone.now() + timedelta(hours=1),
            ends_at=timezone.now() + timedelta(hours=2),
        )
        resp = _client(staff).get(f"{RES_URL}?asset={asset.id}&active=true")
        rows = resp.data["results"] if "results" in resp.data else resp.data
        titles = {r["title"] for r in rows}
        assert titles == {"future"}


class TestOutOfServiceCreate:
    def test_anonymous_rejected(self, asset):
        resp = _client().post(OOS_URL, _oos_payload(asset), format="json")
        assert resp.status_code in (401, 403)

    def test_volunteer_rejected(self, asset, member):
        resp = _client(member).post(OOS_URL, _oos_payload(asset), format="json")
        assert resp.status_code == 403

    def test_staff_can_open(self, asset, staff):
        resp = _client(staff).post(OOS_URL, _oos_payload(asset), format="json")
        assert resp.status_code == 201, resp.data
        assert resp.data["placed_by_username"] == staff.username
        assert resp.data["is_open"] is True

    def test_sig_admin_can_open_own_sig_asset(self, sig_asset, sig_admin):
        resp = _client(sig_admin).post(OOS_URL, _oos_payload(sig_asset), format="json")
        assert resp.status_code == 201

    def test_cannot_open_second_while_first_open(self, asset, staff):
        first = _client(staff).post(OOS_URL, _oos_payload(asset), format="json")
        assert first.status_code == 201
        second = _client(staff).post(OOS_URL, _oos_payload(asset), format="json")
        assert second.status_code == 400


class TestOutOfServiceRestore:
    def test_restore_marks_restored_at_and_by(self, asset, staff):
        created = _client(staff).post(OOS_URL, _oos_payload(asset), format="json")
        oid = created.data["id"]
        resp = _client(staff).post(f"{OOS_URL}{oid}/restore/")
        assert resp.status_code == 200, resp.data
        assert resp.data["restored_at"] is not None
        assert resp.data["restored_by_username"] == staff.username
        assert resp.data["is_open"] is False

    def test_can_open_again_after_restore(self, asset, staff):
        first = _client(staff).post(OOS_URL, _oos_payload(asset), format="json")
        _client(staff).post(f"{OOS_URL}{first.data['id']}/restore/")
        second = _client(staff).post(OOS_URL, _oos_payload(asset), format="json")
        assert second.status_code == 201

    def test_volunteer_cannot_restore(self, asset, staff, member):
        created = _client(staff).post(OOS_URL, _oos_payload(asset), format="json")
        oid = created.data["id"]
        resp = _client(member).post(f"{OOS_URL}{oid}/restore/")
        assert resp.status_code == 403


class TestOutOfServiceList:
    def test_open_filter(self, asset, staff):
        opened = _client(staff).post(OOS_URL, _oos_payload(asset), format="json")
        # Close one
        AssetOutOfService.objects.create(
            asset=asset,
            placed_by=staff,
            reason="historical",
            restored_at=timezone.now(),
            restored_by=staff,
        )
        resp = _client(staff).get(f"{OOS_URL}?asset={asset.id}&open=true")
        rows = resp.data["results"] if "results" in resp.data else resp.data
        ids = {str(r["id"]) for r in rows}
        assert ids == {str(opened.data["id"])}
