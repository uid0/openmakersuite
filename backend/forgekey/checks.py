"""
Django system checks for ForgeKey deployment hygiene.

Run via ``python manage.py check`` (also runs automatically at server start).
The most important check warns operators when the firmware signing key is
missing in production — without it, dispatched firmware payloads ship with
empty signatures and devices on signature-verifying firmware will refuse them.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Tags, Warning, register

W_MISSING = "forgekey.W001"
W_DEFAULT = "forgekey.W002"
W_INVALID = "forgekey.W003"
W_PROVISIONING_TOKEN = "forgekey.W004"  # nosec B105 — Django check ID, not a password
W_EMQX_PASSWORD = "forgekey.W005"  # nosec B105 — Django check ID, not a password
W_EMQX_API = "forgekey.W006"

PLACEHOLDER_PROVISIONING_TOKEN = "REPLACE_ME_PROVISIONING_TOKEN"  # nosec B105
PLACEHOLDER_EMQX_PASSWORD = "change-me-on-first-deploy"  # nosec B105


def _is_production() -> bool:
    return not getattr(settings, "DEBUG", False)


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
