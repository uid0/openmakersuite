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

from django.contrib.auth import get_user_model

import pytest
from rest_framework import status as http_status
from rest_framework.test import APIClient

from inventory.tests.factories import SupplierFactory
from reorder_queue.models import PurchaseOrder
from reorder_queue.serializers import PurchaseOrderSerializer

User = get_user_model()

pytestmark = pytest.mark.django_db


def _staff():
    return User.objects.create_user(username="buyer", password="x", is_staff=True)


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


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
