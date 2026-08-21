"""The PO API exposes a human-readable ``status_label``.

The purchasing list and detail screens render ``status_label`` directly for the
Status column and the detail hero subtitle. ``PurchaseOrderSerializer`` used to
omit it entirely — only an unrelated report endpoint emitted one — so the status
text rendered blank in the running app while the raw ``status`` still drove the
badge colour. That left a purchase order in a terminal state (``received``,
``cancelled``, ``voided``) looking indistinguishable from a live one, which in
turn made the absent "Receive items" affordance impossible to explain from the
screen alone.

These pin the label onto both the serializer and the detail endpoint, for every
status in the lifecycle.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model

import pytest
from rest_framework import status as http_status
from rest_framework.test import APIClient

from inventory.tests.factories import SupplierFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.serializers import PurchaseOrderSerializer

User = get_user_model()

pytestmark = pytest.mark.django_db


def _staff():
    return User.objects.create_user(username="buyer", password="x", is_staff=True)


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _line(po):
    """A freeform line, so ``po`` is not hidden from the list view."""
    return PurchaseOrderItem.objects.create(
        purchase_order=po,
        description="Consumables",
        quantity_ordered=1,
        unit_cost_ordered=Decimal("1.00"),
        order_in_packages=0,
    )


def _po(user=None, **kwargs):
    return PurchaseOrder.objects.create(
        supplier=kwargs.pop("supplier", None) or SupplierFactory(),
        status=kwargs.pop("status", PurchaseOrder.Status.DRAFT),
        created_by=user or _staff(),
        **kwargs,
    )


@pytest.mark.parametrize(
    "value,label",
    [
        ("draft", "Draft"),
        ("sent", "Sent to Supplier"),
        ("confirmed", "Confirmed by Supplier"),
        ("partially_received", "Partially Received"),
        ("received", "Fully Received"),
        ("cancelled", "Cancelled"),
        ("voided", "Voided"),
    ],
)
def test_serializer_labels_every_status(value, label):
    po = _po(status=value)

    data = PurchaseOrderSerializer(po).data

    assert data["status"] == value
    assert data["status_label"] == label


def test_detail_endpoint_carries_the_label_alongside_the_raw_status():
    user = _staff()
    po = _po(user, status=PurchaseOrder.Status.RECEIVED)

    response = _client(user).get(f"/api/reorders/purchase-orders/{po.pk}/")

    assert response.status_code == http_status.HTTP_200_OK
    assert response.data["status"] == "received"
    assert response.data["status_label"] == "Fully Received"


def test_list_endpoint_carries_the_label_too():
    """The Status column on the purchasing list reads the same field.

    The list screen renders ``order.status_label`` for its Status column and
    sorts on it, so the omission blanked that column as well as the detail
    hero. Both screens are fed by ``PurchaseOrderSerializer``, and this pins
    the list route specifically rather than trusting that they share one.
    """
    user = _staff()
    # The list hides orders with no active line item (oms-a8o), so each needs
    # one to appear at all.
    for status_value in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.VOIDED):
        _line(_po(user, status=status_value))

    response = _client(user).get("/api/reorders/purchase-orders/")

    assert response.status_code == http_status.HTTP_200_OK
    rows = response.data["results"] if "results" in response.data else response.data
    assert rows, "expected the two orders just created"
    by_status = {row["status"]: row["status_label"] for row in rows}
    assert by_status["draft"] == "Draft"
    assert by_status["voided"] == "Voided"


def test_status_label_is_read_only():
    user = _staff()
    po = _po(user, status=PurchaseOrder.Status.SENT)

    response = _client(user).patch(
        f"/api/reorders/purchase-orders/{po.pk}/",
        {"status_label": "Nonsense"},
        format="json",
    )

    assert response.status_code == http_status.HTTP_200_OK
    po.refresh_from_db()
    assert po.status == "sent"
    assert response.data["status_label"] == "Sent to Supplier"
