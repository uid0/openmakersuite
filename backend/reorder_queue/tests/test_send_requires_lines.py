"""A purchase order with nothing on it cannot go to the supplier, by any path.

THE RULE, both halves: an order must carry at least one line before it may be
marked sent, and the send must record the moment. A sales order number MAY be
recorded alongside and is never required.

WHY THE SECOND HALF IS NOT COSMETIC.
``services.receiving.create_lead_time_log`` returns early on a falsy
``sent_at``, so a delivery against an order sent without one writes NO
``LeadTimeLog``: the supplier's performance on that order never reaches the
table ``inventory.services.supplier_selection`` scores suppliers from.
``test_a_patch_sent_order_records_its_supplier_s_lead_time_when_it_arrives``
drives that chain through the real receiving service rather than asserting the
stamp and stopping there.

THE SET OF PATHS, derived from "where can a purchase order become sent?" rather
than from where the defect was noticed:

* ``PurchaseOrderViewSet.send_to_supplier`` — the manual API send (ScanTTY's
  route and the web page's "Send to Supplier" button);
* ``PurchaseOrderViewSet.perform_update`` — ``PATCH {"status": "sent"}``, which
  wrote ``status`` straight through ``PurchaseOrderSerializer`` and stamped
  nothing;
* ``PurchaseOrderViewSet.perform_create``/``perform_update`` — the
  sales-order-number auto-send (oms-qdxss);
* ``PurchaseOrderAdmin.mark_as_sent`` — the changelist bulk action;
* ``PurchaseOrderAdmin``'s CHANGE FORM — ``status``, ``sent_at`` and ``sent_by``
  are all editable fields on it, a fact ``AGENTS.md`` already records.

All five now reach ``services.purchase_orders.mark_sent``, which owns the whole
transition, and the one refusal rule ``services.purchase_orders.send_refusal``.

THE STAMPING HALF IS PINNED ELSEWHERE, over the same five paths:
``test_every_send_path_stamps_the_whole_transition`` in
:mod:`reorder_queue.tests.test_admin_status_transitions` is parametrised over
each of them and asserts the whole fact set — status, ``sent_by``, ``sent_at``,
exactly one ``po_send`` audit row and the linked request sweep. A sixth send
path joins that list. What is pinned HERE is the refusal, the operator-facing
reason, and the two facts that belong to one path each: the moment reaching the
response body, and the change form keeping a ``sent_at`` the operator typed.

DELIBERATELY EXCLUDED, with the reason:

* ``services.receiving.refresh_receipt_status`` re-derives a status but only
  within ``IN_RECEIVING_STATUSES`` and only to ``received`` /
  ``partially_received``. It can never produce ``sent``.
* ``PurchaseOrderCreateSerializer`` carries no ``status`` field, and
  ``validate_items`` refuses an empty list while every item dict in
  ``create_purchase_order`` either creates a line or raises — so a freshly
  created order always has a line before ``perform_create``'s auto-send fires.
  ``test_creating_an_order_with_a_sales_order_number_still_sends_it`` pins the
  path anyway, since the guard is what keeps that structural fact from being
  the only thing holding it up.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

import pytest
from freezegun import freeze_time
from rest_framework.test import APIClient

from inventory.tests.factories import InventoryItemFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue.models import (
    LeadTimeLog,
    PurchaseOrder,
    PurchaseOrderAuditEvent,
    PurchaseOrderItem,
    ReorderRequest,
)
from reorder_queue.services.receiving import receive_delivery
from reorder_queue.tests.factories import ReorderRequestFactory, UserFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — one empty draft and one draft with a line, so every check below
# differs from its neighbour in exactly the fact under test.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def staff():
    return UserFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def admin_client(staff):
    client = Client()
    client.force_login(staff)
    return client


@pytest.fixture
def api(staff):
    client = APIClient()
    client.force_authenticate(user=staff)
    return client


def empty_draft(staff):
    """A DRAFT order with NO line items.

    Reachable in the product: ``PurchaseOrderViewSet._destroy_item`` deletes
    lines outright while the order is pre-send (oms-po-line-delete), which is
    what "delete the wrong line, then add the right one" leaves behind between
    the two steps.
    """
    return PurchaseOrder.objects.create(supplier=SupplierFactory(), created_by=staff)


def draft_with_a_line(staff, *, average_lead_time=5, item=None):
    """A DRAFT order carrying one line against a real supplier link."""
    order = empty_draft(staff)
    PurchaseOrderItem.objects.create(
        purchase_order=order,
        item_supplier=ItemSupplierFactory(
            supplier=order.supplier,
            quantity_per_package=1,
            average_lead_time=average_lead_time,
            item=item or InventoryItemFactory(current_stock=0),
        ),
        quantity_ordered=4,
        unit_cost_ordered=Decimal("2.00"),
        order_in_packages=4,
    )
    return order


def detail_url(order):
    return reverse("purchaseorder-detail", args=[order.pk])


def send_url(order):
    return reverse("purchaseorder-send-to-supplier", args=[order.pk])


def run_admin_action(client, action, *objects):
    """Fire a ``PurchaseOrder`` admin action over ``objects``, as an operator does.

    Through the real changelist POST, so the ModelAdmin's own action dispatch
    runs rather than the action method called directly.
    """
    url = reverse("admin:reorder_queue_purchaseorder_changelist")
    response = client.post(
        url,
        {
            "action": action,
            "_selected_action": [str(obj.pk) for obj in objects],
            "index": "0",
        },
        follow=True,
    )
    assert response.status_code == 200
    return response


def change_form_payload(order, **overrides):
    """The change form's own POST, field for field with what the page renders.

    ``sent_at`` and ``order_date`` are ``DateTimeField``s, which the admin
    renders with its split date/time widget — hence the ``_0``/``_1`` keys. The
    line-item inline contributes its management form; ``INITIAL_FORMS`` of 0
    edits none of the existing lines, which is what a save that only touched
    the header posts.

    A payload that is wrong in any required field re-renders the page with a
    200 instead of redirecting, so every caller asserts the 302 rather than
    trusting this dict.
    """
    ordered = timezone.localtime(order.order_date)
    payload = {
        "supplier": str(order.supplier_id),
        "status": order.status,
        "priority": order.priority,
        "order_date_0": ordered.date().isoformat(),
        "order_date_1": ordered.time().strftime("%H:%M:%S"),
        "expected_delivery_date": "",
        "payment_terms": order.payment_terms,
        "freight_terms": order.freight_terms,
        "work_order": "",
        "owning_group": "",
        "estimated_total": str(order.estimated_total),
        "actual_total": "" if order.actual_total is None else str(order.actual_total),
        "created_by": str(order.created_by_id),
        "sent_by": "" if order.sent_by_id is None else str(order.sent_by_id),
        "sent_at_0": "",
        "sent_at_1": "",
        "notes": order.notes,
        "items-TOTAL_FORMS": "0",
        "items-INITIAL_FORMS": "0",
        "items-MIN_NUM_FORMS": "0",
        "items-MAX_NUM_FORMS": "1000",
        "_save": "Save",
    }
    payload.update(overrides)
    return payload


def post_change_form(client, order, **overrides):
    url = reverse("admin:reorder_queue_purchaseorder_change", args=[order.pk])
    return client.post(url, change_form_payload(order, **overrides), follow=True)


def messages_from(response):
    return [str(message) for message in response.context["messages"]]


# ─────────────────────────────────────────────────────────────────────────────
# Half one: an order with no lines cannot be marked sent, by any path
# ─────────────────────────────────────────────────────────────────────────────
def test_the_send_action_refuses_an_order_with_no_lines(api, staff):
    """``POST .../send_to_supplier/`` — ScanTTY's route and the web button."""
    order = empty_draft(staff)

    response = api.post(send_url(order))

    assert response.status_code == 400
    order.refresh_from_db()
    assert order.status == PurchaseOrder.Status.DRAFT
    assert order.sent_at is None


def test_a_patch_to_sent_refuses_an_order_with_no_lines(api, staff):
    """``PATCH {"status": "sent"}`` — the generic update path."""
    order = empty_draft(staff)

    response = api.patch(detail_url(order), {"status": "sent"}, format="json")

    assert response.status_code == 400
    order.refresh_from_db()
    assert order.status == PurchaseOrder.Status.DRAFT
    assert order.sent_at is None


def test_recording_a_sales_order_number_refuses_an_order_with_no_lines(api, staff):
    """The auto-send (oms-qdxss): a sales order number on a DRAFT sends it.

    So the same refusal has to reach it. The number is NOT written either — the
    write and the send it triggers are one intent, and recording "this went to
    the supplier as SO-77" over an order with nothing on it would leave the row
    asserting something untrue about the supplier.
    """
    order = empty_draft(staff)

    response = api.patch(detail_url(order), {"sales_order_number": "SO-77"}, format="json")

    assert response.status_code == 400
    order.refresh_from_db()
    assert order.status == PurchaseOrder.Status.DRAFT
    assert order.sales_order_number == ""


def test_the_admin_bulk_send_refuses_an_order_with_no_lines(admin_client, staff):
    """The changelist's "Mark selected orders as sent"."""
    order = empty_draft(staff)

    run_admin_action(admin_client, "mark_as_sent", order)

    order.refresh_from_db()
    assert order.status == PurchaseOrder.Status.DRAFT
    assert order.sent_at is None


def test_the_admin_change_form_refuses_an_order_with_no_lines(admin_client, staff):
    """Setting ``status`` to Sent on the change form, where the column is editable."""
    order = empty_draft(staff)

    post_change_form(admin_client, order, status=PurchaseOrder.Status.SENT)

    order.refresh_from_db()
    assert order.status == PurchaseOrder.Status.DRAFT
    assert order.sent_at is None


def test_an_order_whose_every_line_was_voided_cannot_be_sent(api, staff):
    """"Has lines" means lines that are still on the order.

    ``void_item`` carries no status gate, so striking off the only line of a
    draft is a live second route to an order with nothing to order — and the
    order pad such an order exports is empty. The predicate is the order's own
    ``has_active_items``, not a raw row count.
    """
    order = draft_with_a_line(staff)
    order.items.update(is_voided=True)

    response = api.post(send_url(order))

    assert response.status_code == 400
    order.refresh_from_db()
    assert order.status == PurchaseOrder.Status.DRAFT


# ─────────────────────────────────────────────────────────────────────────────
# The refusal names the reason and the operator can act on it
# ─────────────────────────────────────────────────────────────────────────────
def test_the_send_action_s_refusal_names_the_missing_lines_and_what_to_do(api, staff):
    """A bare 400 leaves an operator on a screen with a button that does nothing.

    Asserted on the STANDARDIZED envelope (``config.api_errors``) rather than a
    hand-built body, because ``error.message`` is the field both clients put in
    front of an operator: ``extractErrorMessage`` in the web UI's toast and
    ScanTTY's ``APIError.Error()`` at the terminal that sends orders. A
    hand-built ``{"error": "<prose>"}`` defeats ScanTTY's ``parseError`` and
    reaches the operator as a raw JSON dump.

    Both halves of an actionable refusal are pinned: WHY (no line items) and
    WHAT NEXT (add one).
    """
    order = empty_draft(staff)

    response = api.post(send_url(order))

    envelope = response.data["error"]
    assert envelope["code"] == "no_line_items"
    assert "line item" in envelope["message"].lower()
    assert "add" in envelope["message"].lower()


def test_the_send_action_s_draft_refusal_answers_in_the_same_envelope(api, staff):
    """The sibling refusal on the same action, in the same shape.

    Two refusals answering in two shapes is how the second one drifts, and this
    one reached ScanTTY as a raw body for the same reason the one above would
    have. It also NAMES the order and the status it is actually in, which
    "Only draft orders can be sent to suppliers" left the operator to work out.
    """
    order = draft_with_a_line(staff)
    order.status = PurchaseOrder.Status.CONFIRMED
    order.save()

    envelope = api.post(send_url(order)).data["error"]

    assert envelope["code"] == "not_draft"
    assert order.po_number in envelope["message"]
    assert "Confirmed by Supplier" in envelope["message"]


def test_the_patch_refusal_reaches_the_operator_as_the_reason(api, staff):
    """Not the envelope's generic "One or more fields failed validation.".

    A field-keyed error would put the reason in ``error.details`` where neither
    client shows it; ``error.message`` is what an operator reads.
    """
    order = empty_draft(staff)

    envelope = api.patch(detail_url(order), {"status": "sent"}, format="json").data["error"]

    assert "line item" in envelope["message"].lower()
    assert "add" in envelope["message"].lower()


def test_the_sales_order_number_refusal_reaches_the_operator_as_the_reason(api, staff):
    """The auto-send refusal, which is the one a web operator can actually hit.

    The PO detail page PATCHes ``sales_order_number`` (``purchaseOrderAPI.updateOrder``)
    and never PATCHes ``status``, so this is the shape of refusal its toast has
    to be able to render.
    """
    order = empty_draft(staff)

    envelope = api.patch(
        detail_url(order), {"sales_order_number": "SO-77"}, format="json"
    ).data["error"]

    assert "line item" in envelope["message"].lower()


def test_the_admin_bulk_send_says_why_it_sent_nothing(admin_client, staff):
    """The changelist cannot disable a row, so it must report the refusal after.

    Named per order, because a bulk action over a mixed selection has to say
    WHICH orders did not go out.
    """
    order = empty_draft(staff)
    order.refresh_from_db()

    response = run_admin_action(admin_client, "mark_as_sent", order)

    reported = " ".join(messages_from(response))
    assert order.po_number in reported
    assert "line item" in reported.lower()


def test_the_admin_change_form_says_why_the_status_did_not_move(admin_client, staff):
    order = empty_draft(staff)
    order.refresh_from_db()

    response = post_change_form(admin_client, order, status=PurchaseOrder.Status.SENT)

    reported = " ".join(messages_from(response))
    assert order.po_number in reported
    assert "line item" in reported.lower()


def test_a_draft_with_no_lines_says_on_its_own_payload_why_it_cannot_be_sent(api, staff):
    """The surface that OFFERS the send must be able to explain the refusal.

    Served off the API rather than re-derived in each client, the same
    discipline ``can_receive`` and ``can_delete_items`` already follow — a
    client keeping its own copy is how a button and an endpoint come to
    disagree. ``null`` when nothing blocks, so the field is a reason and not a
    flag a caller has to interpret.
    """
    blocked = empty_draft(staff)
    sendable = draft_with_a_line(staff)

    blocked_reason = api.get(detail_url(blocked)).data["send_blocked_reason"]
    assert blocked_reason is not None
    assert "line item" in blocked_reason.lower()
    assert api.get(detail_url(sendable)).data["send_blocked_reason"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Half two: sending stamps the moment — and what the moment is FOR
# ─────────────────────────────────────────────────────────────────────────────
def test_a_patch_to_sent_answers_with_the_moment_it_recorded(api, staff):
    """The response body describes the row the caller now has, stamp included."""
    order = draft_with_a_line(staff)

    response = api.patch(detail_url(order), {"status": "sent"}, format="json")

    assert response.data["status"] == PurchaseOrder.Status.SENT
    assert response.data["sent_at"] is not None


def test_a_patch_sent_order_records_its_supplier_s_lead_time_when_it_arrives(api, staff):
    """The consequence, end to end through the real receiving service.

    ``create_lead_time_log`` returns early on a falsy ``sent_at``, so a
    PATCH-sent order that was later delivered contributed NOTHING to the
    supplier's record — in the very table
    ``inventory.services.supplier_selection``'s performance term reads.
    """
    order = draft_with_a_line(staff, average_lead_time=5)
    line = order.items.get()

    with freeze_time("2026-03-02 12:00:00"):
        assert api.patch(detail_url(order), {"status": "sent"}, format="json").status_code == 200

    order.refresh_from_db()
    receive_delivery(
        order,
        [(line, line.quantity_ordered)],
        received_by=staff,
        delivery_datetime=timezone.now(),
    )

    log = LeadTimeLog.objects.get(purchase_order=order)
    assert log.order_date == order.sent_at
    assert log.estimated_lead_time_days == 5


def test_the_admin_change_form_keeps_a_sent_at_the_operator_typed(admin_client, staff):
    """Backdating a send is the reason that field is editable.

    An order typed up after it went out records when it ACTUALLY went out, so
    routing the change form through the shared transition must pin the moment
    the operator supplied rather than overwrite it with now — the same reason
    ``problem_settlement.settle_problem`` takes an ``at``.
    """
    order = draft_with_a_line(staff)
    backdated = timezone.localtime(timezone.now() - timedelta(days=6))

    response = post_change_form(
        admin_client,
        order,
        status=PurchaseOrder.Status.SENT,
        sent_at_0=backdated.date().isoformat(),
        sent_at_1=backdated.time().strftime("%H:%M:%S"),
    )
    assert response.redirect_chain, messages_from(response)

    order.refresh_from_db()
    assert order.status == PurchaseOrder.Status.SENT
    assert timezone.localtime(order.sent_at).replace(microsecond=0) == backdated.replace(
        microsecond=0
    )


# ─────────────────────────────────────────────────────────────────────────────
# A sales order number is accepted, never required
# ─────────────────────────────────────────────────────────────────────────────
def test_an_order_with_no_sales_order_number_still_sends(api, staff):
    """The captain's "(optionally)" — the absence is not an error.

    The guard being added is about LINES, and a check that only proves the
    lines half would still pass if a sales-order-number requirement were
    smuggled in beside it.
    """
    order = draft_with_a_line(staff)
    assert order.sales_order_number == ""

    response = api.post(send_url(order))

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.status == PurchaseOrder.Status.SENT
    assert order.sent_at is not None
    assert order.sales_order_number == ""


def test_a_sales_order_number_on_an_order_with_lines_is_recorded_and_sends_it(api, staff):
    """The auto-send still works where the order has something to send."""
    order = draft_with_a_line(staff)

    response = api.patch(detail_url(order), {"sales_order_number": "SO-42"}, format="json")

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.sales_order_number == "SO-42"
    assert order.status == PurchaseOrder.Status.SENT
    assert order.sent_at is not None


def test_creating_an_order_with_a_sales_order_number_still_sends_it(api, staff):
    """The create-time auto-send, which the required non-empty ``items`` covers."""
    supplier = SupplierFactory()
    item_supplier = ItemSupplierFactory(
        supplier=supplier, quantity_per_package=1, item=InventoryItemFactory(current_stock=0)
    )

    response = api.post(
        reverse("purchaseorder-list"),
        {
            "supplier": supplier.pk,
            "sales_order_number": "SO-9",
            "items": [{"item_supplier_id": item_supplier.pk, "quantity": 2, "unit_cost": "3.00"}],
        },
        format="json",
    )

    assert response.status_code == 201
    order = PurchaseOrder.objects.get(pk=response.data["id"])
    assert order.status == PurchaseOrder.Status.SENT
    assert order.sent_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# Preconditions the existing paths already carried, kept
# ─────────────────────────────────────────────────────────────────────────────
def test_a_patch_that_does_not_ask_to_send_is_untouched_by_the_rule(api, staff):
    """An empty draft is still editable — the rule gates SENDING, not saving.

    The refusal must not spread to the ordinary header edit; an order whose
    lines were just deleted is mid-edit, and refusing to let the operator save
    notes on it would be a worse defect than the one being fixed.
    """
    order = empty_draft(staff)

    response = api.patch(detail_url(order), {"notes": "waiting on a quote"}, format="json")

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.notes == "waiting on a quote"
    assert order.status == PurchaseOrder.Status.DRAFT


def test_a_patch_to_sent_leaves_an_already_sent_order_s_moment_alone(api, staff):
    """Re-asserting the status a row already holds must not re-stamp it.

    A stale detail page or a client re-sending its form reaches this, and the
    moment the order actually went out is the fact the lead-time record is
    computed from.
    """
    order = draft_with_a_line(staff)
    original = timezone.now() - timedelta(days=9)
    order.status = PurchaseOrder.Status.SENT
    order.sent_at = original
    order.save()

    response = api.patch(detail_url(order), {"status": "sent"}, format="json")

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.sent_at == original


def test_a_patch_can_still_move_a_status_the_rule_says_nothing_about(api, staff):
    """Only the send is routed; the endpoint is not otherwise narrowed.

    ``status`` stays a writable field — the captain authorised the RULE, not a
    contract change — so a PATCH that moves an order somewhere else still lands
    exactly as it did.
    """
    order = draft_with_a_line(staff)

    response = api.patch(detail_url(order), {"status": "cancelled"}, format="json")

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.status == PurchaseOrder.Status.CANCELLED
