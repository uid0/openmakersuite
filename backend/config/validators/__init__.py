"""Production-safety validators.

The category-based design is the future-proofing for the rest of the
#455 production-safety baseline: each PR adds a SafetyCheck subclass and
registers it in :mod:`.registry`. The ``validate_production`` management
command iterates the registry and reports per-category.

Initial scope (gh-710): :class:`.django_core.DjangoCoreCheck`.

Planned (gh-711, gh-712): cookies/CSRF/CORS check; external-credential
placeholder check (Sentry/ForgeKey/EMQX/webhook/email/signing).
"""

from .base import Issue, SafetyCheck
from .registry import CHECKS

__all__ = ["Issue", "SafetyCheck", "CHECKS"]
