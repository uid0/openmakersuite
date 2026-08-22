"""Regression: confirming a PO must not erase an expected delivery date already set.

The operator sets ``expected_delivery_date`` at create/edit time, then clicks
Confirm. The web UI confirms with **no request body**
(``PurchaseOrderPage.tsx`` → ``purchaseOrderAPI.confirmOrder(orderId)``), so the
``confirm_order`` action read an absent key as ``None`` and
``services.confirm_order`` assigned it unconditionally — silently wiping the
date. No error, no audit trace; the operator sees a successful confirm and an
empty date field.

The knock-on damage is worse than the field itself: on ``due_on_receipt`` and
``cod`` terms ``PurchaseOrder.payment_schedule`` anchors ``due_date`` to
``expected_delivery_date``, so the payment fell off the schedule too, and
``services.receiving.create_lead_time_log`` lost the expected date it compares
actual lead time against, silently substituting the supplier's average.

Nothing in the model, the service, the tests or the history makes clearing on
confirm deliberate — the sibling action ``ReorderRequestViewSet.mark_ordered``
documents the opposite convention explicitly ("fields that are omitted are left
untouched so a bare mark-ordered never wipes values a PO already populated").

An *explicitly supplied* value — a date, or an explicit ``null`` — still lands,
so this narrows nothing: only the unsupplied case stops writing.
"""

from __future__ import annotations

import datetime
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.crypto import get_random_string

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.tests.factories import InventoryItemFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue.models import LeadTimeLog, PurchaseOrder, PurchaseOrderItem, Supplier

User = get_user_model()

pytestmark = pytest.mark.django_db

CONFIRM_URL = "/api/reorders/purchase-orders/{}/confirm_order/"
RECEIVE_URL = "/api/reorders/purchase-orders/{}/receive/"

EXPECTED = datetime.date(2026, 9, 15)


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


def _sent_po(user, *, terms=PurchaseOrder.PaymentTerms.DUE_ON_RECEIPT, expected=EXPECTED):
    """A SENT PO the operator has already given an expected delivery date."""
    return PurchaseOrder.objects.create(
        supplier=Supplier.objects.create(name=f"Supplier {get_random_string(4)}"),
        created_by=user,
        status=PurchaseOrder.Status.SENT,
        payment_terms=terms,
        expected_delivery_date=expected,
    )


class TestConfirmPreservesExistingExpectedDeliveryDate:
    def test_confirm_with_no_body_keeps_the_date_the_operator_set(self):
        """The exact path the web app takes: POST confirm_order with no body."""
        client, user = _staff_client()
        po = _sent_po(user)

        resp = client.post(CONFIRM_URL.format(po.id))

        assert resp.status_code == status.HTTP_200_OK, resp.data
        po.refresh_from_db()
        assert po.status == PurchaseOrder.Status.CONFIRMED
        assert po.expected_delivery_date == EXPECTED
        assert resp.data["expected_delivery_date"] == "2026-09-15"

    def test_confirm_with_empty_json_body_keeps_the_date(self):
        client, user = _staff_client()
        po = _sent_po(user)

        resp = client.post(CONFIRM_URL.format(po.id), {}, "json")

        assert resp.status_code == status.HTTP_200_OK, resp.data
        po.refresh_from_db()
        assert po.expected_delivery_date == EXPECTED

    @pytest.mark.parametrize(
        "terms",
        [PurchaseOrder.PaymentTerms.DUE_ON_RECEIPT, PurchaseOrder.PaymentTerms.COD],
    )
    def test_delivery_anchored_payment_stays_on_the_schedule(self, terms):
        """The knock-on: due_on_receipt / cod anchor due_date to the date."""
        client, user = _staff_client()
        po = _sent_po(user, terms=terms)

        resp = client.post(CONFIRM_URL.format(po.id))

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["payment_schedule"]["due_date"] == "2026-09-15"
        assert resp.data["payment_schedule"]["basis"] == "On delivery"
        po.refresh_from_db()
        assert po.payment_schedule["due_date"] == EXPECTED

    def test_receiving_still_sees_the_expected_date(self):
        """The knock-on: create_lead_time_log records the expected date."""
        client, user = _staff_client()
        supplier = SupplierFactory()
        sent_at = timezone.now() - timedelta(days=7)
        average_lead_time = 10
        fallback = sent_at.date() + timedelta(days=average_lead_time)
        expected = sent_at.date() + timedelta(days=average_lead_time + 20)
        po = PurchaseOrder.objects.create(
            supplier=supplier,
            created_by=user,
            status=PurchaseOrder.Status.SENT,
            sent_by=user,
            sent_at=sent_at,
            estimated_total=Decimal("50.00"),
            expected_delivery_date=expected,
        )
        item_supplier = ItemSupplierFactory(
            supplier=supplier,
            quantity_per_package=1,
            average_lead_time=average_lead_time,
            item=InventoryItemFactory(current_stock=0),
        )
        po_item = PurchaseOrderItem.objects.create(
            purchase_order=po,
            item_supplier=item_supplier,
            quantity_ordered=5,
            unit_cost_ordered=Decimal("10.00"),
            order_in_packages=5,
        )

        assert client.post(CONFIRM_URL.format(po.id)).status_code == status.HTTP_200_OK

        resp = client.post(
            RECEIVE_URL.format(po.id),
            {"items": [{"purchase_order_item": po_item.id, "quantity_received": 5}]},
            "json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data

        log = LeadTimeLog.objects.get(purchase_order=po)
        # Not the average-lead-time fallback the wipe forced it onto.
        assert log.expected_delivery_date == expected
        assert log.expected_delivery_date != fallback


class TestConfirmStillHonoursASuppliedValue:
    """Nothing about the supplied-value paths changes."""

    def test_supplied_date_overrides_the_existing_one(self):
        client, user = _staff_client()
        po = _sent_po(user)

        resp = client.post(
            CONFIRM_URL.format(po.id), {"expected_delivery_date": "2026-10-02"}, "json"
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        po.refresh_from_db()
        assert po.expected_delivery_date == datetime.date(2026, 10, 2)

    def test_explicit_null_still_clears_the_date(self):
        """An explicit null IS a supplied value: the operator asked to clear it."""
        client, user = _staff_client()
        po = _sent_po(user)

        resp = client.post(CONFIRM_URL.format(po.id), {"expected_delivery_date": None}, "json")

        assert resp.status_code == status.HTTP_200_OK, resp.data
        po.refresh_from_db()
        assert po.status == PurchaseOrder.Status.CONFIRMED
        assert po.expected_delivery_date is None

    def test_empty_string_is_a_400_and_neither_confirms_nor_wipes(self):
        """An empty string is not an explicit null: it is a malformed date."""
        client, user = _staff_client()
        po = _sent_po(user)

        resp = client.post(CONFIRM_URL.format(po.id), {"expected_delivery_date": ""}, "json")

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
        po.refresh_from_db()
        assert po.status == PurchaseOrder.Status.SENT
        assert po.expected_delivery_date == EXPECTED

    def test_malformed_date_is_a_400_and_leaves_the_existing_date_alone(self):
        client, user = _staff_client()
        po = _sent_po(user)

        resp = client.post(
            CONFIRM_URL.format(po.id), {"expected_delivery_date": "10/02/2026"}, "json"
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
        po.refresh_from_db()
        assert po.status == PurchaseOrder.Status.SENT
        assert po.expected_delivery_date == EXPECTED
