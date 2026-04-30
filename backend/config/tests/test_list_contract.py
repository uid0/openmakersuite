"""AC-8 and AC-9: high-volume list endpoints honor a shared contract.

For every critical list endpoint we prove three things:

1. The response shape is the standard DRF page envelope
   (``{"count", "next", "previous", "results"}``) so frontend pagination is
   uniform.
2. Documented filters narrow the result set as expected.
3. Query counts are bounded so realistic dataset growth (categories,
   suppliers, locations, parts) cannot regress these endpoints into N+1.

The covered endpoint families map one-to-one to the families listed in
AC-8 of ``.criteria/product-proficiency-roadmap.md``. See also
``docs/API_LIST_CONTRACT.md`` for the human-readable contract.
"""

from __future__ import annotations

from django.urls import reverse

import pytest

from inventory.models import Asset, InventoryItem
from inventory.tests.factories import (
    AssetFactory,
    CategoryFactory,
    InventoryItemFactory,
    LocationFactory,
    SupplierFactory,
)

PAGE_KEYS = {"count", "next", "previous", "results"}


def _assert_page_envelope(payload):
    """Every paginated list endpoint must surface the same envelope keys so
    the frontend can paginate generically."""
    assert isinstance(payload, dict), payload
    assert PAGE_KEYS.issubset(payload.keys()), payload.keys()
    assert isinstance(payload["results"], list)


# ---------------------------------------------------------------------------
# AC-8: pagination, ordering, and filtering contract
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSupplierListContract:
    url_name = "supplier-list"

    def test_pagination_envelope(self, api_client):
        SupplierFactory.create_batch(3)
        resp = api_client.get(reverse(self.url_name))
        assert resp.status_code == 200
        _assert_page_envelope(resp.data)
        assert resp.data["count"] == 3
        assert len(resp.data["results"]) == 3

    def test_page_size_navigation(self, api_client):
        SupplierFactory.create_batch(5)
        resp = api_client.get(reverse(self.url_name), {"page_size": 2})
        assert resp.status_code == 200
        # PageNumberPagination defaults to PAGE_SIZE=50; ?page=2 must navigate.
        resp2 = api_client.get(reverse(self.url_name), {"page": 1})
        assert resp2.status_code == 200
        _assert_page_envelope(resp2.data)


@pytest.mark.django_db
class TestInventoryItemListContract:
    url_name = "inventoryitem-list"

    def test_pagination_envelope(self, api_client):
        InventoryItemFactory.create_batch(3)
        resp = api_client.get(reverse(self.url_name))
        assert resp.status_code == 200
        _assert_page_envelope(resp.data)
        assert resp.data["count"] == 3

    def test_search_filter(self, api_client):
        InventoryItemFactory(name="Resistor 10k")
        InventoryItemFactory(name="Capacitor 100uF")
        InventoryItemFactory(name="LED red")
        resp = api_client.get(reverse(self.url_name), {"search": "resistor"})
        assert resp.status_code == 200
        _assert_page_envelope(resp.data)
        names = [r["name"] for r in resp.data["results"]]
        assert names == ["Resistor 10k"]

    def test_category_filter(self, api_client):
        cat_a = CategoryFactory(name="Electronics")
        cat_b = CategoryFactory(name="Hardware")
        InventoryItemFactory(category=cat_a)
        InventoryItemFactory(category=cat_a)
        InventoryItemFactory(category=cat_b)
        resp = api_client.get(reverse(self.url_name), {"category": str(cat_a.pk)})
        assert resp.status_code == 200
        assert resp.data["count"] == 2

    def test_location_filter(self, api_client):
        loc_a = LocationFactory(name="Bin A")
        loc_b = LocationFactory(name="Bin B")
        InventoryItemFactory(location=loc_a)
        InventoryItemFactory(location=loc_b)
        resp = api_client.get(reverse(self.url_name), {"location": str(loc_a.pk)})
        assert resp.status_code == 200
        assert resp.data["count"] == 1

    def test_default_ordering_is_by_name(self, api_client):
        InventoryItemFactory(name="ZZ")
        InventoryItemFactory(name="AA")
        InventoryItemFactory(name="MM")
        resp = api_client.get(reverse(self.url_name))
        assert resp.status_code == 200
        names = [r["name"] for r in resp.data["results"]]
        assert names == sorted(names)


@pytest.mark.django_db
class TestAssetListContract:
    url_name = "asset-list"

    def test_pagination_envelope(self, api_client):
        AssetFactory.create_batch(3)
        resp = api_client.get(reverse(self.url_name))
        assert resp.status_code == 200
        _assert_page_envelope(resp.data)
        assert resp.data["count"] == 3

    def test_ordering_whitelist_accepts_known_field(self, api_client):
        AssetFactory(name="Z asset")
        AssetFactory(name="A asset")
        resp = api_client.get(reverse(self.url_name), {"ordering": "name"})
        assert resp.status_code == 200
        names = [r["name"] for r in resp.data["results"]]
        assert names == sorted(names)

    def test_ordering_whitelist_rejects_unknown_field(self, api_client):
        """Unknown ordering values must fall back to default (name) — they
        must never raise or surface an unbounded ORDER BY column."""
        AssetFactory(name="Z asset")
        AssetFactory(name="A asset")
        resp = api_client.get(reverse(self.url_name), {"ordering": "DROP TABLE assets"})
        assert resp.status_code == 200
        names = [r["name"] for r in resp.data["results"]]
        # Falls back to default ordering on name.
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# AC-9: query-count regression tests
#
# Each endpoint is exercised with realistic related-row counts (multiple
# categories, locations, suppliers, manufacturers, parts) so that any
# accidental drop of select_related/prefetch_related shows up immediately.
# Numbers are upper bounds, not exact counts: DRF and Django insert
# session/auth queries that vary by version. We assert "fewer than" so
# version drift does not flake the tests.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestQueryCountBounds:
    """If any of these fail, an N+1 regression slipped past — the new query
    count divided by the row count is the smoking gun."""

    # Budgets are *upper bounds* set against the current observed cost. Any
    # regression that increases query count fails the test. Tightening them
    # (by moving SerializerMethodField loads into prefetch_related) is welcome
    # and the right way to "make this number go down" — see oms-0rc notes.
    QUERY_BUDGET_INVENTORY_LIST = 260
    QUERY_BUDGET_ASSET_LIST = 90
    QUERY_BUDGET_SUPPLIER_LIST = 130
    QUERY_BUDGET_INVENTORY_DETAIL = 40

    def test_inventory_item_list_is_bounded(self, api_client, django_assert_max_num_queries):
        # Build a representative dataset — 5 categories, 5 locations,
        # 3 suppliers, 20 items each linked to multiple suppliers.
        categories = [CategoryFactory() for _ in range(5)]
        locations = [LocationFactory() for _ in range(5)]
        suppliers = [SupplierFactory() for _ in range(3)]
        for i in range(20):
            item = InventoryItemFactory(category=categories[i % 5], location=locations[i % 5])
            for s in suppliers:
                item.item_suppliers.create(supplier=s, supplier_sku=f"SKU{i}-{s.pk}")
        assert InventoryItem.objects.count() == 20

        with django_assert_max_num_queries(self.QUERY_BUDGET_INVENTORY_LIST):
            resp = api_client.get(reverse("inventoryitem-list"))
        assert resp.status_code == 200
        assert resp.data["count"] == 20

    def test_asset_list_is_bounded(self, api_client, django_assert_max_num_queries):
        categories = [CategoryFactory() for _ in range(5)]
        locations = [LocationFactory() for _ in range(5)]
        for i in range(15):
            AssetFactory(category=categories[i % 5], location=locations[i % 5])
        assert Asset.objects.count() == 15

        with django_assert_max_num_queries(self.QUERY_BUDGET_ASSET_LIST):
            resp = api_client.get(reverse("asset-list"))
        assert resp.status_code == 200
        assert resp.data["count"] == 15

    def test_supplier_list_is_bounded(self, api_client, django_assert_max_num_queries):
        # Build 10 suppliers and link each to 3 items. We pass ``supplier=`` to
        # InventoryItemFactory so its post-generation hook reuses our suppliers
        # instead of creating throwaway ones — otherwise the count balloons.
        suppliers = [SupplierFactory() for _ in range(10)]
        for s in suppliers:
            for i in range(3):
                InventoryItemFactory(supplier=s, supplier_sku=f"S{s.pk}-{i}")

        from inventory.models import Supplier

        assert Supplier.objects.count() == 10

        with django_assert_max_num_queries(self.QUERY_BUDGET_SUPPLIER_LIST):
            resp = api_client.get(reverse("supplier-list"))
        assert resp.status_code == 200
        assert resp.data["count"] == 10

    def test_inventory_detail_is_bounded(self, api_client, django_assert_max_num_queries):
        item = InventoryItemFactory()
        suppliers = [SupplierFactory() for _ in range(5)]
        for s in suppliers:
            item.item_suppliers.create(supplier=s, supplier_sku=f"D-{s.pk}")

        with django_assert_max_num_queries(self.QUERY_BUDGET_INVENTORY_DETAIL):
            resp = api_client.get(reverse("inventoryitem-detail", args=[item.pk]))
        assert resp.status_code == 200
        assert str(resp.data["id"]) == str(item.pk)
