"""Purchase order ↔ work order bridge (op-bu80, B4 of the corrective-WO epic).

A purchase-order line can now say *which job it was bought for*
(``PurchaseOrderItem.work_order``), and receiving such a line threads the parts
back onto that job as an actual-cost material line — so
``WorkOrder.actual_material_cost`` (op-768w) finally sees money that was spent
through a purchase order rather than out of somebody's pocket.

What this file pins down:

* the link is settable at PO creation and after the fact, for all three kinds
  of line (inventory / asset / freeform);
* receiving posts exactly one material line per PO line, carrying the received
  quantity and the actual-or-ordered unit cost;
* the receipt itself is otherwise **unchanged** — stock still increments, the
  committee ledger entry still fires, and the bridge moves no stock of its own;
* re-receiving (partial then full) or re-driving the same receipt never
  double-posts.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import WorkOrder, WorkOrderMaterialUsage
from inventory.services.work_order_purchase_bridge import post_work_order_material
from inventory.tests.factories import AssetFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue import services
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.serializers import PurchaseOrderItemSerializer
from reorder_queue.views import PurchaseOrderViewSet

User = get_user_model()

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _staff():
    return User.objects.create_user(username="wrench", password="x", is_staff=True)


def _staff_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _corrective_wo():
    """A work order with no PM template — the corrective shape (op-svut)."""
    return WorkOrder.objects.create(maintenance_item=None, asset=AssetFactory())


def _po_line_for_wo(user, work_order, *, qty=10, unit_cost=None, stock=0):
    """A SENT PO with one inventory line ordered for ``work_order``."""
    if unit_cost is None:
        unit_cost = Decimal("2.00")
    supplier = SupplierFactory()
    po = PurchaseOrder.objects.create(
        supplier=supplier,
        status=PurchaseOrder.Status.SENT,
        created_by=user,
        sent_at=timezone.now(),
    )
    # quantity_per_package=1 pins the derived unit_cost (see the ItemSupplier
    # save() derivation) so the line's cost is exactly what this test asked for.
    item_supplier = ItemSupplierFactory(
        supplier=supplier,
        unit_cost=unit_cost or Decimal("1.00"),
        quantity_per_package=1,
        average_lead_time=5,
    )
    item = item_supplier.item
    item.current_stock = stock
    item.save()
    line = PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=item_supplier,
        quantity_ordered=qty,
        unit_cost_ordered=unit_cost,
        work_order=work_order,
    )
    return po, line, item


# ─────────────────────────────────────────────────────────────────────────────
# The link itself
# ─────────────────────────────────────────────────────────────────────────────
def test_po_line_records_the_work_order_it_was_ordered_for():
    """The FK exists and reverses onto the work order."""
    user = _staff()
    wo = _corrective_wo()
    _po, line, _item = _po_line_for_wo(user, wo)

    assert line.work_order == wo
    assert list(wo.purchase_order_items.all()) == [line]


def test_deleting_the_work_order_leaves_the_po_line_standing():
    """SET_NULL: purchasing history outlives the job it was raised for."""
    user = _staff()
    wo = _corrective_wo()
    _po, line, _item = _po_line_for_wo(user, wo)

    wo.delete()

    line.refresh_from_db()
    assert line.work_order is None


def test_create_purchase_order_accepts_work_order_id_on_a_line():
    """POST /purchase-orders/ tags the line with the job it is for."""
    user = _staff()
    client = _staff_client(user)
    wo = _corrective_wo()
    supplier = SupplierFactory()
    item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)

    response = client.post(
        "/api/reorders/purchase-orders/",
        {
            "supplier": str(supplier.id),
            "items": [
                {
                    "item_supplier_id": item_supplier.id,
                    "quantity": 4,
                    "unit_cost": "3.00",
                    "work_order_id": str(wo.id),
                }
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    line = PurchaseOrderItem.objects.get(purchase_order__id=response.data["id"])
    assert line.work_order_id == wo.id
    assert response.data["items"][0]["work_order"] == wo.id
    assert response.data["items"][0]["work_order_details"]["short_id"] == wo.short_id


def test_create_purchase_order_rejects_an_unknown_work_order():
    """A typo'd work order id is a 400, not a silently untagged line."""
    user = _staff()
    client = _staff_client(user)
    supplier = SupplierFactory()
    item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)

    response = client.post(
        "/api/reorders/purchase-orders/",
        {
            "supplier": str(supplier.id),
            "items": [
                {
                    "item_supplier_id": item_supplier.id,
                    "quantity": 1,
                    "work_order_id": "3f1d0f2e-0000-4000-8000-000000000000",
                }
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_line_can_be_tagged_and_untagged_after_the_order_went_out():
    """PATCH items/<id>/ — which job the parts are for is often known later."""
    user = _staff()
    client = _staff_client(user)
    wo = _corrective_wo()
    po, line, _item = _po_line_for_wo(user, wo)
    line.work_order = None
    line.save()

    tag = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/items/{line.id}/",
        {"work_order": str(wo.id)},
        format="json",
    )
    assert tag.status_code == status.HTTP_200_OK
    line.refresh_from_db()
    assert line.work_order_id == wo.id

    untag = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/items/{line.id}/",
        {"work_order": ""},
        format="json",
    )
    assert untag.status_code == status.HTTP_200_OK
    line.refresh_from_db()
    assert line.work_order is None


def test_tagging_an_unknown_work_order_is_a_404():
    user = _staff()
    client = _staff_client(user)
    po, line, _item = _po_line_for_wo(user, _corrective_wo())

    response = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/items/{line.id}/",
        {"work_order": "3f1d0f2e-0000-4000-8000-000000000000"},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_rendering_tagged_lines_does_not_cost_a_query_per_line():
    """``work_order_details`` rides a prefetch, not one lookup per PO line.

    Measured on the block itself rather than on the whole PO detail read: that
    read already grows with its line count for reasons predating this bead
    (``item_details`` nests the full inventory serializer), so a budget over
    the whole response would be measuring somebody else's cost. Once the
    viewset's queryset is evaluated, resolving six *distinct* work orders —
    each with the template/problem/asset fallbacks ``display_title`` walks —
    must issue nothing further at all.
    """
    user = _staff()
    supplier = SupplierFactory()
    po = PurchaseOrder.objects.create(
        supplier=supplier, status=PurchaseOrder.Status.SENT, created_by=user
    )
    for _ in range(6):
        item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            item_supplier=item_supplier,
            quantity_ordered=1,
            unit_cost_ordered=Decimal("1.00"),
            # A distinct work order per line — the worst case for an N+1.
            work_order=_corrective_wo(),
        )

    serializer = PurchaseOrderItemSerializer()
    fetched = PurchaseOrderViewSet.queryset.get(pk=po.pk)
    with CaptureQueriesContext(connection) as ctx:
        details = [serializer.get_work_order_details(line) for line in fetched.items.all()]

    assert len({entry["short_id"] for entry in details}) == 6
    assert ctx.captured_queries == []


# ─────────────────────────────────────────────────────────────────────────────
# The receive bridge
# ─────────────────────────────────────────────────────────────────────────────
def test_receiving_a_wo_linked_line_posts_the_material_onto_the_work_order():
    """The headline: receive → the part and its cost appear on the job."""
    user = _staff()
    wo = _corrective_wo()
    po, line, item = _po_line_for_wo(user, wo, qty=5, unit_cost=Decimal("4.00"))

    services.receive_delivery(po, [(line, 5)], received_by=user, delivery_datetime=timezone.now())

    usage = WorkOrderMaterialUsage.objects.get(work_order=wo)
    assert usage.purchase_order_item_id == line.id
    assert usage.inventory_item_id == item.id
    assert usage.material_name == item.name
    assert usage.quantity_used == Decimal("5.00")
    assert usage.unit_cost == Decimal("4.00")
    assert usage.was_used is True
    # No PM template exists for a bought-for-the-job line, so it is ad-hoc —
    # the only kind of material line that is removable (op-768w).
    assert usage.is_ad_hoc is True
    assert usage.material is None

    # And the money lands where the cost report / ledger charge read it.
    wo.refresh_from_db()
    assert wo.actual_material_cost == Decimal("20.00")


def test_receipt_prefers_the_actual_unit_cost_over_the_ordered_one():
    """A price correction typed in at receipt is what the job is charged."""
    user = _staff()
    wo = _corrective_wo()
    po, line, _item = _po_line_for_wo(user, wo, qty=3, unit_cost=Decimal("2.00"))
    line.unit_cost_actual = Decimal("2.50")
    line.save()

    services.receive_delivery(po, [(line, 3)], received_by=user, delivery_datetime=timezone.now())

    usage = WorkOrderMaterialUsage.objects.get(work_order=wo)
    assert usage.unit_cost == Decimal("2.50")
    wo.refresh_from_db()
    assert wo.actual_material_cost == Decimal("7.50")


def test_a_line_receipted_at_zero_cost_stays_free():
    """An explicit zero actual cost is a fact, not a missing value.

    A warranty part or a free replacement is receipted at 0 — falling back to
    the price it was *ordered* at would charge the job for something the space
    never paid for.
    """
    user = _staff()
    wo = _corrective_wo()
    po, line, _item = _po_line_for_wo(user, wo, qty=2, unit_cost=Decimal("9.00"))
    line.unit_cost_actual = Decimal("0")
    line.save()

    services.receive_delivery(po, [(line, 2)], received_by=user, delivery_datetime=timezone.now())

    usage = WorkOrderMaterialUsage.objects.get(work_order=wo)
    assert usage.unit_cost == Decimal("0.00")
    wo.refresh_from_db()
    assert wo.actual_material_cost == Decimal("0.00")


def test_partial_then_full_receive_does_not_double_post():
    """One line, quantity growing to the total received — never two rows."""
    user = _staff()
    wo = _corrective_wo()
    po, line, _item = _po_line_for_wo(user, wo, qty=10, unit_cost=Decimal("1.50"))

    services.receive_delivery(po, [(line, 3)], received_by=user, delivery_datetime=timezone.now())
    usage = WorkOrderMaterialUsage.objects.get(work_order=wo)
    assert usage.quantity_used == Decimal("3.00")

    services.receive_delivery(po, [(line, 7)], received_by=user, delivery_datetime=timezone.now())

    assert WorkOrderMaterialUsage.objects.filter(work_order=wo).count() == 1
    usage.refresh_from_db()
    assert usage.quantity_used == Decimal("10.00")
    wo.refresh_from_db()
    assert wo.actual_material_cost == Decimal("15.00")  # 10 × 1.50, counted once


def test_re_driving_the_same_receipt_is_a_no_op():
    """Replaying the bridge for a line already posted changes nothing."""
    user = _staff()
    wo = _corrective_wo()
    po, line, _item = _po_line_for_wo(user, wo, qty=4, unit_cost=Decimal("3.00"))

    services.receive_delivery(po, [(line, 4)], received_by=user, delivery_datetime=timezone.now())
    line.refresh_from_db()

    post_work_order_material(line)
    post_work_order_material(line)

    assert WorkOrderMaterialUsage.objects.filter(work_order=wo).count() == 1
    usage = WorkOrderMaterialUsage.objects.get(work_order=wo)
    assert usage.quantity_used == Decimal("4.00")


def test_asset_and_freeform_lines_post_under_their_own_label():
    """Not just inventory: anything bought for the job lands on the job."""
    user = _staff()
    wo = _corrective_wo()
    supplier = SupplierFactory()
    po = PurchaseOrder.objects.create(
        supplier=supplier,
        status=PurchaseOrder.Status.SENT,
        created_by=user,
        sent_at=timezone.now(),
    )
    asset = AssetFactory(manufacturer=supplier)
    asset_line = PurchaseOrderItem.objects.create(
        purchase_order=po,
        asset=asset,
        quantity_ordered=1,
        unit_cost_ordered=Decimal("120.00"),
        work_order=wo,
    )
    freeform_line = PurchaseOrderItem.objects.create(
        purchase_order=po,
        description="Emergency gasket, corner store",
        quantity_ordered=2,
        unit_cost_ordered=Decimal("6.25"),
        work_order=wo,
    )

    services.receive_delivery(
        po,
        [(asset_line, 1), (freeform_line, 2)],
        received_by=user,
        delivery_datetime=timezone.now(),
    )

    by_line = {
        u.purchase_order_item_id: u for u in WorkOrderMaterialUsage.objects.filter(work_order=wo)
    }
    assert by_line[asset_line.id].material_name == asset.name
    assert by_line[asset_line.id].inventory_item is None
    assert by_line[freeform_line.id].material_name == "Emergency gasket, corner store"
    assert by_line[freeform_line.id].inventory_item is None
    wo.refresh_from_db()
    assert wo.actual_material_cost == Decimal("132.50")  # 120.00 + 2 × 6.25


# ─────────────────────────────────────────────────────────────────────────────
# The receipt is otherwise unchanged
# ─────────────────────────────────────────────────────────────────────────────
def test_an_untagged_line_posts_nothing_to_any_work_order():
    """The ordinary purchase — no work order, no material line, no change."""
    user = _staff()
    wo = _corrective_wo()
    po, line, item = _po_line_for_wo(user, wo, qty=6, stock=1)
    line.work_order = None
    line.save()

    services.receive_delivery(po, [(line, 6)], received_by=user, delivery_datetime=timezone.now())

    assert WorkOrderMaterialUsage.objects.count() == 0
    item.refresh_from_db()
    assert item.current_stock == 7


def test_stock_still_increments_and_the_bridge_moves_none_of_it():
    """Receiving is additive; only the toggle seam ever decrements (op-uh8z).

    The material line is marked used because the parts were bought *for* this
    job, but ``applied_quantity`` stays null — no decrement is applied here, so
    the received stock is exactly what it was before this bead.
    """
    user = _staff()
    wo = _corrective_wo()
    po, line, item = _po_line_for_wo(user, wo, qty=8, unit_cost=Decimal("1.00"), stock=2)

    services.receive_delivery(po, [(line, 8)], received_by=user, delivery_datetime=timezone.now())

    item.refresh_from_db()
    assert item.current_stock == 10  # 2 + 8, untouched by the bridge

    usage = WorkOrderMaterialUsage.objects.get(work_order=wo)
    assert usage.applied_quantity is None
    assert usage.stock_applied is False
    assert usage.usage_log is None


def test_committee_ledger_entry_still_fires_for_a_wo_linked_line():
    """The PO-receipt ledger adapter (op-3dj5) is unaffected by the bridge."""
    from hordak.models import Transaction

    user = _staff()
    wo = _corrective_wo()
    group = Group.objects.create(name="Welding SIG")
    po, line, item = _po_line_for_wo(user, wo, qty=5, unit_cost=Decimal("4.00"))
    item.owning_group = group
    item.ownership_type = "group"
    item.save()

    services.receive_delivery(po, [(line, 5)], received_by=user, delivery_datetime=timezone.now())

    assert Transaction.objects.filter(meta__source_type="PO_RECEIPT").count() == 1
    assert WorkOrderMaterialUsage.objects.filter(work_order=wo).count() == 1
