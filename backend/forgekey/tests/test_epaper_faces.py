"""Tests for the OOS + reservation faces and the rotation picker.

Layered on top of test_epaper.py — the PM face behaviour stays under
that file, this one covers:

- _pick_face precedence (OOS > reservation+PM rotation > reservation > PM).
- Weighted rotation (event:pm ratios produce the right distribution).
- render_oos_image / render_reservation_image return valid PNGs.
- compute_display_etag changes when the chosen face changes, and is
  stable for the same face when the underlying data hasn't moved.
- EPaperDisplayImageView advances rotation_counter on each fetch.
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.crypto import get_random_string

import pytest
from PIL import Image

from forgekey.models import EPaperDisplay
from forgekey.services.epaper_render import (
    FACE_OOS,
    FACE_PM,
    FACE_RESERVATION,
    _pick_face,
    compute_display_etag,
    render_image,
    render_oos_image,
    render_reservation_image,
)
from inventory.models import (
    Asset,
    AssetOutOfService,
    AssetReservation,
    Location,
    MaintenanceItem,
)

pytestmark = pytest.mark.django_db
User = get_user_model()


def _user() -> User:
    return User.objects.create_user(
        username=get_random_string(12),
        email=f"{get_random_string(8)}@example.com",
        password=get_random_string(24),
        is_staff=True,
    )


def _asset() -> Asset:
    suffix = uuid4().hex[:8].upper()
    location = Location.objects.create(name=f"OOS/Res Loc {suffix}")
    return Asset.objects.create(name="Welder", location=location, asset_tag=f"TEST-{suffix}")


def _display(asset: Asset, **overrides) -> EPaperDisplay:
    return EPaperDisplay.objects.create(asset=asset, **overrides)


def _current_reservation(
    asset: Asset, user: User, *, title: str = "Welding 101"
) -> AssetReservation:
    now = timezone.now()
    return AssetReservation.objects.create(
        asset=asset,
        title=title,
        reserved_by=user,
        starts_at=now - timedelta(minutes=10),
        ends_at=now + timedelta(hours=2),
    )


def _open_oos(asset: Asset, user: User) -> AssetOutOfService:
    return AssetOutOfService.objects.create(
        asset=asset,
        placed_by=user,
        reason="Belt snapped",
        expected_return_at=timezone.now() + timedelta(days=3),
    )


def _eligible_pm(asset: Asset) -> MaintenanceItem:
    item = MaintenanceItem.objects.create(asset=asset, title="Lube ways", interval_days=30)
    # Make it overdue so it's definitely the "next due item".
    item.last_completed_at = timezone.now() - timedelta(days=60)
    item.save(update_fields=["last_completed_at"])
    return item


# ---------------------------------------------------------------------------
# Face precedence
# ---------------------------------------------------------------------------


class TestPickFacePrecedence:
    def test_oos_preempts_reservation_and_pm(self):
        u = _user()
        a = _asset()
        d = _display(a)
        _eligible_pm(a)
        _current_reservation(a, u)
        _open_oos(a, u)
        face, source = _pick_face(a, d)
        assert face == FACE_OOS
        assert isinstance(source, AssetOutOfService)

    def test_reservation_only_when_no_pm(self):
        u = _user()
        a = _asset()
        d = _display(a)
        _current_reservation(a, u)
        face, source = _pick_face(a, d)
        assert face == FACE_RESERVATION
        assert isinstance(source, AssetReservation)

    def test_pm_only_when_no_reservation(self):
        a = _asset()
        d = _display(a)
        _eligible_pm(a)
        face, source = _pick_face(a, d)
        assert face == FACE_PM
        assert source is None

    def test_default_pm_when_nothing(self):
        a = _asset()
        d = _display(a)
        face, source = _pick_face(a, d)
        assert face == FACE_PM
        assert source is None

    def test_past_reservation_does_not_drive(self):
        u = _user()
        a = _asset()
        d = _display(a)
        AssetReservation.objects.create(
            asset=a,
            title="past",
            reserved_by=u,
            starts_at=timezone.now() - timedelta(days=2),
            ends_at=timezone.now() - timedelta(days=1),
        )
        face, _ = _pick_face(a, d)
        assert face == FACE_PM

    def test_cancelled_reservation_does_not_drive(self):
        u = _user()
        a = _asset()
        d = _display(a)
        r = _current_reservation(a, u)
        r.cancelled_at = timezone.now()
        r.save()
        face, _ = _pick_face(a, d)
        assert face == FACE_PM

    def test_restored_oos_does_not_drive(self):
        u = _user()
        a = _asset()
        d = _display(a)
        oos = _open_oos(a, u)
        oos.restored_at = timezone.now()
        oos.save()
        face, _ = _pick_face(a, d)
        assert face == FACE_PM


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


class TestPickFaceRotation:
    def test_default_2_1_ratio(self):
        u = _user()
        a = _asset()
        d = _display(a, event_face_weight=2, pm_face_weight=1)
        _eligible_pm(a)
        _current_reservation(a, u)
        # 6 simulated renders: counter 0..5. With 2:1, we expect
        # event/event/pm repeating.
        faces = []
        for i in range(6):
            d.rotation_counter = i
            face, _ = _pick_face(a, d)
            faces.append(face)
        # Two thirds reservation.
        event_count = sum(1 for f in faces if f == FACE_RESERVATION)
        pm_count = sum(1 for f in faces if f == FACE_PM)
        assert event_count == 4
        assert pm_count == 2

    def test_zero_event_weight_means_always_pm(self):
        u = _user()
        a = _asset()
        d = _display(a, event_face_weight=0, pm_face_weight=1)
        _eligible_pm(a)
        _current_reservation(a, u)
        for i in range(5):
            d.rotation_counter = i
            face, _ = _pick_face(a, d)
            assert face == FACE_PM

    def test_zero_both_weights_picks_pm(self):
        u = _user()
        a = _asset()
        d = _display(a, event_face_weight=0, pm_face_weight=0)
        _eligible_pm(a)
        _current_reservation(a, u)
        face, _ = _pick_face(a, d)
        assert face == FACE_PM

    def test_3_1_ratio(self):
        u = _user()
        a = _asset()
        d = _display(a, event_face_weight=3, pm_face_weight=1)
        _eligible_pm(a)
        _current_reservation(a, u)
        events = 0
        pms = 0
        for i in range(8):
            d.rotation_counter = i
            face, _ = _pick_face(a, d)
            if face == FACE_RESERVATION:
                events += 1
            else:
                pms += 1
        assert events == 6
        assert pms == 2


# ---------------------------------------------------------------------------
# Image bytes
# ---------------------------------------------------------------------------


class TestFaceImages:
    def test_oos_image_is_valid_png(self):
        u = _user()
        a = _asset()
        oos = _open_oos(a, u)
        png = render_oos_image(a, oos)
        image = Image.open(BytesIO(png))
        assert image.format == "PNG"
        assert image.size == (800, 480)

    def test_reservation_image_is_valid_png(self):
        u = _user()
        a = _asset()
        r = _current_reservation(a, u)
        png = render_reservation_image(a, r)
        image = Image.open(BytesIO(png))
        assert image.format == "PNG"
        assert image.size == (800, 480)

    def test_render_image_dispatches_to_oos(self):
        u = _user()
        a = _asset()
        d = _display(a)
        _open_oos(a, u)
        png, face = render_image(a, d)
        assert face == FACE_OOS
        assert Image.open(BytesIO(png)).size == (800, 480)


# ---------------------------------------------------------------------------
# Etag
# ---------------------------------------------------------------------------


class TestDisplayEtag:
    def test_etag_changes_when_face_changes(self):
        u = _user()
        a = _asset()
        d = _display(a)
        _eligible_pm(a)
        pm_only = compute_display_etag(a, d)
        _open_oos(a, u)
        with_oos = compute_display_etag(a, d)
        assert pm_only != with_oos

    def test_etag_stable_for_same_face(self):
        u = _user()
        a = _asset()
        d = _display(a)
        _current_reservation(a, u)
        first = compute_display_etag(a, d)
        second = compute_display_etag(a, d)
        assert first == second

    def test_etag_changes_when_rotation_advances_with_competition(self):
        u = _user()
        a = _asset()
        d = _display(a, event_face_weight=2, pm_face_weight=1)
        _eligible_pm(a)
        _current_reservation(a, u)
        d.rotation_counter = 0
        first = compute_display_etag(a, d)
        d.rotation_counter = 2  # crosses event→pm boundary
        second = compute_display_etag(a, d)
        assert first != second


# ---------------------------------------------------------------------------
# Image endpoint integration — counter advances per fetch
# ---------------------------------------------------------------------------


class TestImageEndpointRotation:
    def _url(self, display_id) -> str:
        return f"/api/forgekey/epaper/{display_id}/image.png"

    def test_counter_advances_on_each_fetch(self, client):
        u = _user()
        a = _asset()
        d = _display(a, event_face_weight=2, pm_face_weight=1)
        _eligible_pm(a)
        _current_reservation(a, u)
        baseline = d.rotation_counter
        client.get(self._url(d.pk))
        d.refresh_from_db()
        assert d.rotation_counter == baseline + 1
        client.get(self._url(d.pk))
        d.refresh_from_db()
        assert d.rotation_counter == baseline + 2

    def test_304_returns_no_advance(self, client):
        a = _asset()
        d = _display(a)
        _eligible_pm(a)
        first = client.get(self._url(d.pk))
        d.refresh_from_db()
        counter_after_first = d.rotation_counter
        etag = first["ETag"].strip('"')
        nm = client.get(self._url(d.pk), HTTP_IF_NONE_MATCH=f'"{etag}"')
        assert nm.status_code == 304
        d.refresh_from_db()
        assert d.rotation_counter == counter_after_first
