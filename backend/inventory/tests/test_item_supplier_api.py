"""The write half of ``/inventory/item-suppliers/``, as the inventory item form drives it.

The relationship editor on the item form used to save nothing at all — the page
carried a ``TODO: Implement supplier relationship saving via ItemSupplier API``
where the writes belong. Nothing on the server was missing;
:class:`~inventory.views.ItemSupplierViewSet` is a full ``ModelViewSet``. These
tests pin down the three properties the form now depends on, through the real
endpoint rather than the model:

* create, update and delete are available to an authenticated caller;
* a PATCH that names only the fields the form offers leaves the rest alone —
  which is why the client patches instead of putting;
* "only one primary" is the *server's* invariant, so the form expresses "make
  this one primary" as a single request rather than as a promote/demote pair
  that could half-land.
"""

from decimal import Decimal

from django.urls import reverse

import pytest

from inventory.models import ItemSupplier
from inventory.tests.factories import (
    InventoryItemFactory,
    ItemSupplierFactory,
    SupplierFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def item():
    return InventoryItemFactory(image=None)


def detail_url(item_supplier):
    return reverse("itemsupplier-detail", args=[item_supplier.pk])


class TestItemSupplierWrites:
    """The requests the inventory item form issues for its relationship editor."""

    def test_create_links_a_supplier_to_the_item(self, authenticated_client, item):
        client, _ = authenticated_client
        supplier = SupplierFactory()

        response = client.post(
            reverse("itemsupplier-list"),
            {
                "item": str(item.pk),
                "supplier": supplier.pk,
                "supplier_sku": "SKU-1",
                "supplier_url": "https://example.com/sku-1",
                "unit_cost": None,
                "package_cost": "24.00",
                "quantity_per_package": 12,
                "average_lead_time": 3,
                "is_primary": True,
            },
            format="json",
        )

        assert response.status_code == 201
        created = ItemSupplier.objects.get(pk=response.data["id"])
        assert created.item_id == item.pk
        assert created.supplier_id == supplier.pk
        assert created.supplier_sku == "SKU-1"
        assert created.quantity_per_package == 12
        assert created.average_lead_time == 3
        assert created.package_cost == Decimal("24.00")
        assert created.is_primary is True

    def test_patch_persists_every_field_the_form_offers(self, authenticated_client, item):
        client, _ = authenticated_client
        link = ItemSupplierFactory(item=item, supplier=SupplierFactory())

        response = client.patch(
            detail_url(link),
            {
                "supplier_sku": "SKU-EDITED",
                "supplier_url": "https://example.com/edited",
                "package_cost": "36.00",
                "quantity_per_package": 6,
                "average_lead_time": 21,
            },
            format="json",
        )

        assert response.status_code == 200
        link.refresh_from_db()
        assert link.supplier_sku == "SKU-EDITED"
        assert link.supplier_url == "https://example.com/edited"
        assert link.package_cost == Decimal("36.00")
        assert link.quantity_per_package == 6
        assert link.average_lead_time == 21

    def test_patch_leaves_the_fields_the_form_does_not_offer(self, authenticated_client, item):
        """The item form shows no UPCs, dimensions, notes or flags — a PUT would blank them."""
        client, _ = authenticated_client
        link = ItemSupplierFactory(
            item=item,
            supplier=SupplierFactory(),
            package_upc="111111111111",
            unit_upc="222222222222",
            package_weight=Decimal("2.500"),
            notes="Ask for the loading dock.",
            is_active=True,
        )

        response = client.patch(detail_url(link), {"supplier_sku": "SKU-EDITED"}, format="json")

        assert response.status_code == 200
        link.refresh_from_db()
        assert link.package_upc == "111111111111"
        assert link.unit_upc == "222222222222"
        assert link.package_weight == Decimal("2.500")
        assert link.notes == "Ask for the loading dock."
        assert link.is_active is True

    def test_delete_removes_the_link(self, authenticated_client, item):
        client, _ = authenticated_client
        link = ItemSupplierFactory(item=item, supplier=SupplierFactory())

        response = client.delete(detail_url(link))

        assert response.status_code == 204
        assert not ItemSupplier.objects.filter(pk=link.pk).exists()

    def test_blank_sku_is_refused_with_a_reason(self, authenticated_client, item):
        """Why the form checks the SKU itself: the endpoint will not store a blank one."""
        client, _ = authenticated_client

        response = client.post(
            reverse("itemsupplier-list"),
            {
                "item": str(item.pk),
                "supplier": SupplierFactory().pk,
                "supplier_sku": "",
                "quantity_per_package": 1,
                "average_lead_time": 0,
                "is_primary": True,
            },
            format="json",
        )

        assert response.status_code == 400
        # Wrapped by the standardized envelope (`config.api_errors`), so the
        # field reason lives under `details` rather than at the top level — the
        # web client has to read it there to tell the operator anything useful.
        assert response.data["error"]["details"]["supplier_sku"] == ["This field may not be blank."]


class TestSinglePrimaryIsServerOwned:
    """Promoting a supplier is one request, because the server does the demoting."""

    def test_patching_is_primary_demotes_the_previous_primary(self, authenticated_client, item):
        client, _ = authenticated_client
        was_primary = ItemSupplierFactory(item=item, supplier=SupplierFactory(), is_primary=True)
        challenger = ItemSupplierFactory(item=item, supplier=SupplierFactory(), is_primary=False)

        response = client.patch(detail_url(challenger), {"is_primary": True}, format="json")

        assert response.status_code == 200
        was_primary.refresh_from_db()
        challenger.refresh_from_db()
        assert challenger.is_primary is True
        assert was_primary.is_primary is False
        assert item.item_suppliers.filter(is_primary=True).count() == 1

    def test_posting_a_primary_demotes_the_previous_primary(self, authenticated_client, item):
        client, _ = authenticated_client
        was_primary = ItemSupplierFactory(item=item, supplier=SupplierFactory(), is_primary=True)

        response = client.post(
            reverse("itemsupplier-list"),
            {
                "item": str(item.pk),
                "supplier": SupplierFactory().pk,
                "supplier_sku": "SKU-NEW",
                "quantity_per_package": 1,
                "average_lead_time": 0,
                "is_primary": True,
            },
            format="json",
        )

        assert response.status_code == 201
        was_primary.refresh_from_db()
        assert was_primary.is_primary is False
        assert item.item_suppliers.filter(is_primary=True).count() == 1

    def test_demotion_is_scoped_to_the_one_item(self, authenticated_client, item):
        client, _ = authenticated_client
        other_item = InventoryItemFactory(image=None)
        elsewhere = ItemSupplierFactory(
            item=other_item, supplier=SupplierFactory(), is_primary=True
        )
        ItemSupplierFactory(item=item, supplier=SupplierFactory(), is_primary=True)
        challenger = ItemSupplierFactory(item=item, supplier=SupplierFactory(), is_primary=False)

        client.patch(detail_url(challenger), {"is_primary": True}, format="json")

        elsewhere.refresh_from_db()
        assert elsewhere.is_primary is True
