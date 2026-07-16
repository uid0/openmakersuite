"""
Tests for the AssetViewSet ``consumable_for_item`` list filter (op-sk0s).

A serialized component installs only into the assets its inventory item is a
consumable/part for (via the AssetPart through-model) — e.g. an ink cartridge
only into its printers. The install picker scopes itself with
``?consumable_for_item=<item id>``; this covers the backend contract behind it.
"""

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from inventory.tests.factories import (
    AssetFactory,
    AssetPartFactory,
    InventoryItemFactory,
)

User = get_user_model()
pytestmark = pytest.mark.django_db

ASSETS_URL = "/api/inventory/assets/"


def _client():
    """A plain authenticated user — LIST ownership policy shows them all assets,
    so the filter (not visibility scoping) is what's under test."""
    user = User.objects.create_user(username="picker", email="p@x.test", password="x")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _ids(response):
    data = response.data
    results = data["results"] if isinstance(data, dict) and "results" in data else data
    return [str(row["id"]) for row in results]


class TestConsumableForItemFilter:
    def test_returns_only_assets_the_item_is_a_part_for(self):
        """?consumable_for_item=<item> → only assets linked to that item via
        AssetPart; an asset without the part is absent."""
        item = InventoryItemFactory()
        printer = AssetFactory(name="UV Printer")
        AssetPartFactory(asset=printer, part=item)
        unrelated = AssetFactory(name="Table Saw")  # no AssetPart for `item`

        resp = _client().get(ASSETS_URL, {"consumable_for_item": str(item.id)})

        assert resp.status_code == 200, resp.content
        ids = _ids(resp)
        assert str(printer.id) in ids
        assert str(unrelated.id) not in ids

    def test_excludes_assets_that_are_a_part_for_a_different_item(self):
        """An asset linked to a *different* item's part is not returned."""
        item = InventoryItemFactory()
        other_item = InventoryItemFactory()
        printer = AssetFactory(name="Printer")
        AssetPartFactory(asset=printer, part=item)
        saw = AssetFactory(name="Saw")
        AssetPartFactory(asset=saw, part=other_item)

        ids = _ids(_client().get(ASSETS_URL, {"consumable_for_item": str(item.id)}))

        assert str(printer.id) in ids
        assert str(saw.id) not in ids

    def test_distinct_no_duplicate_rows(self):
        """An asset that uses the item (and also has other parts) appears exactly
        once — the join doesn't multiply it (.distinct())."""
        item = InventoryItemFactory()
        other_item = InventoryItemFactory()
        printer = AssetFactory(name="Printer")
        AssetPartFactory(asset=printer, part=item)
        AssetPartFactory(asset=printer, part=other_item)  # extra part, same asset

        ids = _ids(_client().get(ASSETS_URL, {"consumable_for_item": str(item.id)}))

        assert ids.count(str(printer.id)) == 1

    def test_blank_value_is_unfiltered(self):
        """A blank value is ignored — the list is not filtered by compatibility."""
        item = InventoryItemFactory()
        printer = AssetFactory(name="Printer")
        AssetPartFactory(asset=printer, part=item)
        unrelated = AssetFactory(name="Saw")

        ids = _ids(_client().get(ASSETS_URL, {"consumable_for_item": ""}))

        # Both assets are present — the filter did not apply.
        assert str(printer.id) in ids
        assert str(unrelated.id) in ids

    def test_garbage_value_is_ignored_not_a_500(self):
        """A non-UUID value is ignored gracefully (unfiltered), not a server error."""
        item = InventoryItemFactory()
        printer = AssetFactory(name="Printer")
        AssetPartFactory(asset=printer, part=item)
        unrelated = AssetFactory(name="Saw")

        resp = _client().get(ASSETS_URL, {"consumable_for_item": "not-a-uuid"})

        assert resp.status_code == 200, resp.content
        ids = _ids(resp)
        assert str(printer.id) in ids
        assert str(unrelated.id) in ids
