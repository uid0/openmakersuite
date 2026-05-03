"""
Django system checks for ForgeKey deployment hygiene.

Run via ``python manage.py check`` (also runs automatically at server start).
The most important check warns operators when the firmware signing key is
missing in production — without it, dispatched firmware payloads ship with
empty signatures and devices on signature-verifying firmware will refuse them.
"""

from __future__ import annotations

import os

from django.conf import settings
from django.core.checks import Tags, Warning, register

W_MISSING = "forgekey.W001"
W_DEFAULT = "forgekey.W002"
W_INVALID = "forgekey.W003"
W_PROVISIONING_TOKEN = "forgekey.W004"  # nosec B105 — Django check ID, not a password
W_EMQX_PASSWORD = "forgekey.W005"  # nosec B105 — Django check ID, not a password
W_EMQX_API = "forgekey.W006"
W_DOCKER_ALLOWED_HOSTS = "forgekey.W007"
DOCKER_BACKEND_HOSTNAME = "backend"

PLACEHOLDER_PROVISIONING_TOKEN = "REPLACE_ME_PROVISIONING_TOKEN"  # nosec B105
PLACEHOLDER_EMQX_PASSWORD = "change-me-on-first-deploy"  # nosec B105


def _is_production() -> bool:
    return not getattr(settings, "DEBUG", False)


def _running_in_docker() -> bool:
    """Heuristic: presence of /.dockerenv (mounted by `docker run`) or a
    DOCKER-style cgroup. We accept either signal so the check still fires in
    rootless containers and on hosts that strip /.dockerenv."""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8") as fh:
            cgroup = fh.read()
    except OSError:
        return False
    return "docker" in cgroup or "containerd" in cgroup or "kubepods" in cgroup


def _looks_like_placeholder(pem: str) -> bool:
    haystack = pem.lower()
    sentinels = ("change-me", "example", "test", "placeholder", "dev-only")
    return any(s in haystack for s in sentinels)


@register(Tags.security)
def check_firmware_signing_key(app_configs, **kwargs):
    warnings = []
    if not _is_production():
        return warnings

    pem = (getattr(settings, "FORGEKEY_FIRMWARE_SIGNING_KEY", "") or "").strip()
    if not pem:
        warnings.append(
            Warning(
                "FORGEKEY_FIRMWARE_SIGNING_KEY is not set; firmware dispatches "
                "will ship with empty signatures and devices on signature-"
                "verifying firmware will refuse them.",
                hint=(
                    "Generate a key with scripts/build/gen-firmware-signing-key.sh "
                    "in the forgekey repo and set FORGEKEY_FIRMWARE_SIGNING_KEY in "
                    "the production environment. The matching public key is baked "
                    "into device firmware via src/security/firmware_pubkey.h."
                ),
                id=W_MISSING,
            )
        )
        return warnings

    if _looks_like_placeholder(pem):
        warnings.append(
            Warning(
                "FORGEKEY_FIRMWARE_SIGNING_KEY appears to be a default/test "
                "value. Devices will accept signatures from this key, "
                "compromising firmware integrity.",
                hint="Replace with the production-only signing key.",
                id=W_DEFAULT,
            )
        )
        return warnings

    # Sanity-check that the configured value actually loads as a P-256 key.
    try:
        from .services.firmware_signing import load_signing_key

        load_signing_key()
    except Exception as exc:  # FirmwareSigningError or library-level errors
        warnings.append(
            Warning(
                f"FORGEKEY_FIRMWARE_SIGNING_KEY is set but failed to load: {exc}",
                hint="Confirm the env var contains a PEM-encoded ECDSA(P-256) "
                "private key. Embedded newlines may need to be escaped as \\n.",
                id=W_INVALID,
            )
        )

    return warnings


def _password_complexity_problem(pw: str) -> str:
    if len(pw) < 8:
        return "must be at least 8 characters"
    if not any(c.isupper() for c in pw):
        return "must contain an uppercase letter"
    if not any(c.islower() for c in pw):
        return "must contain a lowercase letter"
    if not any(c.isdigit() for c in pw):
        return "must contain a digit"
    return ""


@register(Tags.security)
def check_forgekey_provisioning_token(app_configs, **kwargs):
    warnings = []
    if not _is_production():
        return warnings

    token = (getattr(settings, "FORGEKEY_PROVISIONING_TOKEN", "") or "").strip()
    if not token or token == PLACEHOLDER_PROVISIONING_TOKEN:
        warnings.append(
            Warning(
                "FORGEKEY_PROVISIONING_TOKEN is missing or set to the placeholder; "
                "ESP32 device registration will reject every request.",
                hint=(
                    "Set FORGEKEY_PROVISIONING_TOKEN in the production environment "
                    "to a strong shared secret. Devices send it in the "
                    "X-ForgeKey-Provisioning-Token header at registration."
                ),
                id=W_PROVISIONING_TOKEN,
            )
        )
    return warnings


@register(Tags.security)
def check_emqx_dashboard_password(app_configs, **kwargs):
    warnings = []
    if not _is_production():
        return warnings

    pw = (getattr(settings, "EMQX_DASHBOARD_PASSWORD", "") or "").strip()
    if not pw or pw == PLACEHOLDER_EMQX_PASSWORD:
        warnings.append(
            Warning(
                "EMQX_DASHBOARD_PASSWORD is missing or set to the .env.prod.example "
                "placeholder; the EMQX dashboard will fall back to the built-in "
                "'public' default.",
                hint=(
                    "Set EMQX_DASHBOARD_PASSWORD to an 8+ char value with mixed "
                    "case and a digit. deploy.sh renders it into "
                    "scripts/emqx/bootstrap-admins.txt and EMQX re-applies it "
                    "every boot."
                ),
                id=W_EMQX_PASSWORD,
            )
        )
        return warnings

    problem = _password_complexity_problem(pw)
    if problem:
        warnings.append(
            Warning(
                f"EMQX_DASHBOARD_PASSWORD {problem}; EMQX 6.x rejects weak "
                "passwords at bootstrap and the dashboard user will not be created.",
                hint="Pick an 8+ char value with mixed case and at least one digit.",
                id=W_EMQX_PASSWORD,
            )
        )
    return warnings


@register(Tags.security)
def check_docker_allowed_hosts(app_configs, **kwargs):
    """W007: warn when running in docker without `backend` in ALLOWED_HOSTS.

    EMQX (running on the same docker network) fetches JWKS from
    ``http://backend:8000/api/forgekey/jwks/`` and Django returns 400 unless
    the `backend` service hostname is in ALLOWED_HOSTS — see oms-zad. Catch
    the misconfiguration at startup instead of when the first device fails to
    connect.
    """
    warnings = []
    if not _running_in_docker():
        return warnings
    if not _is_production():
        return warnings

    allowed = getattr(settings, "ALLOWED_HOSTS", []) or []
    normalized = {(h or "").strip().lower() for h in allowed}
    if "*" in normalized or DOCKER_BACKEND_HOSTNAME in normalized:
        return warnings

    warnings.append(
        Warning(
            f"ALLOWED_HOSTS does not include '{DOCKER_BACKEND_HOSTNAME}' but "
            "this process appears to be running inside a docker container. "
            "EMQX fetches JWKS via http://backend:8000/api/forgekey/jwks/ on "
            "the docker network; Django will return 400 Bad Request and EMQX "
            "will reject every device CONNECT.",
            hint=(
                "Add 'backend' to ALLOWED_HOSTS in .env, e.g. "
                "ALLOWED_HOSTS=oms.example.com,backend,localhost. "
                "docker-compose.prod.yml also appends 'backend' defensively, "
                "so this warning usually means an override is overriding it."
            ),
            id=W_DOCKER_ALLOWED_HOSTS,
        )
    )
    return warnings


@register(Tags.security)
def check_emqx_api_credentials(app_configs, **kwargs):
    warnings = []
    if not _is_production():
        return warnings

    key = (getattr(settings, "EMQX_API_KEY", "") or "").strip()
    secret = (getattr(settings, "EMQX_API_SECRET", "") or "").strip()
    if not key or not secret:
        warnings.append(
            Warning(
                "EMQX_API_KEY/EMQX_API_SECRET pair is missing; backend calls to "
                "the EMQX REST API (http://emqx:18083/api/v5) will fail.",
                hint=(
                    "After first deploy, log in to the EMQX dashboard as admin, "
                    "generate a key/secret pair under System > API Keys, and "
                    "write the values to EMQX_API_KEY / EMQX_API_SECRET in .env."
                ),
                id=W_EMQX_API,
            )
        )
    return warnings
