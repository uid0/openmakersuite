"""A same-day supplier's delivery record says "same day", not "a fortnight".

``services.receiving.create_lead_time_log`` writes the row that IS a supplier's
delivery record: ``estimated_lead_time_days`` is what the vendor promised,
``actual_lead_time_days`` is what happened, and ``variance_days`` — the
difference, which ``LeadTimeLog.save`` derives — is the column everything else
reads to answer "does this vendor keep its word?".

It used to compute the estimate as ``item_supplier.average_lead_time or 14``.
``average_lead_time`` is a non-null ``PositiveIntegerField``, so that fallback
could only ever fire on a value of **0** — a counter pickup from a local
supplier. The one case the fallback could reach was the one case it got wrong: a
vendor that promised today was recorded as having promised a fortnight, so every
delivery it ever made looked two weeks early.

The two columns are also in the SAME unit — calendar days — which they were not
before. On this row ``estimated_lead_time_days`` comes from
``average_lead_time`` and ``expected_delivery_date`` is derived from it with
``timedelta(days=...)``, while the actual was measured with an inclusive
business-day count, so ``variance_days`` subtracted two different things and
every promise kept inside the working week — same-day, next-day, two-day,
four-day — recorded ``+1`` and read as late. Only spans crossing a weekend
happened to cancel.

That is not cosmetic. ``inventory.services.supplier_selection``'s performance
term reads ``variance_days`` to decide who the next purchase order goes to
(op-2rsp), which
``test_a_punctual_same_day_vendor_keeps_the_whole_performance_weight_and_wins``
below pins end to end — so an estimate invented here, or an actual measured in
the wrong unit, is a wrong purchase.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

import pytest

from inventory.services.supplier_selection import (
    delivery_records_for,
    score_candidate,
    select_supplier,
)
from inventory.tests.factories import InventoryItemFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue.models import LeadTimeLog, PurchaseOrder, PurchaseOrderItem
from reorder_queue.services.receiving import create_lead_time_log
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _po_item(*, average_lead_time, expected_delivery_date=None, sent_days_ago=3, item=None):
    supplier = SupplierFactory()
    sent_at = timezone.now() - timedelta(days=sent_days_ago)
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
            item=item or InventoryItemFactory(current_stock=0),
        ),
        quantity_ordered=4,
        unit_cost_ordered=Decimal("2.00"),
        order_in_packages=4,
    )


def _po_item_for(item_supplier, *, sent_days_ago):
    """A sent PO line against an EXISTING link, so a link builds up a record."""
    po = PurchaseOrder.objects.create(
        supplier=item_supplier.supplier,
        created_by=UserFactory(),
        status=PurchaseOrder.Status.SENT,
        sent_at=timezone.now() - timedelta(days=sent_days_ago),
    )
    return PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=item_supplier,
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


# ── One unit on both sides of the subtraction: calendar days ─────────────────


@pytest.mark.parametrize("promise", [0, 1, 2, 4])
def test_a_promise_kept_exactly_records_no_variance_and_is_not_late(promise):
    """The case the earlier tests never reached: the vendor actually kept its word.

    Every one of these recorded ``variance_days == +1`` and read as LATE before
    the units were reconciled — the estimate was calendar days and the actual
    was an INCLUSIVE business-day count, so a delivery on the promised day was
    counted as one day's work. Only spans crossing a weekend happened to cancel,
    which is why the old tests, which sent the PO three days out, missed it.
    """
    po_item = _po_item(average_lead_time=promise, sent_days_ago=promise)
    promised_day = po_item.purchase_order.sent_at.date() + timedelta(days=promise)

    create_lead_time_log(po_item, promised_day)

    log = LeadTimeLog.objects.get(purchase_order=po_item.purchase_order)
    assert log.estimated_lead_time_days == promise
    assert log.actual_lead_time_days == promise
    assert log.variance_days == 0
    assert log.was_late is False


def test_a_week_long_span_is_seven_days_not_the_five_that_fall_on_weekdays():
    """The actual is the wait the buyer sat through, weekends included.

    A business-day count answered a different question from the one the estimate
    is an answer to: ``average_lead_time`` is what the vendor promised in
    calendar days, and ``expected_delivery_date`` is derived from it with
    ``timedelta(days=...)``.
    """
    po_item = _po_item(average_lead_time=7, sent_days_ago=7)
    delivered = po_item.purchase_order.sent_at.date() + timedelta(days=7)

    create_lead_time_log(po_item, delivered)

    log = LeadTimeLog.objects.get(purchase_order=po_item.purchase_order)
    assert log.actual_lead_time_days == 7
    assert log.variance_days == 0


def test_a_delivery_dated_before_the_order_clamps_to_zero():
    """The clamp the old helper's ``start > end`` guard provided, kept.

    ``actual_lead_time_days`` is a ``PositiveIntegerField``, so a back-dated
    delivery must not try to store a negative wait.
    """
    po_item = _po_item(average_lead_time=2, sent_days_ago=0)
    before_the_order = po_item.purchase_order.sent_at.date() - timedelta(days=5)

    create_lead_time_log(po_item, before_the_order)

    log = LeadTimeLog.objects.get(purchase_order=po_item.purchase_order)
    assert log.actual_lead_time_days == 0
    assert log.variance_days == -2
    assert log.was_late is False


def test_a_punctual_same_day_vendor_keeps_the_whole_performance_weight_and_wins():
    """The end-to-end consequence — this is the behaviour that moves money.

    Two links on one item at the same price. The same-day vendor delivered on
    the day three times; the nine-day vendor delivered on its promised day three
    times. Both records are clean, so both keep the full ``PERFORMANCE_WEIGHT``
    and the faster one wins on speed.

    Before the units were reconciled the same-day vendor's three punctual
    deliveries each recorded ``+1`` and read as late, so its performance factor
    collapsed to 0: it scored ``0.40 + 0.30 + 0.00 = 0.70`` and LOST the
    purchase order to the nine-day vendor's ``0.40 + 0.21 + 0.10 = 0.71``.
    """
    item = InventoryItemFactory(current_stock=0)
    # The factory ships the item with a flagged-primary link of its own, which
    # would win at the GATE and never reach scoring — this test is about the
    # scoring, so the item starts with no links.
    item.item_suppliers.all().delete()
    links = {}
    for promise in (0, 9):
        supplier = SupplierFactory()
        links[promise] = ItemSupplierFactory(
            supplier=supplier,
            item=item,
            quantity_per_package=1,
            # ``ItemSupplier.save`` derives ``unit_cost`` from ``package_cost``
            # when there is one, so the package price is what sets the unit here.
            package_cost=Decimal("5.00"),
            average_lead_time=promise,
            is_primary=False,
        )
        for _ in range(3):
            po_item = _po_item_for(links[promise], sent_days_ago=promise + 30)
            create_lead_time_log(
                po_item, po_item.purchase_order.sent_at.date() + timedelta(days=promise)
            )

    same_day, nine_day = links[0], links[9]
    for log in LeadTimeLog.objects.filter(item_supplier__item=item):
        assert log.variance_days == 0
        assert log.was_late is False

    records = delivery_records_for(list(item.item_suppliers.all()))
    assert records[same_day.pk].factor == Decimal(1)
    assert records[nine_day.pk].factor == Decimal(1)

    average = Decimal("5.00")
    assert score_candidate(same_day, average, records[same_day.pk]) == Decimal("0.80")
    assert score_candidate(nine_day, average, records[nine_day.pk]) == Decimal("0.71")
    assert select_supplier(item).item_supplier.pk == same_day.pk


# ── Two promises on one row, and which one variance scores ───────────────────


def _confirmed_order_delivered_on_the_confirmed_day():
    """Quote 3 days, confirm 10, deliver on day 10 — the finding's exact trace."""
    po_item = _po_item(average_lead_time=3, sent_days_ago=10)
    confirmed = po_item.purchase_order.sent_at.date() + timedelta(days=10)
    po_item.purchase_order.expected_delivery_date = confirmed
    po_item.purchase_order.save(update_fields=["expected_delivery_date"])

    create_lead_time_log(po_item, confirmed)

    return LeadTimeLog.objects.get(purchase_order=po_item.purchase_order)


def test_variance_scores_the_standing_quote_not_the_confirmed_date():
    """DELIBERATE: the link's standing quote is the yardstick, not the PO's date.

    A vendor quoting 3 days confirms 10 once it has the order, then delivers on
    day 10. The row therefore carries ``expected_delivery_date ==
    actual_delivery_date`` AND ``variance_days == +7``, which looks like a
    contradiction and is not: the two dates record the promise the vendor made
    for THIS order, and the variance records the promise it advertises for every
    order.

    Scoring the confirmed date instead would let that vendor quote three days,
    confirm ten, deliver ten, and win on BOTH axes — the lead-time term would
    pay it for a 3-day quote while the performance term found nothing to
    discount. The performance term exists only to discount the standing quote by
    how often the vendor broke it, so it must measure against that same quote.
    """
    log = _confirmed_order_delivered_on_the_confirmed_day()

    assert log.expected_delivery_date == log.actual_delivery_date
    assert log.estimated_lead_time_days == 3
    assert log.actual_lead_time_days == 10
    assert log.variance_days == 7
    assert log.was_late is True
