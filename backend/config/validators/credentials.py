"""External-credential placeholder detection (gh-712 of #455).

Third category in the runtime production-safety validator. Walks the
list of known credential settings and fails the deploy if any of them
contains a documented placeholder fragment (e.g. ``change-me-in-
production``, ``placeholder``, ``replace-me``).

The settings checked here are the ones with operator-meaningful
credential semantics — empty is acceptable when the corresponding
feature is gated off, but a placeholder is always wrong. Empty values
do not fail this check (they would surface as a runtime issue with
the dependent feature, e.g. ForgeKey provisioning returning 401
server_unconfigured per the ``apps.py`` startup banner).

Naming notes — the gh-712 issue body referenced canonical Django/SMTP
names (``EMAIL_HOST_PASSWORD``, ``EMQX_USERNAME``, etc.), but OMS uses
Anymail + Postmark instead of raw SMTP and EMQX API keys instead of
basic-auth credentials. The check uses the actual setting names from
``backend/config/settings.py``. Frontend ``REACT_APP_SENTRY_DSN`` is a
build-time env var, not a Django setting — it's covered by the shell
``scripts/validate-prod-env.sh`` already.
"""

from __future__ import annotations

from typing import Iterable

from .base import Issue, SafetyCheck
from .django_core import PLACEHOLDER_FRAGMENTS

# Credentials we always check for placeholder values when the operator
# has set them. Empty is acceptable here (the dependent feature self-
# reports unconfigured); a placeholder is always a misconfiguration.
KNOWN_CREDENTIALS: tuple[str, ...] = (
    # Observability
    "SENTRY_DSN",
    # ForgeKey
    "FORGEKEY_PROVISIONING_TOKEN",
    "FORGEKEY_SHARED_SECRET",
    "FORGEKEY_JWT_SIGNING_KEY",
    "FORGEKEY_FIRMWARE_SIGNING_KEY",
    "FORGEKEY_WEBHOOK_SECRET",
    "FORGEKEY_CA_KEY_ENCRYPTION_KEY",
    "FORGEKEY_BUILDER_GITHUB_TOKEN",
    # MQTT broker
    "EMQX_API_KEY",
    "EMQX_API_SECRET",
    # Email + webhook tokens
    "POSTMARK_SERVER_TOKEN",
    "POSTMARK_INBOUND_TOKEN",
    "LOCATION_PING_TOKEN",
    # WHMCS integration for maker-box verification
    "WHMCS_API_IDENTIFIER",
    "WHMCS_API_SECRET",
)


class CredentialPlaceholderCheck(SafetyCheck):
    """Flag credential settings still set to a documented placeholder."""

    category = "credentials"

    def run(self, settings) -> Iterable[Issue]:
        for name in KNOWN_CREDENTIALS:
            value = getattr(settings, name, None) or ""
            if not value:
                # Empty is fine — feature gates handle the
                # unconfigured case at runtime.
                continue
            yield from self._check_value(name, value)

    def _check_value(self, name: str, value: str) -> Iterable[Issue]:
        lower = value.lower()
        for fragment in PLACEHOLDER_FRAGMENTS:
            if fragment in lower:
                yield Issue(
                    category=self.category,
                    key=name,
                    reason=(f"{name} contains placeholder fragment '{fragment}'."),
                )
                # One fail per setting; surface every problematic
                # setting in the run, but not every fragment that
                # matches within the same value.
                return
