"""Regression: confirm_order must coerce a client-supplied expected_delivery_date.

Found by the BACKEND-18 sweep — the same defect as inventory's
``generate_work_order``, in a second place. ``confirm_order`` handed the client's
``expected_delivery_date`` string straight to ``services.confirm_order()``, which
assigned it to the ``DateField`` and saved. The row persisted fine, but the
*in-memory* PO kept a ``str``, so the response serializer's ``payment_schedule``
(``render_payment_schedule``) called ``.isoformat()`` on it and raised
``AttributeError`` — a 500 raised AFTER the PO had already been moved to
``confirmed``.

Only the ``due_on_receipt`` / ``cod`` payment terms take the branch that reads
``expected_delivery_date``, which is why nothing caught it. The web UI never sends
the field either (``PurchaseOrderPage.tsx`` calls ``confirmOrder(orderId)`` with no
body) even though ``purchaseOrderAPI.confirmOrder`` declares and forwards it.
"""

from __future__ import annotations

import datetime

from django.contrib.auth import get_user_model
from django.utils.crypto import get_random_string

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from reorder_queue.models import PurchaseOrder, Supplier

User = get_user_model()

pytestmark = pytest.mark.django_db

URL = "/api/reorders/purchase-orders/{}/confirm_order/"


def _staff_client():
    user = User.objects.create_user(
        username=f"staff_{get_random_string(6)}",
        email="staff@example.com",
        password=get_random_string(24),
        is_staff=True,
        is_superuser=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def _sent_po(user) -> PurchaseOrder:
    return PurchaseOrder.objects.create(
        supplier=Supplier.objects.create(name=f"Supplier {get_random_string(4)}"),
        created_by=user,
        status=PurchaseOrder.Status.SENT,
        # the terms whose payment schedule reads expected_delivery_date
        payment_terms=PurchaseOrder.PaymentTerms.DUE_ON_RECEIPT,
    )


class TestConfirmOrderExpectedDeliveryDate:
    def test_client_supplied_date_is_accepted_and_serializes(self):
        client, user = _staff_client()
        po = _sent_po(user)

        resp = client.post(URL.format(po.id), {"expected_delivery_date": "2026-09-01"}, "json")

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["expected_delivery_date"] == "2026-09-01"
        assert resp.data["payment_schedule"]["due_date"] == "2026-09-01"
        po.refresh_from_db()
        assert po.expected_delivery_date == datetime.date(2026, 9, 1)
        assert po.status == PurchaseOrder.Status.CONFIRMED

    def test_malformed_date_is_a_400_and_confirms_nothing(self):
        client, user = _staff_client()
        po = _sent_po(user)

        resp = client.post(URL.format(po.id), {"expected_delivery_date": "09/01/2026"}, "json")

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
        po.refresh_from_db()
        assert po.status == PurchaseOrder.Status.SENT
        assert po.expected_delivery_date is None

    def test_omitted_date_still_confirms(self):
        """The path the web UI takes must be unchanged."""
        client, user = _staff_client()
        po = _sent_po(user)

        resp = client.post(URL.format(po.id), {}, "json")

        assert resp.status_code == status.HTTP_200_OK, resp.data
        po.refresh_from_db()
        assert po.status == PurchaseOrder.Status.CONFIRMED
        assert po.expected_delivery_date is None
