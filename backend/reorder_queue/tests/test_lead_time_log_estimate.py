"""A same-day supplier's delivery record says "same day", not "a fortnight".

``services.receiving.create_lead_time_log`` writes the row that IS a supplier's
delivery record: ``estimated_lead_time_days`` is what the vendor promised,
``actual_lead_time_days`` is what happened, and ``variance_days`` — the
difference, which ``LeadTimeLog.save`` derives — is the column everything else
reads to answer "does this vendor keep its word?".

It used to compute the estimate as ``item_supplier.average_lead_time or 14``.
``average_lead_time`` is a non-null ``PositiveIntegerField``, so that fallback
could only ever fire on a value of **0** — a counter pickup from a local
supplier, which ``inventory.tasks.update_average_lead_times`` derives from real
deliveries. The one case it could reach was the one case it got wrong: a vendor
that promised today was recorded as having promised a fortnight, so every
delivery it ever made looked two weeks early.

That is not cosmetic. ``inventory.services.supplier_selection``'s performance
term reads ``variance_days`` to decide who the next purchase order goes to
(op-2rsp), and the supplier screen's ``on_time_percentage`` reports it, so an
estimate invented here is a wrong purchase and a wrong screen.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

import pytest

from inventory.tests.factories import InventoryItemFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue.models import LeadTimeLog, PurchaseOrder, PurchaseOrderItem
from reorder_queue.services.receiving import create_lead_time_log
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _po_item(*, average_lead_time, expected_delivery_date=None):
    supplier = SupplierFactory()
    sent_at = timezone.now() - timedelta(days=3)
    po = PurchaseOrder.objects.create(
        supplier=supplier,
        created_by=UserFactory(),
        status=PurchaseOrder.Status.SENT,
        sent_at=sent_at,
        expected_delivery_date=expected_delivery_date,
    )
    return PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=ItemSupplierFactory(
            supplier=supplier,
            quantity_per_package=1,
            average_lead_time=average_lead_time,
            item=InventoryItemFactory(current_stock=0),
        ),
        quantity_ordered=4,
        unit_cost_ordered=Decimal("2.00"),
        order_in_packages=4,
    )


def test_a_same_day_supplier_is_recorded_as_having_promised_same_day():
    """0 is the promise the vendor made, not an absence to paper over with 14."""
    po_item = _po_item(average_lead_time=0)

    create_lead_time_log(po_item, timezone.now().date())

    log = LeadTimeLog.objects.get(purchase_order=po_item.purchase_order)
    assert log.estimated_lead_time_days == 0
    assert log.estimated_lead_time_days != 14


def test_a_same_day_supplier_that_took_a_week_is_recorded_as_LATE():
    """The consequence, and why this is a purchasing bug and not a display one.

    A vendor that promised today and delivered days later is late. With the
    estimate forced to 14 the variance came out NEGATIVE, so the same delivery
    was filed as early — and ``supplier_selection``'s performance term, which
    counts ``variance_days <= 0`` as keeping the promise, would have credited a
    broken promise as a kept one.
    """
    po_item = _po_item(average_lead_time=0)

    create_lead_time_log(po_item, timezone.now().date())

    log = LeadTimeLog.objects.get(purchase_order=po_item.purchase_order)
    assert log.actual_lead_time_days > 0
    assert log.variance_days == log.actual_lead_time_days  # estimate of 0
    assert log.was_late is True


def test_the_expected_date_falls_back_to_the_promised_day_not_a_fortnight_later():
    """With no expected date on the PO, the fallback is order date + the promise.

    For a same-day vendor that is the day the order went out; it used to be a
    fortnight after it.
    """
    po_item = _po_item(average_lead_time=0, expected_delivery_date=None)

    create_lead_time_log(po_item, timezone.now().date())

    log = LeadTimeLog.objects.get(purchase_order=po_item.purchase_order)
    assert log.expected_delivery_date == po_item.purchase_order.sent_at.date()


def test_an_ordinary_lead_time_is_unchanged():
    """The other side of the guard: nothing moves for a vendor promising 10 days."""
    po_item = _po_item(average_lead_time=10)

    create_lead_time_log(po_item, timezone.now().date())

    log = LeadTimeLog.objects.get(purchase_order=po_item.purchase_order)
    assert log.estimated_lead_time_days == 10
    assert log.expected_delivery_date == po_item.purchase_order.sent_at.date() + timedelta(days=10)
