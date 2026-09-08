"""Read-only report on status transitions that were recorded without their moment.

Background
----------
``reorder_queue.admin``'s bulk actions performed status transitions with a
hand-written ``queryset.update()``. Each stamped the ACTOR of the change and
dropped the MOMENT:

  * "Mark selected orders as sent"      set ``status``/``sent_by``, not ``sent_at``
  * "Approve selected requests"         set ``status``/``reviewed_by``, not ``reviewed_at``
  * "Cancel selected requests"          set ``status``/``reviewed_by``, not ``reviewed_at``

Every WORKFLOW route is fixed. This command names the rows those bulk writes
left behind, because the consequence outlived the bug:
``services.receiving.create_lead_time_log``
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

**Cannot: what a line-less order was for.** Lines may only be DELETED while
an order is pre-send, and deletion leaves no reason, no ghost and no audit row
(oms-po-line-delete) — that is what makes it the right verb for a typo. So an
order that reached a supplier with no lines cannot have its contents
reconstructed either.

**Can: who, and which.** ``sent_by`` and ``reviewed_by`` were recorded and
survive. And the affected population is exactly countable — which is what this
command prints, so the incompleteness can be weighed rather than guessed at.
What to DO about a line-less sent order is an operator's judgement — void or
cancel it, or add the lines it should have carried — and this command does not
make it for them.

How the population is identified
--------------------------------
By the DAMAGE SIGNATURE the bulk write left on the row, not by the status the
row happens to sit at today — a damaged row keeps moving. Both signatures are
named in the output, in the text report and in the JSON payload, so a count can
never be read as covering something narrower or broader than it does:

  * requests — ``reviewed_by`` set with ``reviewed_at`` NULL, at ANY status.
    A request bulk-approved before the fix is carried on to ``ordered`` and
    ``received`` by paths that never touch the review columns, so status tells
    you nothing about whether the row is damaged. This signature is exact.
  * orders — ``status`` in ``sent``/``confirmed``/``partially_received``/
    ``received`` with ``sent_at`` NULL. This one CANNOT be made
    status-independent, and the difference is not an oversight. A null
    ``sent_at`` on a cancelled or voided order is most likely the truth — the
    order never went out — and no column distinguishes that from an order sent
    before the fix and cancelled afterwards. Reporting those rows would call
    clean records damaged, so they are excluded and the order count is a FLOOR.
    The output says so on the line beside the number.

It is a signature, so it is not a historical set. What has changed since this
command was written is that the SEND TRANSITIONS are closed: every path that
ENTERS an order into the supplier's hands goes through ``services.mark_sent``
(oms-po-send-rule), which stamps.

NOTHING ELSE IS, and that boundary is the honest statement of what a non-zero
count can mean. ``status``, ``sent_at`` and ``sent_by`` are all WRITABLE on the
API and editable on the admin change form — the endpoint was deliberately not
narrowed, because the rule was what needed enforcing, not the contract — so ANY
write that leaves an order in a sent-onward status with a null ``sent_at``
lands this signature, whether it moves the status or clears the stamp. So does
a direct database edit, which no application code can close.

Deliberately NOT enumerated as a list of doors. Earlier drafts named the routes
one at a time and each naming turned out to be short by one, because the routes
are the complement of a small closed set rather than a set anybody can finish
writing down. The boundary above stays true without maintenance. The open
product question — whether those writes should be routed or the fields
narrowed — is filed as https://github.com/uid0/openmakersuite/issues/1053,
which carries the traces.

A SECOND ORDER SIGNATURE
------------------------
``orders_sent_with_no_line_items`` reports orders that went to a supplier with
no line item at all. Distinct from the stamp gap and with a different remedy:
nothing is missing from the ROW, the order simply never had anything on it, so
the supplier holds a document listing nothing and the order sits in ``sent``
for ever — ``services.receiving.refresh_receipt_status`` never moves an order
nothing has arrived against.

An order whose lines are all VOIDED is deliberately NOT in that population.
Striking every line off an order that already went out is a documented
workflow (oms-a8o), not damage — the rows are the record of what was struck.
The two kinds of empty are the same pair ``PurchaseOrderViewSet.get_queryset``
separates: lines that exist and are all struck off, versus lines that were
never there. The send rule refuses both at SEND time, because at that moment
neither has anything to order; only the second is damage afterwards.

This command is deliberately, permanently READ-ONLY. There is no ``--fix`` and
no ``--backfill``: there is nothing truthful to write. Do not add one.

Usage::

    python manage.py report_unstamped_transitions
    python manage.py report_unstamped_transitions --format json
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand
from django.db.models import Count

from reorder_queue.models import LeadTimeLog, PurchaseOrder, ReorderRequest

#: An order that has gone to the supplier owes a ``sent_at``. DRAFT does not,
#: and the two terminal states are reachable from DRAFT without a send — an
#: order cancelled or voided before it ever went out has a null ``sent_at`` that
#: is the truth, so including them would report clean rows as damaged.
#:
#: Read off the model rather than typed out here.
#: :attr:`PurchaseOrder.SENT_ONWARD_STATUSES` is the ONE definition of "already
#: with the supplier", and ``send_request_field`` on the API asks the same
#: question of the same set — a report and a guard that disagreed about which
#: orders have gone out would be the worst possible pair to have drift.
#: Ordered here so the printed signature reads in lifecycle order rather than
#: in whatever order a set iterates.
SENT_ONWARD_STATUSES = tuple(
    status for status in PurchaseOrder.Status if status in PurchaseOrder.SENT_ONWARD_STATUSES
)

#: The two damage signatures, stated in the output so the number a reader takes
#: away is inseparable from the population it covers. They are not symmetrical,
#: and the order one says so: a request's signature is genuinely
#: status-independent, an order's is not, because a cancelled or voided order
#: that never went out has a null ``sent_at`` that is the truth and no column
#: separates it from one that did. The count is therefore a floor on the order
#: side, and the printed line has to admit that rather than imply it covers a
#: row whatever it has since become.
ORDER_SIGNATURE = (
    "status in ({}) with sent_at NULL; cancelled and voided orders are excluded, "
    "because there a null sent_at is more likely the truth than damage".format(
        ", ".join(status.value for status in SENT_ONWARD_STATUSES)
    )
)
REQUEST_SIGNATURE = "reviewed_by set, reviewed_at NULL"

#: The second order signature. Counting line ROWS, not active ones: an order
#: emptied by voiding every line off after it went out is the oms-a8o workflow,
#: and its rows are the record of what was struck off.
EMPTY_ORDER_SIGNATURE = (
    "status in ({}) with no line-item rows at all; an order whose lines are all "
    "VOIDED is excluded, because striking lines off an order that already went "
    "out is a workflow and not damage".format(
        ", ".join(status.value for status in SENT_ONWARD_STATUSES)
    )
)


def orders_sent_without_a_moment():
    """Purchase orders that reached the supplier with no ``sent_at``.

    THE SHAPE, NOT A CLOSED HISTORICAL SET. This finds rows carrying the damage
    signature whenever they were written, and a non-zero count next quarter is
    NOT automatically a count of pre-fix rows.

    WHAT IS CLOSED: the send TRANSITIONS. Every path that ENTERS an order into
    the supplier's hands goes through ``services.mark_sent`` (oms-po-send-rule)
    — all five
    of them, the API's ``send_to_supplier`` action, ``PATCH {"status": "sent"}``,
    the sales-order-number auto-send on create and update, the admin
    changelist's bulk action, and the admin change form — and that service
    stamps the whole fact set inside one unit of work.

    WHAT IS NOT: anything else. ``status``, ``sent_at`` and ``sent_by`` are all
    writable on ``PurchaseOrderSerializer`` (``read_only_fields`` is
    ``["po_number", "updated_at"]``) and editable on the admin change form, so
    ANY write that leaves an order in a sent-onward status with a null
    ``sent_at`` lands this signature — whether it moves the status somewhere
    the send rule says nothing about, or clears the stamp on an order that had
    already gone out. A direct database edit does the same, and no application
    code can close that one.

    The consequence is identical however the row got here: a delivery against
    it writes no ``LeadTimeLog``, because ``receiving.create_lead_time_log``
    returns early on a falsy ``sent_at``, so the supplier's performance on that
    order never reaches the table
    ``inventory.services.supplier_selection`` scores from.

    That boundary is stated as a boundary and NOT as a list of doors, on
    purpose: earlier drafts enumerated the routes and each enumeration proved
    short by one, because they are the complement of a small closed set rather
    than a set that can be finished. Whether to route those writes or narrow
    the fields is an open product decision, filed as
    https://github.com/uid0/openmakersuite/issues/1053 with the traces.
    """
    return (
        PurchaseOrder.objects.filter(status__in=SENT_ONWARD_STATUSES, sent_at__isnull=True)
        .select_related("supplier", "sent_by")
        .order_by("pk")
    )


def orders_sent_with_no_line_items():
    """Purchase orders that reached a supplier carrying nothing.

    ``Count("items")`` over ALL rows, voided included — see
    :data:`EMPTY_ORDER_SIGNATURE`. The consequence is not a missing lead time
    (no line, nothing owed) but an obligation that cannot close:
    ``services.receiving.refresh_receipt_status`` returns early on an order
    nothing has arrived against, so such an order stays ``sent`` until somebody
    voids or cancels it by hand.

    Like its sibling above this is a SHAPE, not a closed historical set, and the
    boundary is the same one: every path that SENDS passes the guard inside
    ``services.mark_sent`` (oms-po-send-rule), and nothing else is guarded — a
    write that puts an order into a sent-onward status by any other means, or a
    direct database edit, still lands it here (issue 1053).
    """
    return (
        PurchaseOrder.objects.filter(status__in=SENT_ONWARD_STATUSES)
        .annotate(_line_rows=Count("items"))
        .filter(_line_rows=0)
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

    Keyed on the damage signature, NOT on status. ``reviewed_by`` present with
    ``reviewed_at`` null IS the damage: a row that names who reviewed it but
    not when. ``reviewed_by`` must be present for the same reason — a request
    that reached ``cancelled`` without any reviewer never claimed a review
    moment in the first place, so its null is the truth.

    Status is deliberately not filtered on, because a damaged row does not stay
    where it was damaged. ``services.purchase_orders.update_reorder_requests_from_po``
    carries an approved request on to ``ordered`` and
    ``services.receiving.close_linked_reorder_request`` carries it on to
    ``received``; neither touches ``reviewed_by``/``reviewed_at``, so a row
    bulk-approved before the fix and since fulfilled is still damaged and still
    owes its moment. An earlier draft restricted this to
    ``approved``/``cancelled`` and therefore under-counted the very population
    the command exists to size exactly — the most likely trajectory of a
    damaged row was the one it dropped. Nor can this widening produce a false
    positive: the only route that sets ``reviewed_by`` on the way to
    ``ordered``, ``ReorderRequestViewSet.mark_ordered``, sets both columns
    together.
    """
    return (
        ReorderRequest.objects.filter(
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
            "signatures": {
                "orders_sent_without_sent_at": ORDER_SIGNATURE,
                "orders_sent_with_no_line_items": EMPTY_ORDER_SIGNATURE,
                "requests_reviewed_without_reviewed_at": REQUEST_SIGNATURE,
            },
            "orders_sent_without_sent_at": [],
            "orders_sent_with_no_line_items": [],
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

        for order in orders_sent_with_no_line_items():
            payload["orders_sent_with_no_line_items"].append(
                {
                    "id": order.pk,
                    "po_number": order.po_number,
                    "supplier": order.supplier.name,
                    "status": order.status,
                    "sent_by": order.sent_by.get_username() if order.sent_by else None,
                    "sent_at": order.sent_at.isoformat() if order.sent_at else None,
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
            "orders_sent_with_no_line_items": len(payload["orders_sent_with_no_line_items"]),
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
        signatures = payload["signatures"]
        self.stdout.write("Status transitions recorded without their moment")
        self.stdout.write("=" * 64)

        self.stdout.write("")
        self.stdout.write(
            f"POSITIVE FINDING: {totals['orders_sent_without_sent_at']} purchase order(s) "
            "reached the supplier with no sent_at."
        )
        self.stdout.write(f"  signature: {signatures['orders_sent_without_sent_at']}")
        self.stdout.write(
            "  this number is therefore a FLOOR: an order sent before the fix and "
            "since cancelled or voided is not counted here, because its null "
            "sent_at can no longer be told apart from one that never went out."
        )
        self.stdout.write(
            "  and it is not necessarily historical. The send TRANSITIONS are "
            "closed: every path that ENTERS an order into the supplier's hands "
            "goes through services.mark_sent, which stamps (oms-po-send-rule)."
        )
        self.stdout.write(
            "  nothing else is. status, sent_at and sent_by are writable on the "
            "API and editable on the admin change form, so ANY write that "
            "leaves an order in a sent-onward status with a null sent_at lands "
            "this signature — whether it moves the status or clears the stamp — "
            "as does a direct database edit. Open question, filed as "
            "https://github.com/uid0/openmakersuite/issues/1053."
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
            f"POSITIVE FINDING: {totals['orders_sent_with_no_line_items']} purchase "
            "order(s) reached a supplier with no line items at all."
        )
        self.stdout.write(f"  signature: {signatures['orders_sent_with_no_line_items']}")
        self.stdout.write(
            "  such an order cannot finish: refresh_receipt_status never moves an "
            "order nothing has arrived against, so it sits in its current status "
            "until somebody voids or cancels it. What it SHOULD have carried is "
            "not recoverable — a pre-send line delete leaves no reason and no "
            "ghost — so the remedy is an operator's call, not this command's."
        )
        for row in payload["orders_sent_with_no_line_items"]:
            name = row["po_number"] or "#{}".format(row["id"])
            self.stdout.write(
                f"  PO {name} — {row['supplier']} [{row['status']}] "
                f"sent by {row['sent_by'] or 'unknown'} at {row['sent_at'] or 'no moment'}"
            )

        self.stdout.write("")
        self.stdout.write(
            f"POSITIVE FINDING: {totals['requests_reviewed_without_reviewed_at']} reorder "
            "request(s) name a reviewer but no review moment."
        )
        self.stdout.write(
            f"  signature: {signatures['requests_reviewed_without_reviewed_at']} "
            "(status is not part of it — a damaged request carries the gap on "
            "into ordered and received)"
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
