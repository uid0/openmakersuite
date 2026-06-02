"""ePaper firmware OTA: check endpoint + rollout wave-advancement.

Covers the HTTPS-pull dispatch model:

* ``GET /api/forgekey/epaper/<display_id>/firmware-check/`` returns
  204 when the panel is on the right firmware (or has no target),
  200 with a download-ready payload when the rollout has promoted it.
* ``advance_rollout`` walks active rollouts in waves of
  ``batch_size_percent`` and flips to COMPLETED when the fleet is on
  the target.
"""

from __future__ import annotations

from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from forgekey.models import DeviceType, EPaperDisplay, EpaperFirmwareRollout, FirmwareVersion
from forgekey.services.epaper_firmware_rollout import advance_rollout
from forgekey.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# ----- fixtures --------------------------------------------------------------


@pytest.fixture
def device_type():
    dt, _ = DeviceType.objects.get_or_create(
        code="epaper_screen", defaults={"name": "E-Paper Screen"}
    )
    return dt


def _firmware(device_type, version: str) -> FirmwareVersion:
    from django.core.files.base import ContentFile

    fv = FirmwareVersion(
        version=version,
        device_type=device_type,
        mandatory=False,
    )
    # Save with a content blob so sha256 + (empty) signature get computed.
    fv.firmware_file.save(
        f"epaper-{version}.bin",
        ContentFile(b"\x00FIRMWARE\xff" * 16),
        save=True,
    )
    return fv


def _display(*, firmware_version: str = "", target=None, is_active=True) -> EPaperDisplay:
    d = EPaperDisplay.objects.create(
        firmware_version=firmware_version,
        target_firmware_version=target,
        is_active=is_active,
    )
    return d


# ----- check endpoint --------------------------------------------------------


def _check(client: APIClient, display, current: str = ""):
    url = reverse("forgekey:epaper-firmware-check", kwargs={"display_id": display.pk})
    if current:
        url += f"?current={current}"
    return client.get(url)


def test_check_returns_204_when_no_target():
    display = _display(firmware_version="0.1.0")
    resp = _check(APIClient(), display, current="0.1.0")
    assert resp.status_code == 204


def test_check_returns_204_when_target_matches_current(device_type):
    fv = _firmware(device_type, "0.2.0")
    display = _display(firmware_version="0.2.0", target=fv)
    resp = _check(APIClient(), display, current="0.2.0")
    assert resp.status_code == 204


def test_check_returns_200_with_payload_when_target_differs(device_type):
    fv = _firmware(device_type, "0.2.0")
    display = _display(firmware_version="0.1.0", target=fv)

    resp = _check(APIClient(), display, current="0.1.0")

    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == "0.2.0"
    assert body["sha256"] == fv.sha256
    # Download URL carries the HMAC token + expiry the download endpoint expects.
    assert "token=" in body["url"]
    assert "exp=" in body["url"]
    # mandatory + signature/signing_cert keys present (signature empty here
    # because no FirmwareSigningKey is configured in this test environment).
    assert body["mandatory"] is False


def test_check_stamps_reported_current_onto_display(device_type):
    fv = _firmware(device_type, "0.2.0")
    display = _display(firmware_version="", target=fv)
    _check(APIClient(), display, current="0.1.5")
    display.refresh_from_db()
    assert display.firmware_version == "0.1.5"


def test_check_404_for_unknown_display():
    import uuid

    url = reverse("forgekey:epaper-firmware-check", kwargs={"display_id": uuid.uuid4()})
    resp = APIClient().get(url)
    assert resp.status_code == 404


def test_check_404_for_inactive_display(device_type):
    fv = _firmware(device_type, "0.2.0")
    display = _display(firmware_version="0.1.0", target=fv, is_active=False)
    resp = _check(APIClient(), display, current="0.1.0")
    assert resp.status_code == 404


# ----- rollout wave-advancement ---------------------------------------------


def _staff_client():
    user = UserFactory(is_staff=True, is_superuser=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def _rollout(fv, *, batch_size_percent=50, status=EpaperFirmwareRollout.STATUS_ACTIVE):
    r = EpaperFirmwareRollout.objects.create(
        firmware_version=fv,
        batch_size_percent=batch_size_percent,
        interval_minutes=1,
        status=status,
    )
    return r


def test_advance_promotes_first_wave(device_type):
    fv = _firmware(device_type, "0.2.0")
    # 4 panels, batch=50% → wave of 2
    panels = [_display(firmware_version="0.1.0") for _ in range(4)]
    rollout = _rollout(fv, batch_size_percent=50)

    promoted = advance_rollout(rollout)

    assert promoted == 2
    # Refresh & count how many now have the target set.
    on_target = sum(
        1 for p in panels if EPaperDisplay.objects.get(pk=p.pk).target_firmware_version_id == fv.id
    )
    assert on_target == 2
    rollout.refresh_from_db()
    assert rollout.status == EpaperFirmwareRollout.STATUS_ACTIVE
    assert rollout.last_advanced_at is not None


def test_advance_completes_when_fleet_caught_up(device_type):
    fv = _firmware(device_type, "0.2.0")
    for _ in range(2):
        _display(firmware_version="0.1.0")
    rollout = _rollout(fv, batch_size_percent=100)

    promoted = advance_rollout(rollout)

    assert promoted == 2
    rollout.refresh_from_db()
    assert rollout.status == EpaperFirmwareRollout.STATUS_COMPLETED
    assert rollout.completed_at is not None


def test_advance_skips_panels_already_on_target(device_type):
    fv = _firmware(device_type, "0.2.0")
    # 2 already on target, 2 not. batch=50% of 4 = 2.
    for _ in range(2):
        _display(firmware_version="0.2.0", target=fv)
    for _ in range(2):
        _display(firmware_version="0.1.0")
    rollout = _rollout(fv, batch_size_percent=50)

    promoted = advance_rollout(rollout)

    # All 2 of the remaining panels promoted; fleet now fully caught up.
    assert promoted == 2
    rollout.refresh_from_db()
    assert rollout.status == EpaperFirmwareRollout.STATUS_COMPLETED


def test_advance_skips_inactive_rollouts(device_type):
    fv = _firmware(device_type, "0.2.0")
    _display(firmware_version="0.1.0")
    rollout = _rollout(fv, status=EpaperFirmwareRollout.STATUS_PAUSED)

    promoted = advance_rollout(rollout)

    assert promoted == 0
    rollout.refresh_from_db()
    assert rollout.status == EpaperFirmwareRollout.STATUS_PAUSED


# ----- viewset start/pause/cancel/advance -----------------------------------


def test_start_action_transitions_draft_to_active(device_type):
    fv = _firmware(device_type, "0.2.0")
    rollout = _rollout(fv, status=EpaperFirmwareRollout.STATUS_DRAFT)
    client, _ = _staff_client()

    url = reverse("forgekey:epaper-firmware-rollout-start", kwargs={"pk": rollout.pk})
    resp = client.post(url)

    assert resp.status_code == 200, resp.data
    rollout.refresh_from_db()
    assert rollout.status == EpaperFirmwareRollout.STATUS_ACTIVE
    assert rollout.started_at is not None


def test_pause_action_blocks_non_active(device_type):
    fv = _firmware(device_type, "0.2.0")
    rollout = _rollout(fv, status=EpaperFirmwareRollout.STATUS_DRAFT)
    client, _ = _staff_client()

    url = reverse("forgekey:epaper-firmware-rollout-pause", kwargs={"pk": rollout.pk})
    resp = client.post(url)

    assert resp.status_code == 400


def test_advance_action_promotes_and_returns_count(device_type):
    fv = _firmware(device_type, "0.2.0")
    _display(firmware_version="0.1.0")
    rollout = _rollout(fv, batch_size_percent=100)
    client, _ = _staff_client()

    url = reverse("forgekey:epaper-firmware-rollout-advance", kwargs={"pk": rollout.pk})
    resp = client.post(url)

    assert resp.status_code == 200, resp.data
    assert resp.data["promoted"] == 1
