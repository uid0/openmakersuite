"""Audit-event recording for SiteSettings updates (gh #358 / #334).

Mirrors the helpers in #352-#357 so the eventual unified review
surface (#359) can join across domains without per-domain quirks.

File fields (``logo``, ``favicon``) are diffed by filename only —
the binary content is never stored in the audit row. This is a
deliberate AC-27 guardrail: audit rows must not become a covert
data-export surface for files that may carry sensitive content.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.contrib.auth import get_user_model
from django.db.models.fields.files import FieldFile

from .models import SiteSettings, SiteSettingsAuditEvent

User = get_user_model()


# Fields the diff helper inspects. Keep this in sync with SiteSettings;
# adding a new SiteSettings field that should be audited just means
# extending this tuple.
AUDITED_FIELDS: tuple = (
    "site_name",
    "site_tagline",
    "logo",
    "logo_alt_text",
    "favicon",
    "primary_color",
    "secondary_color",
    "footer_text",
    "contact_email",
    "contact_phone",
    "website_url",
)


def record_event(
    *,
    action: str,
    site_settings: SiteSettings,
    actor: Optional[User] = None,
    notes: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> SiteSettingsAuditEvent:
    """Record a SiteSettings audit event."""
    return SiteSettingsAuditEvent.objects.create(
        action=action,
        actor=actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None,
        site_settings=site_settings,
        notes=notes,
        metadata=metadata or {},
    )


def diff_audited_fields(before: Dict[str, Any], after: SiteSettings) -> Dict[str, Dict[str, Any]]:
    """Return ``{field: {before, after}}`` for changed audited fields.

    ``before`` is a plain dict snapshotted from the pre-save instance
    (the helper accepts a dict so callers can capture state without
    holding a stale model reference). File fields are stringified to
    their filename via ``_jsonable``; raw FieldFile/None values never
    appear in the resulting metadata.
    """
    changes: Dict[str, Dict[str, Any]] = {}
    for name in AUDITED_FIELDS:
        old = before.get(name)
        new = getattr(after, name)
        old_norm = _jsonable(old)
        new_norm = _jsonable(new)
        if old_norm != new_norm:
            changes[name] = {"before": old_norm, "after": new_norm}
    return changes


def snapshot(instance: SiteSettings) -> Dict[str, Any]:
    """Return a JSON-clean snapshot of the audited fields on
    ``instance``. Use this BEFORE save to capture state for diffing
    against the post-save row.
    """
    return {name: _jsonable(getattr(instance, name)) for name in AUDITED_FIELDS}


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, FieldFile):
        # Empty FieldFile (no file attached) renders as "" via .name;
        # normalize to None so diffs treat "no file" the same regardless
        # of whether the field is unset vs. set-to-empty.
        return value.name or None
    return value
