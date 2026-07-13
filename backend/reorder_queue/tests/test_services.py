"""Focused tests for the reorder_queue service layer (#883).

These exercise the extracted workflow functions directly (create, receive,
status transitions) so the service contract is covered independently of the
API/serializer boundary. The API-level behaviour contract lives in test_api.py.
"""

from datetime import date
from decimal import Decimal

from django.utils import timezone

import pytest
from rest_framework import serializers as drf_serializers

from inventory.tests.factories import AssetFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue import services
from reorder_queue.models import (
    LeadTimeLog,
    OrderDelivery,
    PurchaseOrder,
    PurchaseOrderItem,
    ReorderRequest,
)
from reorder_queue.tests.factories import ReorderRequestFactory, UserFactory

pytestmark = pytest.mark.django_db


class TestCreatePurchaseOrder:
    """create_purchase_order — the three line-item routes + validation."""

    def test_inventory_line_ceildiv_packages_and_total(self):
        user = UserFactory()
        supplier = SupplierFactory()
        # package_cost=None so ItemSupplier.save() keeps our explicit unit_cost
        # instead of deriving it from package_cost / quantity_per_package.
        item_supplier = ItemSupplierFactory(
            supplier=supplier,
            unit_cost=Decimal("10.00"),
            package_cost=None,
            quantity_per_package=6,
        )

        po = services.create_purchase_order(
            {"supplier": supplier},
            [{"item_supplier_id": item_supplier.id, "quantity": 10}],
            user,
        )

        assert po.pk is not None
        # PO numbering stays in PurchaseOrder.save() (#887).
        assert po.po_number
        assert po.created_by == user
        assert po.items.count() == 1
        line = po.items.first()
        assert line.quantity_ordered == 10
        assert line.unit_cost_ordered == Decimal("10.00")
        assert line.order_in_packages == 2  # ceil(10 / 6)
        assert po.estimated_total == Decimal("100.00")

    def test_explicit_packages_and_unit_cost_override(self):
        user = UserFactory()
        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(
            supplier=supplier, unit_cost=Decimal("10.00"), quantity_per_package=6
        )

        po = services.create_purchase_order(
            {"supplier": supplier},
            [
                {
                    "item_supplier_id": item_supplier.id,
                    "quantity": 10,
                    "unit_cost": "3.50",
                    "order_in_packages": 4,
                }
            ],
            user,
        )

        line = po.items.first()
        assert line.order_in_packages == 4
        assert line.unit_cost_ordered == Decimal("3.50")
        assert po.estimated_total == Decimal("35.00")

    def test_asset_line(self):
        user = UserFactory()
        supplier = SupplierFactory()
        asset = AssetFactory(manufacturer=supplier)

        po = services.create_purchase_order(
            {"supplier": supplier},
            [{"asset_id": str(asset.id), "quantity": 2, "unit_cost": "100.00"}],
            user,
        )

        line = po.items.first()
        assert line.asset_id == asset.id
        assert line.order_in_packages == 0
        assert po.estimated_total == Decimal("200.00")

    def test_freeform_line(self):
        user = UserFactory()
        supplier = SupplierFactory()

        po = services.create_purchase_order(
            {"supplier": supplier},
            [{"description": "Custom bracket", "quantity": 3, "unit_cost": "5.00"}],
            user,
        )

        line = po.items.first()
        assert line.description == "Custom bracket"
        assert line.item_supplier is None
        assert line.asset is None
        assert po.estimated_total == Decimal("15.00")

    def test_supplier_mismatch_raises_and_rolls_back(self):
        user = UserFactory()
        supplier = SupplierFactory()
        other = SupplierFactory()
        item_supplier = ItemSupplierFactory(supplier=other, unit_cost=Decimal("1.00"))

        with pytest.raises(drf_serializers.ValidationError):
            services.create_purchase_order(
                {"supplier": supplier},
                [{"item_supplier_id": item_supplier.id, "quantity": 1}],
                user,
            )

        # @transaction.atomic rolls the half-built PO back.
        assert PurchaseOrder.objects.count() == 0

    def test_invalid_quantity_raises(self):
        user = UserFactory()
        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(supplier=supplier)

        with pytest.raises(drf_serializers.ValidationError):
            services.create_purchase_order(
                {"supplier": supplier},
                [{"item_supplier_id": item_supplier.id, "quantity": 0}],
                user,
            )


class TestReceiveDelivery:
    """receive_delivery / mark_delivered_receipt — receipt side effects."""

    def _sent_po(self, user, *, qty=10, unit_cost=None, stock=0):
        if unit_cost is None:
            unit_cost = Decimal("2.00")
        supplier = SupplierFactory()
        po = PurchaseOrder.objects.create(
            supplier=supplier,
            status=PurchaseOrder.Status.SENT,
            created_by=user,
            sent_at=timezone.now(),
        )
        item_supplier = ItemSupplierFactory(
            supplier=supplier, unit_cost=unit_cost, average_lead_time=5
        )
        item = item_supplier.item
        item.current_stock = stock
        item.save()
        line = PurchaseOrderItem.objects.create(
            purchase_order=po,
            item_supplier=item_supplier,
            quantity_ordered=qty,
            unit_cost_ordered=unit_cost,
        )
        return po, line, item

    def test_partial_receipt(self):
        user = UserFactory()
        po, line, item = self._sent_po(user, qty=10, stock=0)

        delivery = services.receive_delivery(
            po, [(line, 4)], received_by=user, delivery_datetime=timezone.now()
        )

        line.refresh_from_db()
        po.refresh_from_db()
        item.refresh_from_db()
        assert line.quantity_received == 4
        assert po.status == PurchaseOrder.Status.PARTIALLY_RECEIVED
        assert delivery.is_complete is False
        assert item.current_stock == 4
        # Not fully received — no lead-time log yet.
        assert LeadTimeLog.objects.filter(purchase_order=po).count() == 0

    def test_full_receipt_logs_lead_time(self):
        user = UserFactory()
        po, line, item = self._sent_po(user, qty=10, stock=1)

        delivery = services.receive_delivery(
            po, [(line, 10)], received_by=user, delivery_datetime=timezone.now()
        )

        line.refresh_from_db()
        po.refresh_from_db()
        item.refresh_from_db()
        assert line.quantity_received == 10
        assert po.status == PurchaseOrder.Status.RECEIVED
        assert delivery.is_complete is True
        assert item.current_stock == 11
        assert LeadTimeLog.objects.filter(purchase_order=po).count() == 1

    def test_mark_delivered_receipt_receives_all_pending(self):
        user = UserFactory()
        po, line, _item = self._sent_po(user, qty=10, stock=0)

        delivery = services.mark_delivered_receipt(
            po, received_by=user, delivery_datetime=timezone.now()
        )

        line.refresh_from_db()
        po.refresh_from_db()
        assert line.quantity_received == 10
        assert po.status == PurchaseOrder.Status.RECEIVED
        assert delivery.is_complete is True
        assert OrderDelivery.objects.filter(purchase_order=po).count() == 1


class TestStatusTransitions:
    """mark_sent / confirm_order / void_po / void_line_item + helpers."""

    def test_mark_sent_stamps_and_syncs_requests(self):
        user = UserFactory()
        supplier = SupplierFactory()
        po = PurchaseOrder.objects.create(
            supplier=supplier, status=PurchaseOrder.Status.DRAFT, created_by=user
        )
        item_supplier = ItemSupplierFactory(supplier=supplier, average_lead_time=3)
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            item_supplier=item_supplier,
            quantity_ordered=5,
            unit_cost_ordered=Decimal("2.00"),
        )
        req = ReorderRequestFactory(
            item=item_supplier.item, status=ReorderRequest.Status.PENDING, quantity=5
        )

        services.mark_sent(po, user)

        po.refresh_from_db()
        req.refresh_from_db()
        assert po.status == PurchaseOrder.Status.SENT
        assert po.sent_by == user
        assert po.sent_at is not None
        assert req.status == ReorderRequest.Status.ORDERED
        assert req.order_number == po.po_number
        assert req.actual_cost == Decimal("10.00")  # 2.00 * 5

    def test_confirm_order(self):
        user = UserFactory()
        supplier = SupplierFactory()
        po = PurchaseOrder.objects.create(
            supplier=supplier, status=PurchaseOrder.Status.SENT, created_by=user
        )

        services.confirm_order(po, date(2026, 1, 15))

        po.refresh_from_db()
        assert po.status == PurchaseOrder.Status.CONFIRMED
        assert po.expected_delivery_date == date(2026, 1, 15)

    def test_void_po_cascades_to_items(self):
        user = UserFactory()
        supplier = SupplierFactory()
        po = PurchaseOrder.objects.create(
            supplier=supplier, status=PurchaseOrder.Status.DRAFT, created_by=user
        )
        item_supplier = ItemSupplierFactory(supplier=supplier)
        line = PurchaseOrderItem.objects.create(
            purchase_order=po,
            item_supplier=item_supplier,
            quantity_ordered=5,
            unit_cost_ordered=Decimal("2.00"),
        )

        services.void_po(po, user, "orphaned")

        po.refresh_from_db()
        line.refresh_from_db()
        assert po.status == PurchaseOrder.Status.VOIDED
        assert po.voided_by == user
        assert po.void_reason == "orphaned"
        assert line.is_voided is True
        assert line.void_reason == "PO voided"

    def test_void_line_item_marks_supplier_discontinued(self):
        user = UserFactory()
        supplier = SupplierFactory()
        po = PurchaseOrder.objects.create(
            supplier=supplier, status=PurchaseOrder.Status.SENT, created_by=user
        )
        item_supplier = ItemSupplierFactory(supplier=supplier, is_active=True)
        line = PurchaseOrderItem.objects.create(
            purchase_order=po,
            item_supplier=item_supplier,
            quantity_ordered=5,
            unit_cost_ordered=Decimal("2.00"),
        )

        services.void_line_item(line, user, "discontinued")

        line.refresh_from_db()
        item_supplier.refresh_from_db()
        assert line.is_voided is True
        assert line.void_reason == "discontinued"
        assert item_supplier.is_discontinued is True
        assert item_supplier.is_active is False

    def test_add_business_days_skips_weekend(self):
        # 2026-01-15 is a Thursday; +3 business days -> Tuesday 2026-01-20.
        assert services.add_business_days(date(2026, 1, 15), 3) == date(2026, 1, 20)


class TestPurchaseOrderAggregates:
    """Single-pass PO aggregates preserve the voided-exclusion semantics (#883)."""

    def test_aggregates_exclude_voided_lines(self):
        user = UserFactory()
        supplier = SupplierFactory()
        po = PurchaseOrder.objects.create(
            supplier=supplier,
            status=PurchaseOrder.Status.SENT,
            created_by=user,
            estimated_total=Decimal("100.00"),
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            item_supplier=ItemSupplierFactory(supplier=supplier),
            quantity_ordered=5,
            unit_cost_ordered=Decimal("10.00"),
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            item_supplier=ItemSupplierFactory(supplier=supplier),
            quantity_ordered=5,
            unit_cost_ordered=Decimal("10.00"),
            is_voided=True,
        )

        po = PurchaseOrder.objects.get(pk=po.pk)  # fresh instance, no cache
        assert po.total_items == 1
        assert po.total_quantity == 5
        assert po.has_active_items is True
        assert po.effective_estimated_total == Decimal("50.00")  # 100 - 50 voided
        assert po.total_received_quantity == 0
        # Voided line has 0 received of 5 ordered, so the PO is not fully received.
        assert po.is_fully_received is False

    def test_is_fully_received_true_when_all_received(self):
        user = UserFactory()
        supplier = SupplierFactory()
        po = PurchaseOrder.objects.create(
            supplier=supplier, status=PurchaseOrder.Status.SENT, created_by=user
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            item_supplier=ItemSupplierFactory(supplier=supplier),
            quantity_ordered=5,
            unit_cost_ordered=Decimal("1.00"),
            quantity_received=5,
        )

        po = PurchaseOrder.objects.get(pk=po.pk)
        assert po.is_fully_received is True
        assert po.total_received_quantity == 5
        assert po.total_items == 1

    def test_all_voided_po_has_no_active_items(self):
        user = UserFactory()
        supplier = SupplierFactory()
        po = PurchaseOrder.objects.create(
            supplier=supplier, status=PurchaseOrder.Status.SENT, created_by=user
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            item_supplier=ItemSupplierFactory(supplier=supplier),
            quantity_ordered=5,
            unit_cost_ordered=Decimal("1.00"),
            is_voided=True,
        )

        po = PurchaseOrder.objects.get(pk=po.pk)
        assert po.has_active_items is False
        assert po.total_items == 0


class TestNextPoNumber:
    """next_po_number — the ``PO-YYYY-NNNN`` composition extracted from save() (#887).

    ``PurchaseOrder.auto_generate_po_number`` delegates to this helper while the
    method + save() retry loop stay on the model (the concurrency patch seam).
    """

    def test_first_number_for_year(self):
        assert services.next_po_number(2099) == "PO-2099-0001"

    def test_increments_from_latest(self):
        PurchaseOrder.objects.create(
            created_by=UserFactory(), supplier=SupplierFactory(), po_number="PO-2099-0007"
        )
        assert services.next_po_number(2099) == "PO-2099-0008"

    def test_unparseable_counter_falls_back_to_one(self):
        PurchaseOrder.objects.create(
            created_by=UserFactory(), supplier=SupplierFactory(), po_number="PO-2099-BAD"
        )
        assert services.next_po_number(2099) == "PO-2099-0001"

    def test_scoped_to_year(self):
        PurchaseOrder.objects.create(
            created_by=UserFactory(), supplier=SupplierFactory(), po_number="PO-2098-0042"
        )
        assert services.next_po_number(2099) == "PO-2099-0001"
