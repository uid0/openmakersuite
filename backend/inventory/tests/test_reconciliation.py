"""Tests for stock reconciliation API (oms-90k)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
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


BATCH_URL = "/api/inventory/reconciliations/batch/"
LIST_URL = "/api/inventory/reconciliations/"


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


class TestBatchReconcile:
    def test_batch_reconcile_updates_current_stock(self, staff_client, item):
        client, _ = staff_client
        payload = {
            "rows": [
                {
                    "item_id": str(item.id),
                    "actual_count": 18,
                    "reason": "miscounted",
                    "notes": "off by two",
                    "skip_reorder": True,
                }
            ]
        }
        response = client.post(BATCH_URL, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED, response.data
        item.refresh_from_db()
        assert item.current_stock == 18

    def test_batch_reconcile_creates_reconciliation_rows_with_delta(self, staff_client, item):
        client, user = staff_client
        payload = {
            "rows": [
                {
                    "item_id": str(item.id),
                    "actual_count": 15,
                    "reason": "lost",
                    "skip_reorder": True,
                }
            ]
        }
        response = client.post(BATCH_URL, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        recs = StockReconciliation.objects.filter(item=item)
        assert recs.count() == 1
        rec = recs.first()
        assert rec.projected_count == 20
        assert rec.actual_count == 15
        assert rec.delta == -5
        assert rec.reason == "lost"
        assert rec.reconciled_by_id == user.id

    def test_auto_reorder_on_low_stock(self, staff_client, low_item):
        client, _ = staff_client
        payload = {
            "rows": [
                {
                    "item_id": str(low_item.id),
                    "actual_count": 3,  # <= minimum_stock (10)
                    "reason": "used_without_scan",
                }
            ]
        }
        response = client.post(BATCH_URL, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["reorders_created"] == 1
        reorder = ReorderRequest.objects.filter(item=low_item).first()
        assert reorder is not None
        assert reorder.quantity == low_item.reorder_quantity
        rec = StockReconciliation.objects.filter(item=low_item).first()
        assert rec.triggered_reorder_id == reorder.id

    def test_skip_reorder_checkbox_honored(self, staff_client, low_item):
        client, _ = staff_client
        payload = {
            "rows": [
                {
                    "item_id": str(low_item.id),
                    "actual_count": 2,
                    "reason": "used_without_scan",
                    "skip_reorder": True,
                }
            ]
        }
        response = client.post(BATCH_URL, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["reorders_created"] == 0
        assert not ReorderRequest.objects.filter(item=low_item).exists()
        rec = StockReconciliation.objects.filter(item=low_item).first()
        assert rec.triggered_reorder is None

    def test_non_admin_for_item_rejected_403(self, item):
        regular = _make_user()
        client = APIClient()
        client.force_authenticate(user=regular)
        payload = {
            "rows": [
                {
                    "item_id": str(item.id),
                    "actual_count": 15,
                    "reason": "miscounted",
                    "skip_reorder": True,
                }
            ]
        }
        response = client.post(BATCH_URL, payload, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        item.refresh_from_db()
        assert item.current_stock == 20  # unchanged

    def test_sig_admin_of_item_group_allowed(self, item):
        group = Group.objects.create(name="Woodshop SIG")
        item.owning_group = group
        item.save(update_fields=["owning_group"])
        sig_admin = _make_user()
        SIGAdmin.objects.create(user=sig_admin, group=group, is_active=True)
        client = APIClient()
        client.force_authenticate(user=sig_admin)
        payload = {
            "rows": [
                {
                    "item_id": str(item.id),
                    "actual_count": 18,
                    "reason": "miscounted",
                    "skip_reorder": True,
                }
            ]
        }
        response = client.post(BATCH_URL, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        item.refresh_from_db()
        assert item.current_stock == 18

    def test_staff_allowed_for_any_item(self, staff_client, item):
        client, _ = staff_client
        # item with an owning_group staff is NOT a SIG admin of
        group = Group.objects.create(name="Some SIG")
        item.owning_group = group
        item.save(update_fields=["owning_group"])
        payload = {
            "rows": [
                {
                    "item_id": str(item.id),
                    "actual_count": 19,
                    "reason": "miscounted",
                    "skip_reorder": True,
                }
            ]
        }
        response = client.post(BATCH_URL, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED

    def test_partial_failure_rolls_back_batch(self, staff_client, item, low_item):
        """If any row in a batch fails permission, the whole batch is rejected."""
        client, _ = staff_client
        # Make one item SIG-owned and assign a different user as the caller
        # (without SIG admin) to force a mixed-permission batch.
        group = Group.objects.create(name="Restricted SIG")
        low_item.owning_group = group
        low_item.save(update_fields=["owning_group"])

        regular = _make_user()
        # Grant SIG admin on one item only — the other row must fail.
        # Use a regular user with SIG admin on `low_item` but no permission on `item`.
        SIGAdmin.objects.create(user=regular, group=group, is_active=True)
        client = APIClient()
        client.force_authenticate(user=regular)

        payload = {
            "rows": [
                {
                    "item_id": str(low_item.id),
                    "actual_count": 1,
                    "reason": "used_without_scan",
                    "skip_reorder": True,
                },
                {
                    "item_id": str(item.id),  # not SIG admin for this one
                    "actual_count": 5,
                    "reason": "miscounted",
                    "skip_reorder": True,
                },
            ]
        }
        response = client.post(BATCH_URL, payload, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        item.refresh_from_db()
        low_item.refresh_from_db()
        assert item.current_stock == 20  # unchanged
        assert low_item.current_stock == 20  # unchanged
        assert StockReconciliation.objects.count() == 0


class TestLocationGrid:
    def test_location_grid_returns_items(self, staff_client, location):
        InventoryItemFactory.create_batch(3, location=location)
        InventoryItemFactory()  # different location
        client, _ = staff_client
        url = reverse("inventory-location-reconcile-grid", kwargs={"location_id": location.pk})
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["location_id"] == str(location.pk)
        assert len(response.data["items"]) == 3
        first = response.data["items"][0]
        assert "projected" in first
        assert "minimum_stock" in first
        assert "reorder_quantity" in first


class TestReconciliationList:
    def test_list_reconciliations(self, staff_client, item):
        client, user = staff_client
        StockReconciliation.objects.create(
            item=item,
            projected_count=20,
            actual_count=18,
            delta=-2,
            reason="miscounted",
            reconciled_by=user,
        )
        response = client.get(LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["delta"] == -2
