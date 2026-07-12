"""Tests for the inventory item cycle-count action + days-since-last-count (op-c7y4).

Covers POST /api/inventory/items/{id}/cycle-count/ and the two read fields
(``last_counted_at`` + ``days_since_last_count``) surfaced on the item
serializers. Reuses the reconciliation contract (``StockReconciliation`` +
``_apply_reconciliation_row``) so the audit trail and reorder automation stay
consistent with the batch/grid flows.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone
from django.utils.crypto import get_random_string

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import StockReconciliation
from inventory.tests.factories import InventoryItemFactory, LocationFactory
from membership.models import SIGAdmin
from reorder_queue.models import ReorderRequest

User = get_user_model()

pytestmark = pytest.mark.django_db


def _cycle_count_url(item_id):
    return f"/api/inventory/items/{item_id}/cycle-count/"


def _item_detail_url(item_id):
    return f"/api/inventory/items/{item_id}/"


def _make_user(**kwargs):
    return User.objects.create_user(
        username=kwargs.pop("username", get_random_string(8)),
        email=kwargs.pop("email", f"{get_random_string(6)}@example.com"),
        password=get_random_string(24),
        **kwargs,
    )


@pytest.fixture
def staff_client():
    user = _make_user(is_staff=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


@pytest.fixture
def location():
    return LocationFactory()


@pytest.fixture
def item(location):
    return InventoryItemFactory(
        location=location, current_stock=20, minimum_stock=5, reorder_quantity=10
    )


@pytest.fixture
def low_item(location):
    return InventoryItemFactory(
        location=location, current_stock=20, minimum_stock=10, reorder_quantity=15
    )


class TestCycleCountAction:
    def test_updates_current_stock_to_counted_qty(self, staff_client, item):
        client, _ = staff_client
        response = client.post(
            _cycle_count_url(item.id),
            {
                "counted_qty": 18,
                "reason": StockReconciliation.ReasonCode.MISCOUNTED,
                "skip_reorder": True,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        item.refresh_from_db()
        assert item.current_stock == 18
        assert response.data["current_stock"] == 18

    def test_stamps_last_counted_at(self, staff_client, item):
        client, _ = staff_client
        assert item.last_counted_at is None
        before = timezone.now()
        response = client.post(
            _cycle_count_url(item.id),
            {
                "counted_qty": 18,
                "reason": StockReconciliation.ReasonCode.MISCOUNTED,
                "skip_reorder": True,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        item.refresh_from_db()
        assert item.last_counted_at is not None
        assert item.last_counted_at >= before
        # Fresh count -> zero whole days elapsed.
        assert response.data["days_since_last_count"] == 0

    def test_creates_reconciliation_row_with_delta(self, staff_client, item):
        client, user = staff_client
        response = client.post(
            _cycle_count_url(item.id),
            {
                "counted_qty": 15,
                "reason": StockReconciliation.ReasonCode.LOST,
                "skip_reorder": True,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        recs = StockReconciliation.objects.filter(item=item)
        assert recs.count() == 1
        rec = recs.first()
        assert rec.projected_count == 20
        assert rec.actual_count == 15
        assert rec.delta == -5
        assert rec.reason == StockReconciliation.ReasonCode.LOST
        assert rec.reconciled_by_id == user.id
        # Response echoes the audit row.
        assert response.data["reconciliation"]["id"] == rec.id
        assert response.data["reconciliation"]["projected_count"] == 20
        assert response.data["reconciliation"]["actual_count"] == 15
        assert response.data["reconciliation"]["delta"] == -5
        assert response.data["reconciliation"]["reason"] == StockReconciliation.ReasonCode.LOST
        assert response.data["reconciliation"]["reconciled_by"] == user.id

    def test_auto_reorder_on_low_stock(self, staff_client, low_item):
        client, _ = staff_client
        response = client.post(
            _cycle_count_url(low_item.id),
            {
                "counted_qty": 3,
                "reason": StockReconciliation.ReasonCode.USED_WITHOUT_SCAN,
            },  # <= minimum_stock (10)
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        reorder = ReorderRequest.objects.filter(item=low_item).first()
        assert reorder is not None
        assert reorder.quantity == low_item.reorder_quantity
        rec = StockReconciliation.objects.filter(item=low_item).first()
        assert rec.triggered_reorder_id == reorder.id

    def test_skip_reorder_respected(self, staff_client, low_item):
        client, _ = staff_client
        response = client.post(
            _cycle_count_url(low_item.id),
            {
                "counted_qty": 2,
                "reason": StockReconciliation.ReasonCode.USED_WITHOUT_SCAN,
                "skip_reorder": True,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert not ReorderRequest.objects.filter(item=low_item).exists()
        rec = StockReconciliation.objects.filter(item=low_item).first()
        assert rec.triggered_reorder is None

    def test_non_reconciler_rejected_403(self, item):
        regular = _make_user()
        client = APIClient()
        client.force_authenticate(user=regular)
        response = client.post(
            _cycle_count_url(item.id),
            {"counted_qty": 15, "reason": StockReconciliation.ReasonCode.MISCOUNTED},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        item.refresh_from_db()
        assert item.current_stock == 20  # unchanged
        assert item.last_counted_at is None
        assert not StockReconciliation.objects.filter(item=item).exists()

    def test_anonymous_rejected(self, item):
        response = APIClient().post(
            _cycle_count_url(item.id),
            {"counted_qty": 15, "reason": StockReconciliation.ReasonCode.MISCOUNTED},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        item.refresh_from_db()
        assert item.current_stock == 20

    def test_sig_admin_of_item_group_allowed(self, item):
        group = Group.objects.create(name="Woodshop SIG")
        item.owning_group = group
        item.save(update_fields=["owning_group"])
        sig_admin = _make_user()
        SIGAdmin.objects.create(user=sig_admin, group=group, is_active=True)
        client = APIClient()
        client.force_authenticate(user=sig_admin)
        response = client.post(
            _cycle_count_url(item.id),
            {
                "counted_qty": 18,
                "reason": StockReconciliation.ReasonCode.MISCOUNTED,
                "skip_reorder": True,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        item.refresh_from_db()
        assert item.current_stock == 18

    def test_invalid_reason_rejected_400(self, staff_client, item):
        client, _ = staff_client
        response = client.post(
            _cycle_count_url(item.id),
            {"counted_qty": 10, "reason": "not_a_reason"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        item.refresh_from_db()
        assert item.current_stock == 20
        assert item.last_counted_at is None

    def test_missing_counted_qty_rejected_400(self, staff_client, item):
        client, _ = staff_client
        response = client.post(
            _cycle_count_url(item.id),
            {"reason": StockReconciliation.ReasonCode.MISCOUNTED},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not StockReconciliation.objects.filter(item=item).exists()

    def test_negative_counted_qty_rejected_400(self, staff_client, item):
        client, _ = staff_client
        response = client.post(
            _cycle_count_url(item.id),
            {"counted_qty": -1, "reason": StockReconciliation.ReasonCode.MISCOUNTED},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        item.refresh_from_db()
        assert item.current_stock == 20


class TestDaysSinceLastCount:
    def test_detail_serializer_exposes_fields_when_never_counted(self, staff_client, item):
        client, _ = staff_client
        response = client.get(_item_detail_url(item.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["last_counted_at"] is None
        assert response.data["days_since_last_count"] is None

    def test_days_since_last_count_computes(self, staff_client, item):
        client, _ = staff_client
        item.last_counted_at = timezone.now() - timedelta(days=9, hours=1)
        item.save(update_fields=["last_counted_at"])
        response = client.get(_item_detail_url(item.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["days_since_last_count"] == 9
        assert response.data["last_counted_at"] is not None

    def test_list_serializer_exposes_fields(self, staff_client, item):
        client, _ = staff_client
        item.last_counted_at = timezone.now() - timedelta(days=2)
        item.save(update_fields=["last_counted_at"])
        response = client.get("/api/inventory/items/")
        assert response.status_code == status.HTTP_200_OK
        results = response.data.get("results", response.data)
        row = next(r for r in results if str(r["id"]) == str(item.id))
        assert row["days_since_last_count"] == 2
        assert row["last_counted_at"] is not None

    def test_cycle_count_then_days_since_is_zero(self, staff_client, item):
        client, _ = staff_client
        client.post(
            _cycle_count_url(item.id),
            {
                "counted_qty": 12,
                "reason": StockReconciliation.ReasonCode.MISCOUNTED,
                "skip_reorder": True,
            },
            format="json",
        )
        response = client.get(_item_detail_url(item.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["days_since_last_count"] == 0
