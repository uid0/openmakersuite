"""Which reorder requests approval has cleared for purchasing (op-tm70).

A reorder request starts life ``pending`` and only becomes ``approved`` when an
approver signs off (or when an approver raised it themselves — see
``ReorderRequestViewSet.create``). Everything that *spends money* off the back
of a request — the PO-candidate pad, the per-supplier cart links, and the
"sending this PO closes these requests" sweep — must therefore consider
approved requests only, so an unapproved ask can never be ordered by accident.

⚠️ Deliberately separate from :meth:`inventory.models.InventoryItem.get_active_reorder_request`
and :meth:`~inventory.models.InventoryItem.has_pending_reorder`. Those two are
the *inventory* view of the same rows — they answer "is there anything in
flight for this item?" for the list/detail "active reorder" badge, and they
must keep counting ``pending``, or a member would file a request and see no
sign of it. Do not collapse the two notions: the badge asks whether an ask
exists, this module asks whether it may be bought.
"""

from __future__ import annotations

from django.db.models import Prefetch

from ..models import ReorderRequest

#: Statuses a reorder request must be in to feed purchasing. Approval is the
#: gate, so this is deliberately a one-element tuple rather than the
#: ``[pending, approved]`` pair these call sites used before op-tm70; it stays a
#: named collection so the (identical) filters below read the same at each site.
PO_ELIGIBLE_STATUSES = (ReorderRequest.Status.APPROVED,)

#: ``Prefetch(to_attr=...)`` target filled by :func:`approved_requests_prefetch`.
APPROVED_REQUESTS_ATTR = "_approved_reorder_requests"


def approved_requests_prefetch():
    """A ``Prefetch`` of each item's approved reorder requests, newest first.

    Mirrors the filter + ``-requested_at`` order :func:`get_approved_reorder_request`
    applies, so the cached list and the live query return the same row (the
    #890 ordering trap: a prefetch whose order differs from its reader's
    silently hands back a different request).
    """
    return Prefetch(
        "reorder_requests",
        queryset=ReorderRequest.objects.filter(status__in=PO_ELIGIBLE_STATUSES).order_by(
            "-requested_at"
        ),
        to_attr=APPROVED_REQUESTS_ATTR,
    )


def get_approved_reorder_request(item):
    """Return ``item``'s most recent approved reorder request, or ``None``.

    Reads the :func:`approved_requests_prefetch` cache when the caller
    prefetched it (avoiding a per-row query), else falls back to a live query
    so the answer is identical either way.
    """
    cached = getattr(item, APPROVED_REQUESTS_ATTR, None)
    if cached is not None:
        return cached[0] if cached else None
    return (
        item.reorder_requests.filter(status__in=PO_ELIGIBLE_STATUSES)
        .order_by("-requested_at")
        .first()
    )
