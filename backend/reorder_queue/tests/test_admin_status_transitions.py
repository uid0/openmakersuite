"""A status transition sets everything derived from it, by every path.

The class, stated once: ``reorder_queue.admin``'s bulk actions performed status
transitions with a hand-written ``queryset.update()`` sitting beside a service
function that already owned the same transition. Each copy stamped the ACTOR of
the change and dropped the MOMENT of it — ``sent_at`` on a purchase order,
``reviewed_at`` on a reorder request — along with everything else the transition
owed: the linked reorder requests, the audit row the staff feed reads, and the
``auto_now`` ``updated_at`` that a queryset write never touches.

The worst consequence is not the blank date. ``services.receiving.create_lead_time_log``
returns early on a falsy ``sent_at``, so an order sent from the admin changelist
wrote NO ``LeadTimeLog`` when it was delivered: the supplier's performance on
that order never entered the record that
``inventory.services.supplier_selection``'s performance term reads to decide
who the next order goes to. ``test_an_admin_sent_order_records_its_supplier_s_lead_time``
is that end to end, through the real receiving service.

Every check here drives the REAL Django admin action dispatch by POSTing to the
changelist, so it exercises what an operator's click exercises; none of them
re-implement the transition and then assert on their own arithmetic.
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


@pytest.fixture
def staff():
    return UserFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def admin_client(staff):
    client = Client()
    client.force_login(staff)
    return client


def run_admin_action(client, model, action, *objects):
    """Fire one of ``model``'s admin actions over ``objects``, as an operator does.

    Goes through the real changelist POST so the ModelAdmin's own action
    dispatch runs — not the action method called directly, which would skip
    everything the admin does around it.
    """
    meta = model._meta
    url = reverse(f"admin:{meta.app_label}_{meta.model_name}_changelist")
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


def draft_order(staff, *, average_lead_time=5, item=None):
    """A DRAFT purchase order with one line against a real supplier link."""
    supplier = SupplierFactory()
    order = PurchaseOrder.objects.create(supplier=supplier, created_by=staff)
    PurchaseOrderItem.objects.create(
        purchase_order=order,
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
    return order


# ── The moment of the transition ────────────────────────────────────────────


def test_admin_send_stamps_when_the_order_went_out_not_only_who_sent_it(admin_client, staff):
    """DRAFT -> SENT records ``sent_at``, the fact the rest of the app reads."""
    order = draft_order(staff)

    run_admin_action(admin_client, PurchaseOrder, "mark_as_sent", order)

    order.refresh_from_db()
    assert order.status == PurchaseOrder.Status.SENT
    assert order.sent_by == staff
    assert order.sent_at is not None


def test_an_admin_sent_order_ages_on_the_screens_that_report_its_age(admin_client, staff):
    """``days_since_ordered`` counts from the send, so the aging report ages.

    ``PurchaseOrder.days_since_ordered`` returns 0 when ``sent_at`` is absent,
    and the ``pending_orders`` endpoint, the admin changelist's "Days Since
    Ordered" column and ScanTTY's "Age" field all read it. An order sent from
    the admin used to read "Today" for ever.
    """
    order = draft_order(staff)

    with freeze_time("2026-03-02 12:00:00"):
        run_admin_action(admin_client, PurchaseOrder, "mark_as_sent", order)

    order.refresh_from_db()
    with freeze_time("2026-03-12 12:00:00"):
        assert order.days_since_ordered == 10


def test_admin_confirm_moves_the_updated_stamp_the_detail_screens_show(admin_client, staff):
    """SENT -> CONFIRMED advances ``updated_at``.

    ``updated_at`` is ``auto_now``, which a ``queryset.update()`` never touches,
    so the admin's "Updated" field and ScanTTY's ``Updated`` row went on
    reporting a moment before the confirm.
    """
    order = draft_order(staff)
    order.status = PurchaseOrder.Status.SENT
    order.sent_at = timezone.now()
    order.save()
    before = PurchaseOrder.objects.get(pk=order.pk).updated_at

    run_admin_action(admin_client, PurchaseOrder, "mark_as_confirmed", order)

    order.refresh_from_db()
    assert order.status == PurchaseOrder.Status.CONFIRMED
    assert order.updated_at > before


# ── The linked records ──────────────────────────────────────────────────────


def test_admin_send_closes_out_the_reorder_requests_it_fulfils(admin_client, staff):
    """Sending a PO marks the approved requests for its items ``ordered``.

    ``services.update_reorder_requests_from_po`` is what carries the PO number
    and the order moment onto the request; the admin path did not call it, so
    the request stayed ``approved`` and sat in the queue an operator had
    already ordered against.
    """
    item = InventoryItemFactory(current_stock=0)
    order = draft_order(staff, item=item)
    request_row = ReorderRequestFactory(item=item, status=ReorderRequest.Status.APPROVED)

    run_admin_action(admin_client, PurchaseOrder, "mark_as_sent", order)

    order.refresh_from_db()
    request_row.refresh_from_db()
    assert request_row.status == ReorderRequest.Status.ORDERED
    assert request_row.order_number == order.po_number
    assert request_row.ordered_at == order.sent_at


def test_an_admin_sent_order_records_its_supplier_s_lead_time_when_it_arrives(admin_client, staff):
    """The consequence that is worse than a blank date.

    ``create_lead_time_log`` returns early on a falsy ``sent_at``, so a delivery
    against an admin-sent order wrote no ``LeadTimeLog`` at all — the supplier's
    performance on that order never reached the table
    ``inventory.services.supplier_selection`` scores suppliers from. The receipt
    here goes through the real ``receive_delivery`` service, not a direct call
    to ``create_lead_time_log``.
    """
    order = draft_order(staff, average_lead_time=5)
    line = order.items.get()

    with freeze_time("2026-03-02 12:00:00"):
        run_admin_action(admin_client, PurchaseOrder, "mark_as_sent", order)

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


def test_admin_send_reaches_the_staff_audit_feed(admin_client, staff):
    """A send leaves a ``po_send`` row naming who did it.

    ``dashboard.audit_feed`` is the staff "who did what, when, on this entity"
    surface; a send performed from the admin changelist contributed nothing to
    it.
    """
    order = draft_order(staff)

    run_admin_action(admin_client, PurchaseOrder, "mark_as_sent", order)

    event = PurchaseOrderAuditEvent.objects.get(
        purchase_order=order, action=PurchaseOrderAuditEvent.Action.PO_SEND
    )
    assert event.actor == staff
    assert event.metadata["po_number"] == PurchaseOrder.objects.get(pk=order.pk).po_number


# ── The same transition by every path ───────────────────────────────────────


def send_via_admin(client_for, order, actor):
    run_admin_action(client_for(actor), PurchaseOrder, "mark_as_sent", order)


def send_via_api(client_for, order, actor):
    api = APIClient()
    api.force_authenticate(user=actor)
    response = api.post(reverse("purchaseorder-send-to-supplier", args=[order.pk]))
    assert response.status_code == 200


@pytest.mark.parametrize("send", [send_via_admin, send_via_api], ids=["admin", "api"])
def test_every_send_path_stamps_the_whole_transition(send, staff):
    """DRAFT -> SENT owes the same set of facts however it is performed.

    Parameterised over the two paths that perform it so a path that stamps only
    part of the set fails here rather than in whichever downstream reader
    noticed first.
    """
    order = draft_order(staff)

    def client_for(actor):
        client = Client()
        client.force_login(actor)
        return client

    send(client_for, order, staff)

    order.refresh_from_db()
    assert order.status == PurchaseOrder.Status.SENT
    assert order.sent_by == staff
    assert order.sent_at is not None
    assert PurchaseOrderAuditEvent.objects.filter(
        purchase_order=order, action=PurchaseOrderAuditEvent.Action.PO_SEND
    ).exists()


# ── The same shape on the reorder-request actions ───────────────────────────


def test_admin_approve_stamps_when_the_request_was_reviewed(admin_client, staff):
    """PENDING -> APPROVED records ``reviewed_at`` beside ``reviewed_by``.

    ``reviewed_at`` is on the admin's own "Admin Review" fieldset, on the
    reorder-request API serializer, and on ``inventory``'s active-request block,
    so a bulk approval left every one of them saying the request was signed off
    at no time.
    """
    request_row = ReorderRequestFactory(status=ReorderRequest.Status.PENDING)

    run_admin_action(admin_client, ReorderRequest, "approve_requests", request_row)

    request_row.refresh_from_db()
    assert request_row.status == ReorderRequest.Status.APPROVED
    assert request_row.reviewed_by == staff
    assert request_row.reviewed_at is not None


def test_admin_cancel_stamps_when_the_request_was_reviewed(admin_client, staff):
    """-> CANCELLED records ``reviewed_at`` beside ``reviewed_by``."""
    request_row = ReorderRequestFactory(status=ReorderRequest.Status.PENDING)

    run_admin_action(admin_client, ReorderRequest, "cancel_requests", request_row)

    request_row.refresh_from_db()
    assert request_row.status == ReorderRequest.Status.CANCELLED
    assert request_row.reviewed_by == staff
    assert request_row.reviewed_at is not None


# ── The preconditions the bulk actions already carried ──────────────────────


def test_admin_send_leaves_an_already_sent_order_alone(admin_client, staff):
    """The DRAFT filter still holds, so a re-send never re-stamps the moment.

    Routing the action through the service must not lose the precondition the
    ``queryset.filter()`` carried: an order already with the supplier keeps the
    moment it actually went out.
    """
    order = draft_order(staff)
    original = timezone.now() - timedelta(days=9)
    order.status = PurchaseOrder.Status.SENT
    order.sent_at = original
    order.save()

    run_admin_action(admin_client, PurchaseOrder, "mark_as_sent", order)

    order.refresh_from_db()
    assert order.sent_at == original
    assert not PurchaseOrderAuditEvent.objects.filter(
        purchase_order=order, action=PurchaseOrderAuditEvent.Action.PO_SEND
    ).exists()


def test_admin_confirm_leaves_a_draft_alone(admin_client, staff):
    """The SENT filter still holds: a draft is not confirmed into the supplier's hands."""
    order = draft_order(staff)

    run_admin_action(admin_client, PurchaseOrder, "mark_as_confirmed", order)

    order.refresh_from_db()
    assert order.status == PurchaseOrder.Status.DRAFT


def test_admin_approve_leaves_a_non_pending_request_alone(admin_client, staff):
    """The PENDING filter still holds, so an ordered request is not re-approved."""
    request_row = ReorderRequestFactory(status=ReorderRequest.Status.ORDERED)

    run_admin_action(admin_client, ReorderRequest, "approve_requests", request_row)

    request_row.refresh_from_db()
    assert request_row.status == ReorderRequest.Status.ORDERED
    assert request_row.reviewed_at is None
