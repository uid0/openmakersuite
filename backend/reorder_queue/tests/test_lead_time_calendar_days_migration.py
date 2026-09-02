"""The backfill that stops ``variance_days`` mixing two units across time.

``0035_backfill_lead_time_calendar_days`` recomputes ``actual_lead_time_days``
and ``variance_days`` on every existing :class:`~reorder_queue.models.LeadTimeLog`
from the ``order_date`` and ``actual_delivery_date`` already on the row. Rows
written before this branch hold an INCLUSIVE business-day count while new rows
hold calendar days, and ``supplier_selection``'s performance term reads the
difference to decide which vendor a purchase order goes to — so a link whose
history straddles the change would be judged on two conventions at once.

Like ``devices.tests.test_migrations``, the migration has already run by the
time these start, so they seed rows in the old unit and call the migration
callables directly.
"""

from datetime import date, datetime, timedelta
from importlib import import_module

from django.apps import apps as django_apps
from django.utils import timezone

import pytest

from inventory.tests.factories import InventoryItemFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue.models import LeadTimeLog, PurchaseOrder
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

backfill = import_module("reorder_queue.migrations.0035_backfill_lead_time_calendar_days")

# A Monday, so every span below lands on a known weekday and the inclusive
# business-day arithmetic the reverse restores is unambiguous.
MONDAY = date(2026, 3, 2)


def _log(*, promised, delivered_after, stored_actual):
    """One log whose stored actual is ``stored_actual`` regardless of its dates.

    ``LeadTimeLog.save`` derives ``variance_days``, so seeding a row in the OLD
    unit is a matter of storing the business-day count against dates that are
    ``delivered_after`` calendar days apart.
    """
    supplier = SupplierFactory()
    order_date = timezone.make_aware(datetime.combine(MONDAY, datetime.min.time()))
    return LeadTimeLog.objects.create(
        item_supplier=ItemSupplierFactory(
            supplier=supplier,
            quantity_per_package=1,
            average_lead_time=promised,
            item=InventoryItemFactory(current_stock=0),
        ),
        purchase_order=PurchaseOrder.objects.create(supplier=supplier, created_by=UserFactory()),
        order_date=order_date,
        expected_delivery_date=MONDAY + timedelta(days=promised),
        actual_delivery_date=MONDAY + timedelta(days=delivered_after),
        estimated_lead_time_days=promised,
        actual_lead_time_days=stored_actual,
        quantity_ordered=10,
        quantity_received=10,
    )


def test_a_kept_promise_stops_reading_as_late():
    """Monday to Monday on a same-day promise: one business day became zero.

    This is the row the finding traced — a punctual counter-pickup filed as a
    day late, which cost the vendor the whole performance weight.
    """
    log = _log(promised=0, delivered_after=0, stored_actual=1)
    assert log.variance_days == 1 and log.was_late is True

    backfill.to_calendar_days(django_apps, None)

    log.refresh_from_db()
    assert log.actual_lead_time_days == 0
    assert log.variance_days == 0
    assert log.was_late is False


def test_a_span_across_a_weekend_is_measured_in_calendar_days():
    """Monday to the following Monday is 7 days waited, not the 6 weekdays counted."""
    log = _log(promised=7, delivered_after=7, stored_actual=6)

    backfill.to_calendar_days(django_apps, None)

    log.refresh_from_db()
    assert log.actual_lead_time_days == 7
    assert log.variance_days == 0


def test_the_promise_itself_is_left_exactly_as_it_was_recorded():
    """``estimated_lead_time_days`` is history, not something to recompute.

    The link's ``average_lead_time`` may have moved since the order, and rows
    written before this branch recorded 14 for a same-day vendor because of the
    ``average_lead_time or 14`` guard. Rewriting it would fabricate a promise
    nobody made.
    """
    log = _log(promised=14, delivered_after=0, stored_actual=1)
    log.item_supplier.average_lead_time = 0
    log.item_supplier.save(update_fields=["average_lead_time"])

    backfill.to_calendar_days(django_apps, None)

    log.refresh_from_db()
    assert log.estimated_lead_time_days == 14
    assert log.actual_lead_time_days == 0
    assert log.variance_days == -14


def test_the_backfill_is_reversible_to_the_inclusive_business_day_rule():
    """The change can be backed out: the reverse puts the old counts back."""
    monday_to_monday = _log(promised=7, delivered_after=7, stored_actual=6)
    same_day = _log(promised=0, delivered_after=0, stored_actual=1)

    backfill.to_calendar_days(django_apps, None)
    backfill.to_inclusive_business_days(django_apps, None)

    monday_to_monday.refresh_from_db()
    same_day.refresh_from_db()
    # Mon-Sun inclusive is 6 weekdays; Mon-Mon inclusive is 1.
    assert monday_to_monday.actual_lead_time_days == 6
    assert monday_to_monday.variance_days == -1
    assert same_day.actual_lead_time_days == 1
    assert same_day.variance_days == 1


def test_running_the_backfill_twice_changes_nothing_the_second_time():
    log = _log(promised=2, delivered_after=2, stored_actual=3)

    backfill.to_calendar_days(django_apps, None)
    log.refresh_from_db()
    once = (log.actual_lead_time_days, log.variance_days)

    backfill.to_calendar_days(django_apps, None)
    log.refresh_from_db()
    assert (log.actual_lead_time_days, log.variance_days) == once == (2, 0)
