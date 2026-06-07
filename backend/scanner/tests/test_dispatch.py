"""End-to-end tests for the scanner dispatch endpoint.

Covers each resolution path:
  - URL-shaped QR codes for inventory_item / asset / location / fixture / donation_item
  - UPC-A (12) / EAN-13 (13) matching against ItemSupplier.unit_upc and .package_upc
  - 6-char Location.access_code
  - Direct Asset.asset_tag exact match
  - Unknown payloads (empty, gibberish, wrong-shape URL)
"""

from django.urls import reverse

import pytest
from rest_framework import status

from inventory.tests.factories import (
    AssetFactory,
    FixtureFactory,
    InventoryItemFactory,
    ItemSupplierFactory,
    LocationFactory,
)

pytestmark = pytest.mark.django_db


def _dispatch(api_client, payload):
    return api_client.post(
        reverse("scanner:dispatch"),
        data={"payload": payload},
        format="json",
    )


@pytest.mark.integration
class TestScannerDispatch:
    def test_empty_payload_rejected(self, api_client):
        url = reverse("scanner:dispatch")
        response = api_client.post(url, data={"payload": ""}, format="json")
        # serializer disallows blank
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_payload_field(self, api_client):
        url = reverse("scanner:dispatch")
        response = api_client.post(url, data={}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_qr_inventory_item_reorder(self, api_client):
        item = InventoryItemFactory(current_stock=3)
        response = _dispatch(api_client, f"https://oms.example.com/inventory/scan/{item.id}")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "inventory_reorder"
        assert response.data["target_type"] == "inventory_item"
        assert response.data["target_id"] == str(item.id)
        assert response.data["current_stock"] == 3

    def test_qr_bare_path_inventory_item(self, api_client):
        # Path-only payload (no scheme) — should still parse.
        item = InventoryItemFactory()
        response = _dispatch(api_client, f"/inventory/scan/{item.id}")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "inventory_reorder"
        assert response.data["target_id"] == str(item.id)

    def test_qr_asset_checkin(self, api_client):
        asset = AssetFactory()
        response = _dispatch(api_client, f"https://oms.example.com/inventory/scan/asset/{asset.id}")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "asset_checkin"
        assert response.data["target_type"] == "asset"
        assert response.data["target_id"] == str(asset.id)

    def test_qr_location_checkin(self, api_client):
        loc = LocationFactory()
        response = _dispatch(
            api_client, f"https://oms.example.com/inventory/scan/location/{loc.id}"
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "location_checkin"
        assert response.data["target_type"] == "location"
        assert response.data["target_id"] == str(loc.id)

    def test_qr_fixture(self, api_client):
        fixture = FixtureFactory()
        response = _dispatch(
            api_client, f"https://oms.example.com/inventory/scan/fixture/{fixture.id}"
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "fixture"
        assert response.data["target_type"] == "fixture"
        assert response.data["target_id"] == str(fixture.id)

    def test_upc_unit_match_receives_single(self, api_client):
        item = InventoryItemFactory(current_stock=5)
        link = ItemSupplierFactory(item=item, unit_upc="012345678905", package_upc="")
        response = _dispatch(api_client, "012345678905")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "inventory_receive"
        assert response.data["item_supplier_id"] == link.pk
        assert response.data["item_id"] == str(item.id)
        assert response.data["is_package"] is False
        assert response.data["current_stock"] == 5

    def test_upc_package_match_marks_is_package(self, api_client):
        item = InventoryItemFactory()
        link = ItemSupplierFactory(item=item, unit_upc="", package_upc="1234567890128")
        response = _dispatch(api_client, "1234567890128")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "inventory_receive"
        assert response.data["item_supplier_id"] == link.pk
        assert response.data["is_package"] is True

    def test_upc_unmatched_returns_unknown_with_hint(self, api_client):
        response = _dispatch(api_client, "999999999993")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "unknown"
        assert "UPC" in response.data["message"]

    def test_location_access_code(self, api_client):
        loc = LocationFactory(access_code="A2B3C4")
        response = _dispatch(api_client, "A2B3C4")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "location_checkin"
        assert response.data["target_id"] == str(loc.id)

    def test_asset_tag_exact_match(self, api_client):
        asset = AssetFactory(asset_tag="OMS-LATHE-001")
        response = _dispatch(api_client, "OMS-LATHE-001")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "asset_checkin"
        assert response.data["target_id"] == str(asset.id)

    def test_unknown_payload(self, api_client):
        response = _dispatch(api_client, "this-is-not-anything")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "unknown"
        assert response.data["raw_payload"] == "this-is-not-anything"

    def test_url_with_unknown_type_returns_unknown(self, api_client):
        response = _dispatch(api_client, "https://oms.example.com/inventory/scan/blarg/abc-123")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "unknown"

    def test_anonymous_access_allowed(self, api_client):
        # No auth header set on api_client by default — endpoint must be
        # reachable without JWT for kiosk mode.
        item = InventoryItemFactory()
        response = _dispatch(api_client, f"/inventory/scan/{item.id}")
        assert response.status_code == status.HTTP_200_OK

    # ------------------------------------------------------------------
    # Project storage stints (PS-XXXXXXXX)
    # ------------------------------------------------------------------

    def test_project_storage_stint_resolved(self, api_client):
        from project_storage.tests.factories import ProjectStorageStintFactory

        stint = ProjectStorageStintFactory(
            stint_id="PS-AB23CDFG",
            username="alice",
            first_name="Alice",
            last_name="A",
        )
        response = _dispatch(api_client, "PS-AB23CDFG")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "project_storage_stint"
        assert response.data["target_type"] == "project_storage_stint"
        assert response.data["target_id"] == stint.stint_id
        assert response.data["target_name"] == "Alice A"
        assert response.data["target_url"] == f"/facilities/project-storage/{stint.stint_id}"

    def test_project_storage_stint_lowercase_resolved(self, api_client):
        # The stint_id alphabet is uppercase; the resolver normalizes
        # before matching so a wedge scanner that down-cases the QR
        # text still routes correctly.
        from project_storage.tests.factories import ProjectStorageStintFactory

        ProjectStorageStintFactory(stint_id="PS-AB23CDFG")
        response = _dispatch(api_client, "ps-ab23cdfg")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "project_storage_stint"

    def test_project_storage_unknown_stint_falls_through(self, api_client):
        # PS-format string but no row by that id — caller gets the
        # generic "unknown" so the dispatcher doesn't half-confirm
        # something that isn't real.
        response = _dispatch(api_client, "PS-99999999")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "unknown"

    def test_ps_prefixed_asset_tag_still_routes_to_stint_format_first(self, api_client):
        # An asset_tag that happens to look like "PS-NOTREAL" is NOT a
        # stint — should fall through to asset-tag matching. The PS-
        # format regex is strict enough (8 chars from the Crockford
        # alphabet) that a free-text tag is unlikely to match by accident.
        asset = AssetFactory(asset_tag="PS-LATHE")
        response = _dispatch(api_client, "PS-LATHE")
        # "PS-LATHE" has only 5 chars after the prefix, so the regex
        # rejects it and we fall through to asset-tag.
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "asset_checkin"
        assert response.data["target_id"] == str(asset.id)
