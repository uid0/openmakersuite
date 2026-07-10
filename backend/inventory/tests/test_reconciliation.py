"""Tests for stock reconciliation API (oms-90k, oms-sig)."""

import csv
import io

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
UPLOAD_URL = "/api/inventory/reconciliations/upload/"


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

    def test_retired_item_not_auto_reordered(self, staff_client, location):
        """Reconciling a retired item below minimum must not auto-create a reorder."""
        client, _ = staff_client
        retired = InventoryItemFactory(
            location=location,
            current_stock=20,
            minimum_stock=10,
            reorder_quantity=15,
            is_retired=True,
        )
        payload = {
            "rows": [
                {
                    "item_id": str(retired.id),
                    "actual_count": 1,  # well under minimum_stock (10)
                    "reason": "used_without_scan",
                }
            ]
        }
        response = client.post(BATCH_URL, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["reorders_created"] == 0
        assert not ReorderRequest.objects.filter(item=retired).exists()
        rec = StockReconciliation.objects.filter(item=retired).first()
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


def _csv_bytes(header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for r in rows:
        writer.writerow(r)
    return buf.getvalue().encode("utf-8")


def _upload(client, csv_bytes, *, partial=False, filename="recon.csv"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    upload = SimpleUploadedFile(filename, csv_bytes, content_type="text/csv")
    url = UPLOAD_URL + ("?partial=true" if partial else "")
    return client.post(url, {"file": upload}, format="multipart")


class TestCsvUpload:
    def test_csv_upload_happy_path(self, staff_client, location):
        client, _ = staff_client
        items = InventoryItemFactory.create_batch(
            10, location=location, current_stock=20, minimum_stock=1
        )
        rows = [
            [str(it.id), "", it.sku, 15 + i, "miscounted", "", "true"] for i, it in enumerate(items)
        ]
        header = [
            "item_id",
            "ignored",
            "sku",
            "actual_count",
            "reason",
            "notes",
            "skip_reorder",
        ]
        response = _upload(client, _csv_bytes(header, rows))
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["created"] == 10
        assert response.data["skipped"] == 0
        assert response.data["errors"] == []
        for i, it in enumerate(items):
            it.refresh_from_db()
            assert it.current_stock == 15 + i
        assert StockReconciliation.objects.count() == 10

    def test_csv_upload_invalid_item_row_reports_error(self, staff_client, location):
        client, _ = staff_client
        good = InventoryItemFactory(location=location, current_stock=20, minimum_stock=1)
        header = ["item_id", "actual_count", "reason", "skip_reorder"]
        rows = [
            [str(good.id), 18, "miscounted", "true"],
            ["00000000-0000-0000-0000-000000000000", 5, "lost", "true"],
        ]
        # default atomic mode — any bad row rolls back
        response = _upload(client, _csv_bytes(header, rows))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["created"] == 0
        assert len(response.data["errors"]) == 1
        assert response.data["errors"][0]["row"] == 3
        good.refresh_from_db()
        assert good.current_stock == 20  # rolled back

    def test_csv_upload_permission_denied_on_unauthorized_item(self, location):
        # Regular user (no staff, no SIG admin) is forbidden.
        regular = _make_user()
        client = APIClient()
        client.force_authenticate(user=regular)
        item = InventoryItemFactory(location=location, current_stock=20, minimum_stock=1)
        header = ["item_id", "actual_count", "reason", "skip_reorder"]
        rows = [[str(item.id), 10, "miscounted", "true"]]
        response = _upload(client, _csv_bytes(header, rows))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["created"] == 0
        assert "Permission denied" in response.data["errors"][0]["error"]
        item.refresh_from_db()
        assert item.current_stock == 20

    def test_csv_export_includes_all_items_at_location(self, staff_client, location):
        client, _ = staff_client
        items = InventoryItemFactory.create_batch(4, location=location)
        InventoryItemFactory()  # different location
        url = reverse(
            "inventory-location-reconcile-export",
            kwargs={"location_id": location.pk},
        )
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK
        body = b"".join(response.streaming_content).decode("utf-8")
        reader = csv.DictReader(io.StringIO(body))
        rows = list(reader)
        assert len(rows) == 4
        sku_set = {r["sku"] for r in rows}
        assert sku_set == {it.sku for it in items}

    def test_csv_export_columns_match_expected(self, staff_client, location):
        client, _ = staff_client
        InventoryItemFactory(location=location, current_stock=7, minimum_stock=2)
        url = reverse(
            "inventory-location-reconcile-export",
            kwargs={"location_id": location.pk},
        )
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK
        body = b"".join(response.streaming_content).decode("utf-8")
        reader = csv.reader(io.StringIO(body))
        header = next(reader)
        assert header == [
            "item_id",
            "sku",
            "name",
            "projected",
            "minimum_stock",
            "actual_count",
            "reason",
            "notes",
            "skip_reorder",
        ]
        first = next(reader)
        # actual_count/reason/notes/skip_reorder left blank for volunteer fill-in
        assert first[3] == "7"
        assert first[4] == "2"
        assert first[5] == ""
        assert first[6] == ""

    def test_partial_mode_allows_bad_rows_to_skip(self, staff_client, location):
        client, _ = staff_client
        good = InventoryItemFactory(location=location, current_stock=20, minimum_stock=1)
        header = ["item_id", "actual_count", "reason", "skip_reorder"]
        rows = [
            [str(good.id), 18, "miscounted", "true"],
            ["00000000-0000-0000-0000-000000000000", 5, "lost", "true"],
            [str(good.id), -3, "miscounted", "true"],  # negative actual_count
            [str(good.id), 17, "not_a_reason", "true"],  # bad reason
        ]
        response = _upload(client, _csv_bytes(header, rows), partial=True)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["created"] == 1
        assert response.data["skipped"] == 3
        err_rows = [e["row"] for e in response.data["errors"]]
        assert err_rows == [3, 4, 5]
        good.refresh_from_db()
        assert good.current_stock == 18  # first row committed

    def test_atomic_mode_rolls_back_entire_batch_on_any_error(self, staff_client, location):
        client, _ = staff_client
        a = InventoryItemFactory(location=location, current_stock=20, minimum_stock=1)
        b = InventoryItemFactory(location=location, current_stock=30, minimum_stock=1)
        header = ["item_id", "actual_count", "reason", "skip_reorder"]
        rows = [
            [str(a.id), 18, "miscounted", "true"],
            [str(b.id), 25, "miscounted", "true"],
            ["not-a-uuid", 0, "miscounted", "true"],
        ]
        response = _upload(client, _csv_bytes(header, rows))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["created"] == 0
        assert response.data["skipped"] == 3
        assert len(response.data["errors"]) == 1
        a.refresh_from_db()
        b.refresh_from_db()
        assert a.current_stock == 20
        assert b.current_stock == 30
        assert StockReconciliation.objects.count() == 0

    def test_csv_upload_sku_fallback_when_item_id_missing(self, staff_client, location):
        # Volunteers may only have sku handy, not UUID. Ensure lookup by sku works.
        client, _ = staff_client
        item = InventoryItemFactory(
            location=location, current_stock=20, minimum_stock=1, sku="ABC-123"
        )
        header = ["sku", "actual_count", "reason", "skip_reorder"]
        rows = [["ABC-123", 16, "miscounted", "true"]]
        response = _upload(client, _csv_bytes(header, rows))
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["created"] == 1
        item.refresh_from_db()
        assert item.current_stock == 16
