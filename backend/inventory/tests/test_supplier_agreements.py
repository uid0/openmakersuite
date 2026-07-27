"""Per-supplier purchase/pricing agreements (op-yoos).

Some suppliers give the makerspace contract pricing, a standing quote or a
nonprofit discount. ``SupplierAgreement`` records that paperwork against the
supplier — name, terms notes and an optional document — and a purchase order
can point at the agreement it was placed under.

What this file pins down:

* the model itself (fields, ordering, ``__str__``, cascade off the supplier);
* the serializer's field set;
* the ``inventory/supplier-agreements/`` endpoint, its ``?supplier=`` and
  ``?is_active=`` filters, and its read-open/write-authenticated gate;
* creating a purchase order **with** an agreement sets the FK and surfaces it
  on the read serializer — and an agreement from a *different* supplier is
  rejected.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import SupplierAgreement
from inventory.serializers import SupplierAgreementSerializer
from inventory.tests.factories import (
    ItemSupplierFactory,
    SupplierAgreementFactory,
    SupplierFactory,
)
from reorder_queue.models import PurchaseOrder
from reorder_queue.serializers import PurchaseOrderSerializer
from reorder_queue.views import PurchaseOrderViewSet

User = get_user_model()

pytestmark = pytest.mark.django_db


AGREEMENTS_URL = "/api/inventory/supplier-agreements/"


@pytest.fixture(autouse=True)
def _isolated_media(settings, tmp_path):
    """Keep uploaded test files out of the tracked backend/media tree."""
    settings.MEDIA_ROOT = str(tmp_path)


def _authed_client():
    user = User.objects.create_user(username="buyer", password="x")
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


# ─────────────────────────────────────────────────────────────────────────────
# Model
# ─────────────────────────────────────────────────────────────────────────────
class TestSupplierAgreementModel:
    def test_create_with_ians_default_fields(self):
        """Name + notes + optional document is the field default Ian asked for."""
        supplier = SupplierFactory(name="Acme Supply")
        agreement = SupplierAgreement.objects.create(
            supplier=supplier,
            name="2026 nonprofit pricing",
            notes="15% off list, net 30.",
        )

        agreement.refresh_from_db()
        assert agreement.supplier == supplier
        assert agreement.name == "2026 nonprofit pricing"
        assert agreement.notes == "15% off list, net 30."
        assert not agreement.document
        assert agreement.is_active is True
        assert agreement.created_at is not None
        assert agreement.updated_at is not None

    def test_document_is_optional_and_stores_a_file(self):
        agreement = SupplierAgreementFactory(
            document=SimpleUploadedFile(
                "quote.pdf", b"%PDF-1.4 fake", content_type="application/pdf"
            )
        )

        agreement.refresh_from_db()
        assert agreement.document
        assert "quote" in agreement.document.name

    def test_str_is_supplier_then_agreement_name(self):
        agreement = SupplierAgreementFactory(
            supplier=SupplierFactory(name="Acme Supply"), name="Standing quote"
        )
        assert str(agreement) == "Acme Supply — Standing quote"

    def test_related_name_and_newest_first_ordering(self):
        supplier = SupplierFactory()
        first = SupplierAgreementFactory(supplier=supplier, name="Older")
        second = SupplierAgreementFactory(supplier=supplier, name="Newer")

        assert list(supplier.agreements.all()) == [second, first]

    def test_deleting_the_supplier_cascades(self):
        supplier = SupplierFactory()
        SupplierAgreementFactory(supplier=supplier)

        supplier.delete()

        assert SupplierAgreement.objects.count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# Serializer
# ─────────────────────────────────────────────────────────────────────────────
class TestSupplierAgreementSerializer:
    def test_exposes_the_agreed_field_set(self):
        agreement = SupplierAgreementFactory(name="Contract pricing")

        data = SupplierAgreementSerializer(agreement).data

        assert set(data) == {
            "id",
            "supplier",
            "supplier_name",
            "name",
            "notes",
            "document",
            "is_active",
            "created_at",
            "updated_at",
        }
        assert data["name"] == "Contract pricing"
        assert data["supplier"] == agreement.supplier_id
        assert data["supplier_name"] == agreement.supplier.name
        assert data["is_active"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────
class TestSupplierAgreementEndpoint:
    def test_list_returns_agreements(self):
        client, _ = _authed_client()
        SupplierAgreementFactory(name="One")
        SupplierAgreementFactory(name="Two")

        response = client.get(AGREEMENTS_URL)

        assert response.status_code == status.HTTP_200_OK
        names = {row["name"] for row in response.data["results"]}
        assert names == {"One", "Two"}

    def test_filters_by_supplier(self):
        client, _ = _authed_client()
        mine = SupplierFactory()
        theirs = SupplierFactory()
        SupplierAgreementFactory(supplier=mine, name="Mine")
        SupplierAgreementFactory(supplier=theirs, name="Theirs")

        response = client.get(AGREEMENTS_URL, {"supplier": mine.id})

        assert response.status_code == status.HTTP_200_OK
        assert [row["name"] for row in response.data["results"]] == ["Mine"]

    def test_filters_by_is_active(self):
        client, _ = _authed_client()
        supplier = SupplierFactory()
        SupplierAgreementFactory(supplier=supplier, name="Live", is_active=True)
        SupplierAgreementFactory(supplier=supplier, name="Retired", is_active=False)

        active = client.get(AGREEMENTS_URL, {"supplier": supplier.id, "is_active": "true"})
        retired = client.get(AGREEMENTS_URL, {"supplier": supplier.id, "is_active": "false"})

        assert [row["name"] for row in active.data["results"]] == ["Live"]
        assert [row["name"] for row in retired.data["results"]] == ["Retired"]

    def test_a_junk_supplier_filter_is_an_empty_page_not_a_500(self):
        client, _ = _authed_client()
        SupplierAgreementFactory()

        response = client.get(AGREEMENTS_URL, {"supplier": "not-an-id"})

        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == []

    def test_create_requires_authentication(self):
        supplier = SupplierFactory()

        response = APIClient().post(
            AGREEMENTS_URL, {"supplier": supplier.id, "name": "Nope"}, format="json"
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert SupplierAgreement.objects.count() == 0

    def test_authenticated_user_can_create(self):
        client, _ = _authed_client()
        supplier = SupplierFactory()

        response = client.post(
            AGREEMENTS_URL,
            {"supplier": supplier.id, "name": "2026 pricing", "notes": "Net 30"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        agreement = SupplierAgreement.objects.get(id=response.data["id"])
        assert agreement.supplier == supplier
        assert agreement.name == "2026 pricing"
        assert agreement.notes == "Net 30"

    def test_can_upload_a_document(self):
        client, _ = _authed_client()
        supplier = SupplierFactory()

        response = client.post(
            AGREEMENTS_URL,
            {
                "supplier": supplier.id,
                "name": "Signed contract",
                "document": SimpleUploadedFile(
                    "contract.pdf", b"%PDF-1.4 fake", content_type="application/pdf"
                ),
            },
            format="multipart",
        )

        assert response.status_code == status.HTTP_201_CREATED
        agreement = SupplierAgreement.objects.get(id=response.data["id"])
        assert agreement.document
        assert "contract" in agreement.document.name

    def test_can_retire_an_agreement(self):
        client, _ = _authed_client()
        agreement = SupplierAgreementFactory(is_active=True)

        response = client.patch(
            f"{AGREEMENTS_URL}{agreement.id}/", {"is_active": False}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        agreement.refresh_from_db()
        assert agreement.is_active is False


# ─────────────────────────────────────────────────────────────────────────────
# Purchase-order wiring
# ─────────────────────────────────────────────────────────────────────────────
class TestPurchaseOrderAgreement:
    def _po_payload(self, supplier, **extra):
        item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)
        payload = {
            "supplier": supplier.id,
            "items": [{"item_supplier_id": item_supplier.id, "quantity": 2}],
        }
        payload.update(extra)
        return payload

    def test_agreement_is_optional_on_create(self):
        client, _ = _authed_client()
        supplier = SupplierFactory()

        response = client.post(
            "/api/reorders/purchase-orders/", self._po_payload(supplier), format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["supplier_agreement"] is None
        assert response.data["supplier_agreement_details"] is None
        assert PurchaseOrder.objects.get(id=response.data["id"]).supplier_agreement is None

    def test_creating_a_po_with_an_agreement_sets_the_fk(self):
        client, _ = _authed_client()
        supplier = SupplierFactory()
        agreement = SupplierAgreementFactory(supplier=supplier, name="2026 pricing")

        response = client.post(
            "/api/reorders/purchase-orders/",
            self._po_payload(supplier, supplier_agreement=agreement.id),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        purchase_order = PurchaseOrder.objects.get(id=response.data["id"])
        assert purchase_order.supplier_agreement == agreement
        assert agreement.purchase_orders.get() == purchase_order

    def test_read_serializer_surfaces_the_agreement_for_display(self):
        client, _ = _authed_client()
        supplier = SupplierFactory()
        agreement = SupplierAgreementFactory(supplier=supplier, name="2026 pricing")

        created = client.post(
            "/api/reorders/purchase-orders/",
            self._po_payload(supplier, supplier_agreement=agreement.id),
            format="json",
        )
        detail = client.get(f"/api/reorders/purchase-orders/{created.data['id']}/")

        assert detail.status_code == status.HTTP_200_OK
        assert detail.data["supplier_agreement"] == agreement.id
        assert detail.data["supplier_agreement_details"] == {
            "id": agreement.id,
            "name": "2026 pricing",
        }

    def test_rejects_an_agreement_belonging_to_another_supplier(self):
        client, _ = _authed_client()
        supplier = SupplierFactory()
        other_agreement = SupplierAgreementFactory(supplier=SupplierFactory())

        response = client.post(
            "/api/reorders/purchase-orders/",
            self._po_payload(supplier, supplier_agreement=other_agreement.id),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert PurchaseOrder.objects.count() == 0

    def test_can_attach_an_agreement_after_the_fact(self):
        """The order was placed before the paperwork was filed."""
        client, _ = _authed_client()
        supplier = SupplierFactory()
        agreement = SupplierAgreementFactory(supplier=supplier, name="Backdated quote")
        created = client.post(
            "/api/reorders/purchase-orders/", self._po_payload(supplier), format="json"
        )

        response = client.patch(
            f"/api/reorders/purchase-orders/{created.data['id']}/",
            {"supplier_agreement": agreement.id},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["supplier_agreement_details"] == {
            "id": agreement.id,
            "name": "Backdated quote",
        }
        assert PurchaseOrder.objects.get(id=created.data["id"]).supplier_agreement == agreement

    def test_patching_in_another_suppliers_agreement_is_rejected(self):
        client, _ = _authed_client()
        supplier = SupplierFactory()
        other_agreement = SupplierAgreementFactory(supplier=SupplierFactory())
        created = client.post(
            "/api/reorders/purchase-orders/", self._po_payload(supplier), format="json"
        )

        response = client.patch(
            f"/api/reorders/purchase-orders/{created.data['id']}/",
            {"supplier_agreement": other_agreement.id},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert PurchaseOrder.objects.get(id=created.data["id"]).supplier_agreement is None

    def test_moving_the_order_to_another_supplier_cannot_strand_the_agreement(self):
        """Changing only ``supplier`` must not leave a foreign agreement attached."""
        client, _ = _authed_client()
        supplier = SupplierFactory()
        agreement = SupplierAgreementFactory(supplier=supplier)
        other_supplier = SupplierFactory()
        created = client.post(
            "/api/reorders/purchase-orders/",
            self._po_payload(supplier, supplier_agreement=agreement.id),
            format="json",
        )

        response = client.patch(
            f"/api/reorders/purchase-orders/{created.data['id']}/",
            {"supplier": other_supplier.id},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        purchase_order = PurchaseOrder.objects.get(id=created.data["id"])
        assert purchase_order.supplier == supplier
        assert purchase_order.supplier_agreement == agreement

    def test_moving_suppliers_is_allowed_when_the_agreement_is_cleared_too(self):
        client, _ = _authed_client()
        supplier = SupplierFactory()
        agreement = SupplierAgreementFactory(supplier=supplier)
        other_supplier = SupplierFactory()
        created = client.post(
            "/api/reorders/purchase-orders/",
            self._po_payload(supplier, supplier_agreement=agreement.id),
            format="json",
        )

        response = client.patch(
            f"/api/reorders/purchase-orders/{created.data['id']}/",
            {"supplier": other_supplier.id, "supplier_agreement": None},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        purchase_order = PurchaseOrder.objects.get(id=created.data["id"])
        assert purchase_order.supplier == other_supplier
        assert purchase_order.supplier_agreement is None

    def test_editing_an_unrelated_field_on_an_agreement_order_still_works(self):
        """The stricter guard must not block ordinary edits."""
        client, _ = _authed_client()
        supplier = SupplierFactory()
        agreement = SupplierAgreementFactory(supplier=supplier)
        created = client.post(
            "/api/reorders/purchase-orders/",
            self._po_payload(supplier, supplier_agreement=agreement.id),
            format="json",
        )

        response = client.patch(
            f"/api/reorders/purchase-orders/{created.data['id']}/",
            {"sales_order_number": "SO-123"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        purchase_order = PurchaseOrder.objects.get(id=created.data["id"])
        assert purchase_order.sales_order_number == "SO-123"
        assert purchase_order.supplier_agreement == agreement

    def test_agreement_details_cost_no_extra_query_per_order(self):
        """The viewset's select_related feeds the display field (no N+1)."""
        client, _ = _authed_client()
        for _ in range(5):
            supplier = SupplierFactory()
            client.post(
                "/api/reorders/purchase-orders/",
                self._po_payload(
                    supplier,
                    supplier_agreement=SupplierAgreementFactory(supplier=supplier).id,
                ),
                format="json",
            )

        serializer = PurchaseOrderSerializer()
        orders = list(PurchaseOrderViewSet.queryset.all())
        with CaptureQueriesContext(connection) as ctx:
            details = [serializer.get_supplier_agreement_details(order) for order in orders]

        assert len(details) == 5
        assert all(entry is not None for entry in details)
        assert ctx.captured_queries == []

    def test_deleting_the_agreement_leaves_the_po_and_nulls_the_link(self):
        """SET_NULL — purchasing history must survive retiring the paperwork."""
        client, _ = _authed_client()
        supplier = SupplierFactory()
        agreement = SupplierAgreementFactory(supplier=supplier)

        created = client.post(
            "/api/reorders/purchase-orders/",
            self._po_payload(supplier, supplier_agreement=agreement.id),
            format="json",
        )
        agreement.delete()

        purchase_order = PurchaseOrder.objects.get(id=created.data["id"])
        assert purchase_order.supplier_agreement is None
