"""Audit-event recording for donation actions (gh #354 / #334).

Mirrors ``forgekey.audit.record_event`` (gh #352) and
``reorder_queue.audit.record_event`` (gh #353) so the eventual unified
review surface (gh #359) can join across domains without per-domain
quirks.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.contrib.auth import get_user_model

from .models import (
    Disposition,
    Donation,
    DonationAuditEvent,
    DonationItem,
    TaxReceipt,
)

User = get_user_model()


def record_event(
    *,
    action: str,
    actor: Optional[User] = None,
    donation: Optional[Donation] = None,
    donation_item: Optional[DonationItem] = None,
    tax_receipt: Optional[TaxReceipt] = None,
    disposition: Optional[Disposition] = None,
    notes: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> DonationAuditEvent:
    """Record a donation audit event.

    At least one of ``donation``, ``donation_item``, ``tax_receipt``, or
    ``disposition`` must be supplied so the row is queryable by entity.
    The ``donation`` FK is auto-derived from the supplied tax_receipt /
    donation_item / disposition when one is passed without an explicit
    donation, so audit rows always have an entity-level pointer for
    review-surface queries.

    Anonymous / system-initiated actions are allowed (``actor=None``);
    the audit row simply records "no known user."
    """
    if donation is None:
        if tax_receipt is not None:
            donation = tax_receipt.donation
        elif donation_item is not None:
            donation = donation_item.donation
        elif disposition is not None:
            # Disposition -> DonationItem -> Donation
            disposition_item = getattr(disposition, "donation_item", None)
            if disposition_item is not None:
                donation = disposition_item.donation

    return DonationAuditEvent.objects.create(
        action=action,
        actor=actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None,
        donation=donation,
        donation_item=donation_item,
        tax_receipt=tax_receipt,
        disposition=disposition,
        notes=notes,
        metadata=metadata or {},
    )
