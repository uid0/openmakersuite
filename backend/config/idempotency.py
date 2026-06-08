"""Helpers for business-layer idempotency on public-write endpoints.

When a kiosk QR scan or a phone form-submit retries on a flaky network,
the second POST shouldn't surface as a 409 ("already exists") or a 500
("duplicate key") — both look like errors to the member when the actual
state already reflects what they wanted. This module gives endpoint
authors a one-liner to recognize "this is a retry of a request we
just accepted" and return the existing record with a 200 instead of
either creating a duplicate or failing the second request.

The dedup is *business-layer*: we match on the fields the operator
would consider "the same submission" (e.g. username + project +
location for a project-storage start), bounded by a short time window.
That keeps the contract intuitive: same payload, same window → same
record, 200. Different payload OR after the window → treated as a
distinct request.

Why not Idempotency-Key headers: the kiosk frontends don't generate
them, and adding the header would require frontend churn for every
public-write surface. Business-layer dedup catches the network-retry
shape with zero client work. Headers can layer on top later if needed.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from django.db.models import Model
from django.utils import timezone

DEFAULT_IDEMPOTENCY_WINDOW = timedelta(minutes=5)


def find_recent_duplicate(
    model: type[Model],
    *,
    lookup_fields: dict[str, Any],
    window: timedelta = DEFAULT_IDEMPOTENCY_WINDOW,
    created_at_field: str = "created_at",
    now=None,
) -> Optional[Model]:
    """Return the most recent record matching ``lookup_fields`` within
    ``window``, or None.

    Use this BEFORE create()-ing a new row. If it returns a record, the
    caller should return that record with 200 (a duplicate submit) rather
    than creating a second row. If it returns None, proceed with the
    normal create path.

    Example:

        existing = find_recent_duplicate(
            ProjectStorageStint,
            lookup_fields={
                "username": payload["username"],
                "project_title": payload.get("project_title", ""),
                "storage_location_name": payload.get("storage_location_name", ""),
                "removed_at__isnull": True,
            },
            created_at_field="started_at",
        )
        if existing:
            return Response(serialize(existing), status=200)
    """
    now = now or timezone.now()
    cutoff = now - window
    qs = model.objects.filter(**lookup_fields, **{f"{created_at_field}__gte": cutoff})
    return qs.order_by(f"-{created_at_field}").first()
