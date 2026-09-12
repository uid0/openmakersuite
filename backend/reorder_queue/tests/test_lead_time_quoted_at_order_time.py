"""A vendor is judged against the promise it made WHEN THE ORDER WAS PLACED.

``LeadTimeLog`` is a supplier's punctuality record, and
``inventory.services.supplier_selection`` reads ``variance_days`` off it to
decide where the next purchase order goes. The estimate it is measured against
used to be read out of ``ItemSupplier.average_lead_time`` at RECEIPT time,
because no order-time copy of the quote existed.

That column does not hold still. An operator edits it, and
``inventory.tasks.update_average_lead_times`` rewrites it on every run from the
last six months of receipts. So a vendor that quoted ten days and delivered in
eight — inside the promise it was given — was graded against whatever the quote
said on the day the goods turned up: revised to 2 while they were in transit, the
row read estimated 2, actual 8, variance +6, late. Revised the other way, a
genuinely late delivery came out punctual.

``services.purchase_orders.mark_sent`` now freezes the quote onto each line as
``PurchaseOrderItem.quoted_lead_time_days``, and ``create_lead_time_log`` grades
against that copy.

Orders SENT BEFORE that column existed carry no snapshot. They are not
backfilled — see ``migrations/0037_lead_time_quoted_at_order_time`` for why
inventing one would be worse than the absence — so they fall back to the live
quote, exactly as they always did, and the row says so on
``estimated_lead_time_basis`` rather than claiming a promise it never read.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

import pytest

from inventory.services.supplier_selection import DeliveryRecord, delivery_records_for
from inventory.tests.factories import InventoryItemFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue.models import LeadTimeLog, PurchaseOrder, PurchaseOrderItem
from reorder_queue.services.purchase_orders import mark_sent
from reorder_queue.services.receiving import create_lead_time_log
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _draft_with_a_line(*, quoted_lead_time):
    """A DRAFT order carrying one line against a link quoting ``quoted_lead_time``."""
    supplier = SupplierFactory()
    order = PurchaseOrder.objects.create(supplier=supplier, created_by=UserFactory())
    PurchaseOrderItem.objects.create(
        purchase_order=order,
        item_supplier=ItemSupplierFactory(
            supplier=supplier,
            quantity_per_package=1,
            average_lead_time=quoted_lead_time,
            item=InventoryItemFactory(current_stock=0),
        ),
        quantity_ordered=4,
        unit_cost_ordered=Decimal("2.00"),
        order_in_packages=4,
    )
    return order


def _send(order, *, days_ago):
    """Send ``order`` through the one transition every send path goes through."""
    mark_sent(order, UserFactory(), at=timezone.now() - timedelta(days=days_ago))
    return order


def _requote(link, days):
    """What an operator edit — or ``update_average_lead_times`` — does to the quote."""
    link.average_lead_time = days
    link.save(update_fields=["average_lead_time"])


def test_a_quote_edited_while_the_order_is_in_flight_does_not_move_the_judgement():
    """The vendor kept the promise it was GIVEN, so the record must say so.

    The order goes out against a quote of 10 and the goods land on day 8 — two
    days inside that promise. The quote is revised to 2 in between, which is an
    ordinary thing for it to do: an operator retypes it, or the scheduled
    ``update_average_lead_times`` recomputes it from other orders entirely.

    Reading the quote at receipt filed this delivery estimated 2, actual 8,
    variance +6, ``was_late`` True — a kept promise recorded as broken, and then
    charged against the vendor by ``supplier_selection``'s performance term.
    """
    order = _send(_draft_with_a_line(quoted_lead_time=10), days_ago=8)
    line = order.items.get()

    _requote(line.item_supplier, 2)
    line.refresh_from_db()

    create_lead_time_log(line, timezone.now().date())

    log = LeadTimeLog.objects.get(purchase_order=order)
    assert log.estimated_lead_time_days == 10
    assert log.actual_lead_time_days == 8
    assert log.variance_days == -2
    assert log.was_late is False
    assert log.estimated_lead_time_basis == LeadTimeLog.ESTIMATE_FROM_ORDER_SNAPSHOT


def test_a_quote_edited_the_other_way_does_not_launder_a_late_delivery():
    """The mirror, which is the half that flatters a vendor.

    Quote 3, delivered on day 8 — five days late. Revising the quote up to 10
    afterwards used to file the same delivery as three days EARLY, so a vendor
    could be made to look punctual by relaxing the promise it had already
    broken. A record that can be rewritten by editing the yardstick is not a
    record, and this is the direction nobody would report.
    """
    order = _send(_draft_with_a_line(quoted_lead_time=3), days_ago=8)
    line = order.items.get()

    _requote(line.item_supplier, 10)
    line.refresh_from_db()

    create_lead_time_log(line, timezone.now().date())

    log = LeadTimeLog.objects.get(purchase_order=order)
    assert log.estimated_lead_time_days == 3
    assert log.variance_days == 5
    assert log.was_late is True


def test_a_recorded_judgement_and_the_score_built_on_it_survive_a_later_requote():
    """The other ordering: the goods are in, THEN the quote moves.

    A punctual delivery is recorded, and both the row and the delivery record
    ``supplier_selection`` builds from it stay exactly where they were when the
    link is requoted afterwards. The judgement is a fact about an order that is
    finished, not a live comparison against whatever the catalogue says today —
    and it is a vendor's score, which people act on.
    """
    order = _send(_draft_with_a_line(quoted_lead_time=3), days_ago=3)
    line = order.items.get()
    create_lead_time_log(line, timezone.now().date())

    log = LeadTimeLog.objects.get(purchase_order=order)
    assert log.variance_days == 0
    assert delivery_records_for([line.item_supplier]) == {
        line.item_supplier.pk: DeliveryRecord(on_time=1, total=1)
    }

    _requote(line.item_supplier, 1)

    log.refresh_from_db()
    assert log.estimated_lead_time_days == 3
    assert log.variance_days == 0
    assert log.was_late is False
    assert delivery_records_for([line.item_supplier]) == {
        line.item_supplier.pk: DeliveryRecord(on_time=1, total=1)
    }


def test_a_same_day_quote_is_frozen_as_a_promise_and_not_as_an_absence():
    """A snapshot of 0 is a counter pickup, and it must not fall back.

    ``quoted_lead_time_days`` is tested for ``None``, never for truthiness. The
    ``or 14`` this column replaces made exactly this mistake in the other
    direction — the one lead time the guard could reach was the one it got
    wrong — so a same-day vendor that is later requoted to a week must still be
    graded against the same day it promised.
    """
    order = _send(_draft_with_a_line(quoted_lead_time=0), days_ago=0)
    line = order.items.get()
    assert line.quoted_lead_time_days == 0

    _requote(line.item_supplier, 7)
    line.refresh_from_db()

    create_lead_time_log(line, timezone.now().date())

    log = LeadTimeLog.objects.get(purchase_order=order)
    assert log.estimated_lead_time_days == 0
    assert log.variance_days == 0
    assert log.estimated_lead_time_basis == LeadTimeLog.ESTIMATE_FROM_ORDER_SNAPSHOT


def test_an_order_sent_before_the_snapshot_existed_falls_back_and_says_so():
    """No snapshot is "not recorded", and the row admits it rather than pretending.

    This is every order that was already with a supplier when the column landed.
    Nothing holds the promise they were given, so they are graded against the
    live quote — the number they were always going to be graded against — and
    ``estimated_lead_time_basis`` marks them, so a reader of this vendor's
    record can tell a promise we hold from one read at the wrong end.
    """
    order = _send(_draft_with_a_line(quoted_lead_time=4), days_ago=6)
    line = order.items.get()
    # Exactly the shape a pre-migration row has: sent, with no snapshot on it.
    PurchaseOrderItem.objects.filter(pk=line.pk).update(quoted_lead_time_days=None)
    line.refresh_from_db()

    create_lead_time_log(line, timezone.now().date())

    log = LeadTimeLog.objects.get(purchase_order=order)
    assert log.estimated_lead_time_days == 4
    assert log.estimated_lead_time_basis == LeadTimeLog.ESTIMATE_FROM_RECEIPT_QUOTE


def test_a_line_with_no_supplier_link_freezes_no_promise():
    """Asset and freeform lines have no vendor quote, so they keep their NULL.

    They write no ``LeadTimeLog`` either — ``create_lead_time_log`` returns on a
    line with no ``item_supplier`` — so there is nothing here to snapshot and a
    fabricated 0 would only look like a same-day promise nobody made.
    """
    order = _draft_with_a_line(quoted_lead_time=5)
    freeform = PurchaseOrderItem.objects.create(
        purchase_order=order,
        description="Pallet freight",
        quantity_ordered=1,
        unit_cost_ordered=Decimal("75.00"),
        order_in_packages=0,
    )

    _send(order, days_ago=1)

    freeform.refresh_from_db()
    assert freeform.quoted_lead_time_days is None
    assert order.items.get(item_supplier__isnull=False).quoted_lead_time_days == 5
