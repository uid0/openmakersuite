"""Stock transactions through the pack chain (op-ev14, phase 2b).

Phase 1 (op-hzji) described an item's packaging; phase 2a (op-es7c) let the
*reorder* decision read it. Phase 2b lets the *write* paths read it: a
reconciliation, a cycle count, a usage entry, a purchase-order quantity and a
receipt can all be expressed in the unit the item is stocked in, converted
through the chain to the base units ``current_stock`` is always kept in.

The load-bearing class is the FIRST one: the flag is opt-in, so an item in
``count_mode=each`` — every item that exists today, including the legacy
``use_case_based_reorder`` ones — and every caller that does not send it come
out of these paths exactly where they went in. Everything after covers items
deliberately opted into a pack-counting mode.

The purchase-order and receipt halves live in
``reorder_queue/tests/test_po_at_level.py``.
"""

import csv
import io

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.crypto import get_random_string

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import InventoryItem, PackagingLevel, StockReconciliation, UsageLog
from inventory.services.pack_transitions import finish_open_pack, open_pack
from inventory.services.packaging import (
    count_unit,
    order_level,
    parse_at_level,
    resolve_base_quantity,
)
from inventory.tests.factories import InventoryItemFactory, LocationFactory

pytestmark = pytest.mark.django_db

USED = StockReconciliation.ReasonCode.USED_WITHOUT_SCAN
BATCH_URL = "/api/inventory/reconciliations/batch/"


@pytest.fixture
def staff_api_client():
    """Reconciliation + the container transitions are login-gated."""
    user = get_user_model().objects.create_user(
        username=get_random_string(8),
        email=f"{get_random_string(6)}@example.com",
        password=get_random_string(24),
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _pack_item(mode=InventoryItem.CountMode.BY_LEVEL, case_size=12, **kwargs):
    """An item counted in whole cases of ``case_size`` base units ("bottles").

    Mirrors the phase-2a helper: a two-rung chain (case, bottle) with the case as
    the counting rung, so ``current_stock`` stays a bottle count.
    """
    kwargs.setdefault("image", None)
    kwargs.setdefault("base_unit", "bottle")
    item = InventoryItemFactory(**kwargs)
    case = PackagingLevel.objects.create(item=item, name="case", sort_order=0, base_units=case_size)
    PackagingLevel.objects.create(item=item, name="bottle", sort_order=1, base_units=1)
    item.count_mode = mode
    item.count_level = None if mode == InventoryItem.CountMode.EACH else case
    item.save(update_fields=["count_mode", "count_level"])
    return item


def _cycle_count_url(item):
    return f"/api/inventory/items/{item.id}/cycle-count/"


def _log_usage_url(item):
    return f"/api/inventory/items/{item.id}/log_usage/"


def _pack_container_url(item):
    return f"/api/inventory/items/{item.id}/pack-container/"


def _batch_row(item, actual_count, **extra):
    return {"rows": [{"item_id": str(item.id), "actual_count": actual_count, **extra}]}


class TestEachItemsAreUntouched:
    """The regression guard: base units stay base units without the flag."""

    def test_log_usage_without_flag_decrements_base_units(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=20)

        response = api_client.post(_log_usage_url(item), {"quantity": 3}, format="json")

        assert response.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.current_stock == 17
        assert UsageLog.objects.get(item=item).quantity_used == 3

    def test_cycle_count_without_flag_sets_base_units(self, staff_api_client):
        item = InventoryItemFactory(image=None, current_stock=20)

        response = staff_api_client.post(
            _cycle_count_url(item), {"counted_qty": 7, "reason": USED}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["current_stock"] == 7
        assert response.data["counted_unit"] == "unit"
        item.refresh_from_db()
        assert item.current_stock == 7

    def test_batch_reconcile_without_flag_sets_base_units(self, staff_api_client):
        item = InventoryItemFactory(image=None, current_stock=20)

        response = staff_api_client.post(BATCH_URL, _batch_row(item, 5, reason=USED), format="json")

        assert response.status_code == status.HTTP_201_CREATED
        item.refresh_from_db()
        assert item.current_stock == 5

    def test_pack_counted_item_ignores_a_quantity_sent_without_the_flag(self, staff_api_client):
        """A caller that never learned about packs keeps writing base units.

        This is the whole reason the conversion is opt-in rather than inferred
        from ``count_mode``: a work-order template or a PO line holds base units,
        and flipping an item to ``by_level`` must not silently multiply them.
        """
        item = _pack_item(case_size=12, current_stock=120)

        response = staff_api_client.post(
            _cycle_count_url(item), {"counted_qty": 24, "reason": USED}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        item.refresh_from_db()
        assert item.current_stock == 24

    def test_at_level_on_an_each_item_is_rejected(self, api_client):
        """Not silently read as base units — a mis-sent flag would corrupt stock."""
        item = InventoryItemFactory(image=None, current_stock=20)

        response = api_client.post(
            _log_usage_url(item), {"quantity": 2, "at_level": True}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "not counted in packs" in response.data["detail"]
        item.refresh_from_db()
        assert item.current_stock == 20

    def test_at_level_on_a_legacy_case_based_item_is_rejected(self, staff_api_client):
        """``use_case_based_reorder`` is not a packaging chain; it stays ``each``."""
        item = InventoryItemFactory(
            image=None,
            current_stock=30,
            use_case_based_reorder=True,
            minimum_cases=2,
            reorder_cases=1,
        )

        response = staff_api_client.post(
            _cycle_count_url(item),
            {"counted_qty": 2, "reason": USED, "at_level": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        item.refresh_from_db()
        assert item.current_stock == 30

    def test_at_level_on_a_half_configured_item_is_rejected(self, staff_api_client):
        """Pack mode with no usable ``count_level`` has nothing to convert with."""
        item = _pack_item(current_stock=30)
        item.count_level = None
        item.save(update_fields=["count_level"])

        response = staff_api_client.post(
            _cycle_count_url(item),
            {"counted_qty": 2, "reason": USED, "at_level": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        item.refresh_from_db()
        assert item.current_stock == 30


class TestParseAtLevel:
    """``bool("false")`` is True, so the flag needs real coercion.

    Scantty and any form-encoded client post strings, so an endpoint that reads
    ``request.data`` by hand would otherwise read ``at_level=false`` as a pack
    count and multiply the quantity.
    """

    @pytest.mark.parametrize("raw", ["false", "False", "0", "no", "off", "f", "n", False, 0])
    def test_false_spellings(self, raw):
        assert parse_at_level(raw) is False

    @pytest.mark.parametrize("raw", ["true", "True", "1", "yes", "on", "t", "y", True, 1])
    def test_true_spellings(self, raw):
        assert parse_at_level(raw) is True

    @pytest.mark.parametrize("raw", [None, ""])
    def test_absent_means_base_units(self, raw):
        assert parse_at_level(raw) is False

    def test_garbage_is_an_error_not_a_guess(self):
        with pytest.raises(ValidationError):
            parse_at_level("maybe")

    def test_agrees_with_drf_booleanfield(self):
        """Pinned so the hand-written value sets cannot drift from DRF's."""
        from rest_framework import serializers as drf

        field = drf.BooleanField()
        for raw in drf.BooleanField.TRUE_VALUES | drf.BooleanField.FALSE_VALUES:
            assert parse_at_level(raw) == field.to_internal_value(raw), raw

    def test_string_false_leaves_an_each_item_alone_over_the_wire(self, staff_api_client):
        item = InventoryItemFactory(image=None, current_stock=20)

        response = staff_api_client.post(
            _cycle_count_url(item),
            {"counted_qty": 7, "reason": USED, "at_level": "false"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        item.refresh_from_db()
        assert item.current_stock == 7

    def test_string_false_does_not_multiply_a_pack_item(self, api_client):
        item = _pack_item(case_size=12, current_stock=60)

        response = api_client.post(
            _log_usage_url(item), {"quantity": 2, "at_level": "false"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        item.refresh_from_db()
        assert item.current_stock == 58


class TestResolveBaseQuantity:
    """The one conversion seam every write path shares."""

    def test_without_the_flag_every_mode_is_an_identity(self):
        for mode in InventoryItem.CountMode.values:
            item = _pack_item(mode=mode, case_size=12, current_stock=0)
            assert resolve_base_quantity(item, 7) == 7
            assert resolve_base_quantity(item, 7, at_level=False) == 7

    @pytest.mark.parametrize(
        "mode", [InventoryItem.CountMode.BY_LEVEL, InventoryItem.CountMode.OPEN_CLOSED]
    )
    def test_pack_count_converts_through_the_count_level(self, mode):
        item = _pack_item(mode=mode, case_size=12, current_stock=0)

        assert resolve_base_quantity(item, 3, at_level=True) == 36
        assert resolve_base_quantity(item, 0, at_level=True) == 0

    def test_each_item_raises(self):
        item = _pack_item(mode=InventoryItem.CountMode.EACH, current_stock=0)

        with pytest.raises(ValidationError):
            resolve_base_quantity(item, 3, at_level=True)

    def test_explicit_level_overrides_the_count_level(self):
        """A caller may name another rung — the PO paths pass the order level."""
        item = _pack_item(case_size=12, current_stock=0)
        bottle = item.packaging_levels.get(name="bottle")

        assert resolve_base_quantity(item, 3, at_level=True, level=bottle) == 3

    def test_count_unit_names_the_entry_unit(self):
        assert count_unit(_pack_item(current_stock=0)) == "case"
        assert count_unit(InventoryItemFactory(image=None, base_unit="sheet")) == "sheet"

    def test_order_level_is_the_outermost_rung(self):
        """An item bought by the pallet but counted by the case."""
        item = InventoryItemFactory(image=None, base_unit="bottle", current_stock=0)
        pallet = PackagingLevel.objects.create(
            item=item, name="pallet", sort_order=0, base_units=120
        )
        case = PackagingLevel.objects.create(item=item, name="case", sort_order=1, base_units=12)
        PackagingLevel.objects.create(item=item, name="bottle", sort_order=2, base_units=1)
        item.count_mode = InventoryItem.CountMode.BY_LEVEL
        item.count_level = case
        item.save(update_fields=["count_mode", "count_level"])

        assert order_level(item).pk == pallet.pk
        # ``at_level`` still means the COUNT level — the one unit the API names.
        assert resolve_base_quantity(item, 2, at_level=True) == 24
        assert resolve_base_quantity(item, 2, at_level=True, level=pallet) == 240

    def test_order_level_is_none_for_a_base_unit_item(self):
        assert order_level(InventoryItemFactory(image=None)) is None


class TestOnHandSetAtCountLevel:
    """The write complement of ``on_hand_display``: setting stock in packs."""

    def test_cycle_count_at_level_stores_base_units(self, staff_api_client):
        item = _pack_item(case_size=12, current_stock=0)

        response = staff_api_client.post(
            _cycle_count_url(item),
            {"counted_qty": 3, "reason": USED, "at_level": True},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["current_stock"] == 36
        assert response.data["counted_unit"] == "case"
        assert response.data["on_hand_display"]["level_count"] == 3
        item.refresh_from_db()
        assert item.current_stock == 36

    def test_audit_row_stays_base_unit_canonical(self, staff_api_client):
        """Snapshot + delta are base units whatever unit the entry arrived in."""
        item = _pack_item(case_size=12, current_stock=24)

        response = staff_api_client.post(
            _cycle_count_url(item),
            {"counted_qty": 3, "reason": USED, "at_level": True},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        row = StockReconciliation.objects.get(item=item)
        assert (row.projected_count, row.actual_count, row.delta) == (24, 36, 12)

    def test_open_closed_sealed_and_open_pair(self, staff_api_client):
        item = _pack_item(mode=InventoryItem.CountMode.OPEN_CLOSED, case_size=12, current_stock=0)

        response = staff_api_client.post(
            _cycle_count_url(item),
            {"counted_qty": 4, "reason": USED, "at_level": True, "open_count": 1},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        item.refresh_from_db()
        assert item.current_stock == 48
        assert item.open_container_count == 1
        assert response.data["on_hand_display"]["sealed"] == 4
        assert response.data["on_hand_display"]["open"] == 1

    def test_open_count_on_a_by_level_item_is_rejected(self, staff_api_client):
        item = _pack_item(case_size=12, current_stock=24)

        response = staff_api_client.post(
            _cycle_count_url(item),
            {"counted_qty": 2, "reason": USED, "at_level": True, "open_count": 1},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "open containers" in response.data["detail"]
        item.refresh_from_db()
        assert item.current_stock == 24

    def test_batch_reconcile_at_level(self, staff_api_client):
        item = _pack_item(case_size=12, current_stock=0)

        response = staff_api_client.post(
            BATCH_URL, _batch_row(item, 2, reason=USED, at_level=True), format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        item.refresh_from_db()
        assert item.current_stock == 24

    def test_batch_reconcile_rejects_at_level_on_an_each_item(self, staff_api_client):
        item = InventoryItemFactory(image=None, current_stock=30)

        response = staff_api_client.post(
            BATCH_URL, _batch_row(item, 2, reason=USED, at_level=True), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        item.refresh_from_db()
        assert item.current_stock == 30

    def test_auto_reorder_note_names_the_pack_unit(self, staff_api_client):
        """A base count read against a pack threshold would be nonsense."""
        from reorder_queue.models import ReorderRequest

        item = _pack_item(case_size=12, current_stock=120, minimum_stock=2, reorder_quantity=3)

        response = staff_api_client.post(
            _cycle_count_url(item),
            {"counted_qty": 2, "reason": USED, "at_level": True},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        reorder = ReorderRequest.objects.get(item=item)
        assert "actual=2 case, minimum=2 case" in reorder.request_notes
        # Stored quantity is still base units: 3 cases of 12.
        assert reorder.quantity == 36

    def test_each_item_auto_reorder_note_is_unchanged(self, staff_api_client):
        from reorder_queue.models import ReorderRequest

        item = InventoryItemFactory(
            image=None, current_stock=20, minimum_stock=10, reorder_quantity=25
        )

        staff_api_client.post(
            _cycle_count_url(item), {"counted_qty": 3, "reason": USED}, format="json"
        )

        reorder = ReorderRequest.objects.get(item=item)
        assert "(actual=3, minimum=10)" in reorder.request_notes


class TestManualStockSetAtCountLevel:
    """``current_stock_at_level`` on the item edit form — the other set path."""

    def _patch(self, client, item, payload):
        return client.patch(f"/api/inventory/items/{item.id}/", payload, format="json")

    def test_pack_count_sets_base_stock(self, staff_api_client):
        item = _pack_item(case_size=12, current_stock=0)

        response = self._patch(staff_api_client, item, {"current_stock_at_level": 3})

        assert response.status_code == status.HTTP_200_OK, response.data
        item.refresh_from_db()
        assert item.current_stock == 36
        # Write-only: it never comes back on the read side.
        assert "current_stock_at_level" not in response.data
        assert response.data["on_hand_display"]["level_count"] == 3

    def test_plain_current_stock_write_is_unchanged(self, staff_api_client):
        item = _pack_item(case_size=12, current_stock=0)

        response = self._patch(staff_api_client, item, {"current_stock": 36})

        assert response.status_code == status.HTTP_200_OK, response.data
        item.refresh_from_db()
        assert item.current_stock == 36

    def test_both_at_once_is_rejected(self, staff_api_client):
        item = _pack_item(case_size=12, current_stock=5)

        response = self._patch(
            staff_api_client, item, {"current_stock": 36, "current_stock_at_level": 3}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        item.refresh_from_db()
        assert item.current_stock == 5

    def test_rejected_on_an_each_item(self, staff_api_client):
        item = InventoryItemFactory(image=None, current_stock=5)

        response = self._patch(staff_api_client, item, {"current_stock_at_level": 3})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        item.refresh_from_db()
        assert item.current_stock == 5

    def test_opting_into_a_pack_mode_and_setting_stock_in_one_request(self, staff_api_client):
        """The conversion reads the mode the item will HAVE, not the stored one."""
        item = _pack_item(mode=InventoryItem.CountMode.EACH, case_size=12, current_stock=0)
        case = item.packaging_levels.get(name="case")

        response = self._patch(
            staff_api_client,
            item,
            {
                "count_mode": InventoryItem.CountMode.BY_LEVEL,
                "count_level": case.pk,
                "current_stock_at_level": 2,
            },
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        item.refresh_from_db()
        assert item.count_mode == InventoryItem.CountMode.BY_LEVEL
        assert item.current_stock == 24


class TestReconcileCsvAndGrid:
    """The offline round-trip: the grid/template name the unit, upload reads it."""

    def test_location_grid_reports_the_count_unit(self, staff_api_client):
        location = LocationFactory()
        pack = _pack_item(case_size=12, current_stock=36, location=location)
        plain = InventoryItemFactory(image=None, current_stock=5, location=location)

        response = staff_api_client.get(f"/api/inventory/locations/{location.pk}/reconcile/")

        assert response.status_code == status.HTTP_200_OK
        rows = {row["sku"]: row for row in response.data["items"]}
        assert rows[pack.sku]["count_unit"] == "case"
        assert rows[pack.sku]["projected_at_unit"] == 3
        assert rows[pack.sku]["projected"] == 36
        assert rows[plain.sku]["count_unit"] == "unit"
        assert rows[plain.sku]["projected_at_unit"] == 5

    def test_export_template_carries_the_unit_columns(self, staff_api_client):
        location = LocationFactory()
        item = _pack_item(
            mode=InventoryItem.CountMode.OPEN_CLOSED,
            case_size=12,
            current_stock=36,
            location=location,
        )
        item.open_container_count = 1
        item.save(update_fields=["open_container_count"])

        response = staff_api_client.get(f"/api/inventory/locations/{location.pk}/reconcile/export/")

        assert response.status_code == status.HTTP_200_OK
        body = b"".join(response.streaming_content).decode()
        rows = list(csv.DictReader(io.StringIO(body)))
        assert rows[0]["count_unit"] == "case"
        assert rows[0]["projected_at_unit"] == "3"
        assert rows[0]["open_count"] == "1"

    def test_upload_reads_the_at_level_and_open_count_columns(self, staff_api_client):
        item = _pack_item(mode=InventoryItem.CountMode.OPEN_CLOSED, case_size=12, current_stock=0)
        csv_body = (
            "item_id,actual_count,reason,at_level,open_count\n" f"{item.id},4,{USED},true,1\n"
        ).encode()

        response = staff_api_client.post(
            "/api/inventory/reconciliations/upload/",
            {"file": SimpleUploadedFile("recon.csv", csv_body, content_type="text/csv")},
            format="multipart",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        item.refresh_from_db()
        assert item.current_stock == 48
        assert item.open_container_count == 1

    def test_upload_without_the_column_stays_base_units(self, staff_api_client):
        item = _pack_item(case_size=12, current_stock=0)
        csv_body = f"item_id,actual_count,reason\n{item.id},4,{USED}\n".encode()

        response = staff_api_client.post(
            "/api/inventory/reconciliations/upload/",
            {"file": SimpleUploadedFile("recon.csv", csv_body, content_type="text/csv")},
            format="multipart",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        item.refresh_from_db()
        assert item.current_stock == 4


class TestUsageAtCountLevel:
    """ "Used two cases" — converted before the decrement, logged in base units."""

    def test_usage_at_level_decrements_the_converted_quantity(self, api_client):
        item = _pack_item(case_size=12, current_stock=60)

        response = api_client.post(
            _log_usage_url(item), {"quantity": 2, "at_level": True}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.current_stock == 36
        # The stored log is base units — the wire shape of UsageLog is unchanged.
        assert UsageLog.objects.get(item=item).quantity_used == 24
        assert response.data["entered_quantity"] == 2
        assert response.data["entered_unit"] == "case"
        assert response.data["quantity_used"] == 24

    def test_usage_response_reports_the_base_unit_when_no_flag(self, api_client):
        item = _pack_item(case_size=12, current_stock=60)

        response = api_client.post(_log_usage_url(item), {"quantity": 2}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["entered_unit"] == "bottle"
        item.refresh_from_db()
        assert item.current_stock == 58

    def test_usage_at_level_on_open_closed(self, api_client):
        item = _pack_item(mode=InventoryItem.CountMode.OPEN_CLOSED, case_size=12, current_stock=60)

        response = api_client.post(
            _log_usage_url(item), {"quantity": 1, "at_level": True}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.current_stock == 48

    def test_usage_beyond_stock_still_never_drives_stock_negative(self, api_client):
        """Unchanged guard: the log is written, the stock is not pushed below 0."""
        item = _pack_item(case_size=12, current_stock=12)

        response = api_client.post(
            _log_usage_url(item), {"quantity": 3, "at_level": True}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.current_stock == 12
        assert UsageLog.objects.get(item=item).quantity_used == 36


class TestOpenClosedTransitions:
    """Opening a pack is consumption; finishing it is bookkeeping."""

    def test_open_moves_a_pack_out_of_sealed_stock(self, staff_api_client):
        item = _pack_item(mode=InventoryItem.CountMode.OPEN_CLOSED, case_size=12, current_stock=36)

        response = staff_api_client.post(
            _pack_container_url(item), {"transition": "open"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.current_stock == 24
        assert item.open_container_count == 1
        assert response.data["on_hand_display"]["sealed"] == 2
        assert response.data["on_hand_display"]["open"] == 1
        # Opening consumes the pack, so usage history and stock agree.
        assert response.data["usage_log"]["quantity_used"] == 12
        assert UsageLog.objects.get(item=item).quantity_used == 12

    def test_finish_clears_a_container_without_touching_stock(self, staff_api_client):
        item = _pack_item(mode=InventoryItem.CountMode.OPEN_CLOSED, case_size=12, current_stock=24)
        item.open_container_count = 1
        item.save(update_fields=["open_container_count"])

        response = staff_api_client.post(
            _pack_container_url(item), {"transition": "finish"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.current_stock == 24
        assert item.open_container_count == 0
        # Nothing was consumed here: the contents left at open time.
        assert response.data["usage_log"] is None
        assert not UsageLog.objects.filter(item=item).exists()

    def test_open_then_finish_round_trip(self, staff_api_client):
        item = _pack_item(mode=InventoryItem.CountMode.OPEN_CLOSED, case_size=12, current_stock=36)

        staff_api_client.post(_pack_container_url(item), {"transition": "open"}, format="json")
        staff_api_client.post(_pack_container_url(item), {"transition": "finish"}, format="json")

        item.refresh_from_db()
        assert item.current_stock == 24
        assert item.open_container_count == 0

    def test_open_with_no_sealed_pack_left_is_rejected(self, staff_api_client):
        item = _pack_item(mode=InventoryItem.CountMode.OPEN_CLOSED, case_size=12, current_stock=5)

        response = staff_api_client.post(
            _pack_container_url(item), {"transition": "open"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "No sealed case" in response.data["detail"]
        item.refresh_from_db()
        assert item.current_stock == 5
        assert item.open_container_count == 0

    def test_finish_with_nothing_open_is_rejected(self, staff_api_client):
        item = _pack_item(mode=InventoryItem.CountMode.OPEN_CLOSED, case_size=12, current_stock=36)

        response = staff_api_client.post(
            _pack_container_url(item), {"transition": "finish"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "no open case" in response.data["detail"]

    @pytest.mark.parametrize(
        "mode", [InventoryItem.CountMode.EACH, InventoryItem.CountMode.BY_LEVEL]
    )
    def test_other_modes_have_no_open_container(self, staff_api_client, mode):
        item = _pack_item(mode=mode, case_size=12, current_stock=36)

        response = staff_api_client.post(
            _pack_container_url(item), {"transition": "open"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "does not track open containers" in response.data["detail"]
        item.refresh_from_db()
        assert item.current_stock == 36

    def test_unknown_transition_is_rejected(self, staff_api_client):
        item = _pack_item(mode=InventoryItem.CountMode.OPEN_CLOSED, case_size=12, current_stock=36)

        response = staff_api_client.post(
            _pack_container_url(item), {"transition": "reopen"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        item.refresh_from_db()
        assert item.current_stock == 36

    def test_requires_authentication(self, api_client):
        item = _pack_item(mode=InventoryItem.CountMode.OPEN_CLOSED, case_size=12, current_stock=36)

        response = api_client.post(_pack_container_url(item), {"transition": "open"}, format="json")

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        item.refresh_from_db()
        assert item.current_stock == 36

    def test_service_transitions_are_callable_directly(self):
        """The services own the rules; the action is a thin wrapper."""
        item = _pack_item(mode=InventoryItem.CountMode.OPEN_CLOSED, case_size=12, current_stock=24)

        item, usage_log = open_pack(item)
        assert (item.current_stock, item.open_container_count) == (12, 1)
        assert usage_log.quantity_used == 12

        item = finish_open_pack(item)
        assert (item.current_stock, item.open_container_count) == (12, 0)

        with pytest.raises(ValidationError):
            finish_open_pack(item)


class TestForecastPresentation:
    """Forecast rows read at the item's counting granularity — display only."""

    URL = "/api/inventory/reports/inventory/demand_forecast/"

    def test_pack_counted_row_presents_its_count_unit(self, authenticated_client):
        from inventory.tests.factories import DemandForecastFactory

        client, _ = authenticated_client
        item = _pack_item(case_size=12, current_stock=36)
        DemandForecastFactory(item=item, avg_interval_days=30.0, days_until_due=2.0)

        response = client.get(self.URL)

        assert response.status_code == status.HTTP_200_OK
        row = response.data[0]
        assert row["count_mode"] == InventoryItem.CountMode.BY_LEVEL
        assert row["count_unit"] == "case"
        assert row["on_hand_display"]["level_count"] == 3

    def test_each_row_presents_base_units(self, authenticated_client):
        from inventory.tests.factories import DemandForecastFactory

        client, _ = authenticated_client
        item = InventoryItemFactory(image=None, current_stock=9, base_unit="sheet")
        DemandForecastFactory(item=item, avg_interval_days=30.0, days_until_due=2.0)

        response = client.get(self.URL)

        assert response.status_code == status.HTTP_200_OK
        row = response.data[0]
        assert row["count_mode"] == InventoryItem.CountMode.EACH
        assert row["count_unit"] == "sheet"
        assert row["on_hand_display"]["base_units"] == 9
