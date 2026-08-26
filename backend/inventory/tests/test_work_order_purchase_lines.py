"""What the *job* shows about what was ordered for it (op-bu80, B4).

The receive-side of the PO ↔ WO bridge lives in
``reorder_queue/tests/test_work_order_bridge.py``. This file covers the other
half: what the *job* shows. A tech looking at a work order needs to know that
the part is on order — before it arrives there is no material line to look at,
only a purchase order somewhere else in the system.

``WorkOrderSerializer.purchase_order_lines`` is that view: every live PO line
tagged with this work order, on order or received, newest order first.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import WorkOrder, WorkOrderMaterialUsage
from inventory.serializers import WorkOrderSerializer
from inventory.services.work_order_context import build_purchase_lines_context
from inventory.tests.factories import AssetFactory, ItemSupplierFactory, SupplierFactory
from inventory.views import WorkOrderViewSet
from reorder_queue import services
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem

User = get_user_model()

pytestmark = pytest.mark.django_db


def _staff_client():
    user = User.objects.create_user(username="planner", password="x", is_staff=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def _corrective_wo():
    return WorkOrder.objects.create(maintenance_item=None, asset=AssetFactory())


def _line_for(work_order, user, *, qty=10, unit_cost=None, status_=None):
    unit_cost = Decimal("2.00") if unit_cost is None else unit_cost
    supplier = SupplierFactory()
    po = PurchaseOrder.objects.create(
        supplier=supplier,
        status=status_ or PurchaseOrder.Status.SENT,
        created_by=user,
        sent_at=timezone.now(),
    )
    item_supplier = ItemSupplierFactory(
        supplier=supplier, unit_cost=unit_cost, quantity_per_package=1
    )
    line = PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=item_supplier,
        quantity_ordered=qty,
        unit_cost_ordered=unit_cost,
        work_order=work_order,
    )
    return po, line


def _serialize(work_order):
    """Serialize through the viewset queryset so prefetches are exercised."""
    obj = WorkOrderViewSet.queryset.get(pk=work_order.pk)
    return WorkOrderSerializer(obj).data


def test_a_line_still_on_order_is_visible_before_anything_arrives():
    """The whole point: "it's ordered" is knowable from the job."""
    _client, user = _staff_client()
    wo = _corrective_wo()
    _po, line = _line_for(wo, user, qty=10, unit_cost=Decimal("2.00"))

    lines = _serialize(wo)["purchase_order_lines"]

    assert len(lines) == 1
    assert lines[0]["id"] == str(line.id)
    assert lines[0]["name"] == line.item.name
    assert lines[0]["item_type"] == "inventory_item"
    assert lines[0]["quantity_ordered"] == 10
    assert lines[0]["quantity_received"] == 0
    assert lines[0]["quantity_pending"] == 10
    assert lines[0]["is_fully_received"] is False
    assert lines[0]["unit_cost"] == "2.0000"
    assert lines[0]["po_status"] == PurchaseOrder.Status.SENT

    # Nothing has arrived, so nothing has been posted as a material yet.
    assert _serialize(wo)["material_usage"] == []


def test_a_received_line_shows_on_both_the_order_list_and_the_materials():
    """After receipt the same purchase is visible as ordering *and* as spend."""
    _client, user = _staff_client()
    wo = _corrective_wo()
    po, line = _line_for(wo, user, qty=4, unit_cost=Decimal("5.00"))

    services.receive_delivery(po, [(line, 4)], received_by=user, delivery_datetime=timezone.now())

    data = _serialize(wo)
    ordered = data["purchase_order_lines"]
    assert len(ordered) == 1
    assert ordered[0]["quantity_received"] == 4
    assert ordered[0]["is_fully_received"] is True

    materials = data["material_usage"]
    assert len(materials) == 1
    assert materials[0]["purchase_order_item"] == line.id
    assert materials[0]["was_used"] is True
    assert Decimal(materials[0]["actual_cost"]) == Decimal("20.00")
    assert Decimal(data["actual_material_cost"]) == Decimal("20.00")


def test_voided_lines_drop_off_the_list():
    """A voided line was un-ordered; showing it as "on order" would be a lie."""
    _client, user = _staff_client()
    wo = _corrective_wo()
    _po, live = _line_for(wo, user)
    _voided_po, voided = _line_for(wo, user)
    services.void_line_item(voided, user, "supplier discontinued it")

    lines = _serialize(wo)["purchase_order_lines"]

    assert [entry["id"] for entry in lines] == [str(live.id)]


def test_lines_ordered_for_other_work_orders_stay_out():
    _client, user = _staff_client()
    mine = _corrective_wo()
    theirs = _corrective_wo()
    _po, my_line = _line_for(mine, user)
    _line_for(theirs, user)

    lines = _serialize(mine)["purchase_order_lines"]

    assert [entry["id"] for entry in lines] == [str(my_line.id)]


def test_a_work_order_with_no_purchases_reports_an_empty_list():
    wo = _corrective_wo()

    assert _serialize(wo)["purchase_order_lines"] == []


def test_the_detail_endpoint_serves_the_list():
    """End-to-end through the API, not just the serializer."""
    client, user = _staff_client()
    wo = _corrective_wo()
    _po, line = _line_for(wo, user)

    response = client.get(reverse("workorder-detail", args=[wo.id]))

    assert response.status_code == status.HTTP_200_OK
    assert [entry["id"] for entry in response.data["purchase_order_lines"]] == [str(line.id)]


def test_the_list_costs_a_fixed_number_of_queries():
    """Prefetched: five ordered lines must not cost five round trips."""
    _client, user = _staff_client()
    one_line_wo = _corrective_wo()
    _line_for(one_line_wo, user)
    five_line_wo = _corrective_wo()
    for _ in range(5):
        _line_for(five_line_wo, user)

    def serialize_queries(work_order):
        obj = WorkOrderViewSet.queryset.get(pk=work_order.pk)
        with CaptureQueriesContext(connection) as ctx:
            data = WorkOrderSerializer(obj).data
        return data["purchase_order_lines"], len(ctx.captured_queries)

    one, one_queries = serialize_queries(one_line_wo)
    five, five_queries = serialize_queries(five_line_wo)

    assert len(one) == 1 and len(five) == 5
    # Rendering the block itself issues nothing — the queryset's prefetch
    # already holds the lines, their PO header and their target — so five
    # ordered lines cost exactly what one does.
    assert five_queries == one_queries

    obj = WorkOrderViewSet.queryset.get(pk=five_line_wo.pk)
    with CaptureQueriesContext(connection) as ctx:
        build_purchase_lines_context(obj)
    assert ctx.captured_queries == []


def test_removing_a_bridged_material_line_does_not_disturb_the_order_list():
    """The two views are independent — deleting the spend keeps the history."""
    _client, user = _staff_client()
    wo = _corrective_wo()
    po, line = _line_for(wo, user, qty=2, unit_cost=Decimal("1.00"))
    services.receive_delivery(po, [(line, 2)], received_by=user, delivery_datetime=timezone.now())

    WorkOrderMaterialUsage.objects.filter(work_order=wo).delete()

    lines = _serialize(wo)["purchase_order_lines"]
    assert [entry["id"] for entry in lines] == [str(line.id)]


def test_a_line_closed_short_is_not_shown_as_still_on_its_way():
    """The written-off balance is not a part the tech is waiting for.

    Receiving is finished with a closed-short line — that is what settlement
    means — and this panel is asking "is anything still coming?", not "did the
    ordered quantity arrive?". Answering it with ``is_fully_received`` told a
    tech that units nobody expects were still in transit, with an expected
    delivery date, for ever.
    """
    _client, user = _staff_client()
    wo = _corrective_wo()
    po, line = _line_for(wo, user, qty=10, unit_cost=Decimal("2.00"))

    services.receive_delivery(po, [(line, 8)], received_by=user, delivery_datetime=timezone.now())
    services.close_lines_short(po, [(line, "backorder cancelled")], actor=user)

    row = _serialize(wo)["purchase_order_lines"][0]

    assert row["is_settled"] is True
    assert row["receipt_state"] == PurchaseOrderItem.ReceiptState.CLOSED_SHORT
    assert row["receipt_state_label"] == "Closed short"
    # The shortfall stays readable — the panel reports it, it just does not
    # present it as a delivery still to come.
    assert row["is_fully_received"] is False
    assert row["quantity_received"] == 8
    assert row["quantity_variance"] == -2


def test_a_line_still_genuinely_outstanding_is_reported_as_such():
    """The gray badge must not swallow the case it exists to distinguish."""
    _client, user = _staff_client()
    wo = _corrective_wo()
    po, line = _line_for(wo, user, qty=10, unit_cost=Decimal("2.00"))

    services.receive_delivery(po, [(line, 8)], received_by=user, delivery_datetime=timezone.now())

    row = _serialize(wo)["purchase_order_lines"][0]

    assert row["is_settled"] is False
    assert row["receipt_state"] == PurchaseOrderItem.ReceiptState.PARTIALLY_RECEIVED
    assert row["quantity_pending"] == 2


def test_a_fully_received_line_is_settled_too():
    _client, user = _staff_client()
    wo = _corrective_wo()
    po, line = _line_for(wo, user, qty=4, unit_cost=Decimal("5.00"))

    services.receive_delivery(po, [(line, 4)], received_by=user, delivery_datetime=timezone.now())

    row = _serialize(wo)["purchase_order_lines"][0]

    assert row["is_fully_received"] is True
    assert row["is_settled"] is True
    assert row["receipt_state"] == PurchaseOrderItem.ReceiptState.RECEIVED
