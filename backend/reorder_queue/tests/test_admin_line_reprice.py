"""Changing a line's ordered price through the admin leaves a trace.

The price-trace invariant this branch establishes: a ``PurchaseOrderItem``'s
``unit_cost_ordered`` never changes without a ``PO_LINE_REPRICE`` audit event
naming the figure it replaced. The API routes enforce it; the Django admin is
the remaining way to write that column, both on the line's own change form and
on the line inline under a purchase order.

``unit_cost_ordered`` stays editable in the admin on purpose — that is what the
admin is for, the exceptional correction the draft-only API reprice cannot
serve. What it owes is the record, which is what these tests pin.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from django.contrib import admin
from django.forms.models import model_to_dict
from django.test import Client, RequestFactory

import pytest

from inventory.tests.factories import ItemSupplierFactory, SupplierFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderAuditEvent, PurchaseOrderItem
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_user():
    return UserFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def admin_client(admin_user):
    client = Client()
    client.force_login(admin_user)
    return client


@pytest.fixture
def line(admin_user):
    supplier = SupplierFactory(name="Acme Fasteners")
    po = PurchaseOrder.objects.create(supplier=supplier, created_by=admin_user)
    return PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=ItemSupplierFactory(supplier=supplier),
        quantity_ordered=4,
        unit_cost_ordered=Decimal("5.00"),
        notes="",
    )


def reprice_events(line_item):
    return PurchaseOrderAuditEvent.objects.filter(
        line_item=line_item, action=PurchaseOrderAuditEvent.Action.PO_LINE_REPRICE
    )


def change_form_data(line_item, admin_user, **overrides):
    """The line's current values, shaped for a POST to its admin change form.

    Built from the ModelAdmin's own form so it stays correct as the admin's
    field set changes, rather than hard-coding a payload that silently rots.
    """
    modeladmin = admin.site._registry[PurchaseOrderItem]
    request = RequestFactory().get("/")
    request.user = admin_user
    form_class = modeladmin.get_form(request, obj=line_item, change=True)

    data = {}
    for name in form_class.base_fields:
        value = model_to_dict(line_item, fields=[name]).get(name)
        if value is None:
            data[name] = ""
        elif isinstance(value, (dict, list)):
            data[name] = json.dumps(value)
        else:
            data[name] = value
    data.update(overrides)
    return data


def test_repricing_a_line_in_the_admin_records_both_figures(admin_client, admin_user, line):
    url = f"/admin/reorder_queue/purchaseorderitem/{line.pk}/change/"

    response = admin_client.post(url, change_form_data(line, admin_user, unit_cost_ordered="0.01"))

    assert response.status_code == 302, getattr(response, "context_data", None)
    line.refresh_from_db()
    assert line.unit_cost_ordered == Decimal("0.0100")

    event = reprice_events(line).get()
    assert event.actor == admin_user
    assert Decimal(event.metadata["previous_unit_cost_ordered"]) == Decimal("5.00")
    assert Decimal(event.metadata["unit_cost_ordered"]) == Decimal("0.01")


def test_changing_something_other_than_the_price_records_no_reprice(admin_client, admin_user, line):
    url = f"/admin/reorder_queue/purchaseorderitem/{line.pk}/change/"

    response = admin_client.post(url, change_form_data(line, admin_user, notes="Chased the vendor"))

    assert response.status_code == 302
    line.refresh_from_db()
    assert line.notes == "Chased the vendor"
    assert line.unit_cost_ordered == Decimal("5.0000")
    assert not reprice_events(line).exists()


def test_restating_the_same_price_in_the_admin_records_no_reprice(admin_client, admin_user, line):
    url = f"/admin/reorder_queue/purchaseorderitem/{line.pk}/change/"

    response = admin_client.post(
        url, change_form_data(line, admin_user, unit_cost_ordered="5.0000")
    )

    assert response.status_code == 302
    assert not reprice_events(line).exists()


def test_repricing_a_line_through_the_purchase_order_inline_records_both_figures(
    admin_client, admin_user, line
):
    """The inline writes the same column, so it owes the same record."""
    po = line.purchase_order
    modeladmin = admin.site._registry[PurchaseOrder]
    request = RequestFactory().get("/")
    request.user = admin_user
    po_form = modeladmin.get_form(request, obj=po, change=True)

    data = {}
    for name in po_form.base_fields:
        value = model_to_dict(po, fields=[name]).get(name)
        if isinstance(value, datetime):
            # The admin renders datetimes through a split date/time widget.
            data[f"{name}_0"] = value.date().isoformat()
            data[f"{name}_1"] = value.time().isoformat()
        else:
            data[name] = "" if value is None else value

    prefix = "items"
    data.update(
        {
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-INITIAL_FORMS": "1",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
    )
    inline_form = modeladmin.get_inline_instances(request, po)[0].get_formset(request, po).form
    for name in inline_form.base_fields:
        value = model_to_dict(line, fields=[name]).get(name)
        if value is None:
            data[f"{prefix}-0-{name}"] = ""
        elif isinstance(value, (dict, list)):
            data[f"{prefix}-0-{name}"] = json.dumps(value)
        else:
            data[f"{prefix}-0-{name}"] = value
    data[f"{prefix}-0-id"] = str(line.pk)
    data[f"{prefix}-0-purchase_order"] = str(po.pk)
    data[f"{prefix}-0-unit_cost_ordered"] = "0.01"

    response = admin_client.post(f"/admin/reorder_queue/purchaseorder/{po.pk}/change/", data)

    assert response.status_code == 302, getattr(response, "context_data", None)
    line.refresh_from_db()
    assert line.unit_cost_ordered == Decimal("0.0100")

    event = reprice_events(line).get()
    assert Decimal(event.metadata["previous_unit_cost_ordered"]) == Decimal("5.00")
    assert Decimal(event.metadata["unit_cost_ordered"]) == Decimal("0.01")
