"""Cookie / CSRF / CORS / HSTS safety checks (gh-711 of #455).

Second category in the runtime production-safety validator. Same shape
as :mod:`.django_core`: yields :class:`Issue` instances against the
loaded Django settings; never prints values, only the setting name and
a category-tagged reason.

What this check covers:

  - ``SESSION_COOKIE_SECURE`` and ``CSRF_COOKIE_SECURE`` must be True
    (plaintext cookies disable the secure-by-default deploy contract).
  - ``SESSION_COOKIE_SAMESITE`` and ``CSRF_COOKIE_SAMESITE`` must be
    set to ``Lax`` or ``Strict``. ``None`` (the cross-site shape) is
    only safe when paired with Secure, and bare unset would also fall
    back to browser defaults that vary by version.
  - ``SECURE_SSL_REDIRECT`` must be truthy in production posture so
    plaintext clients are bounced to https before any cookie work.
  - ``SECURE_HSTS_SECONDS`` must be non-zero so a returning client
    can't be downgraded back to http on a hostile network.
  - ``CORS_ALLOWED_ORIGINS`` and ``CSRF_TRUSTED_ORIGINS`` must not
    contain ``*`` (the trust-anyone shape).
"""

from __future__ import annotations

from typing import Iterable

from .base import Issue, SafetyCheck

SAFE_SAMESITE_VALUES = frozenset({"Lax", "Strict"})


class CookiesCSRFCORSCheck(SafetyCheck):
    """Validate session/CSRF cookie hardening + CORS origin allowlists."""

    category = "cookies_csrf_cors"

    def run(self, settings) -> Iterable[Issue]:
        yield from self._check_cookie_secure(settings, "SESSION_COOKIE_SECURE")
        yield from self._check_cookie_secure(settings, "CSRF_COOKIE_SECURE")
        yield from self._check_samesite(settings, "SESSION_COOKIE_SAMESITE")
        yield from self._check_samesite(settings, "CSRF_COOKIE_SAMESITE")
        yield from self._check_ssl_redirect(settings)
        yield from self._check_hsts(settings)
        yield from self._check_wildcard_origins(settings, "CORS_ALLOWED_ORIGINS")
        yield from self._check_wildcard_origins(settings, "CSRF_TRUSTED_ORIGINS")

    def _check_cookie_secure(self, settings, name: str) -> Iterable[Issue]:
        if not getattr(settings, name, False):
            yield Issue(
                category=self.category,
                key=name,
                reason=(
                    f"{name} must be True in production — plaintext cookies "
                    "break the secure-by-default deploy contract."
                ),
            )

    def _check_samesite(self, settings, name: str) -> Iterable[Issue]:
        value = getattr(settings, name, None)
        if value not in SAFE_SAMESITE_VALUES:
            # We don't print the actual value because a misconfigured
            # SAMESITE could in theory be set to something operator-
            # specific. Naming the safe set is enough to remediate.
            yield Issue(
                category=self.category,
                key=name,
                reason=(
                    f"{name} must be 'Lax' or 'Strict'. Other values fall back "
                    "to browser defaults that vary by vendor + version."
                ),
            )

    def _check_ssl_redirect(self, settings) -> Iterable[Issue]:
        if not getattr(settings, "SECURE_SSL_REDIRECT", False):
            yield Issue(
                category=self.category,
                key="SECURE_SSL_REDIRECT",
                reason=(
                    "SECURE_SSL_REDIRECT must be True in production so "
                    "plaintext clients are bounced to https before cookies work."
                ),
            )

    def _check_hsts(self, settings) -> Iterable[Issue]:
        seconds = getattr(settings, "SECURE_HSTS_SECONDS", 0) or 0
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            seconds = 0
        if seconds <= 0:
            yield Issue(
                category=self.category,
                key="SECURE_HSTS_SECONDS",
                reason=(
                    "SECURE_HSTS_SECONDS must be > 0 in production so a "
                    "returning client can't be downgraded to http."
                ),
            )

    def _check_wildcard_origins(self, settings, name: str) -> Iterable[Issue]:
        origins = list(getattr(settings, name, None) or [])
        # CSRF_TRUSTED_ORIGINS / CORS_ALLOWED_ORIGINS may be a string in
        # some configs; normalize to a list. We never print the values
        # themselves — origins can be operator-specific (internal hosts,
        # vendor URLs) and shouldn't show up in container logs.
        if isinstance(origins, str):
            origins = [origins]
        if any(o.strip() == "*" for o in origins):
            yield Issue(
                category=self.category,
                key=name,
                reason=f"{name} must not contain '*' (the trust-anyone shape).",
            )
