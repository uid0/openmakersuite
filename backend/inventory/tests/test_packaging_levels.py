"""Unit-of-measure / packaging-matrix foundation (op-hzji, phase 1).

Covers the four surfaces the feature adds, and — most importantly — that it
adds them without touching how any existing item behaves:

* ``PackagingLevel`` chain rules (model ``clean``) and the count-mode/count-level
  pair on ``InventoryItem``.
* The pure conversion service (``to_base`` / ``to_level_count`` /
  ``on_hand_display``) across all three counting modes, including the
  half-configured fallback.
* The API: nested read/write of ``packaging_levels`` on the item serializer,
  ``per_parent`` and ``on_hand_display`` on read, and the validation rejections.
* Back-compat: an item that sets none of the new fields serializes and behaves
  exactly as before, with ``current_stock`` still the canonical quantity.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

import pytest
from rest_framework import status
from rest_framework.reverse import reverse

from inventory.models import InventoryItem, PackagingLevel
from inventory.services.packaging import (
    on_hand_display,
    to_base,
    to_level_count,
    validate_packaging_chain,
)
from inventory.tests.factories import InventoryItemFactory

pytestmark = pytest.mark.django_db


# A paper-style chain: a case holds 5 reams, a ream holds 100 sheets, and the
# sheet is the base unit that ``current_stock`` is counted in.
CHAIN = [
    {"name": "case", "sort_order": 0, "base_units": 500},
    {"name": "ream", "sort_order": 1, "base_units": 100},
    {"name": "sheet", "sort_order": 2, "base_units": 1},
]


def _make_chain(item, chain=CHAIN):
    """Create ``chain`` on ``item`` and return the rungs keyed by name."""
    return {level["name"]: PackagingLevel.objects.create(item=item, **level) for level in chain}


def _list_url():
    return reverse("inventoryitem-list")


def _detail_url(item):
    return reverse("inventoryitem-detail", kwargs={"pk": str(item.id)})


def _errors(response):
    """Field errors out of the project's wrapped DRF error envelope."""
    return response.data["error"]["details"]


class TestPackagingLevelModel:
    def test_three_level_chain_is_valid(self):
        item = InventoryItemFactory(image=None, base_unit="sheet")
        levels = _make_chain(item)

        for level in levels.values():
            level.full_clean()

        assert list(item.packaging_levels.values_list("name", flat=True)) == [
            "case",
            "ream",
            "sheet",
        ]

    def test_str_names_the_rung_and_its_base_units(self):
        item = InventoryItemFactory(image=None, name="Copy paper", base_unit="sheet")
        levels = _make_chain(item)

        assert str(levels["ream"]) == "Copy paper ream (=100 sheet)"

    def test_two_base_rungs_rejected(self):
        item = InventoryItemFactory(image=None)
        PackagingLevel.objects.create(item=item, name="case", sort_order=0, base_units=1)
        extra = PackagingLevel(item=item, name="sheet", sort_order=1, base_units=1)

        with pytest.raises(ValidationError) as exc:
            extra.full_clean()

        assert any("Exactly one packaging level" in message for message in exc.value.messages)

    def test_no_base_rung_rejected(self):
        item = InventoryItemFactory(image=None)
        PackagingLevel.objects.create(item=item, name="case", sort_order=0, base_units=500)
        inner = PackagingLevel(item=item, name="ream", sort_order=1, base_units=100)

        with pytest.raises(ValidationError) as exc:
            inner.full_clean()

        assert any("Exactly one packaging level" in message for message in exc.value.messages)

    def test_non_decreasing_base_units_rejected(self):
        item = InventoryItemFactory(image=None)
        PackagingLevel.objects.create(item=item, name="case", sort_order=0, base_units=100)
        PackagingLevel.objects.create(item=item, name="sheet", sort_order=2, base_units=1)
        # A middle rung that holds MORE than the case containing it.
        inner = PackagingLevel(item=item, name="ream", sort_order=1, base_units=500)

        with pytest.raises(ValidationError) as exc:
            inner.full_clean()

        assert any("fewer base units" in message for message in exc.value.messages)

    def test_base_rung_must_be_innermost(self):
        item = InventoryItemFactory(image=None)
        # base_units 1 at sort_order 0, with a bigger rung inside it.
        PackagingLevel.objects.create(item=item, name="sheet", sort_order=0, base_units=1)
        outer = PackagingLevel(item=item, name="case", sort_order=1, base_units=500)

        with pytest.raises(ValidationError) as exc:
            outer.full_clean()

        assert any("innermost" in message for message in exc.value.messages)

    def test_blank_name_rejected(self):
        item = InventoryItemFactory(image=None)
        level = PackagingLevel(item=item, name="   ", sort_order=0, base_units=1)

        with pytest.raises(ValidationError):
            level.full_clean()

    def test_sort_order_unique_per_item(self):
        item = InventoryItemFactory(image=None)
        PackagingLevel.objects.create(item=item, name="case", sort_order=0, base_units=500)

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                PackagingLevel.objects.create(item=item, name="box", sort_order=0, base_units=250)

    def test_same_sort_order_allowed_on_a_different_item(self):
        first = InventoryItemFactory(image=None)
        second = InventoryItemFactory(image=None)
        _make_chain(first)
        _make_chain(second)

        assert PackagingLevel.objects.filter(sort_order=0).count() == 2

    def test_deleting_the_item_cascades_and_clears_count_level(self):
        item = InventoryItemFactory(image=None)
        levels = _make_chain(item)
        item.count_mode = InventoryItem.CountMode.BY_LEVEL
        item.count_level = levels["ream"]
        item.save()

        item.delete()

        assert not PackagingLevel.objects.filter(item_id=item.pk).exists()


class TestCountModeValidation:
    def test_each_mode_rejects_a_count_level(self):
        item = InventoryItemFactory(image=None)
        levels = _make_chain(item)
        item.count_level = levels["ream"]

        with pytest.raises(ValidationError) as exc:
            item.full_clean()

        assert "count_level" in exc.value.message_dict

    def test_by_level_requires_a_count_level(self):
        item = InventoryItemFactory(image=None)
        _make_chain(item)
        item.count_mode = InventoryItem.CountMode.BY_LEVEL

        with pytest.raises(ValidationError) as exc:
            item.full_clean()

        assert "required" in exc.value.message_dict["count_level"][0]

    def test_count_level_from_another_item_rejected(self):
        item = InventoryItemFactory(image=None)
        _make_chain(item)
        other = InventoryItemFactory(image=None)
        other_levels = _make_chain(other)

        item.count_mode = InventoryItem.CountMode.OPEN_CLOSED
        item.count_level = other_levels["ream"]

        with pytest.raises(ValidationError) as exc:
            item.full_clean()

        assert "one of this item's packaging levels" in exc.value.message_dict["count_level"][0]

    def test_own_count_level_accepted(self):
        item = InventoryItemFactory(image=None)
        levels = _make_chain(item)
        item.count_mode = InventoryItem.CountMode.BY_LEVEL
        item.count_level = levels["ream"]

        item.full_clean()  # does not raise

    def test_default_item_passes_full_clean(self):
        """The back-compat guard for the new model validation: untouched items pass."""
        InventoryItemFactory(image=None).full_clean()


class TestChainValidator:
    def test_empty_chain_is_valid(self):
        validate_packaging_chain([])

    def test_accepts_payload_dicts(self):
        validate_packaging_chain(CHAIN)

    def test_rejects_duplicate_sort_orders(self):
        with pytest.raises(ValidationError) as exc:
            validate_packaging_chain(
                [
                    {"name": "case", "sort_order": 0, "base_units": 500},
                    {"name": "box", "sort_order": 0, "base_units": 250},
                    {"name": "sheet", "sort_order": 1, "base_units": 1},
                ]
            )

        assert any("distinct sort orders" in message for message in exc.value.messages)

    def test_rejects_zero_base_units(self):
        with pytest.raises(ValidationError) as exc:
            validate_packaging_chain(
                [
                    {"name": "case", "sort_order": 0, "base_units": 500},
                    {"name": "sheet", "sort_order": 1, "base_units": 0},
                ]
            )

        assert any("at least one base unit" in message for message in exc.value.messages)

    def test_single_base_rung_is_valid(self):
        validate_packaging_chain([{"name": "each", "sort_order": 0, "base_units": 1}])


class TestConversionService:
    def test_to_base_multiplies_by_the_rung_size(self):
        item = InventoryItemFactory(image=None)
        levels = _make_chain(item)

        assert to_base(levels["case"], 3) == 1500
        assert to_base(levels["ream"], 3) == 300
        assert to_base(levels["sheet"], 3) == 3

    def test_to_level_count_splits_whole_rungs_from_the_remainder(self):
        item = InventoryItemFactory(image=None)
        levels = _make_chain(item)

        assert to_level_count(250, levels["ream"]) == (2, 50)
        assert to_level_count(200, levels["ream"]) == (2, 0)
        assert to_level_count(99, levels["ream"]) == (0, 99)
        assert to_level_count(250, levels["sheet"]) == (250, 0)

    def test_to_level_count_rejects_a_rung_that_holds_nothing(self):
        item = InventoryItemFactory(image=None)
        broken = PackagingLevel(item=item, name="broken", sort_order=0, base_units=0)

        with pytest.raises(ValueError):
            to_level_count(10, broken)

    def test_each_mode_displays_base_units(self):
        item = InventoryItemFactory(image=None, current_stock=250, base_unit="sheet")

        assert on_hand_display(item) == {
            "mode": "each",
            "base_units": 250,
            "unit": "sheet",
            "text": "250 sheet",
        }

    def test_by_level_mode_displays_whole_rungs_and_keeps_the_remainder(self):
        item = InventoryItemFactory(image=None, current_stock=250, base_unit="sheet")
        levels = _make_chain(item)
        item.count_mode = InventoryItem.CountMode.BY_LEVEL
        item.count_level = levels["ream"]

        assert on_hand_display(item) == {
            "mode": "by_level",
            "level": "ream",
            "level_count": 2,
            "remainder_base": 50,
            "text": "2 ream(s)",
        }

    def test_open_closed_mode_displays_sealed_plus_open(self):
        item = InventoryItemFactory(image=None, current_stock=250, base_unit="sheet")
        levels = _make_chain(item)
        item.count_mode = InventoryItem.CountMode.OPEN_CLOSED
        item.count_level = levels["ream"]
        item.open_container_count = 1

        assert on_hand_display(item) == {
            "mode": "open_closed",
            "level": "ream",
            "sealed": 2,
            "open": 1,
            "text": "2 sealed + 1 open",
        }

    def test_pack_mode_without_a_count_level_falls_back_to_base_display(self):
        """A half-configured item renders instead of crashing."""
        item = InventoryItemFactory(image=None, current_stock=250, base_unit="sheet")
        item.count_mode = InventoryItem.CountMode.BY_LEVEL
        item.count_level = None

        assert on_hand_display(item) == {
            "mode": "each",
            "base_units": 250,
            "unit": "sheet",
            "text": "250 sheet",
        }

    def test_display_never_changes_current_stock(self):
        item = InventoryItemFactory(image=None, current_stock=250)
        levels = _make_chain(item)
        item.count_mode = InventoryItem.CountMode.BY_LEVEL
        item.count_level = levels["ream"]
        item.save()

        on_hand_display(item)

        item.refresh_from_db()
        assert item.current_stock == 250


class TestPackagingApi:
    def test_nested_chain_round_trips_on_create(self, authenticated_client):
        client, _ = authenticated_client

        response = client.post(
            _list_url(),
            {
                "name": "Copy paper",
                "description": "20lb letter",
                "reorder_quantity": 5,
                "current_stock": 250,
                "base_unit": "sheet",
                "packaging_levels": CHAIN,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        item = InventoryItem.objects.get(id=response.data["id"])
        assert list(item.packaging_levels.values_list("name", "sort_order", "base_units")) == [
            ("case", 0, 500),
            ("ream", 1, 100),
            ("sheet", 2, 1),
        ]

    def test_read_exposes_per_parent_and_on_hand_display(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=250, base_unit="sheet")
        levels = _make_chain(item)
        item.count_mode = InventoryItem.CountMode.BY_LEVEL
        item.count_level = levels["ream"]
        item.save()

        response = api_client.get(_detail_url(item))

        assert response.status_code == status.HTTP_200_OK
        # "a case is 5 reams, a ream is 100 sheets, a sheet is the base".
        assert [level["per_parent"] for level in response.data["packaging_levels"]] == [
            5,
            100,
            None,
        ]
        assert response.data["on_hand_display"]["text"] == "2 ream(s)"

    def test_update_upserts_the_chain_and_keeps_the_count_level_row(self, authenticated_client):
        client, _ = authenticated_client
        item = InventoryItemFactory(image=None, current_stock=250)
        levels = _make_chain(item)
        item.count_mode = InventoryItem.CountMode.BY_LEVEL
        item.count_level = levels["ream"]
        item.save()
        ream_pk = levels["ream"].pk

        response = client.patch(
            _detail_url(item),
            {
                "packaging_levels": [
                    {"name": "pallet", "sort_order": 0, "base_units": 5000},
                    {"name": "ream", "sort_order": 1, "base_units": 100},
                    {"name": "sheet", "sort_order": 2, "base_units": 1},
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        item.refresh_from_db()
        assert list(item.packaging_levels.values_list("name", flat=True)) == [
            "pallet",
            "ream",
            "sheet",
        ]
        # The rung that kept its position kept its primary key, so the item's
        # count_level still points at a live row.
        assert item.count_level_id == ream_pk

    def test_update_can_shorten_the_chain(self, authenticated_client):
        client, _ = authenticated_client
        item = InventoryItemFactory(image=None)
        _make_chain(item)

        response = client.patch(
            _detail_url(item),
            {
                "packaging_levels": [
                    {"name": "ream", "sort_order": 0, "base_units": 100},
                    {"name": "sheet", "sort_order": 1, "base_units": 1},
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert list(item.packaging_levels.values_list("name", flat=True)) == ["ream", "sheet"]

    def test_invalid_chain_rejected(self, authenticated_client):
        client, _ = authenticated_client
        item = InventoryItemFactory(image=None)

        response = client.patch(
            _detail_url(item),
            {
                "packaging_levels": [
                    {"name": "case", "sort_order": 0, "base_units": 100},
                    {"name": "ream", "sort_order": 1, "base_units": 500},
                    {"name": "sheet", "sort_order": 2, "base_units": 1},
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "packaging_levels" in _errors(response)
        assert not item.packaging_levels.exists()

    def test_each_mode_with_a_count_level_rejected(self, authenticated_client):
        client, _ = authenticated_client
        item = InventoryItemFactory(image=None)
        levels = _make_chain(item)

        response = client.patch(
            _detail_url(item),
            {"count_mode": InventoryItem.CountMode.EACH, "count_level": levels["ream"].pk},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "count_level" in _errors(response)

    def test_by_level_without_a_count_level_rejected(self, authenticated_client):
        client, _ = authenticated_client
        item = InventoryItemFactory(image=None)
        _make_chain(item)

        response = client.patch(
            _detail_url(item),
            {"count_mode": InventoryItem.CountMode.BY_LEVEL},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "count_level" in _errors(response)

    def test_count_level_from_another_item_rejected(self, authenticated_client):
        client, _ = authenticated_client
        item = InventoryItemFactory(image=None)
        _make_chain(item)
        other_levels = _make_chain(InventoryItemFactory(image=None))

        response = client.patch(
            _detail_url(item),
            {
                "count_mode": InventoryItem.CountMode.BY_LEVEL,
                "count_level": other_levels["ream"].pk,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "count_level" in _errors(response)

    def test_chain_replacement_cannot_orphan_the_count_level(self, authenticated_client):
        """Dropping the counted rung in the same request is rejected, not silently nulled."""
        client, _ = authenticated_client
        item = InventoryItemFactory(image=None)
        levels = _make_chain(item)
        item.count_mode = InventoryItem.CountMode.BY_LEVEL
        item.count_level = levels["ream"]
        item.save()

        response = client.patch(
            _detail_url(item),
            {
                "packaging_levels": [
                    {"name": "case", "sort_order": 0, "base_units": 500},
                    {"name": "sheet", "sort_order": 2, "base_units": 1},
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "count_level" in _errors(response)
        item.refresh_from_db()
        assert item.count_level_id == levels["ream"].pk

    def test_valid_count_mode_write_persists(self, authenticated_client):
        client, _ = authenticated_client
        item = InventoryItemFactory(image=None, current_stock=250)
        levels = _make_chain(item)

        response = client.patch(
            _detail_url(item),
            {
                "base_unit": "sheet",
                "count_mode": InventoryItem.CountMode.OPEN_CLOSED,
                "count_level": levels["ream"].pk,
                "open_container_count": 1,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        item.refresh_from_db()
        assert item.count_mode == InventoryItem.CountMode.OPEN_CLOSED
        assert item.count_level_id == levels["ream"].pk
        assert item.open_container_count == 1
        assert response.data["on_hand_display"]["text"] == "2 sealed + 1 open"


class TestBackwardCompatibility:
    """An item that never touches the new fields behaves exactly as before."""

    def test_defaults_leave_the_item_counting_base_units(self):
        item = InventoryItemFactory(image=None, current_stock=7)

        assert item.base_unit == "unit"
        assert item.count_mode == InventoryItem.CountMode.EACH
        assert item.count_level is None
        assert item.open_container_count == 0
        assert not item.packaging_levels.exists()

    def test_existing_style_item_serializes_with_the_each_display(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=7, minimum_stock=10)

        response = api_client.get(_detail_url(item))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["packaging_levels"] == []
        assert response.data["on_hand_display"] == {
            "mode": "each",
            "base_units": 7,
            "unit": "unit",
            "text": "7 unit",
        }
        # The quantity flows are untouched: current_stock is still the canonical
        # count and still drives reordering on its own.
        assert response.data["current_stock"] == 7
        assert response.data["needs_reorder"] is True

    def test_stock_and_reorder_math_ignores_the_packaging_chain(self):
        """Configuring packaging does not move stock or change the reorder trigger."""
        item = InventoryItemFactory(image=None, current_stock=250, minimum_stock=300)
        levels = _make_chain(item)
        item.count_mode = InventoryItem.CountMode.BY_LEVEL
        item.count_level = levels["ream"]
        item.save()
        item.refresh_from_db()

        assert item.current_stock == 250
        # 2 reams on hand, but the trigger still compares base units to
        # minimum_stock exactly as it did before this feature existed.
        assert item.needs_reorder is True

    def test_item_create_without_packaging_fields_still_works(self, authenticated_client):
        client, _ = authenticated_client

        response = client.post(
            _list_url(),
            {"name": "Widget", "description": "A widget", "reorder_quantity": 5},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["count_mode"] == InventoryItem.CountMode.EACH
        assert response.data["packaging_levels"] == []
