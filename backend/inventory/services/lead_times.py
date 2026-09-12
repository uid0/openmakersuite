"""The one place a quoted lead time becomes a delivery DATE.

``ItemSupplier.average_lead_time`` is a CALENDAR-day figure, and that is not a
convention this module picked: :func:`inventory.tasks.update_average_lead_times`
writes the column from ``(reorder.actual_delivery - reorder.ordered_at.date())
.days`` — plain elapsed days, weekends included — and
:class:`~reorder_queue.models.LeadTimeLog` scores a delivery by subtracting the
same column from an actual lead time measured the same way. So every date
derived from the quote has to be counted in calendar days too, or the system
publishes a promise in one unit and grades it in another.

It did. ``purchase_orders.update_reorder_requests_from_po`` used to count the
quote in BUSINESS days through a local ``add_business_days`` helper, so a
five-day quote sent on Monday 2026-03-02 published 2026-03-09 (five business
days, seven calendar) while the log expected 2026-03-07. A vendor that delivered
on 2026-03-09 — exactly the date the system had shown the operator — was filed
``variance_days: +2, was_late: True`` and discounted in
:mod:`inventory.services.supplier_selection`'s performance term for keeping the
promise it was given. That is the THIRD time this quote has been counted in the
wrong unit, so the counting now happens once, here, and ``add_business_days`` is
gone rather than left beside a lead time for a fourth caller to reach for.

Every producer of an operator-facing expected delivery date calls
:func:`published_delivery_date`: ``update_reorder_requests_from_po``
(``ReorderRequest.estimated_delivery``, the reorder queue),
``InventoryItem.get_expected_delivery_date`` (the inventory list's
``expected_delivery_date``) and ``receiving.create_lead_time_log``
(``LeadTimeLog.expected_delivery_date``, the fallback when the order carries no
confirmed date). Pinned end to end by
``reorder_queue/tests/test_published_delivery_date_is_the_yardstick.py``.

A CONFIRMED date on the purchase order still wins over all of this — the quote is
only ever the fallback — and that precedence is the callers' business, not this
module's.
"""

from datetime import timedelta

#: The unit ``ItemSupplier.average_lead_time`` is recorded in, named once so a
#: surface that has to tell an operator what it counted does not have to guess.
QUOTED_LEAD_TIME_UNIT = "calendar_days"


def published_delivery_date(order_date, quoted_lead_time_calendar_days):
    """The delivery date a quote of ``N`` calendar days implies from ``order_date``.

    ``order_date`` may be a ``date`` or an aware ``datetime``; a datetime is
    narrowed to its own date, so callers holding ``PurchaseOrder.sent_at`` or
    ``ReorderRequest.ordered_at`` need no conversion of their own.

    Returns ``None`` when there is no quote to count, so a caller can tell "we
    have no basis for a date" apart from "the date is today".
    """
    if quoted_lead_time_calendar_days is None:
        return None

    if hasattr(order_date, "date"):
        order_date = order_date.date()

    return order_date + timedelta(days=quoted_lead_time_calendar_days)
