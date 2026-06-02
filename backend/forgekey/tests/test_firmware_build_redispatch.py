"""Redispatch action on FirmwareBuildViewSet + the _clone_url PAT helper."""

from __future__ import annotations

from unittest.mock import patch

from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from forgekey.models import DeviceType, FirmwareBuild
from forgekey.services.firmware_build import _clone_url
from forgekey.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _build(status=FirmwareBuild.STATUS_QUEUED) -> FirmwareBuild:
    dt = DeviceType.objects.create(name="People counter", code="people-counter")
    return FirmwareBuild.objects.create(
        device_type=dt,
        pio_env="seeed_xiao_esp32s3",
        version="0.8.0",
        source_ref="main",
        status=status,
    )


def _staff_client():
    user = UserFactory(is_staff=True, is_superuser=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


# ----- redispatch DRF action -------------------------------------------------


def test_redispatch_queued_build_dispatches_celery_task():
    build = _build()
    client, _ = _staff_client()
    url = reverse("forgekey:firmware-build-redispatch", args=[build.pk])

    with patch("forgekey.tasks.build_firmware.delay") as delay:
        resp = client.post(url)

    assert resp.status_code == 200, resp.json()
    delay.assert_called_once_with(str(build.pk))


def test_redispatch_failed_build_resets_state_and_dispatches():
    build = _build(status=FirmwareBuild.STATUS_FAILED)
    build.error_message = "previous run: timed out"
    build.log = "stale log output"
    build.save(update_fields=["error_message", "log"])

    client, _ = _staff_client()
    url = reverse("forgekey:firmware-build-redispatch", args=[build.pk])

    with patch("forgekey.tasks.build_firmware.delay") as delay:
        resp = client.post(url)

    assert resp.status_code == 200
    delay.assert_called_once_with(str(build.pk))
    build.refresh_from_db()
    assert build.status == FirmwareBuild.STATUS_QUEUED
    assert build.error_message == ""
    assert build.log == ""
    assert build.completed_at is None
    assert build.started_at is None


@pytest.mark.parametrize(
    "status",
    [
        FirmwareBuild.STATUS_BUILDING,
        FirmwareBuild.STATUS_SUCCEEDED,
        FirmwareBuild.STATUS_CANCELLED,
    ],
)
def test_redispatch_refuses_non_queued_non_failed_states(status):
    build = _build(status=status)
    client, _ = _staff_client()
    url = reverse("forgekey:firmware-build-redispatch", args=[build.pk])

    with patch("forgekey.tasks.build_firmware.delay") as delay:
        resp = client.post(url)

    assert resp.status_code == 409
    delay.assert_not_called()


def test_redispatch_requires_staff():
    build = _build()
    user = UserFactory(is_staff=False, is_superuser=False)
    client = APIClient()
    client.force_authenticate(user=user)
    url = reverse("forgekey:firmware-build-redispatch", args=[build.pk])

    resp = client.post(url)
    assert resp.status_code in (401, 403)


# ----- _clone_url PAT helper -------------------------------------------------


def test_clone_url_injects_pat_into_https_url():
    out = _clone_url("https://github.com/uid0/ForgeKey.git", "ghp_secret123")
    assert out == "https://x-access-token:ghp_secret123@github.com/uid0/ForgeKey.git"


def test_clone_url_passthrough_when_no_token():
    out = _clone_url("https://github.com/uid0/ForgeKey.git", "")
    assert out == "https://github.com/uid0/ForgeKey.git"


def test_clone_url_passthrough_for_ssh_urls_even_with_token():
    """SSH URLs use the mounted deploy key; injecting a PAT would do nothing."""
    out = _clone_url("git@github.com:uid0/ForgeKey.git", "ghp_secret123")
    assert out == "git@github.com:uid0/ForgeKey.git"


def test_clone_url_does_not_double_inject_creds():
    """If the URL already has credentials, leave it alone (operator override)."""
    pre = "https://someuser:sometoken@github.com/uid0/ForgeKey.git"
    out = _clone_url(pre, "ghp_secret123")
    assert out == pre
