"""Read-only report on status transitions that were recorded without their moment.

Background
----------
``reorder_queue.admin``'s bulk actions performed status transitions with a
hand-written ``queryset.update()``. Each stamped the ACTOR of the change and
dropped the MOMENT:

  * "Mark selected orders as sent"      set ``status``/``sent_by``, not ``sent_at``
  * "Approve selected requests"         set ``status``/``reviewed_by``, not ``reviewed_at``
  * "Cancel selected requests"          set ``status``/``reviewed_by``, not ``reviewed_at``

The code is fixed. This command names the rows written BEFORE the fix, because
the consequence outlived the bug: ``services.receiving.create_lead_time_log``
returns early on a falsy ``sent_at``, so when one of those orders was delivered
NO ``LeadTimeLog`` was written — and that table is what
``inventory.services.supplier_selection``'s performance term scores suppliers
from. The affected suppliers are being chosen on an incomplete record.

What can and cannot be recovered
--------------------------------
**Cannot: the moments themselves.** Nothing else on the row, or anywhere else in
the schema, recorded when these transitions happened.

  * ``order_date`` is a DIFFERENT fact — "when the order was actually placed",
    operator-editable and backdatable, defaulting to row creation. It is not
    when the document went to the supplier.
  * ``updated_at`` is ``auto_now``: the bulk write never touched it, and every
    edit since has overwritten it.
  * The pre-fix admin send recorded no ``po_send`` audit event either, so there
    is no event row carrying the moment.

**Cannot: the missing lead-time rows.** A lead time is (delivery date − order
date). The delivery date survives on ``OrderDelivery``; the order date does not
exist. Writing rows anchored on ``order_date`` would put invented numbers into
the exact column that decides which supplier gets the next order — strictly
worse than the gap, because the gap is currently honest: ``DeliveryRecord.factor``
returns 1 for a link with no history, documented as "do not punish for absence
of evidence", while a fabricated row actively mis-scores.

**Can: who, and which.** ``sent_by`` and ``reviewed_by`` were recorded and
survive. And the affected population is exactly countable — which is what this
command prints, so the incompleteness can be weighed rather than guessed at.

This command is deliberately, permanently READ-ONLY. There is no ``--fix`` and
no ``--backfill``: there is nothing truthful to write. Do not add one.

Usage::

    python manage.py report_unstamped_transitions
    python manage.py report_unstamped_transitions --format json
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand
from django.db.models import Q

from reorder_queue.models import LeadTimeLog, PurchaseOrder, ReorderRequest

#: An order that has gone to the supplier owes a ``sent_at``. DRAFT does not,
#: and the two terminal states are reachable from DRAFT without a send — an
#: order cancelled or voided before it ever went out has a null ``sent_at`` that
#: is the truth, so including them would report clean rows as damaged.
SENT_ONWARD_STATUSES = (
    PurchaseOrder.Status.SENT,
    PurchaseOrder.Status.CONFIRMED,
    PurchaseOrder.Status.PARTIALLY_RECEIVED,
    PurchaseOrder.Status.RECEIVED,
)

#: A request in one of these states was reviewed by somebody, so it owes a
#: ``reviewed_at``. ``ordered``/``received`` are excluded: those are reachable
#: from ``pending`` through paths that never claimed to review the request.
REVIEWED_STATUSES = (
    ReorderRequest.Status.APPROVED,
    ReorderRequest.Status.CANCELLED,
)


def orders_sent_without_a_moment():
    """Purchase orders that reached the supplier with no ``sent_at``.

    The population, not the cause: this is every route that could produce the
    shape, and the pre-fix admin action is the only one in the code that did
    (``services.mark_sent`` has always stamped it, and the API's send refuses a
    non-DRAFT order, so such a row could never be re-stamped by a later send).
    A row edited directly in the database would land here too.
    """
    return (
        PurchaseOrder.objects.filter(status__in=SENT_ONWARD_STATUSES, sent_at__isnull=True)
        .select_related("supplier", "sent_by")
        .order_by("pk")
    )


def lines_owed_a_lead_time_log(order):
    """Lines of ``order`` whose delivery should have produced a ``LeadTimeLog``.

    A line qualifies when it has a supplier link (asset-only and freeform lines
    never produce one), is not struck off, and is fully received — which is the
    state ``receipt_completed_line`` was answering "yes" for at the moment the
    last receipt landed. Lines closed short are excluded: no receipt ever
    finished them, so no row was owed.
    """
    logged = set(
        LeadTimeLog.objects.filter(purchase_order=order).values_list("item_supplier_id", flat=True)
    )
    owed = []
    for line in order.items.filter(item_supplier__isnull=False, is_voided=False).select_related(
        "item_supplier__supplier", "item_supplier__item"
    ):
        if line.is_fully_received and line.item_supplier_id not in logged:
            owed.append(line)
    return owed


def requests_reviewed_without_a_moment():
    """Reorder requests carrying a reviewer but no ``reviewed_at``.

    ``reviewed_by`` must be present: a request that reached ``cancelled``
    without any reviewer never claimed a review moment in the first place.
    """
    return (
        ReorderRequest.objects.filter(
            Q(status__in=REVIEWED_STATUSES),
            reviewed_by__isnull=False,
            reviewed_at__isnull=True,
        )
        .select_related("item", "reviewed_by")
        .order_by("pk")
    )


class Command(BaseCommand):
    help = "Report status transitions recorded without their timestamp. Read-only."

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=("text", "json"),
            default="text",
            help="Output format (default: text).",
        )

    def handle(self, *args, **options):
        orders = list(orders_sent_without_a_moment())
        payload = {
            "orders_sent_without_sent_at": [],
            "requests_reviewed_without_reviewed_at": [],
        }

        missing_logs = 0
        affected_links = set()
        for order in orders:
            owed = lines_owed_a_lead_time_log(order)
            missing_logs += len(owed)
            affected_links.update(line.item_supplier_id for line in owed)
            payload["orders_sent_without_sent_at"].append(
                {
                    "id": order.pk,
                    "po_number": order.po_number,
                    "supplier": order.supplier.name,
                    "status": order.status,
                    "sent_by": order.sent_by.get_username() if order.sent_by else None,
                    "order_date": order.order_date.isoformat() if order.order_date else None,
                    "delivery_count": order.deliveries.count(),
                    "lines_missing_a_lead_time_log": [
                        {
                            "line_id": line.pk,
                            "item_supplier_id": line.item_supplier_id,
                            "supplier": line.item_supplier.supplier.name,
                            "item": str(line.item_supplier.item),
                        }
                        for line in owed
                    ],
                }
            )

        for reorder_request in requests_reviewed_without_a_moment():
            payload["requests_reviewed_without_reviewed_at"].append(
                {
                    "id": reorder_request.pk,
                    "item": reorder_request.item.name,
                    "status": reorder_request.status,
                    "reviewed_by": reorder_request.reviewed_by.get_username(),
                }
            )

        payload["totals"] = {
            "orders_sent_without_sent_at": len(orders),
            "lead_time_rows_never_written": missing_logs,
            "supplier_links_scored_on_incomplete_evidence": len(affected_links),
            "requests_reviewed_without_reviewed_at": len(
                payload["requests_reviewed_without_reviewed_at"]
            ),
        }

        if options["format"] == "json":
            self.stdout.write(json.dumps(payload, indent=2, default=str))
            return

        self._write_text(payload)

    def _write_text(self, payload):
        totals = payload["totals"]
        self.stdout.write("Status transitions recorded without their moment")
        self.stdout.write("=" * 64)

        self.stdout.write("")
        self.stdout.write(
            f"POSITIVE FINDING: {totals['orders_sent_without_sent_at']} purchase order(s) "
            "reached the supplier with no sent_at."
        )
        for row in payload["orders_sent_without_sent_at"]:
            name = row["po_number"] or "#{}".format(row["id"])
            self.stdout.write(
                f"  PO {name} — {row['supplier']} "
                f"[{row['status']}] sent by {row['sent_by'] or 'unknown'}, "
                f"{row['delivery_count']} delivery/deliveries"
            )
            for line in row["lines_missing_a_lead_time_log"]:
                self.stdout.write(
                    f"      no lead-time row: {line['item']} via {line['supplier']} "
                    f"(link {line['item_supplier_id']})"
                )

        self.stdout.write("")
        self.stdout.write(
            "ESTABLISHED ABSENCE: "
            f"{totals['lead_time_rows_never_written']} lead-time row(s) were owed by a "
            "completed delivery on those orders and were never written, across "
            f"{totals['supplier_links_scored_on_incomplete_evidence']} supplier link(s). "
            "inventory.services.supplier_selection scores those links on the deliveries "
            "that ARE recorded."
        )

        self.stdout.write("")
        self.stdout.write(
            f"POSITIVE FINDING: {totals['requests_reviewed_without_reviewed_at']} reorder "
            "request(s) name a reviewer but no review moment."
        )
        for row in payload["requests_reviewed_without_reviewed_at"]:
            self.stdout.write(
                f"  request #{row['id']} — {row['item']} [{row['status']}] "
                f"reviewed by {row['reviewed_by']}"
            )

        self.stdout.write("")
        self.stdout.write("EXPLICIT UNKNOWN")
        self.stdout.write(
            "  When each of those transitions happened is not recoverable. No column, "
            "audit row or related record holds it: order_date is a different, editable "
            "fact; updated_at is auto_now and has been overwritten; the pre-fix admin "
            "send wrote no po_send event. The lead-time rows above cannot be "
            "reconstructed either — a lead time needs the order date, and the order "
            "date was never written. This command therefore has no --fix, and adding "
            "one would put invented numbers into the column that chooses suppliers."
        )
