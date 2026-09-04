"""Every surface that shows a lead-time lateness names what it is measured against.

A :class:`~reorder_queue.models.LeadTimeLog` row holds TWO promises. The supplier
link's standing quoted lead time is the one ``variance_days`` scores — see
``test_variance_scores_the_standing_quote_not_the_confirmed_date`` in
``test_lead_time_log_estimate.py``, which pins that and is deliberately not
changed here. ``expected_delivery_date`` is the other: the date the operator
confirmed on the purchase order, which nothing scores.

So a vendor quoting 3 days, confirmed for day 10, delivering on day 10 produces a
row carrying ``expected_delivery_date == actual_delivery_date`` alongside
``variance_days: 7, was_late: True``. The number is right. What was wrong was
every rendering of it: the admin said "⚠️ 7 days late", the supplier analytics
payload served ``was_late`` with nothing naming the yardstick, and the purchasing
report's "On-Time Rate" said on-time against nothing in particular. The captain
chases vendors off those screens, so each one could report a supplier a week late
on an order it delivered exactly when agreed.

These tests pin the rendering, not the arithmetic. They fail if any surface goes
back to a bare lateness, and they fail if two surfaces start naming two different
promises.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.urls import reverse
from django.utils import timezone

import pytest

from inventory.serializers import SupplierDetailSerializer
from inventory.tests.factories import InventoryItemFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue.admin import DeliveryPerformanceFilter, LeadTimeLogAdmin
from reorder_queue.models import LeadTimeLog, PurchaseOrder, PurchaseOrderItem
from reorder_queue.services.receiving import create_lead_time_log
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# ── the finding's exact trace, built once ────────────────────────────────────


def _delivery(*, quoted, delivered_after, confirmed_after=None, supplier=None):
    """One received line: link quotes ``quoted``, goods land ``delivered_after``.

    ``confirmed_after`` is the date the OPERATOR confirmed on the order,
    expressed in days from despatch; ``None`` leaves the order with no confirmed
    date at all, which is a real and different case.
    """
    supplier = supplier or SupplierFactory()
    sent_at = timezone.now() - timedelta(days=max(delivered_after, confirmed_after or 0))
    po = PurchaseOrder.objects.create(
        supplier=supplier,
        created_by=UserFactory(),
        status=PurchaseOrder.Status.SENT,
        sent_at=sent_at,
        expected_delivery_date=(
            None if confirmed_after is None else sent_at.date() + timedelta(days=confirmed_after)
        ),
    )
    po_item = PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=ItemSupplierFactory(
            supplier=supplier,
            quantity_per_package=1,
            average_lead_time=quoted,
            item=InventoryItemFactory(current_stock=0),
        ),
        quantity_ordered=4,
        unit_cost_ordered=Decimal("2.00"),
        order_in_packages=4,
    )
    create_lead_time_log(po_item, sent_at.date() + timedelta(days=delivered_after))
    return LeadTimeLog.objects.get(purchase_order=po)


def _kept_promise_broken_quote(**kwargs):
    """Quote 3, confirm 10, deliver 10 — over the quote, on the agreed day."""
    return _delivery(quoted=3, confirmed_after=10, delivered_after=10, **kwargs)


# ── the root: one yardstick, derived once ────────────────────────────────────


def test_the_yardstick_label_is_the_machine_name_in_words():
    """The two constants must stay the same promise said two ways.

    Surfaces pick whichever suits them — JSON takes the machine name, the admin
    takes the words — so a drift between them would put two different promises on
    two screens showing the same number.
    """
    assert LeadTimeLog.VARIANCE_YARDSTICK == "quoted_lead_time"
    assert LeadTimeLog.VARIANCE_YARDSTICK_LABEL == "quoted lead time"
    assert LeadTimeLog.VARIANCE_YARDSTICK_LABEL.replace(" ", "_") == (
        LeadTimeLog.VARIANCE_YARDSTICK
    )


def test_a_kept_confirmed_date_shows_as_met_beside_a_broken_quote():
    """The row the whole task is about, at the root that every surface reads."""
    log = _kept_promise_broken_quote()

    # Unchanged, and deliberately so: the quote is still the yardstick.
    assert log.variance_days == 7
    assert log.was_late is True
    # The other promise, which nothing scores and no surface used to show.
    assert log.met_confirmed_date is True
    assert log.confirmed_delivery_date == log.actual_delivery_date


def test_a_missed_confirmed_date_shows_as_missed():
    """Quote 3, confirm 5, deliver 9: over the quote AND past the agreed day."""
    log = _delivery(quoted=3, confirmed_after=5, delivered_after=9)

    assert log.was_late is True
    assert log.met_confirmed_date is False


def test_a_log_with_no_confirmed_date_on_the_order_says_so():
    """``None``, not ``True`` — there is no agreed date to have met.

    ``create_lead_time_log`` fills this row's ``expected_delivery_date`` from
    ``order_date + the standing quote`` when the order carries no confirmed date.
    Judging against that fallback would score the quote twice and report a date
    somebody agreed to when nobody did, so the answer comes off the purchase
    order and is ``None`` when it is empty.
    """
    log = _delivery(quoted=3, confirmed_after=None, delivered_after=3)

    assert log.confirmed_delivery_date is None
    assert log.met_confirmed_date is None
    # The fallback still populated the row's own column, which is exactly why
    # that column cannot be read as a confirmed date.
    assert log.expected_delivery_date is not None


# ── the admin (S1, S2, S3) ───────────────────────────────────────────────────


def _admin():
    return LeadTimeLogAdmin(LeadTimeLog, AdminSite())


def test_a_kept_confirmed_date_is_not_rendered_as_simply_late():
    """The changelist cell that used to read "⚠️ 7 days late" and nothing else."""
    log = _kept_promise_broken_quote()

    cell = _admin().variance_display(log)

    # It still reports the vendor broke the quote — the number is right.
    assert "7 days over quoted lead time" in cell
    # But never as a bare lateness naming no promise.
    assert "7 days late" not in cell
    # And the other promise is in the same cell, so the operator sees both.
    assert "Met the confirmed date" in cell


def test_a_delivery_inside_the_quote_names_the_quote_too():
    """Early and on-time cells name the yardstick as well — not just the late one."""
    admin = _admin()

    early = admin.variance_display(_delivery(quoted=10, confirmed_after=10, delivered_after=7))
    on_quote = admin.variance_display(_delivery(quoted=5, confirmed_after=5, delivered_after=5))

    assert "3 days inside quoted lead time" in early
    assert "days early" not in early
    assert "On quoted lead time" in on_quote
    assert ">✓ On Time<" not in on_quote


def test_the_change_form_says_when_no_confirmed_date_was_agreed():
    """Rather than presenting the quote-derived fallback as an agreed date."""
    log = _delivery(quoted=3, confirmed_after=None, delivered_after=3)

    assert "No delivery date was confirmed" in _admin().confirmed_date_display(log)


def test_the_change_form_never_renders_a_bare_was_late_row(client, admin_user):
    """``was_late`` renders as "Was late: True", which names no yardstick.

    Rendered through the admin client rather than read off ``readonly_fields``:
    the class attribute is not what a reader sees, and a later
    ``get_readonly_fields()`` override putting the bare field back for some users
    would leave an attribute check green while the defect is on the screen again.
    """
    log = _kept_promise_broken_quote()
    client.force_login(admin_user)

    response = client.get(reverse("admin:reorder_queue_leadtimelog_change", args=[log.pk]))

    assert response.status_code == 200
    html = response.content.decode()
    assert "Was late" not in html
    assert "Was early" not in html
    # What stands in their place, naming the promise each scores.
    assert "7 days over quoted lead time" in html
    # Exactly once: the verdict is the second line of the variance cell, so
    # listing ``confirmed_date_display`` as its own row would repeat it.
    assert html.count("Met the confirmed date") == 1


def test_the_delivery_performance_filter_names_the_yardstick():
    """Its buckets are quote-relative, so "Late Delivery" alone was a false label."""
    lookups = dict(DeliveryPerformanceFilter(None, {}, LeadTimeLog, _admin()).lookups(None, None))

    assert lookups == {
        "early": "Inside quoted lead time",
        "on_time": "On quoted lead time",
        "late": "Over quoted lead time",
    }
    assert "quoted lead time" in DeliveryPerformanceFilter.title


def test_the_changelist_calls_the_quote_column_the_quote(client, admin_user):
    """So "N days over quoted lead time" lines up with a column of that name.

    The header a person reads, taken off the rendered page — "Estimated lead
    time days" left them to guess that the column two over was the same number.
    """
    log = _kept_promise_broken_quote()
    client.force_login(admin_user)

    response = client.get(reverse("admin:reorder_queue_leadtimelog_changelist"))

    assert response.status_code == 200
    html = response.content.decode()
    assert "Quoted lead time (days)" in html
    assert "Estimated lead time days" not in html
    # And the cell that phrase has to line up with, on the same page.
    assert "7 days over quoted lead time" in html
    assert str(log.estimated_lead_time_days) in html


# ── the served payloads (S4, S9, S10, S11) ───────────────────────────────────


def test_the_supplier_payload_names_the_yardstick_and_carries_both_promises():
    """``recent_logs`` used to serve ``was_late`` with nothing saying vs. what.

    The KEYS carry the yardstick here, not only the labels the web puts on them:
    a consumer reading ``on_time_percentage`` or ``was_late`` off this block
    asserts a bare lateness however the screen is worded. This block has no
    external consumer, so the rename is safe here where it is not for the
    reorders payloads ScanTTY decodes by name.
    """
    log = _kept_promise_broken_quote()

    analytics = SupplierDetailSerializer(log.supplier).data["lead_time_analytics"]

    assert analytics["variance_measured_against"] == LeadTimeLog.VARIANCE_YARDSTICK
    assert analytics["within_quoted_lead_time_pct"] == 0.0
    assert analytics["avg_variance_vs_quoted_lead_time_days"] == 7.0
    # No key on the block asserts a bare on-time-ness any more.
    assert "on_time_percentage" not in analytics
    assert "average_variance" not in analytics
    (row,) = analytics["recent_logs"]
    assert row["variance_days"] == 7
    assert row["was_over_quoted_lead_time"] is True
    assert "was_late" not in row
    assert row["expected_delivery_date"] == row["actual_delivery_date"]
    # And the promise that resolves the apparent contradiction — the verdict AND
    # the date the operator would chase the vendor with.
    assert row["met_confirmed_date"] is True
    assert row["confirmed_delivery_date"] == log.actual_delivery_date.isoformat()


def test_a_row_with_no_confirmed_date_serves_no_date_to_present_as_agreed():
    """``null``, never the row's own quote-derived ``expected_delivery_date``."""
    log = _delivery(quoted=3, confirmed_after=None, delivered_after=9)

    analytics = SupplierDetailSerializer(log.supplier).data["lead_time_analytics"]

    (row,) = analytics["recent_logs"]
    assert row["met_confirmed_date"] is None
    assert row["confirmed_delivery_date"] is None
    # The row still HAS a date of its own; it just is not an agreed one.
    assert row["expected_delivery_date"] is not None


def test_the_supplier_payload_names_the_yardstick_with_no_deliveries_yet():
    """The key must not appear and vanish with the data a consumer reads."""
    analytics = SupplierDetailSerializer(SupplierFactory()).data["lead_time_analytics"]

    assert analytics["total_orders"] == 0
    assert analytics["variance_measured_against"] == LeadTimeLog.VARIANCE_YARDSTICK


def test_the_two_supplier_analytics_endpoints_agree_when_there_are_no_deliveries(
    authenticated_client,
):
    """Same numbers, same names — including the shape of "no numbers yet".

    ``SupplierViewSet.analytics`` and ``SupplierDetailSerializer`` both serve a
    ``lead_time_analytics`` block. If only one of them names the yardstick when a
    supplier has no deliveries, a consumer reading the other is left to assume
    which promise the rates will be about once they arrive.
    """
    client, _ = authenticated_client
    supplier = SupplierFactory()

    response = client.get(reverse("supplier-analytics", kwargs={"pk": supplier.pk}))

    assert response.status_code == 200
    from_action = response.data["lead_time_analytics"]
    from_detail = SupplierDetailSerializer(supplier).data["lead_time_analytics"]

    assert from_action["variance_measured_against"] == LeadTimeLog.VARIANCE_YARDSTICK
    # ``recent_logs`` is the detail block's only extra key; the rest must match.
    assert from_action == {k: v for k, v in from_detail.items() if k != "recent_logs"}


def _both_supplier_blocks(client, supplier):
    """The ``lead_time_analytics`` block as each of the two endpoints serves it.

    They are separate code paths over the same aggregate, so a guard fixed on one
    and not the other would put two different answers on two screens.
    """
    response = client.get(reverse("supplier-analytics", kwargs={"pk": supplier.pk}))
    assert response.status_code == 200
    return (
        response.data["lead_time_analytics"],
        SupplierDetailSerializer(supplier).data["lead_time_analytics"],
    )


def test_a_vendor_that_hit_its_quote_exactly_reads_as_zero_not_as_no_data(
    authenticated_client,
):
    """Quote 5, deliver on day 5: average variance 0.0, and 0.0 is an ANSWER.

    Served as ``null`` it renders "N/A" on the very card this change exists to
    make honest, beside a sibling card reading "100.0% within quoted lead time".
    A perfect record is the one a buyer most needs to read.
    """
    client, _ = authenticated_client
    log = _delivery(quoted=5, delivered_after=5)
    assert log.variance_days == 0

    from_action, from_detail = _both_supplier_blocks(client, log.supplier)

    for block in (from_action, from_detail):
        assert block["avg_variance_vs_quoted_lead_time_days"] == 0.0
        assert block["avg_variance_vs_quoted_lead_time_days"] is not None
        assert block["within_quoted_lead_time_pct"] == 100.0


def test_a_same_day_vendors_zero_day_lead_time_reads_as_zero_not_as_no_data(
    authenticated_client,
):
    """A counter pickup that lands the day it is ordered averages 0 days.

    ``create_lead_time_log`` already refuses to invent a fortnight for these
    suppliers; the payload must not undo that by reporting the 0 it recorded as
    an absence of data.
    """
    client, _ = authenticated_client
    log = _delivery(quoted=3, delivered_after=0)
    assert log.actual_lead_time_days == 0

    from_action, from_detail = _both_supplier_blocks(client, log.supplier)

    for block in (from_action, from_detail):
        assert block["average_lead_time"] == 0.0
        assert block["average_lead_time"] is not None
        assert block["min_lead_time"] == 0
        assert block["max_lead_time"] == 0


def test_the_reorders_analytics_payloads_name_the_yardstick(authenticated_client):
    """``supplier_performance``, ``lead_time_trends`` and ``dashboard_summary``.

    The first two are on ``AnalyticsViewSet``; ``dashboard_summary`` hangs off
    ``PurchaseOrderViewSet`` and serves ``OrderMetricsSerializer``.

    These three serve ``on_time_delivery_rate`` / ``late_delivery_rate`` /
    ``average_variance_days``, all against the standing quote. They are the
    payloads ScanTTY renders as "On-time %" / "Late %" / "Avg var d" columns, so
    each carries the yardstick rather than leaving the reader to assume the
    rates count missed agreed dates.
    """
    client, _ = authenticated_client
    _kept_promise_broken_quote()

    for route in ("analytics-supplier-performance", "analytics-lead-time-trends"):
        response = client.get(reverse(route))
        assert response.status_code == 200, route
        assert response.data, f"{route} returned no rows to check"
        for row in response.data:
            assert row["variance_measured_against"] == LeadTimeLog.VARIANCE_YARDSTICK, route

    metrics = client.get(reverse("purchaseorder-dashboard-summary"))
    assert metrics.status_code == 200
    assert metrics.data["variance_measured_against"] == LeadTimeLog.VARIANCE_YARDSTICK


def test_the_purchasing_lead_time_report_names_the_yardstick(authenticated_client):
    """The JSON behind the web report table and ScanTTY's "Lead time" tab."""
    client, _ = authenticated_client
    log = _kept_promise_broken_quote()

    response = client.get(
        reverse("purchasing-reports-lead-time-analysis"),
        {
            "start_date": (log.actual_delivery_date - timedelta(days=1)).isoformat(),
            "end_date": (log.actual_delivery_date + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 200
    (row,) = [r for r in response.data if r["supplier_id"] == log.supplier.id]
    # Untouched, because ScanTTY decodes both of these by name.
    assert row["avg_variance"] == 7.0
    assert row["on_time_rate"] == 0.0
    assert row["variance_measured_against"] == LeadTimeLog.VARIANCE_YARDSTICK


def test_the_lead_time_csv_header_names_the_yardstick(authenticated_client):
    """The download a buyer opens in a spreadsheet, away from any of this context.

    "on_time_rate" as a column reads as the share of agreed dates the vendor hit.
    It is not — it is the share of deliveries inside the standing quote — so the
    COLUMN NAME says which, rather than a human label doing it: this endpoint
    emits machine keys on every other ``?type=``, and one export answering in two
    header conventions would break whatever reads the others. The row VALUES are
    untouched, which this pins by checking each number still lands under its
    renamed column.
    """
    client, _ = authenticated_client
    log = _kept_promise_broken_quote()

    response = client.get(
        reverse("purchasing-reports-export"),
        {
            "type": "lead_time_analysis",
            "start_date": (log.actual_delivery_date - timedelta(days=1)).isoformat(),
            "end_date": (log.actual_delivery_date + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 200
    header, *body = response.content.decode().strip().splitlines()
    columns = header.split(",")
    assert "avg_quoted_lead_time_days" in columns
    assert "avg_variance_vs_quoted_lead_time_days" in columns
    assert "within_quoted_lead_time_pct" in columns
    # Neither the bare key nor a bare human label may come back.
    assert "on_time_rate" not in columns
    assert "avg_variance" not in columns
    assert "On-Time Rate" not in header
    # The values did not move with the column names.
    row = dict(zip(columns, body[0].split(",")))
    assert row["avg_quoted_lead_time_days"] == "3.0"
    assert row["avg_variance_vs_quoted_lead_time_days"] == "7.0"
    assert row["within_quoted_lead_time_pct"] == "0.0%"


# ── which promise the operator is SHOWN before delivery ──────────────────────


def _approved_request_for(po_item):
    """An approved request the PO fulfils, so sending it sets a delivery date."""
    from reorder_queue.models import ReorderRequest
    from reorder_queue.tests.factories import ReorderRequestFactory

    return ReorderRequestFactory(
        item=po_item.item_supplier.item,
        status=ReorderRequest.Status.APPROVED,
    )


def test_the_shown_delivery_date_prefers_the_confirmed_date_over_the_quote():
    """The confirmed date WINS — the quote is only a fallback, never an override.

    ``ENHANCED_REORDER_WORKFLOW.md`` used to describe this as
    ``order_date + lead_time`` flat out, which names the wrong promise: when the
    operator has confirmed a date on the order, the lead time is not consulted at
    all. That is the same two-promises confusion the rest of this file is about,
    written into a document.
    """
    from reorder_queue.services.purchase_orders import update_reorder_requests_from_po

    supplier = SupplierFactory()
    sent_at = timezone.now() - timedelta(days=1)
    confirmed = sent_at.date() + timedelta(days=30)
    po = PurchaseOrder.objects.create(
        supplier=supplier,
        created_by=UserFactory(),
        status=PurchaseOrder.Status.SENT,
        sent_at=sent_at,
        expected_delivery_date=confirmed,
    )
    po_item = PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=ItemSupplierFactory(
            supplier=supplier,
            quantity_per_package=1,
            average_lead_time=3,
            item=InventoryItemFactory(current_stock=0),
        ),
        quantity_ordered=1,
        unit_cost_ordered=Decimal("2.00"),
        order_in_packages=1,
    )
    request = _approved_request_for(po_item)

    update_reorder_requests_from_po(po)

    request.refresh_from_db()
    assert request.estimated_delivery == confirmed


def test_the_quote_fallback_counts_BUSINESS_days_not_calendar_days():
    """And the doc must not call it plain addition, because it is not.

    With no confirmed date on the order the shown date comes from
    ``add_business_days``, which skips weekends. A five-day quote sent on a
    Monday shows the NEXT Monday, seven calendar days out — which is also why
    ``create_lead_time_log``'s calendar-day variance can differ from the date the
    operator was shown (see the standing REPORTED marker in
    ``services/receiving.py``; this test pins the unit, not that discrepancy).
    """
    from datetime import date, datetime
    from datetime import timezone as dt_timezone

    from reorder_queue.services.purchase_orders import (
        add_business_days,
        update_reorder_requests_from_po,
    )

    monday = datetime(2026, 3, 2, 12, 0, tzinfo=dt_timezone.utc)
    assert monday.date().weekday() == 0

    supplier = SupplierFactory()
    po = PurchaseOrder.objects.create(
        supplier=supplier,
        created_by=UserFactory(),
        status=PurchaseOrder.Status.SENT,
        sent_at=monday,
        expected_delivery_date=None,
    )
    po_item = PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=ItemSupplierFactory(
            supplier=supplier,
            quantity_per_package=1,
            average_lead_time=5,
            item=InventoryItemFactory(current_stock=0),
        ),
        quantity_ordered=1,
        unit_cost_ordered=Decimal("2.00"),
        order_in_packages=1,
    )
    request = _approved_request_for(po_item)

    update_reorder_requests_from_po(po)

    request.refresh_from_db()
    # Five BUSINESS days, so the following Monday — not 2026-03-07.
    assert request.estimated_delivery == date(2026, 3, 9)
    assert request.estimated_delivery == add_business_days(monday.date(), 5)
    assert request.estimated_delivery != monday.date() + timedelta(days=5)
