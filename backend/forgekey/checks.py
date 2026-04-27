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
