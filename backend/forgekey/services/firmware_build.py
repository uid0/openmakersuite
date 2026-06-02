"""Self-hosted firmware build worker logic.

Runs OFF the OMS app image — only the dedicated ``builds`` Celery worker
(which ships git + PlatformIO) executes this. It clones the ForgeKey repo at
a requested ref, writes the live OMS CA + command public key into the
firmware security headers (honored by the headers' ``#ifndef`` defaults),
builds with PlatformIO, and uploads the resulting binary as a signed
:class:`~forgekey.models.FirmwareVersion`.

Kept out of ``tasks.py`` so the orchestration is unit-testable with the git /
pio subprocess calls mocked.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess  # nosec B404 — fixed git/pio argv, no shell; see _run()
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.utils import timezone

logger = logging.getLogger(__name__)

# Security headers in the cloned checkout, overwritten with live values before
# the build. Two distinct CAs here, not one:
#
#   * forgekey_ca.h carries the OMS-internal CA cert — the root of the
#     mTLS / firmware-leaf trust chain (PR #33 on the ForgeKey repo). This
#     is what we inject from the active CertificateAuthority row on every
#     build, so each artifact is bound to the current operator CA.
#
#   * oms_ca.h carries the *public* CA that signs the OMS HTTPS server cert
#     (typically the Let's Encrypt root). That value doesn't change per
#     build and is refreshed by scripts/build/fetch-oms-ca.sh when LE
#     rotates roots — we do NOT touch it during automated builds, since
#     overwriting it with the OMS-internal CA would break HTTPS server-
#     cert verification on every device.
#
# (Previously oms_ca.h was getting the OMS-internal CA. Devices flashed
# from those builds couldn't have verified the OMS HTTPS endpoint; the bug
# never bit because no field device was flashed via the automated path yet.)
_FORGEKEY_CA_HEADER = "src/security/forgekey_ca.h"
_PUBKEY_HEADER = "src/security/oms_command_pubkey.h"

_DEFAULT_REPO_URL = "https://github.com/uid0/ForgeKey.git"
_LOG_TAIL_LIMIT = 20000
_BUILD_TIMEOUT_S = 25 * 60


def _clone_url(repo_url: str, token: str) -> str:
    """Inject a GitHub PAT into an HTTPS repo URL.

    Returns the URL unchanged when no token is configured (operators
    still on the SSH-key path) or when the URL isn't HTTPS (SSH URLs
    use the mounted key instead). When both are set, rewrites to
    ``https://x-access-token:<token>@host/path`` so git clones over
    HTTPS without ever shelling out to ssh-agent. The PAT can be a
    classic PAT with `repo:read` or a fine-grained PAT scoped to the
    ForgeKey repo's `Contents: Read` permission — read-only by design,
    since this worker only ever clones.
    """
    if not token or "://" not in repo_url:
        return repo_url
    scheme, rest = repo_url.split("://", 1)
    if scheme.lower() != "https":
        return repo_url
    # If someone already pre-baked credentials into the URL, leave it.
    if "@" in rest.split("/", 1)[0]:
        return repo_url
    return f"https://x-access-token:{token}@{rest}"


def _run(
    cmd: list[str],
    cwd: str,
    log_lines: list[str],
    redacted_cmd: list[str] | None = None,
) -> str:
    """Run a subprocess, capturing combined output into ``log_lines``.

    Raises ``RuntimeError`` on a non-zero exit so the caller records a failure.

    ``redacted_cmd`` is what gets logged when the actual command contains
    a secret (e.g. a PAT injected into a clone URL); the executed command
    is still ``cmd``. The error message on a non-zero exit uses
    ``redacted_cmd`` too, since the FirmwareBuild row's error_message
    field is visible to every staff user.
    """
    log_cmd = redacted_cmd if redacted_cmd is not None else cmd
    log_lines.append(f"$ {' '.join(log_cmd)}")
    # `cmd` is a fixed git/pio argv list (no shell=True); the only variable
    # parts (source_ref, pio_env) come from staff-created FirmwareBuild rows.
    proc = subprocess.run(  # nosec B603
        cmd, cwd=cwd, capture_output=True, text=True, timeout=_BUILD_TIMEOUT_S
    )
    if proc.stdout:
        log_lines.append(proc.stdout)
    if proc.stderr:
        log_lines.append(proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"`{' '.join(log_cmd)}` failed (exit {proc.returncode})")
    return proc.stdout or ""


def _c_string_literal(pem: str) -> str:
    """Render a PEM block as a multi-line C string-literal define body."""
    return " \\\n".join(f'"{line}\\n"' for line in pem.strip().splitlines()) + "\n"


def _write_security_headers(repo: Path, ca_pem: str, cmd_pubkey_pem: str) -> None:
    """Inject the live OMS-internal CA + command public key into the checkout.

    ``ca_pem`` lands in ``forgekey_ca.h`` (firmware-leaf-cert trust root),
    NOT ``oms_ca.h`` (HTTPS server-cert trust root). ``oms_ca.h`` is left
    untouched on purpose — see the module-level comment on _FORGEKEY_CA_HEADER.

    Both rewrites preserve the `extern const char* k…;` declaration that
    the original headers carry. Without it, every consumer .cpp that uses
    the symbol fails with `'kX' was not declared in this scope` even
    though the defining .cpp would still compile fine (it picks up the
    macro and does `const char* kX = MACRO;`).
    """
    (repo / _FORGEKEY_CA_HEADER).write_text(
        "#pragma once\n"
        "#define FORGEKEY_INTERNAL_CA_PEM \\\n" + _c_string_literal(ca_pem) + "\n"
        "extern const char* kForgekeyInternalCaPem;\n"
    )
    (repo / _PUBKEY_HEADER).write_text(
        "#pragma once\n"
        "#define FORGEKEY_OMS_COMMAND_PUBKEY_PEM \\\n" + _c_string_literal(cmd_pubkey_pem) + "\n"
        "extern const char* kOmsCommandPubKeyPem;\n"
    )


def _truncate(text: str) -> str:
    return text if len(text) <= _LOG_TAIL_LIMIT else text[-_LOG_TAIL_LIMIT:]


def _notify_requester(build, *, succeeded: bool) -> None:
    """Notify the user who queued a build that it finished.

    Best-effort and in-app (the notifications surface the frontend already
    polls): a notification failure must never mask the recorded build outcome.
    """
    requester = build.requested_by
    if requester is None:
        return
    try:
        from notifications.services import create_notification

        if succeeded:
            create_notification(
                user=requester,
                type="success",
                title="Firmware build succeeded",
                message=(
                    f"{build.pio_env} {build.version} built successfully and is "
                    "ready to roll out."
                ),
                action_url="/facilities/forgekey-rollouts",
                metadata={"build_id": str(build.pk), "kind": "firmware_build_succeeded"},
            )
        else:
            create_notification(
                user=requester,
                type="error",
                title="Firmware build failed",
                message=(
                    f"{build.pio_env} {build.version} failed to build: "
                    f"{build.error_message or 'see the build log'}"
                ),
                action_url="/facilities/forgekey-rollouts",
                metadata={"build_id": str(build.pk), "kind": "firmware_build_failed"},
            )
    except Exception:  # noqa: BLE001 — notification delivery must not break build recording
        logger.exception("Failed to notify %s about firmware build %s", requester, build.pk)


def run_firmware_build(build_id: str) -> dict:
    """Execute a :class:`~forgekey.models.FirmwareBuild` end to end.

    Clone → inject CA + pubkey → ``pio run`` → upload a signed FirmwareVersion.
    Records the outcome (status + log + error) on the build row; never raises.
    """
    from ..models import CertificateAuthority, FirmwareBuild, FirmwareVersion
    from .jwt_signing import get_jwt_public_key_pem

    build = FirmwareBuild.objects.select_related("device_type", "requested_by").get(pk=build_id)
    if build.status == FirmwareBuild.STATUS_CANCELLED:
        return {"status": "cancelled", "build_id": str(build.pk)}

    log_lines: list[str] = []
    build.status = FirmwareBuild.STATUS_BUILDING
    build.started_at = timezone.now()
    build.save(update_fields=["status", "started_at"])

    repo_url = getattr(settings, "FORGEKEY_FIRMWARE_REPO_URL", _DEFAULT_REPO_URL)
    pat = getattr(settings, "FORGEKEY_BUILDER_GITHUB_TOKEN", "") or ""
    clone_url = _clone_url(repo_url, pat)

    try:
        ca = CertificateAuthority.get_active()
        if ca is None:
            raise RuntimeError("No active CertificateAuthority to inject into the firmware.")
        ca_pem = ca.cert_pem
        ca_fingerprint = hashlib.sha256(ca_pem.encode("ascii")).hexdigest()
        cmd_pubkey_pem = get_jwt_public_key_pem()

        with tempfile.TemporaryDirectory(prefix="fkbuild-") as tmp:
            repo = Path(tmp) / "ForgeKey"
            # Use the PAT-injected URL for the actual clone, but show
            # the bare repo_url in the build log so the PAT never lands
            # in DB-stored output visible to other staff.
            _run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    build.source_ref,
                    clone_url,
                    str(repo),
                ],
                cwd=tmp,
                log_lines=log_lines,
                redacted_cmd=(
                    [
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        "--branch",
                        build.source_ref,
                        repo_url,
                        str(repo),
                    ]
                    if clone_url != repo_url
                    else None
                ),
            )
            commit = _run(["git", "rev-parse", "HEAD"], cwd=str(repo), log_lines=log_lines).strip()
            _write_security_headers(repo, ca_pem, cmd_pubkey_pem)
            _run(["pio", "run", "-e", build.pio_env], cwd=str(repo), log_lines=log_lines)

            bin_path = repo / ".pio" / "build" / build.pio_env / "firmware.bin"
            if not bin_path.exists():
                raise RuntimeError(f"Build produced no binary at {bin_path}")

            firmware = FirmwareVersion(
                version=build.version,
                device_type=build.device_type,
                mandatory=build.mandatory,
                release_notes=(
                    build.release_notes or f"Built from {build.source_ref}@{commit[:8]}"
                ),
                created_by=build.requested_by,
            )
            with bin_path.open("rb") as handle:
                # save() auto-computes sha256 + ECDSA signature.
                firmware.firmware_file.save(
                    f"{build.pio_env}-{build.version}.bin", File(handle), save=True
                )

        build.firmware_version = firmware
        build.commit_sha = commit
        build.ca_fingerprint = ca_fingerprint
        build.status = FirmwareBuild.STATUS_SUCCEEDED
        build.completed_at = timezone.now()
        build.log = _truncate("\n".join(log_lines))
        build.save()
        _notify_requester(build, succeeded=True)
        return {
            "status": "succeeded",
            "build_id": str(build.pk),
            "firmware_version_id": str(firmware.pk),
        }
    except Exception as exc:  # noqa: BLE001 — record any build failure for the operator
        logger.warning("Firmware build %s failed: %s", build_id, exc)
        build.status = FirmwareBuild.STATUS_FAILED
        build.error_message = str(exc)
        build.completed_at = timezone.now()
        build.log = _truncate("\n".join(log_lines))
        build.save()
        _notify_requester(build, succeeded=False)
        return {"status": "failed", "build_id": str(build.pk), "error": str(exc)}
