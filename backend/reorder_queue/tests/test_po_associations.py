"""Purchase-order ↔ work-order / committee association (op-shb9).

A purchase order can now say *who it was placed for*, at two levels:

* the **order** — ``PurchaseOrder.work_order`` and ``PurchaseOrder.owning_group``
  (committee == the owning SIG, an ``auth.Group``);
* the **line** — ``PurchaseOrderItem.owning_group``, the committee twin of the
  already-shipped ``PurchaseOrderItem.work_order`` (op-bu80).

Both are settable at create time and re-settable afterwards (the job or the
committee that wanted the parts is often identified after the order goes out).

What this file pins down:

* the four FKs exist, reverse, and survive the deletion of their target
  (SET_NULL — purchasing history outlives a closed job or a retired committee);
* the read serializers expose each association plus a ``*_details`` identity
  block, and ``None`` when unset;
* create accepts the order-level pair and a per-line ``owning_group_id`` on all
  three line kinds, and rejects an unknown committee;
* ``update_item`` and ``PATCH`` on the order both set *and clear* them;
* it is **attribution only** — an order-level ``work_order`` posts no material
  onto that job (the bridge stays line-level) and moves no stock.
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
from inventory.tests.factories import AssetFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue import services
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.serializers import PurchaseOrderItemSerializer, PurchaseOrderSerializer
from reorder_queue.views import PurchaseOrderViewSet

User = get_user_model()

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _staff():
    return User.objects.create_user(username="buyer", password="x", is_staff=True)


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _committee(name="Woodshop"):
    return Group.objects.create(name=name)


def _corrective_wo():
    """A work order with no PM template — the corrective shape (op-svut)."""
    return WorkOrder.objects.create(maintenance_item=None, asset=AssetFactory())


def _po(user, **kwargs):
    return PurchaseOrder.objects.create(
        supplier=kwargs.pop("supplier", None) or SupplierFactory(),
        status=kwargs.pop("status", PurchaseOrder.Status.DRAFT),
        created_by=user,
        **kwargs,
    )


def _line(po, **kwargs):
    """One inventory line on ``po``. quantity_per_package=1 pins the unit cost."""
    item_supplier = kwargs.pop("item_supplier", None) or ItemSupplierFactory(
        supplier=po.supplier, quantity_per_package=1
    )
    return PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=item_supplier,
        quantity_ordered=kwargs.pop("quantity_ordered", 5),
        unit_cost_ordered=kwargs.pop("unit_cost_ordered", Decimal("2.00")),
        **kwargs,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The fields themselves
# ─────────────────────────────────────────────────────────────────────────────
def test_order_records_the_work_order_and_committee_it_was_placed_for():
    user = _staff()
    wo = _corrective_wo()
    committee = _committee()

    po = _po(user, work_order=wo, owning_group=committee)

    assert po.work_order == wo
    assert po.owning_group == committee
    assert list(wo.purchase_orders.all()) == [po]
    assert list(committee.reorder_purchaseorder_owned.all()) == [po]


def test_line_records_the_committee_it_was_ordered_for():
    user = _staff()
    committee = _committee()

    line = _line(_po(user), owning_group=committee)

    assert line.owning_group == committee
    assert list(committee.reorder_purchaseorderitem_owned.all()) == [line]


def test_deleting_the_work_order_leaves_the_order_standing():
    """SET_NULL: purchasing history outlives the job it was raised for."""
    user = _staff()
    wo = _corrective_wo()
    po = _po(user, work_order=wo)

    wo.delete()

    po.refresh_from_db()
    assert po.work_order is None
    assert PurchaseOrder.objects.filter(pk=po.pk).exists()


def test_deleting_the_committee_leaves_the_order_and_its_lines_standing():
    """A retired SIG must not take its purchase history down with it."""
    user = _staff()
    committee = _committee()
    po = _po(user, owning_group=committee)
    line = _line(po, owning_group=committee)

    committee.delete()

    po.refresh_from_db()
    line.refresh_from_db()
    assert po.owning_group is None
    assert line.owning_group is None


# ─────────────────────────────────────────────────────────────────────────────
# Read serializers
# ─────────────────────────────────────────────────────────────────────────────
def test_order_serializer_exposes_both_associations_with_identity_blocks():
    user = _staff()
    wo = _corrective_wo()
    committee = _committee("Metal Shop")
    po = _po(user, work_order=wo, owning_group=committee)

    data = PurchaseOrderSerializer(po).data

    assert data["work_order"] == wo.id
    assert data["work_order_details"] == {
        "id": str(wo.id),
        "short_id": wo.short_id,
        "display_title": wo.display_title,
        "status": wo.status,
    }
    assert data["owning_group"] == committee.id
    assert data["owning_group_details"] == {"id": committee.id, "name": "Metal Shop"}


def test_order_serializer_reports_none_when_unassociated():
    data = PurchaseOrderSerializer(_po(_staff())).data

    assert data["work_order"] is None
    assert data["work_order_details"] is None
    assert data["owning_group"] is None
    assert data["owning_group_details"] is None


def test_line_serializer_exposes_the_committee_with_an_identity_block():
    user = _staff()
    committee = _committee("Textiles")
    line = _line(_po(user), owning_group=committee)

    data = PurchaseOrderItemSerializer(line).data

    assert data["owning_group"] == committee.id
    assert data["owning_group_details"] == {"id": committee.id, "name": "Textiles"}


def test_line_serializer_reports_none_when_unassociated():
    data = PurchaseOrderItemSerializer(_line(_po(_staff()))).data

    assert data["owning_group"] is None
    assert data["owning_group_details"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Create
# ─────────────────────────────────────────────────────────────────────────────
def test_create_accepts_order_level_work_order_and_committee():
    user = _staff()
    client = _client(user)
    wo = _corrective_wo()
    committee = _committee()
    supplier = SupplierFactory()
    item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)

    response = client.post(
        "/api/reorders/purchase-orders/",
        {
            "supplier": str(supplier.id),
            "work_order": str(wo.id),
            "owning_group": committee.id,
            "items": [{"item_supplier_id": item_supplier.id, "quantity": 3, "unit_cost": "1.50"}],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    po = PurchaseOrder.objects.get(id=response.data["id"])
    assert po.work_order_id == wo.id
    assert po.owning_group_id == committee.id
    assert response.data["work_order_details"]["short_id"] == wo.short_id
    assert response.data["owning_group_details"]["name"] == committee.name


def test_create_accepts_a_per_line_committee_on_every_line_kind():
    """Inventory, asset and freeform lines can each carry their own committee."""
    user = _staff()
    client = _client(user)
    committee = _committee()
    supplier = SupplierFactory()
    item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)
    asset = AssetFactory(manufacturer=supplier)

    response = client.post(
        "/api/reorders/purchase-orders/",
        {
            "supplier": str(supplier.id),
            "items": [
                {
                    "item_supplier_id": item_supplier.id,
                    "quantity": 2,
                    "unit_cost": "1.00",
                    "owning_group_id": committee.id,
                },
                {
                    "asset_id": str(asset.id),
                    "quantity": 1,
                    "unit_cost": "500.00",
                    "owning_group_id": committee.id,
                },
                {
                    "description": "Custom bracket",
                    "quantity": 4,
                    "unit_cost": "12.00",
                    "owning_group_id": committee.id,
                },
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    lines = PurchaseOrderItem.objects.filter(purchase_order__id=response.data["id"])
    assert lines.count() == 3
    assert {line.owning_group_id for line in lines} == {committee.id}
    assert all(
        item["owning_group_details"]["name"] == committee.name for item in response.data["items"]
    )


def test_create_leaves_lines_unassociated_when_no_committee_is_given():
    user = _staff()
    client = _client(user)
    supplier = SupplierFactory()
    item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)

    response = client.post(
        "/api/reorders/purchase-orders/",
        {
            "supplier": str(supplier.id),
            "items": [{"item_supplier_id": item_supplier.id, "quantity": 1}],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    po = PurchaseOrder.objects.get(id=response.data["id"])
    assert po.owning_group_id is None
    assert po.work_order_id is None
    assert po.items.get().owning_group_id is None


def test_create_rejects_an_unknown_per_line_committee():
    """A typo'd committee id is a 400, not a silently unattributed line."""
    user = _staff()
    client = _client(user)
    supplier = SupplierFactory()
    item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)

    response = client.post(
        "/api/reorders/purchase-orders/",
        {
            "supplier": str(supplier.id),
            "items": [
                {"item_supplier_id": item_supplier.id, "quantity": 1, "owning_group_id": 987654}
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not PurchaseOrder.objects.exists()


# ─────────────────────────────────────────────────────────────────────────────
# Editing after the fact — line level
# ─────────────────────────────────────────────────────────────────────────────
def test_update_item_sets_the_committee_on_a_line():
    user = _staff()
    client = _client(user)
    committee = _committee()
    po = _po(user, status=PurchaseOrder.Status.SENT)
    line = _line(po)

    response = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/items/{line.id}/",
        {"owning_group": committee.id},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    line.refresh_from_db()
    assert line.owning_group_id == committee.id
    assert response.data["owning_group_details"]["name"] == committee.name


@pytest.mark.parametrize("cleared", ["", None])
def test_update_item_clears_the_committee_on_a_line(cleared):
    user = _staff()
    client = _client(user)
    po = _po(user, status=PurchaseOrder.Status.SENT)
    line = _line(po, owning_group=_committee())

    response = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/items/{line.id}/",
        {"owning_group": cleared},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    line.refresh_from_db()
    assert line.owning_group is None
    assert response.data["owning_group_details"] is None


def test_update_item_rejects_an_unknown_committee():
    user = _staff()
    client = _client(user)
    po = _po(user, status=PurchaseOrder.Status.SENT)
    line = _line(po)

    response = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/items/{line.id}/",
        {"owning_group": 987654},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    line.refresh_from_db()
    assert line.owning_group is None


def test_update_item_leaves_the_committee_alone_when_not_in_the_payload():
    """A shipment-date edit must not wipe an existing attribution."""
    user = _staff()
    client = _client(user)
    committee = _committee()
    po = _po(user, status=PurchaseOrder.Status.SENT)
    line = _line(po, owning_group=committee)

    response = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/items/{line.id}/",
        {"expected_shipment_date": "2026-09-01"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    line.refresh_from_db()
    assert line.owning_group_id == committee.id


# ─────────────────────────────────────────────────────────────────────────────
# Editing after the fact — order level
# ─────────────────────────────────────────────────────────────────────────────
def test_patch_order_sets_both_associations():
    user = _staff()
    client = _client(user)
    wo = _corrective_wo()
    committee = _committee()
    po = _po(user, status=PurchaseOrder.Status.SENT)
    _line(po)

    response = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/",
        {"work_order": str(wo.id), "owning_group": committee.id},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    po.refresh_from_db()
    assert po.work_order_id == wo.id
    assert po.owning_group_id == committee.id
    assert response.data["work_order_details"]["display_title"] == wo.display_title


def test_patch_order_clears_both_associations():
    user = _staff()
    client = _client(user)
    po = _po(
        user,
        status=PurchaseOrder.Status.SENT,
        work_order=_corrective_wo(),
        owning_group=_committee(),
    )
    _line(po)

    response = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/",
        {"work_order": None, "owning_group": None},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    po.refresh_from_db()
    assert po.work_order is None
    assert po.owning_group is None


def test_patch_order_rejects_an_unknown_committee():
    user = _staff()
    client = _client(user)
    po = _po(user, status=PurchaseOrder.Status.SENT)
    _line(po)

    response = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/",
        {"owning_group": 987654},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    po.refresh_from_db()
    assert po.owning_group is None


def test_patch_order_association_does_not_disturb_other_fields():
    """Re-tagging an order must not move its status or its numbers."""
    user = _staff()
    client = _client(user)
    committee = _committee()
    po = _po(user, status=PurchaseOrder.Status.SENT, sales_order_number="SO-7")
    _line(po)

    response = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/",
        {"owning_group": committee.id},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    po.refresh_from_db()
    assert po.status == PurchaseOrder.Status.SENT
    assert po.sales_order_number == "SO-7"


# ─────────────────────────────────────────────────────────────────────────────
# The identity blocks cost no query
# ─────────────────────────────────────────────────────────────────────────────
def test_order_identity_blocks_ride_on_the_viewset_queryset():
    """Six tagged orders, no extra query for either association block."""
    user = _staff()
    for index in range(6):
        # A distinct job and committee per order — the worst case for an N+1.
        _po(
            user,
            status=PurchaseOrder.Status.SENT,
            work_order=_corrective_wo(),
            owning_group=_committee(f"Committee {index}"),
        )

    serializer = PurchaseOrderSerializer()
    orders = list(PurchaseOrderViewSet.queryset.all())
    with CaptureQueriesContext(connection) as ctx:
        jobs = [serializer.get_work_order_details(order) for order in orders]
        committees = [serializer.get_owning_group_details(order) for order in orders]

    assert len({entry["short_id"] for entry in jobs}) == 6
    assert len({entry["name"] for entry in committees}) == 6
    assert ctx.captured_queries == []


def test_line_committee_block_rides_on_the_viewset_queryset():
    """Six lines, each on its own committee, still one prefetch."""
    user = _staff()
    po = _po(user, status=PurchaseOrder.Status.SENT)
    for index in range(6):
        _line(po, owning_group=_committee(f"Committee {index}"))

    serializer = PurchaseOrderItemSerializer()
    fetched = PurchaseOrderViewSet.queryset.get(pk=po.pk)
    with CaptureQueriesContext(connection) as ctx:
        details = [serializer.get_owning_group_details(line) for line in fetched.items.all()]

    assert len({entry["name"] for entry in details}) == 6
    assert ctx.captured_queries == []


# ─────────────────────────────────────────────────────────────────────────────
# Attribution only
# ─────────────────────────────────────────────────────────────────────────────
def test_order_level_work_order_posts_no_material_onto_that_job():
    """The PO -> work-order material bridge stays line-level (op-bu80).

    An order-level tag is attribution metadata; only a *line* tagged with a work
    order threads its received parts back onto the job.
    """
    user = _staff()
    wo = _corrective_wo()
    po = _po(user, status=PurchaseOrder.Status.SENT, work_order=wo)
    line = _line(po, quantity_ordered=10)
    item = line.item_supplier.item
    item.current_stock = 0
    item.save()

    services.receive_delivery(po, [(line, 10)], received_by=user, delivery_datetime=timezone.now())

    assert not WorkOrderMaterialUsage.objects.filter(work_order=wo).exists()
    item.refresh_from_db()
    assert item.current_stock == 10
