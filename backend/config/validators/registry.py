"""Registered safety checks. Adding a check is a one-line append here.

Future PRs (gh-711, gh-712) add CookiesCheck, CredentialPlaceholderCheck,
etc. by importing and appending.
"""

from __future__ import annotations

from .cookies import CookiesCSRFCORSCheck
from .django_core import DjangoCoreCheck

CHECKS = [
    DjangoCoreCheck(),
    CookiesCSRFCORSCheck(),
]
