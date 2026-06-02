"""Tests for the firmware build pipeline.

The real ``git`` / ``pio`` subprocesses can't run in CI (no toolchain in the
app image — the build runs on the self-hosted firmware-builder worker), so the
build orchestration is tested with ``subprocess.run`` + the active-CA / pubkey
accessors mocked.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from forgekey.models import DeviceType, FirmwareBuild
from forgekey.services import firmware_build as fb
from notifications.models import Notification

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_api_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def device_type():
    dt, _ = DeviceType.objects.get_or_create(
        code="epaper_screen", defaults={"name": "E-paper screen"}
    )
    return dt


def _fake_run(cmd, cwd=None, capture_output=True, text=True, timeout=None):
    """Stand in for subprocess.run: materialise the files the real git/pio
    would, and report success."""
    result = MagicMock()
    result.returncode = 0
    result.stderr = ""
    result.stdout = ""
    if cmd[:2] == ["git", "clone"]:
        repo = Path(cmd[-1])
        (repo / "src" / "security").mkdir(parents=True, exist_ok=True)
        result.stdout = "Cloning into 'ForgeKey'...\n"
    elif cmd[:2] == ["git", "rev-parse"]:
        result.stdout = "abc1234deadbeefcafebabe\n"
    elif cmd[0] == "pio":
        env = cmd[-1]
        build_dir = Path(cwd) / ".pio" / "build" / env
        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / "firmware.bin").write_bytes(b"\x00FIRMWARE\xff" * 16)
        result.stdout = "Building...\n[SUCCESS]\n"
    return result


_FAKE_CA = MagicMock()
_FAKE_CA.cert_pem = "-----BEGIN CERTIFICATE-----\nMIIBfakeCA==\n-----END CERTIFICATE-----\n"
_FAKE_PUBKEY = "-----BEGIN PUBLIC KEY-----\nMFkwEfake==\n-----END PUBLIC KEY-----\n"


class TestRunFirmwareBuild:
    def test_build_succeeds_and_uploads_firmware_version(self, device_type):
        build = FirmwareBuild.objects.create(
            device_type=device_type,
            pio_env="seeed_xiao_epaper",
            source_ref="main",
            version="9.9.9",
        )
        with (
            patch("forgekey.models.CertificateAuthority.get_active", return_value=_FAKE_CA),
            patch(
                "forgekey.services.jwt_signing.get_jwt_public_key_pem",
                return_value=_FAKE_PUBKEY,
            ),
            patch("forgekey.services.firmware_build.subprocess.run", side_effect=_fake_run),
        ):
            result = fb.run_firmware_build(str(build.id))

        assert result["status"] == "succeeded", result
        build.refresh_from_db()
        assert build.status == FirmwareBuild.STATUS_SUCCEEDED
        assert build.firmware_version is not None
        assert build.firmware_version.version == "9.9.9"
        assert build.firmware_version.device_type_id == device_type.id
        assert build.ca_fingerprint  # SHA-256 of the injected CA
        assert build.commit_sha.startswith("abc1234")
        assert "[SUCCESS]" in build.log

    def test_build_failure_is_recorded(self, device_type):
        build = FirmwareBuild.objects.create(
            device_type=device_type, pio_env="x", source_ref="main", version="8.8.8"
        )

        def _boom(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            result.stderr = "fatal: repository not found"
            return result

        with (
            patch("forgekey.models.CertificateAuthority.get_active", return_value=_FAKE_CA),
            patch(
                "forgekey.services.jwt_signing.get_jwt_public_key_pem",
                return_value=_FAKE_PUBKEY,
            ),
            patch("forgekey.services.firmware_build.subprocess.run", side_effect=_boom),
        ):
            result = fb.run_firmware_build(str(build.id))

        assert result["status"] == "failed"
        build.refresh_from_db()
        assert build.status == FirmwareBuild.STATUS_FAILED
        assert build.error_message
        assert build.firmware_version is None

    def test_no_active_ca_fails_cleanly(self, device_type):
        build = FirmwareBuild.objects.create(
            device_type=device_type, pio_env="x", source_ref="main", version="7.7.7"
        )
        with patch("forgekey.models.CertificateAuthority.get_active", return_value=None):
            result = fb.run_firmware_build(str(build.id))
        assert result["status"] == "failed"
        build.refresh_from_db()
        assert "CertificateAuthority" in build.error_message


class TestBuildCompletionNotifications:
    """The requester gets an in-app notification when their build finishes."""

    def test_success_notifies_requester(self, device_type, admin_user):
        build = FirmwareBuild.objects.create(
            device_type=device_type,
            pio_env="seeed_xiao_epaper",
            source_ref="main",
            version="9.9.9",
            requested_by=admin_user,
        )
        with (
            patch("forgekey.models.CertificateAuthority.get_active", return_value=_FAKE_CA),
            patch(
                "forgekey.services.jwt_signing.get_jwt_public_key_pem",
                return_value=_FAKE_PUBKEY,
            ),
            patch("forgekey.services.firmware_build.subprocess.run", side_effect=_fake_run),
        ):
            fb.run_firmware_build(str(build.id))

        note = Notification.objects.filter(user=admin_user, type="success").first()
        assert note is not None
        assert "succeeded" in note.title.lower()
        assert note.metadata.get("build_id") == str(build.id)
        assert note.action_url == "/facilities/forgekey-rollouts"

    def test_failure_notifies_requester(self, device_type, admin_user):
        # The "no active CA" path fails fast without needing the subprocess mock.
        build = FirmwareBuild.objects.create(
            device_type=device_type,
            pio_env="x",
            source_ref="main",
            version="8.8.8",
            requested_by=admin_user,
        )
        with patch("forgekey.models.CertificateAuthority.get_active", return_value=None):
            fb.run_firmware_build(str(build.id))

        note = Notification.objects.filter(user=admin_user, type="error").first()
        assert note is not None
        assert "failed" in note.title.lower()

    def test_no_requester_means_no_notification(self, device_type):
        build = FirmwareBuild.objects.create(
            device_type=device_type, pio_env="x", source_ref="main", version="6.6.6"
        )
        with patch("forgekey.models.CertificateAuthority.get_active", return_value=None):
            fb.run_firmware_build(str(build.id))
        assert not Notification.objects.exists()


class TestFirmwareBuildViewSet:
    def test_staff_create_enqueues_build(self, admin_api_client, device_type):
        with patch("forgekey.tasks.build_firmware.delay") as mock_delay:
            resp = admin_api_client.post(
                reverse("forgekey:firmware-build-list"),
                {
                    "device_type": str(device_type.id),
                    "pio_env": "seeed_xiao_epaper",
                    "source_ref": "main",
                    "version": "1.2.3",
                },
                format="json",
            )
        assert resp.status_code == 201, resp.data
        build = FirmwareBuild.objects.get(version="1.2.3")
        assert build.requested_by is not None
        mock_delay.assert_called_once_with(str(build.id))

    def test_non_staff_cannot_create_build(self, device_type):
        user = User.objects.create_user(username="m", email="m@example.com", password="x" * 20)
        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.post(
            reverse("forgekey:firmware-build-list"),
            {
                "device_type": str(device_type.id),
                "pio_env": "x",
                "source_ref": "main",
                "version": "1.0",
            },
            format="json",
        )
        assert resp.status_code == 403
        assert not FirmwareBuild.objects.exists()

    def test_cancel_a_queued_build(self, admin_api_client, device_type):
        build = FirmwareBuild.objects.create(
            device_type=device_type, pio_env="x", source_ref="main", version="2.0.0"
        )
        resp = admin_api_client.post(
            reverse("forgekey:firmware-build-cancel", kwargs={"pk": build.id})
        )
        assert resp.status_code == 200, resp.data
        build.refresh_from_db()
        assert build.status == FirmwareBuild.STATUS_CANCELLED


class TestWriteSecurityHeaders:
    """Cover the contract for `_write_security_headers` that the firmware
    worker calls between `git clone` and `pio run`."""

    def _setup(self, tmp_path: Path) -> Path:
        (tmp_path / "src" / "security").mkdir(parents=True)
        # Place a sentinel `oms_ca.h` so we can prove the rewrite leaves
        # it alone — overwriting it with the OMS-internal CA would break
        # HTTPS server-cert verification on every flashed device.
        (tmp_path / "src/security/oms_ca.h").write_text(
            "/* canary — must survive _write_security_headers */\n"
        )
        return tmp_path

    def test_writes_forgekey_ca_with_extern(self, tmp_path):
        self._setup(tmp_path)
        fb._write_security_headers(
            tmp_path,
            ca_pem="-----BEGIN CERTIFICATE-----\nABC\n-----END CERTIFICATE-----",
            cmd_pubkey_pem="-----BEGIN PUBLIC KEY-----\nXYZ\n-----END PUBLIC KEY-----",
        )
        out = (tmp_path / "src/security/forgekey_ca.h").read_text()
        assert "#define FORGEKEY_INTERNAL_CA_PEM" in out
        # extern declaration is what consumer .cpp files link against.
        assert "extern const char* kForgekeyInternalCaPem;" in out

    def test_writes_command_pubkey_with_extern(self, tmp_path):
        self._setup(tmp_path)
        fb._write_security_headers(
            tmp_path,
            ca_pem="-----BEGIN CERTIFICATE-----\nABC\n-----END CERTIFICATE-----",
            cmd_pubkey_pem="-----BEGIN PUBLIC KEY-----\nXYZ\n-----END PUBLIC KEY-----",
        )
        out = (tmp_path / "src/security/oms_command_pubkey.h").read_text()
        assert "#define FORGEKEY_OMS_COMMAND_PUBKEY_PEM" in out
        # Regression: every consumer (command_validation.cpp, register.cpp)
        # needs this extern; an early version dropped it and every build
        # failed to compile.
        assert "extern const char* kOmsCommandPubKeyPem;" in out

    def test_does_not_touch_oms_ca(self, tmp_path):
        """oms_ca.h is the LE root for HTTPS server-cert verification —
        rewriting it with the OMS-internal CA would break every device's
        ability to verify the OMS HTTPS endpoint."""
        self._setup(tmp_path)
        fb._write_security_headers(
            tmp_path,
            ca_pem="-----BEGIN CERTIFICATE-----\nABC\n-----END CERTIFICATE-----",
            cmd_pubkey_pem="-----BEGIN PUBLIC KEY-----\nXYZ\n-----END PUBLIC KEY-----",
        )
        # Sentinel intact.
        assert (
            "/* canary — must survive _write_security_headers */"
            in (tmp_path / "src/security/oms_ca.h").read_text()
        )
