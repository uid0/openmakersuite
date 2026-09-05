"""Reorder-request status-transition workflow.

The sibling of :mod:`reorder_queue.services.purchase_orders`, for the other
entity in this app that carries a status an operator moves. :mod:`.approvals`
answers "which requests may be bought"; this module PERFORMS the review that
puts a request into one of those states.

Both transitions here record WHO decided and WHEN. That pairing is the whole
point of the module existing: ``reviewed_by`` and ``reviewed_at`` were written
out by hand at four call sites, and the two admin bulk actions — the only ones
that used ``queryset.update()`` rather than a per-row save — set the actor and
dropped the moment, so a request signed off in bulk showed a reviewer and a
blank date on the admin's "Admin Review" fieldset, on the reorder-request API
serializer, and in ``inventory``'s active-request block.
"""

from __future__ import annotations

from django.utils import timezone

from ..models import ReorderRequest

#: Sentinel for "the caller supplied no notes", distinct from an explicitly
#: supplied ``""``. Same discipline as
#: :data:`reorder_queue.services.purchase_orders.UNCHANGED`: a review that names
#: no notes must leave the ones already on the request alone, and the bulk admin
#: actions have no field to type them into at all. Only a caller that really
#: sends a value overwrites.
UNCHANGED = object()


def _review(reorder_request, *, status, actor, admin_notes):
    """Apply a review decision, stamping the actor AND the moment together.

    ``update_fields`` is explicit so a review never persists unrelated in-memory
    edits the caller happened to be holding — the shape
    ``ReorderRequestViewSet.create``'s auto-approval already used.
    """
    fields = ["status", "reviewed_by", "reviewed_at", "updated_at"]
    reorder_request.status = status
    reorder_request.reviewed_by = actor
    reorder_request.reviewed_at = timezone.now()
    if admin_notes is not UNCHANGED:
        reorder_request.admin_notes = admin_notes
        fields.append("admin_notes")
    reorder_request.save(update_fields=fields)
    return reorder_request


def approve_request(reorder_request, actor, admin_notes=UNCHANGED):
    """Sign a reorder request off for purchasing.

    The caller owns the approver permission check
    (``ReorderRequestViewSet._is_reorder_approver``) and any status
    precondition.
    """
    return _review(
        reorder_request,
        status=ReorderRequest.Status.APPROVED,
        actor=actor,
        admin_notes=admin_notes,
    )


def cancel_request(reorder_request, actor, admin_notes=UNCHANGED):
    """Close a reorder request without ordering against it."""
    return _review(
        reorder_request,
        status=ReorderRequest.Status.CANCELLED,
        actor=actor,
        admin_notes=admin_notes,
    )
