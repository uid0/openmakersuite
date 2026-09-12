"""A delivery on the date the system PUBLISHED is recorded on time.

The system tells the operator one date and then scores the vendor against a
different one. ``purchase_orders.update_reorder_requests_from_po`` wrote
``ReorderRequest.estimated_delivery`` by counting the supplier's quoted lead
time in BUSINESS days; ``receiving.create_lead_time_log`` measures the delivery
that arrives against that same quote in CALENDAR days. A five-day quote sent on
Monday 2026-03-02 published 2026-03-09 — five business days, seven calendar —
and the vendor that hit 2026-03-09 exactly was filed ``variance_days: +2,
was_late: True``, which then discounts it in
``inventory.services.supplier_selection``'s performance term.

The quote is a CALENDAR-day figure at its source: ``inventory.tasks``'
``update_average_lead_times`` writes ``ItemSupplier.average_lead_time`` from
``(actual_delivery - ordered_at).days``, plain elapsed days. So calendar is the
unit both ends must speak, and the published date was the end that was wrong.

These tests pin the promise and the measurement to ONE unit by landing a
delivery exactly on the published date and on each side of it.
"""

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

import pytest

from inventory.tests.factories import InventoryItemFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue.models import LeadTimeLog, PurchaseOrder, PurchaseOrderItem, ReorderRequest
from reorder_queue.services.purchase_orders import update_reorder_requests_from_po
from reorder_queue.services.receiving import create_lead_time_log
from reorder_queue.tests.factories import ReorderRequestFactory, UserFactory

pytestmark = pytest.mark.django_db

#: Monday, so a business-day count and a calendar-day count diverge over the
#: weekend that a five-day quote straddles.
MONDAY = datetime(2026, 3, 2, 12, 0, tzinfo=dt_timezone.utc)
QUOTE_DAYS = 5


def _ordered_line(*, quote=QUOTE_DAYS, primary=False):
    """One sent PO line with an approved request, and the date it publishes."""
    assert MONDAY.date().weekday() == 0

    supplier = SupplierFactory()
    item = InventoryItemFactory(current_stock=0)
    link = ItemSupplierFactory(
        supplier=supplier,
        item=item,
        quantity_per_package=1,
        average_lead_time=quote,
        is_primary=primary,
    )
    po = PurchaseOrder.objects.create(
        supplier=supplier,
        created_by=UserFactory(),
        status=PurchaseOrder.Status.SENT,
        sent_at=MONDAY,
        expected_delivery_date=None,
    )
    po_item = PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=link,
        # Frozen by ``mark_sent`` on every real send path (oms-ltsnap); set here
        # so the graded end reads the same quote the published date came from.
        quoted_lead_time_days=quote,
        quantity_ordered=1,
        unit_cost_ordered=Decimal("2.00"),
        order_in_packages=1,
    )
    request = ReorderRequestFactory(item=item, status=ReorderRequest.Status.APPROVED)

    update_reorder_requests_from_po(po)
    request.refresh_from_db()

    return po_item, request


def test_the_published_date_counts_the_quote_in_calendar_days():
    """Five days from Monday is Saturday 2026-03-07, not the following Monday."""
    _, request = _ordered_line()

    assert request.estimated_delivery == date(2026, 3, 7)
    assert request.estimated_delivery == MONDAY.date() + timedelta(days=QUOTE_DAYS)


def test_a_delivery_on_the_published_date_is_recorded_on_time():
    """The finding: exactly the date we published must score zero variance."""
    po_item, request = _ordered_line()

    create_lead_time_log(po_item, request.estimated_delivery)

    log = LeadTimeLog.objects.get(purchase_order=po_item.purchase_order)
    assert log.actual_lead_time_days == log.estimated_lead_time_days
    assert log.variance_days == 0
    assert log.was_late is False
    assert log.was_early is False


def test_a_zero_day_quote_publishes_and_scores_the_order_date():
    po_item, request = _ordered_line(quote=0, primary=True)

    assert request.estimated_delivery is not None
    assert request.estimated_delivery == MONDAY.date()

    item = po_item.item_supplier.item
    item.refresh_from_db()
    assert item.get_expected_delivery_date() == MONDAY.date()

    create_lead_time_log(po_item, request.estimated_delivery)

    log = LeadTimeLog.objects.get(purchase_order=po_item.purchase_order)
    assert log.variance_days == 0
    assert log.was_late is False


def test_a_delivery_a_day_before_the_published_date_is_recorded_early():
    po_item, request = _ordered_line()

    create_lead_time_log(po_item, request.estimated_delivery - timedelta(days=1))

    log = LeadTimeLog.objects.get(purchase_order=po_item.purchase_order)
    assert log.variance_days == -1
    assert log.was_late is False
    assert log.was_early is True


def test_a_delivery_a_day_after_the_published_date_is_recorded_late():
    po_item, request = _ordered_line()

    create_lead_time_log(po_item, request.estimated_delivery + timedelta(days=1))

    log = LeadTimeLog.objects.get(purchase_order=po_item.purchase_order)
    assert log.variance_days == 1
    assert log.was_late is True


def test_the_log_expects_the_same_date_the_operator_was_published():
    """``expected_delivery_date`` on the row and the operator's date are one date.

    Both fall back to the link's quote when the order carries no confirmed date,
    so a reader comparing the row against the screen must not find two answers.
    """
    po_item, request = _ordered_line()

    create_lead_time_log(po_item, request.estimated_delivery)

    log = LeadTimeLog.objects.get(purchase_order=po_item.purchase_order)
    assert log.expected_delivery_date == request.estimated_delivery


def test_both_surfaces_that_publish_a_delivery_date_publish_the_same_one():
    """The class, not the instance: two derivations of one operator-facing date.

    ``InventoryItem.get_expected_delivery_date`` (served on the inventory list as
    ``expected_delivery_date``) and ``ReorderRequest.estimated_delivery`` (the
    reorder queue) answer the same question from the same quote. They counted it
    in different units, so the two screens showed two dates for one order.
    """
    po_item, request = _ordered_line(primary=True)

    item = po_item.item_supplier.item
    item.refresh_from_db()

    assert item.get_expected_delivery_date() == request.estimated_delivery
