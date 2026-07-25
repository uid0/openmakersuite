"""Supplier delete-confirmation page must render its whole cascade (BACKEND-13).

Prod repro: ``GET /admin/inventory/supplier/{id}/delete/`` 500'd with
``AttributeError: 'NoneType' object has no attribute 'name'``. Django's admin
delete collector (``admin.utils.get_deleted_objects`` -> ``format_callback``)
calls ``str(obj)`` on *every* object in the cascade, so one un-renderable row
takes the whole page down and the supplier becomes undeletable.

The un-renderable row was a received ``DeliveryItem`` for an **asset-only** PO
line: ``PurchaseOrderItem.item`` is None there (the line carries ``asset``), and
``DeliveryItem.__str__`` dereferenced ``.item.name``. Cascade path:
Supplier -> PurchaseOrder -> PurchaseOrderItem -> DeliveryItem.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib import admin
from django.contrib.admin.utils import get_deleted_objects
from django.test import Client, RequestFactory
from django.utils import timezone

import pytest

from inventory.models import Supplier
from inventory.tests.factories import AssetFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue.models import (
    DeliveryItem,
    LeadTimeLog,
    OrderDelivery,
    PurchaseOrder,
    PurchaseOrderItem,
)
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _supplier_with_received_lines(user):
    """A supplier carrying the full receiving cascade, asset line included.

    Mirrors the prod shape: one inventory line and one asset-only line on the
    same PO, both received into an ``OrderDelivery``, plus a ``LeadTimeLog``
    (which hangs off both the PO and the ItemSupplier, so it is collected twice
    over).
    """
    supplier = SupplierFactory(name="Acme Industrial")
    item_supplier = ItemSupplierFactory(supplier=supplier)
    asset = AssetFactory(manufacturer=supplier, name="Bandsaw 14in")

    po = PurchaseOrder.objects.create(supplier=supplier, created_by=user)
    inventory_line = PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=item_supplier,
        quantity_ordered=6,
        unit_cost_ordered=Decimal("3.25"),
    )
    asset_line = PurchaseOrderItem.objects.create(
        purchase_order=po,
        asset=asset,
        quantity_ordered=1,
        unit_cost_ordered=Decimal("1299.00"),
    )

    delivery = OrderDelivery.objects.create(purchase_order=po, received_by=user)
    DeliveryItem.objects.create(
        delivery=delivery, purchase_order_item=inventory_line, quantity_received=6
    )
    DeliveryItem.objects.create(
        delivery=delivery, purchase_order_item=asset_line, quantity_received=1
    )
    LeadTimeLog.objects.create(
        item_supplier=item_supplier,
        purchase_order=po,
        order_date=timezone.now(),
        expected_delivery_date=timezone.now().date(),
        actual_delivery_date=timezone.now().date(),
        estimated_lead_time_days=5,
        actual_lead_time_days=5,
        variance_days=0,
        quantity_ordered=6,
        quantity_received=6,
    )
    return supplier, asset


def test_get_deleted_objects_renders_asset_only_delivery_item():
    """The collector str()s every cascade row — none of them may raise."""
    user = UserFactory(is_staff=True, is_superuser=True)
    supplier, asset = _supplier_with_received_lines(user)

    request = RequestFactory().get(f"/admin/inventory/supplier/{supplier.pk}/delete/")
    request.user = user

    to_delete, model_count, perms_needed, protected = get_deleted_objects(
        [supplier], request, admin.site
    )

    assert not perms_needed
    assert not protected
    assert model_count["delivery items"] == 2
    # The asset name is what the previously-crashing DeliveryItem now renders.
    assert asset.name in str(to_delete)


def test_supplier_delete_confirmation_page_renders():
    """The exact prod request: it must be a 200, not a 500."""
    user = UserFactory(is_staff=True, is_superuser=True)
    supplier, asset = _supplier_with_received_lines(user)

    client = Client()
    client.force_login(user)

    response = client.get(f"/admin/inventory/supplier/{supplier.pk}/delete/")

    assert response.status_code == 200
    body = response.content.decode()
    assert "Are you sure" in body
    assert asset.name in body


def test_supplier_can_actually_be_deleted():
    """Confirming the delete removes the supplier and its cascade."""
    user = UserFactory(is_staff=True, is_superuser=True)
    supplier, _asset = _supplier_with_received_lines(user)

    client = Client()
    client.force_login(user)

    response = client.post(f"/admin/inventory/supplier/{supplier.pk}/delete/", {"post": "yes"})

    assert response.status_code == 302
    assert not Supplier.objects.filter(pk=supplier.pk).exists()
    assert not DeliveryItem.objects.exists()
    assert not PurchaseOrder.objects.filter(supplier_id=supplier.pk).exists()
