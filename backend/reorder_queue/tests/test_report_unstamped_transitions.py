"""The report names the rows the pre-fix bulk actions left without a moment.

It decides nothing and changes nothing: these checks assert both halves of that
— the population it finds, and that the rows it names are byte-for-byte
unchanged after it runs.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.utils import timezone

import pytest

from inventory.tests.factories import InventoryItemFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue.management.commands.report_unstamped_transitions import (
    ORDER_SIGNATURE,
    REQUEST_SIGNATURE,
    SENT_ONWARD_STATUSES,
    lines_owed_a_lead_time_log,
    orders_sent_without_a_moment,
    requests_reviewed_without_a_moment,
)
from reorder_queue.models import (
    LeadTimeLog,
    OrderDelivery,
    PurchaseOrder,
    PurchaseOrderItem,
    ReorderRequest,
)
from reorder_queue.tests.factories import ReorderRequestFactory, UserFactory

pytestmark = pytest.mark.django_db


def order_with_line(*, status, sent_at, quantity_received=0):
    supplier = SupplierFactory()
    order = PurchaseOrder.objects.create(
        supplier=supplier,
        created_by=UserFactory(),
        status=status,
        sent_at=sent_at,
        sent_by=UserFactory(),
    )
    line = PurchaseOrderItem.objects.create(
        purchase_order=order,
        item_supplier=ItemSupplierFactory(
            supplier=supplier,
            quantity_per_package=1,
            average_lead_time=5,
            item=InventoryItemFactory(current_stock=0),
        ),
        quantity_ordered=4,
        quantity_received=quantity_received,
        unit_cost_ordered=Decimal("2.00"),
        order_in_packages=4,
    )
    return order, line


def test_it_finds_an_order_that_reached_the_supplier_with_no_moment():
    damaged, _ = order_with_line(status=PurchaseOrder.Status.SENT, sent_at=None)
    stamped, _ = order_with_line(status=PurchaseOrder.Status.SENT, sent_at=timezone.now())

    found = list(orders_sent_without_a_moment())

    assert found == [damaged]
    assert stamped not in found


def test_a_draft_owes_no_send_moment():
    """A null ``sent_at`` on an unsent order is the truth, not damage."""
    draft, _ = order_with_line(status=PurchaseOrder.Status.DRAFT, sent_at=None)

    assert draft not in list(orders_sent_without_a_moment())


def test_it_names_the_delivered_line_whose_lead_time_was_never_written():
    order, line = order_with_line(
        status=PurchaseOrder.Status.RECEIVED, sent_at=None, quantity_received=4
    )
    OrderDelivery.objects.create(
        purchase_order=order, delivery_date=timezone.now(), received_by=UserFactory()
    )

    assert lines_owed_a_lead_time_log(order) == [line]


def test_a_line_whose_lead_time_was_written_is_not_named():
    """The report finds the ABSENCE, so a recorded delivery must not appear."""
    order, line = order_with_line(
        status=PurchaseOrder.Status.RECEIVED,
        sent_at=timezone.now() - timedelta(days=5),
        quantity_received=4,
    )
    LeadTimeLog.objects.create(
        item_supplier=line.item_supplier,
        purchase_order=order,
        order_date=order.sent_at,
        expected_delivery_date=(order.sent_at + timedelta(days=5)).date(),
        actual_delivery_date=timezone.now().date(),
        estimated_lead_time_days=5,
        actual_lead_time_days=5,
        quantity_ordered=4,
        quantity_received=4,
    )

    assert lines_owed_a_lead_time_log(order) == []


def test_a_line_still_in_transit_is_not_named():
    """Nothing was owed yet, so an outstanding line is not a missing record."""
    order, _ = order_with_line(status=PurchaseOrder.Status.SENT, sent_at=None, quantity_received=1)

    assert lines_owed_a_lead_time_log(order) == []


def test_it_finds_a_request_reviewed_by_somebody_at_no_time():
    damaged = ReorderRequestFactory(
        status=ReorderRequest.Status.APPROVED, reviewed_by=UserFactory(), reviewed_at=None
    )
    stamped = ReorderRequestFactory(
        status=ReorderRequest.Status.APPROVED,
        reviewed_by=UserFactory(),
        reviewed_at=timezone.now(),
    )
    unreviewed = ReorderRequestFactory(status=ReorderRequest.Status.PENDING)
    # A closed request that names NO reviewer never claimed a moment, so a null
    # ``reviewed_at`` on it is the truth. This is the row the
    # ``reviewed_by__isnull=False`` clause exists for, and without it here that
    # clause could be deleted with every check still passing.
    no_reviewer = ReorderRequestFactory(
        status=ReorderRequest.Status.CANCELLED, reviewed_by=None, reviewed_at=None
    )

    found = list(requests_reviewed_without_a_moment())

    assert found == [damaged]
    assert stamped not in found
    assert unreviewed not in found
    assert no_reviewer not in found


def test_the_report_runs_and_changes_nothing():
    """Read-only: every row it names is unchanged after it has run."""
    order, line = order_with_line(
        status=PurchaseOrder.Status.RECEIVED, sent_at=None, quantity_received=4
    )
    request_row = ReorderRequestFactory(
        status=ReorderRequest.Status.APPROVED, reviewed_by=UserFactory(), reviewed_at=None
    )
    before = (
        PurchaseOrder.objects.filter(pk=order.pk).values().first(),
        PurchaseOrderItem.objects.filter(pk=line.pk).values().first(),
        ReorderRequest.objects.filter(pk=request_row.pk).values().first(),
        LeadTimeLog.objects.count(),
    )

    out = StringIO()
    call_command("report_unstamped_transitions", stdout=out)

    after = (
        PurchaseOrder.objects.filter(pk=order.pk).values().first(),
        PurchaseOrderItem.objects.filter(pk=line.pk).values().first(),
        ReorderRequest.objects.filter(pk=request_row.pk).values().first(),
        LeadTimeLog.objects.count(),
    )
    assert after == before
    assert "EXPLICIT UNKNOWN" in out.getvalue()


def test_the_json_report_counts_what_it_found():
    order, _ = order_with_line(
        status=PurchaseOrder.Status.RECEIVED, sent_at=None, quantity_received=4
    )
    ReorderRequestFactory(
        status=ReorderRequest.Status.CANCELLED, reviewed_by=UserFactory(), reviewed_at=None
    )

    out = StringIO()
    call_command("report_unstamped_transitions", "--format", "json", stdout=out)
    payload = json.loads(out.getvalue())

    assert payload["totals"] == {
        "orders_sent_without_sent_at": 1,
        "lead_time_rows_never_written": 1,
        "supplier_links_scored_on_incomplete_evidence": 1,
        "requests_reviewed_without_reviewed_at": 1,
    }
    assert payload["orders_sent_without_sent_at"][0]["id"] == order.pk


def test_the_command_offers_no_way_to_write_the_moments_back():
    """There is nothing truthful to write, so there is no flag that would.

    Pinned rather than left to a comment: a later ``--fix`` would put invented
    order dates into the column ``supplier_selection`` scores suppliers from.
    """
    import argparse

    from reorder_queue.management.commands.report_unstamped_transitions import Command

    parser = argparse.ArgumentParser()
    Command().add_arguments(parser)
    flags = {option for action in parser._actions for option in action.option_strings}

    assert not {flag for flag in flags if "fix" in flag or "backfill" in flag or "repair" in flag}


def test_a_damaged_request_is_still_found_after_it_has_moved_on():
    """The signature is the reviewer with no moment, not the status.

    A request bulk-approved before the fix does not stay at ``approved``: the
    next purchase order for its item carries it to ``ordered``
    (``update_reorder_requests_from_po``) and receiving carries it to
    ``received`` (``close_linked_reorder_request``), neither of which touches
    the review columns. Keying the report on status therefore dropped the most
    likely trajectory of a damaged row and reported a number smaller than the
    truth — worse than no report, because a small number reads as reassurance.
    """
    ordered = ReorderRequestFactory(
        status=ReorderRequest.Status.ORDERED, reviewed_by=UserFactory(), reviewed_at=None
    )
    received = ReorderRequestFactory(
        status=ReorderRequest.Status.RECEIVED, reviewed_by=UserFactory(), reviewed_at=None
    )
    approved = ReorderRequestFactory(
        status=ReorderRequest.Status.APPROVED, reviewed_by=UserFactory(), reviewed_at=None
    )
    # Still excluded: a row that never named a reviewer never claimed a moment.
    no_reviewer_pending = ReorderRequestFactory(
        status=ReorderRequest.Status.PENDING, reviewed_by=None, reviewed_at=None
    )
    no_reviewer_cancelled = ReorderRequestFactory(
        status=ReorderRequest.Status.CANCELLED, reviewed_by=None, reviewed_at=None
    )

    found = list(requests_reviewed_without_a_moment())

    assert found == [ordered, received, approved]
    assert no_reviewer_pending not in found
    assert no_reviewer_cancelled not in found


def test_the_report_states_the_signature_each_count_covers():
    """A count is only readable next to the population it covers.

    The report's stdout is its interface — a human reads the text form and a
    caller parses the JSON — so both name the rule that selected the rows,
    and neither number can be mistaken for a count of something narrower.
    """
    ReorderRequestFactory(
        status=ReorderRequest.Status.ORDERED, reviewed_by=UserFactory(), reviewed_at=None
    )
    order_with_line(status=PurchaseOrder.Status.SENT, sent_at=None)

    out = StringIO()
    call_command("report_unstamped_transitions", "--format", "json", stdout=out)
    payload = json.loads(out.getvalue())

    assert payload["signatures"] == {
        "orders_sent_without_sent_at": ORDER_SIGNATURE,
        "requests_reviewed_without_reviewed_at": REQUEST_SIGNATURE,
    }
    assert payload["signatures"]["requests_reviewed_without_reviewed_at"] == (
        "reviewed_by set, reviewed_at NULL"
    )
    assert "sent_at NULL" in payload["signatures"]["orders_sent_without_sent_at"]

    text = StringIO()
    call_command("report_unstamped_transitions", stdout=text)
    printed = text.getvalue()
    assert "reviewed_by set, reviewed_at NULL" in printed
    assert "sent_at NULL" in printed


def test_the_order_signature_admits_the_rows_it_cannot_count():
    """The order count is a floor, and the report has to say so.

    Unlike the request signature, the order one is not status-independent and
    cannot be made so: an order sent before the fix and later voided or
    cancelled still carries a null ``sent_at``, but so does an order that was
    cancelled before it ever went out, and no column separates them. The filter
    stays narrow — reporting the second kind would call clean rows damaged —
    so the OUTPUT carries the exclusion, or the number reads as a complete
    count of a population it does not cover.
    """
    voided_after_a_damaged_send, _ = order_with_line(
        status=PurchaseOrder.Status.VOIDED, sent_at=None
    )

    assert voided_after_a_damaged_send not in list(orders_sent_without_a_moment())

    out = StringIO()
    call_command("report_unstamped_transitions", "--format", "json", stdout=out)
    signature = json.loads(out.getvalue())["signatures"]["orders_sent_without_sent_at"]

    for excluded in (PurchaseOrder.Status.CANCELLED, PurchaseOrder.Status.VOIDED):
        assert excluded.value in signature
    for counted in SENT_ONWARD_STATUSES:
        assert counted.value in signature

    text = StringIO()
    call_command("report_unstamped_transitions", stdout=text)
    printed = text.getvalue()
    assert signature in printed
    assert "whatever it has since become" not in printed
