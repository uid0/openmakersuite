"""Django-core safety checks: DEBUG, SECRET_KEY, ALLOWED_HOSTS.

First slice of the production safety baseline (gh-710 of #455). Looks
at the loaded Django settings — complementary to the shell
``scripts/validate-prod-env.sh`` validator which looks at the ``.env``
file before the process starts.
"""

from __future__ import annotations

from typing import Iterable

from .base import Issue, SafetyCheck

# Substrings that mark a value as a placeholder. The list is public — the
# secret is whatever the operator was supposed to choose beyond the
# fragment, so naming the fragment in an error message does not leak
# anything sensitive. Keep this list in sync with the corresponding
# fragments in `scripts/validate-prod-env.sh`.
PLACEHOLDER_FRAGMENTS = (
    "django-insecure",
    "change-me",
    "changeme",
    "change_me",
    "replace-me",
    "replaceme",
    "replace_me",
    "placeholder",
    "your-secret-here",
    "your-key-here",
    "dev-key",
    "test-key",
    "example",
)

SECRET_KEY_MIN_LEN = 50

# nosec B104 — these are validator literals we *check against* in
# ALLOWED_HOSTS, not bind addresses. The whole point is to flag deploys
# that ship 0.0.0.0 here.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})  # nosec B104


class DjangoCoreCheck(SafetyCheck):
    """Validate DEBUG, SECRET_KEY, and ALLOWED_HOSTS for production posture."""

    category = "django_core"

    def run(self, settings) -> Iterable[Issue]:
        yield from self._check_debug(settings)
        yield from self._check_secret_key(settings)
        yield from self._check_allowed_hosts(settings)

    def _check_debug(self, settings) -> Iterable[Issue]:
        # The command's --strict gate decides whether this runs at all;
        # if we got here with DEBUG=True it's an enforce-anyway request.
        if getattr(settings, "DEBUG", False):
            yield Issue(
                category=self.category,
                key="DEBUG",
                reason="DEBUG=True is not permitted in a production environment.",
            )

    def _check_secret_key(self, settings) -> Iterable[Issue]:
        secret = getattr(settings, "SECRET_KEY", "") or ""
        if not secret:
            yield Issue(self.category, "SECRET_KEY", "SECRET_KEY is empty.")
            return

        lower = secret.lower()
        for fragment in PLACEHOLDER_FRAGMENTS:
            if fragment in lower:
                yield Issue(
                    category=self.category,
                    key="SECRET_KEY",
                    reason=(f"SECRET_KEY contains placeholder fragment '{fragment}'."),
                )
                # One placeholder fail per key is enough — surface the
                # length problem separately if it also applies.
                break

        if len(secret) < SECRET_KEY_MIN_LEN:
            yield Issue(
                category=self.category,
                key="SECRET_KEY",
                reason=(
                    f"SECRET_KEY is shorter than {SECRET_KEY_MIN_LEN} characters "
                    f"(got {len(secret)})."
                ),
            )

    def _check_allowed_hosts(self, settings) -> Iterable[Issue]:
        hosts = list(getattr(settings, "ALLOWED_HOSTS", None) or [])
        if not hosts:
            yield Issue(self.category, "ALLOWED_HOSTS", "ALLOWED_HOSTS is empty.")
            return

        if "*" in hosts:
            yield Issue(
                category=self.category,
                key="ALLOWED_HOSTS",
                reason="ALLOWED_HOSTS contains '*' (any host accepted).",
            )

        non_loopback = [h for h in hosts if h.strip().lower() not in LOOPBACK_HOSTS]
        if not non_loopback:
            yield Issue(
                category=self.category,
                key="ALLOWED_HOSTS",
                reason="ALLOWED_HOSTS only contains loopback hosts.",
            )
