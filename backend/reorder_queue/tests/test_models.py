"""
Unit tests for reorder queue models.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

import pytest
from freezegun import freeze_time

from inventory.models import ItemSupplier
from inventory.tests.factories import (
    AssetFactory,
    InventoryItemFactory,
    ItemSupplierFactory,
    SupplierFactory,
)
from reorder_queue.models import (
    DeliveryItem,
    LeadTimeLog,
    OrderDelivery,
    PurchaseOrder,
    PurchaseOrderItem,
    ReorderRequest,
)
from reorder_queue.tests.factories import ReorderRequestFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.mark.unit
class TestPurchaseOrderItemTypedTarget:
    """PurchaseOrderItem's at-most-one + freeform typed-target accessor (#884).

    Behaviour-preserving: .item / .supplier / __str__ now route through
    .target / .target_type but must keep their exact legacy results.
    """

    def test_inventory_item_line(self):
        item_supplier = ItemSupplierFactory()
        line = PurchaseOrderItem(item_supplier=item_supplier, quantity_ordered=3)
        assert line.target_type == "inventory_item"
        assert line.target == item_supplier.item
        assert line.item == item_supplier.item
        assert line.supplier == item_supplier.supplier
        assert str(line) == f"{item_supplier.item.name} - 3 units"

    def test_asset_line(self):
        supplier = SupplierFactory()
        asset = AssetFactory(manufacturer=supplier)
        line = PurchaseOrderItem(asset=asset, quantity_ordered=2)
        assert line.target_type == "asset"
        assert line.target == asset
        assert line.item is None
        assert line.supplier == supplier
        assert str(line) == f"{asset.name} - 2 units"

    def test_asset_line_without_manufacturer_has_no_supplier(self):
        asset = AssetFactory(manufacturer=None)
        line = PurchaseOrderItem(asset=asset, quantity_ordered=1)
        assert line.target_type == "asset"
        assert line.supplier is None

    def test_freeform_line(self):
        line = PurchaseOrderItem(description="Custom bracket", quantity_ordered=5)
        assert line.target_type == "freeform"
        assert line.target is None
        assert line.item is None
        assert line.supplier is None
        assert str(line) == "Purchase Order Item - 5 units"

    def test_item_supplier_takes_priority_over_description(self):
        """Legacy get_item_type priority: item_supplier wins even if a
        description co-exists (allowed by the constraint)."""
        item_supplier = ItemSupplierFactory()
        line = PurchaseOrderItem(
            item_supplier=item_supplier, description="note", quantity_ordered=1
        )
        assert line.target_type == "inventory_item"


@pytest.mark.unit
class TestDeliveryItemStr:
    """DeliveryItem.__str__ must never deref ``.name`` on a null target.

    Regression for BACKEND-13: ``purchase_order_item.item`` is None on
    asset-only lines, so the old ``self.purchase_order_item.item.name`` raised
    ``AttributeError: 'NoneType' object has no attribute 'name'`` — which took
    down the admin delete-confirmation page (it str()s every cascade-related
    object). See TestSupplierDeleteCascade in test_admin_delete_cascade.py for
    the end-to-end proof.
    """

    def _delivery(self, purchase_order):
        return OrderDelivery.objects.create(
            purchase_order=purchase_order, received_by=UserFactory()
        )

    def test_inventory_item_line_shows_item_name(self):
        item_supplier = ItemSupplierFactory()
        user = UserFactory()
        po = PurchaseOrder.objects.create(supplier=item_supplier.supplier, created_by=user)
        line = PurchaseOrderItem.objects.create(
            purchase_order=po,
            item_supplier=item_supplier,
            quantity_ordered=4,
            unit_cost_ordered=Decimal("1.50"),
        )
        received = DeliveryItem.objects.create(
            delivery=self._delivery(po), purchase_order_item=line, quantity_received=4
        )

        assert str(received) == f"{item_supplier.item.name} - 4 received"

    def test_asset_only_line_shows_asset_name(self):
        """The crash case: an asset line has no ``item``, only an ``asset``."""
        supplier = SupplierFactory()
        asset = AssetFactory(manufacturer=supplier)
        user = UserFactory()
        po = PurchaseOrder.objects.create(supplier=supplier, created_by=user)
        line = PurchaseOrderItem.objects.create(
            purchase_order=po,
            asset=asset,
            quantity_ordered=1,
            unit_cost_ordered=Decimal("899.00"),
        )
        received = DeliveryItem.objects.create(
            delivery=self._delivery(po), purchase_order_item=line, quantity_received=1
        )

        assert received.item is None  # the None that used to be dereferenced
        assert str(received) == f"{asset.name} - 1 received"

    def test_freeform_line_falls_back_to_generic_label(self):
        """Neither item nor asset — a generic label, still no crash."""
        supplier = SupplierFactory()
        user = UserFactory()
        po = PurchaseOrder.objects.create(supplier=supplier, created_by=user)
        line = PurchaseOrderItem.objects.create(
            purchase_order=po,
            description="Custom steel bracket",
            quantity_ordered=2,
            unit_cost_ordered=Decimal("12.00"),
        )
        received = DeliveryItem.objects.create(
            delivery=self._delivery(po), purchase_order_item=line, quantity_received=2
        )

        assert received.item is None
        assert received.supplier is None
        assert str(received) == "Purchase Order Item - 2 received"


@pytest.mark.unit
class TestLeadTimeLogStr:
    """LeadTimeLog.__str__ audit for the same BACKEND-13 pattern.

    It reads ``item_supplier.item.name`` / ``item_supplier.supplier.name``, but
    all three FKs in that chain are ``null=False``, so no persisted row can
    carry the None that broke DeliveryItem. Django proves the stronger half:
    assigning None to a non-nullable FK does not yield None on read, it raises
    ``RelatedObjectDoesNotExist`` at attribute access — so a ``is not None``
    guard there would be unreachable code, not a fix. These tests pin that.
    """

    def test_str_shows_item_and_supplier_names(self):
        item_supplier = ItemSupplierFactory()
        user = UserFactory()
        po = PurchaseOrder.objects.create(supplier=item_supplier.supplier, created_by=user)
        log = LeadTimeLog.objects.create(
            item_supplier=item_supplier,
            purchase_order=po,
            order_date=timezone.now() - timedelta(days=9),
            expected_delivery_date=timezone.now().date() - timedelta(days=2),
            actual_delivery_date=timezone.now().date(),
            estimated_lead_time_days=5,
            actual_lead_time_days=7,
            variance_days=2,
            quantity_ordered=10,
            quantity_received=10,
        )

        assert str(log) == (
            f"{item_supplier.item.name} from {item_supplier.supplier.name} - 7 days"
        )

    def test_relations_in_the_str_chain_are_all_non_nullable(self):
        """The audit assertion: there is no None to guard against here."""
        assert LeadTimeLog._meta.get_field("item_supplier").null is False
        assert ItemSupplier._meta.get_field("item").null is False
        assert ItemSupplier._meta.get_field("supplier").null is False


@pytest.mark.unit
class TestReorderRequestModel:
    """Tests for the ReorderRequest model."""

    def test_reorder_request_creation(self):
        """Test creating a reorder request."""
        item = InventoryItemFactory()
        request = ReorderRequestFactory(item=item, quantity=25, requested_by="John Doe")

        assert request.item == item
        assert request.quantity == 25
        assert request.requested_by == "John Doe"
        assert request.status == ReorderRequest.Status.PENDING
        assert str(request).startswith(item.name)

    def test_estimated_cost_calculation(self):
        """Test estimated_cost property calculates correctly."""
        item = InventoryItemFactory(unit_cost=Decimal("10.50"))
        request = ReorderRequestFactory(item=item, quantity=10)

        assert request.estimated_cost == Decimal("105.00")

    def test_estimated_cost_without_unit_cost(self):
        """Test estimated_cost returns None when item has no unit_cost."""
        item = InventoryItemFactory(unit_cost=None)
        request = ReorderRequestFactory(item=item, quantity=10)

        assert request.estimated_cost is None

    @freeze_time("2024-01-15 12:00:00")
    def test_days_pending_calculation(self):
        """Test days_pending property calculates correctly."""
        # Create request 5 days ago
        with freeze_time("2024-01-10 12:00:00"):
            request = ReorderRequestFactory(status=ReorderRequest.Status.PENDING)

        assert request.days_pending == 5

    def test_days_pending_for_non_pending_status(self):
        """Test days_pending returns 0 for non-pending requests."""
        request = ReorderRequestFactory(status=ReorderRequest.Status.APPROVED)
        assert request.days_pending == 0

    def test_request_ordering(self):
        """Test requests are ordered by requested_at descending."""
        req1 = ReorderRequestFactory()
        req2 = ReorderRequestFactory()

        requests = ReorderRequest.objects.all()
        assert requests[0] == req2  # Most recent first
        assert requests[1] == req1

    def test_status_choices(self):
        """Test all status choices are valid."""
        statuses = [
            ReorderRequest.Status.PENDING,
            ReorderRequest.Status.APPROVED,
            ReorderRequest.Status.ORDERED,
            ReorderRequest.Status.RECEIVED,
            ReorderRequest.Status.CANCELLED,
        ]

        for status_choice in statuses:
            request = ReorderRequestFactory(status=status_choice)
            assert request.status == status_choice

    def test_priority_choices(self):
        """Test all priority choices are valid."""
        priorities = [
            ReorderRequest.Priority.LOW,
            ReorderRequest.Priority.NORMAL,
            ReorderRequest.Priority.HIGH,
            ReorderRequest.Priority.URGENT,
        ]

        for priority in priorities:
            request = ReorderRequestFactory(priority=priority)
            assert request.priority == priority

    def test_reviewed_by_relationship(self):
        """Test reviewed_by relationship with User."""
        user = UserFactory(username="admin")
        request = ReorderRequestFactory(
            status=ReorderRequest.Status.APPROVED, reviewed_by=user, reviewed_at=timezone.now()
        )

        assert request.reviewed_by == user
        assert request.reviewed_at is not None

    def test_order_tracking_fields(self):
        """Test order tracking fields."""
        request = ReorderRequestFactory(
            status=ReorderRequest.Status.ORDERED,
            ordered_at=timezone.now(),
            order_number="ORD-12345",
            actual_cost=Decimal("125.50"),
        )

        assert request.order_number == "ORD-12345"
        assert request.actual_cost == Decimal("125.50")
        assert request.ordered_at is not None

    def test_delivery_tracking_fields(self):
        """Test delivery tracking fields."""
        estimated = timezone.now().date() + timedelta(days=7)
        actual = timezone.now().date() + timedelta(days=5)

        request = ReorderRequestFactory(
            status=ReorderRequest.Status.RECEIVED,
            estimated_delivery=estimated,
            actual_delivery=actual,
        )

        assert request.estimated_delivery == estimated
        assert request.actual_delivery == actual


@pytest.mark.unit
class TestPurchaseOrderModel:
    """Tests for the PurchaseOrder model."""

    @freeze_time("2024-04-10 09:30:00")
    def test_po_number_auto_generated_on_save(self):
        """PO number should be generated when missing."""
        supplier = SupplierFactory()
        user = UserFactory()

        po = PurchaseOrder.objects.create(supplier=supplier, created_by=user)

        assert po.po_number == "PO-2024-0001"

    @freeze_time("2024-04-10 09:30:00")
    def test_po_number_increments_sequentially(self):
        """Subsequent POs in same year should increment."""
        supplier = SupplierFactory()
        user = UserFactory()

        po1 = PurchaseOrder.objects.create(supplier=supplier, created_by=user)
        po2 = PurchaseOrder.objects.create(supplier=supplier, created_by=user)

        assert po1.po_number == "PO-2024-0001"
        assert po2.po_number == "PO-2024-0002"


@pytest.mark.unit
class TestPurchaseOrderItemOrderInPackages:
    """Regression tests for oms-qqn: order_in_packages must accept integers.

    Production drifted to a boolean column, breaking PO create with
    `column "order_in_packages" is of type boolean but expression is of
    type integer`. Migration 0016 corrects the column type in PostgreSQL.
    These tests assert the model and storage accept non-boolean integer
    values.
    """

    def test_order_in_packages_stores_large_integer(self):
        supplier = SupplierFactory()
        user = UserFactory()
        item_supplier = ItemSupplierFactory(supplier=supplier)

        po = PurchaseOrder.objects.create(supplier=supplier, created_by=user)
        po_item = PurchaseOrderItem.objects.create(
            purchase_order=po,
            item_supplier=item_supplier,
            quantity_ordered=25,
            unit_cost_ordered=Decimal("1.00"),
            order_in_packages=7,
        )

        po_item.refresh_from_db()
        assert po_item.order_in_packages == 7
        assert not isinstance(po_item.order_in_packages, bool)
