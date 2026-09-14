"""What a surface that FILES a reorder shows, and what it files, are one number.

``reorder_display`` has always carried the item's CONFIGURED reorder amount in
the item's own counting unit — "3 cases". A ``ReorderRequest.quantity`` is
stored in BASE units: ``mark-received`` adds it straight to ``current_stock``,
which :class:`inventory.models.InventoryItem` documents as always base units,
and ``estimated_cost`` multiplies it by a per-base-unit price. Nothing on the
wire related the two, so every client that both showed a reorder quantity and
filed one had to re-derive the second from raw columns — and the QR-scan page
got it wrong in both directions: it printed "3 cases" and POSTed the raw
``reorder_quantity`` column, which for a pack-counting item is a count of PACKS.

``order_quantity``/``order_text`` close that: one server answer, in base units,
with its wording. The invariants below are what a client may rely on.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils.crypto import get_random_string

import pytest
from rest_framework import status
from rest_framework.reverse import reverse
from rest_framework.test import APIClient

from inventory.models import (
    InventoryItem,
    MaintenanceItem,
    MaintenanceMaterial,
    PackagingLevel,
    StockReconciliation,
)
from inventory.services.pack_size import (
    PACK_SIZE_KNOWN,
    PACK_SIZE_NO_ORDERABLE_LINK,
    PACK_SIZE_NOT_RECORDED,
    PACK_SIZE_RECORDED_ZERO,
)
from inventory.services.packaging import (
    base_reorder_quantity,
    case_order,
    count_at_level,
    counts_in_packs,
    on_hand_display,
    order_quantity_text,
    reorder_display,
)
from inventory.tests.factories import AssetFactory, InventoryItemFactory, ItemSupplierFactory
from reorder_queue.models import ReorderRequest
from reorder_queue.services import default_quantity

pytestmark = pytest.mark.django_db

_USED = StockReconciliation.ReasonCode.USED_WITHOUT_SCAN


def _pack_item(mode=InventoryItem.CountMode.BY_LEVEL, case_size=12, **kwargs):
    """An item counted in whole cases of ``case_size`` base units."""
    kwargs.setdefault("image", None)
    kwargs.setdefault("base_unit", "bottle")
    item = InventoryItemFactory(**kwargs)
    case = PackagingLevel.objects.create(item=item, name="case", sort_order=0, base_units=case_size)
    PackagingLevel.objects.create(item=item, name="bottle", sort_order=1, base_units=1)
    item.count_mode = mode
    item.count_level = case
    item.save(update_fields=["count_mode", "count_level"])
    return item


def _box_item(case_size=12, **kwargs):
    """A pack-counting item whose rungs are BOTH sibilant nouns ("box"/"patch")."""
    kwargs.setdefault("image", None)
    kwargs.setdefault("base_unit", "patch")
    item = InventoryItemFactory(**kwargs)
    box = PackagingLevel.objects.create(item=item, name="box", sort_order=0, base_units=case_size)
    PackagingLevel.objects.create(item=item, name="patch", sort_order=1, base_units=1)
    item.count_mode = InventoryItem.CountMode.BY_LEVEL
    item.count_level = box
    item.save(update_fields=["count_mode", "count_level"])
    return item


def _case_item(**kwargs):
    """A LEGACY ``use_case_based_reorder`` item — cases without a packaging chain."""
    kwargs.setdefault("image", None)
    kwargs.setdefault("current_stock", 24)
    kwargs.setdefault("quantity_per_package", 10)
    return InventoryItemFactory(
        use_case_based_reorder=True,
        minimum_cases=kwargs.pop("minimum_cases", 2),
        reorder_cases=kwargs.pop("reorder_cases", 4),
        **kwargs,
    )


def _without_supplier_links(item):
    """``item`` re-read after its factory-made supplier link is deleted."""
    item.item_suppliers.all().delete()
    return InventoryItem.objects.get(pk=item.pk)


def _bridged_case_item(case_size=12, **kwargs):
    """A BRIDGED item — the legacy case columns AND a packaging chain at once.

    What ``bridge_case_reorder_to_packaging`` leaves behind: it writes the
    two-rung chain and points ``count_mode``/``count_level`` at the case rung
    while deliberately KEEPING ``use_case_based_reorder`` and its columns. The
    item form can produce the same shape by hand. The bridge copies
    ``reorder_cases`` into ``reorder_quantity``, so the two agree the moment it
    runs; they are set apart here because nothing keeps them in step afterwards
    and the columns are what the shape has to be pinned against.
    """
    kwargs.setdefault("use_case_based_reorder", True)
    kwargs.setdefault("minimum_cases", 2)
    kwargs.setdefault("reorder_cases", 4)
    return _pack_item(case_size=case_size, **kwargs)


def _every_item_shape():
    """One item of each shape ``reorder_display`` branches on, freshly built."""
    return {
        "each": InventoryItemFactory(
            image=None, current_stock=50, minimum_stock=10, reorder_quantity=25
        ),
        "each_below_minimum": InventoryItemFactory(
            image=None, current_stock=0, minimum_stock=100, reorder_quantity=25
        ),
        "pack": _pack_item(case_size=12, current_stock=35, minimum_stock=2, reorder_quantity=3),
        "pack_below_minimum": _pack_item(
            case_size=12, current_stock=0, minimum_stock=10, reorder_quantity=3
        ),
        "open_closed": _pack_item(
            mode=InventoryItem.CountMode.OPEN_CLOSED,
            case_size=12,
            current_stock=30,
            open_container_count=1,
            minimum_stock=6,
            reorder_quantity=1,
        ),
        "case_known_size": _case_item(reorder_quantity=25),
        "case_unknown_size": _case_item(reorder_quantity=40, quantity_per_package=0),
    }


class TestOrderQuantityIsTheOneFilingDerivation:
    """``order_quantity`` IS ``base_reorder_quantity`` — not a second opinion."""

    def test_it_equals_base_reorder_quantity_for_every_item_shape(self):
        """The payload a client files from and the number the PO paths derive agree.

        The point of the field: ``reorder_queue`` fills purchase-order pads from
        ``base_reorder_quantity`` and the scan page files from this key, so an
        item cannot be ordered in two different amounts depending on which
        surface asked.
        """
        for shape, item in _every_item_shape().items():
            assert reorder_display(item)["order_quantity"] == base_reorder_quantity(
                item
            ), f"{shape} diverged from base_reorder_quantity"

    def test_pack_counting_item_orders_whole_packs_of_base_units(self):
        """3 cases of 12 is 36 bottles, not the number 3.

        The raw ``reorder_quantity`` column for a pack-counting item is a count
        of PACKS; filed as base units it orders a twelfth of the intended order.
        """
        item = _pack_item(case_size=12, current_stock=35, minimum_stock=2, reorder_quantity=3)

        display = reorder_display(item)

        assert item.reorder_quantity == 3
        assert display["reorder_quantity"] == 3  # cases, as configured
        assert display["order_quantity"] == 36  # bottles, as filed

    def test_each_item_files_exactly_its_reorder_quantity(self):
        """Every item that has not opted into a pack mode is untouched."""
        item = InventoryItemFactory(
            image=None, current_stock=50, minimum_stock=10, reorder_quantity=25
        )

        display = reorder_display(item)

        assert display["reorder_quantity"] == 25
        assert display["order_quantity"] == 25

    def test_an_each_item_deep_below_minimum_files_the_shortage(self):
        """The ``each`` half of the shortage clause, pinned here as well.

        ``test_reorder_at_level.py`` owns ``base_reorder_quantity`` itself; this
        asserts the number REACHES a filing client, which is the only reason the
        key exists. Without it the parity assertion above moves with the
        derivation and cannot see the clause disappear.
        """
        item = InventoryItemFactory(
            image=None, current_stock=0, minimum_stock=100, reorder_quantity=25
        )

        assert reorder_display(item)["order_quantity"] == 100

    def test_a_deep_shortage_is_carried_into_what_is_filed(self):
        """``base_reorder_quantity``'s shortage clause reaches the filing client.

        This is the case the CONFIGURED amount cannot express: the item is ten
        cases short, so a reorder orders ten cases even though the standing
        quantity is three. A client that showed ``reorder_quantity`` and filed
        this would print 3 and order 120.
        """
        item = _pack_item(case_size=12, current_stock=0, minimum_stock=10, reorder_quantity=3)

        display = reorder_display(item)

        assert display["reorder_quantity"] == 3
        assert display["order_quantity"] == 120


class TestOrderTextNamesTheNumberItFiles:
    """The wording and the number are one answer, so a page cannot show a third."""

    def test_every_shape_words_the_exact_quantity_it_files(self):
        for shape, item in _every_item_shape().items():
            display = reorder_display(item)
            assert (
                str(display["order_quantity"]) in display["order_text"]
            ), f"{shape}: {display['order_text']!r} does not name {display['order_quantity']}"

    def test_pack_counting_text_leads_with_the_pack_reading(self):
        """The shelf noun leads, and the filed number rides along: 3 cases (36 bottles)."""
        item = _pack_item(case_size=12, current_stock=35, minimum_stock=2, reorder_quantity=3)

        assert reorder_display(item)["order_text"] == "3 cases (36 bottles)"

    def test_each_item_text_is_the_plain_base_unit_count(self):
        item = InventoryItemFactory(
            image=None, current_stock=50, minimum_stock=10, reorder_quantity=25
        )

        assert reorder_display(item)["order_text"] == "25 units"

    def test_one_pack_is_singular_in_both_readings(self):
        item = _pack_item(case_size=1, current_stock=0, minimum_stock=0, reorder_quantity=1)

        assert reorder_display(item)["order_text"] == "1 case (1 bottle)"

    def test_a_quantity_that_is_not_whole_packs_is_named_in_base_units_only(self):
        """No "2.5 cases": a member cannot act on a number of boxes that cannot exist.

        Reached directly rather than through ``reorder_display`` because
        ``base_reorder_quantity`` always returns whole packs for a pack-counting
        item; the guard exists for any caller that words a quantity of its own.
        """
        item = _pack_item(case_size=12, current_stock=35, minimum_stock=2, reorder_quantity=3)

        assert order_quantity_text(item, 30) == "30 bottles"

    def test_an_each_item_never_offers_a_pack_reading(self):
        item = InventoryItemFactory(image=None, base_unit="sheet", reorder_quantity=7)

        assert counts_in_packs(item) is False
        assert order_quantity_text(item, 7) == "7 sheets"


class TestASibilantUnitNounIsPluralisedTheWayTheWebDoes:
    """ "box" → "boxes", not "boxs" — on the server, because the server renders it.

    ``base_unit`` and ``PackagingLevel.name`` are hand-typed free text, and
    ``order_text`` is printed verbatim by the page that files the reorder. When
    ``_plural`` only appended "s" that page showed a member "25 brushs" for a
    quantity the web's own ``pluralizeUnit`` would have worded "25 brushes".
    """

    def test_an_each_item_with_a_sibilant_base_unit_reads_es(self):
        item = InventoryItemFactory(
            image=None, base_unit="brush", current_stock=50, minimum_stock=10, reorder_quantity=25
        )

        assert order_quantity_text(item, 25) == "25 brushes"
        assert reorder_display(item)["order_text"] == "25 brushes"

    def test_a_sibilant_count_level_name_reads_es_in_both_halves(self):
        item = _box_item(current_stock=35, minimum_stock=2, reorder_quantity=3)

        display = reorder_display(item)

        assert display["order_text"] == "3 boxes (36 patches)"
        assert order_quantity_text(item, 36) == "3 boxes (36 patches)"

    def test_the_on_hand_line_words_the_same_unit_the_same_way(self):
        """``reorder_display['text']`` moves too, and that is the correction."""
        item = _box_item(current_stock=24, minimum_stock=1, reorder_quantity=3)

        assert reorder_display(item)["text"] == "2 boxes on hand · reorder at 1 box"

    def test_exactly_one_is_still_singular(self):
        item = InventoryItemFactory(
            image=None, base_unit="brush", current_stock=50, minimum_stock=10, reorder_quantity=1
        )

        assert order_quantity_text(item, 1) == "1 brush"


class TestLegacyCaseBasedItemsOrderByTheCase:
    """ "We are ordering by cases and counting by items." (captain, 2026-09-05)

    A legacy ``use_case_based_reorder`` item with no packaging chain of its own
    used to DISPLAY ``reorder_cases`` while every filing path ordered
    ``reorder_quantity`` base units, so "Reorder Cases: 4" was discarded by the
    ordering path. Now ``base_reorder_quantity`` — and so every surface that
    files — orders enough whole cases to cover both ``reorder_cases`` and the
    current shortage. When case size is unknown, ordering stays exactly as it
    was before this change, including the shortage term.

    ``reorder_display.case_order`` says which happened and whether the two
    columns disagree, because an item that cannot be ordered by the case is a
    fact the operator needs rather than a value to invent. Counting — stock,
    the case count, the threshold — does not move.

    ``InventoryItem.reorder_cases``'s ``help_text`` is worded off this class.
    """

    def test_the_order_is_reorder_cases_times_the_order_pack_size(self):
        """The rule, with numbers on it: 4 cases of 10 is 40 units, whatever reorder_quantity says."""
        item = _case_item(reorder_cases=4, reorder_quantity=25, quantity_per_package=10)

        display = reorder_display(item)

        assert base_reorder_quantity(item) == 40
        assert display["unit"] == "case"
        assert display["reorder_quantity"] == 4
        assert display["order_quantity"] == 40
        assert display["order_text"] == "4 cases (40 units)"

    def test_columns_that_name_different_amounts_are_reported_as_disagreeing(self):
        """25 units against 4 cases of 10: the operator is told, not left to notice."""
        item = _case_item(reorder_cases=4, reorder_quantity=25, quantity_per_package=10)

        assert reorder_display(item)["case_order"] == {
            "reorder_cases": 4,
            "reorder_quantity": 25,
            "order_quantity": 40,
            "case_size": 10,
            "case_size_state": PACK_SIZE_KNOWN,
            "orders_cases": True,
            "columns_disagree": True,
        }

    def test_columns_that_name_the_same_amount_do_not_disagree(self):
        item = _case_item(reorder_cases=4, reorder_quantity=40, quantity_per_package=10)

        case = reorder_display(item)["case_order"]

        assert case["orders_cases"] is True
        assert case["columns_disagree"] is False

    def test_a_deeply_short_item_orders_enough_whole_cases_for_the_shortage(self):
        item = _case_item(
            reorder_cases=2,
            reorder_quantity=5,
            quantity_per_package=10,
            current_stock=0,
            minimum_stock=100,
        )

        assert base_reorder_quantity(item) == 100

    def test_a_shortage_is_rounded_up_to_a_whole_case(self):
        item = _case_item(
            reorder_cases=2,
            reorder_quantity=5,
            quantity_per_package=10,
            current_stock=0,
            minimum_stock=95,
        )

        assert base_reorder_quantity(item) == 100

    def test_the_pack_size_is_the_order_question_not_the_shelf_one(self):
        """A discontinued vendor's 12-pack describes the SHELF; the next order ships in 10s."""
        item = _case_item(
            reorder_cases=3,
            reorder_quantity=1,
            quantity_per_package=12,
            item_supplier_kwargs={"is_discontinued": True},
        )
        ItemSupplierFactory(item=item, quantity_per_package=10, is_primary=False)

        assert item.current_cases == 2  # 24 units / the shelf's 12
        assert base_reorder_quantity(item) == 30
        assert reorder_display(item)["case_order"]["case_size"] == 10

    @pytest.mark.parametrize(
        ("make_unknown", "state"),
        [
            (
                lambda: _case_item(reorder_cases=2, reorder_quantity=7, quantity_per_package=0),
                PACK_SIZE_RECORDED_ZERO,
            ),
            (
                lambda: _without_supplier_links(_case_item(reorder_cases=2, reorder_quantity=7)),
                PACK_SIZE_NOT_RECORDED,
            ),
            (
                lambda: _case_item(
                    reorder_cases=2,
                    reorder_quantity=7,
                    quantity_per_package=10,
                    item_supplier_kwargs={"is_discontinued": True},
                ),
                PACK_SIZE_NO_ORDERABLE_LINK,
            ),
        ],
        ids=["recorded_zero", "not_recorded", "no_orderable_link"],
    )
    def test_an_unknown_case_size_orders_as_before_and_says_it_cannot_order_cases(
        self, make_unknown, state
    ):
        """The captain's fallback, and the fact beside it — never an invented case size."""
        item = make_unknown()

        display = reorder_display(item)

        assert base_reorder_quantity(item) == 7
        assert display["order_quantity"] == 7
        assert display["order_text"] == "7 units"
        assert display["case_order"] == {
            "reorder_cases": 2,
            "reorder_quantity": 7,
            "order_quantity": 7,
            "case_size": None,
            "case_size_state": state,
            "orders_cases": False,
            "columns_disagree": None,
        }

    def test_counting_stays_in_individual_items(self):
        """Ordering moved; counting did not. Every count reads base units exactly as before."""
        item = _case_item(
            reorder_cases=4, reorder_quantity=25, quantity_per_package=10, current_stock=24
        )

        display = reorder_display(item)

        assert counts_in_packs(item) is False
        assert count_at_level(item) == 24
        assert on_hand_display(item)["mode"] == "each"
        assert on_hand_display(item)["base_units"] == 24
        assert display["current"] == pytest.approx(2.4)  # cases ON HAND, from units
        assert display["threshold"] == 2  # minimum_cases
        assert item.needs_reorder is False  # 2.4 cases > 2

    def test_an_item_that_is_not_case_based_is_not_governed(self):
        item = InventoryItemFactory(
            image=None, reorder_cases=4, reorder_quantity=25, quantity_per_package=10
        )

        assert case_order(item) is None
        assert reorder_display(item)["case_order"] is None
        assert base_reorder_quantity(item) == 25

    def test_a_bridged_item_reads_reorder_quantity_on_both_halves(self):
        """COUNTING MODE, not the legacy flag, decides whether ``reorder_cases`` means anything.

        ``counts_in_packs`` is tested first in ``reorder_display``, in
        ``base_reorder_quantity`` and in ``case_order``, so an item carrying the
        legacy flag AND a packaging chain reads ``reorder_quantity`` twice and
        ``reorder_cases`` never — the display in PACKS, the order in base units
        at the pack size.
        """
        item = _bridged_case_item(
            case_size=12, current_stock=36, minimum_stock=2, reorder_quantity=3, reorder_cases=4
        )

        display = reorder_display(item)

        assert counts_in_packs(item) is True
        assert display["unit"] == "case"
        assert display["reorder_quantity"] == 3  # reorder_quantity, in packs
        assert display["reorder_quantity"] != item.reorder_cases  # ...and never this column
        assert display["order_quantity"] == 3 * item.count_level.base_units == 36
        assert display["order_text"] == "3 cases (36 bottles)"
        assert display["case_order"] is None


class TestEveryFilingPathOrdersTheCaseFigure:
    """Each path that files or prefills an order, fed one legacy case item.

    4 cases of 10 against a ``reorder_quantity`` of 25: every path used to order
    25 (the pad rounded it to 30) and must now order 40. The item sits AT its
    base-unit floor so the pad's ``low_stock_q`` selects it.
    """

    def _item(self, **kwargs):
        kwargs.setdefault("current_stock", 5)
        kwargs.setdefault("minimum_stock", 5)
        return _case_item(reorder_cases=4, reorder_quantity=25, quantity_per_package=10, **kwargs)

    def _staff(self):
        user = get_user_model().objects.create_user(
            username=get_random_string(8), password=get_random_string(24), is_staff=True
        )
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_the_scan_page_payload_files_forty(self):
        item = self._item()

        response = APIClient().get(reverse("inventoryitem-detail", kwargs={"pk": str(item.id)}))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["reorder_display"]["order_quantity"] == 40
        assert response.data["reorder_display"]["case_order"]["orders_cases"] is True

    def test_the_purchase_order_pad_suggests_forty(self):
        item = self._item()

        response = self._staff().get("/api/reorders/purchase-orders/reorder_data/")

        assert response.status_code == status.HTTP_200_OK
        lines = [
            line
            for group in response.data["suppliers"]
            for line in group["items"]
            if line["item_id"] == str(item.id)
        ]
        assert [line["suggested_quantity"] for line in lines] == [40]

    def test_the_optimized_order_recommends_forty(self):
        item = self._item()

        response = self._staff().post(
            "/api/reorders/purchase-orders/create_optimized_order/", {}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        quantities = {
            str(line["item_id"]): line["recommended_quantity"]
            for rec in response.data.get("recommendations", [])
            for line in rec["items"]
        }
        assert quantities[str(item.id)] == 40

    def test_a_freshly_added_po_line_defaults_to_forty(self):
        item = self._item()

        assert default_quantity(item.item_suppliers.get()) == 40

    def test_the_maintenance_alert_files_forty(self):
        item = self._item(current_stock=0)
        maintenance_item = MaintenanceItem.objects.create(
            asset=AssetFactory(), title="Monthly inspection", description="", interval_days=30
        )
        MaintenanceMaterial.objects.create(
            maintenance_item=maintenance_item,
            name=item.name,
            quantity=Decimal("1.00"),
            inventory_item=item,
        )

        response = APIClient().get(
            reverse("maintenanceitem-check-material-stock", args=[maintenance_item.id])
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["low_stock_alerts"][0]["reorder_qty"] == 40

    def test_a_reconciliation_that_trips_the_floor_files_forty(self):
        item = self._item(current_stock=20)

        response = self._staff().post(
            "/api/inventory/reconciliations/batch/",
            {"rows": [{"item_id": str(item.id), "actual_count": 3, "reason": _USED}]},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert ReorderRequest.objects.get(item=item).quantity == 40
        item.refresh_from_db()
        assert item.current_stock == 3  # the count itself stayed in units

    def test_unknown_case_size_keeps_the_existing_reconciliation_calculation(self):
        item = _case_item(
            reorder_cases=4,
            reorder_quantity=25,
            quantity_per_package=0,
            current_stock=20,
            minimum_stock=5,
        )

        response = self._staff().post(
            "/api/inventory/reconciliations/batch/",
            {"rows": [{"item_id": str(item.id), "actual_count": 3, "reason": _USED}]},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert ReorderRequest.objects.get(item=item).quantity == 25

    def test_a_deeply_short_reconciliation_with_unknown_case_size_files_reorder_quantity(self):
        """Reconciliation never had a shortage term, and an unknown case size changes nothing.

        25 configured units against a 100-unit floor counted to zero: main filed
        25 on this path, so it still files 25 — not the 100 the pad's shortage
        clause would give.
        """
        item = _case_item(
            reorder_cases=4,
            reorder_quantity=25,
            quantity_per_package=0,
            current_stock=120,
            minimum_stock=100,
        )

        response = self._staff().post(
            "/api/inventory/reconciliations/batch/",
            {"rows": [{"item_id": str(item.id), "actual_count": 0, "reason": _USED}]},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert ReorderRequest.objects.get(item=item).quantity == 25

    def test_a_deeply_short_reconciliation_with_known_case_size_files_the_cases_only(self):
        """Same shape, case quantity: ``reorder_cases × case size`` with no shortage term.

        4 cases of 10 against a 100-unit floor counted to zero files 40, not the
        100 a case-rounded shortage would give, because this path's formula has
        never carried a shortage term.
        """
        item = _case_item(
            reorder_cases=4,
            reorder_quantity=25,
            quantity_per_package=10,
            current_stock=120,
            minimum_stock=100,
        )

        response = self._staff().post(
            "/api/inventory/reconciliations/batch/",
            {"rows": [{"item_id": str(item.id), "actual_count": 0, "reason": _USED}]},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert ReorderRequest.objects.get(item=item).quantity == 40

    @pytest.mark.parametrize("bridged", [False, True], ids=["unit", "pack"])
    def test_non_governed_reconciliation_keeps_its_configured_quantity(self, bridged):
        if bridged:
            item = _pack_item(
                case_size=12,
                current_stock=1200,
                minimum_stock=100,
                reorder_quantity=5,
                use_case_based_reorder=True,
            )
            expected = 60
        else:
            item = InventoryItemFactory(
                image=None,
                current_stock=120,
                minimum_stock=100,
                reorder_quantity=5,
            )
            expected = 5

        response = self._staff().post(
            "/api/inventory/reconciliations/batch/",
            {"rows": [{"item_id": str(item.id), "actual_count": 0, "reason": _USED}]},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert ReorderRequest.objects.get(item=item).quantity == expected


class TestNonLegacyQuantitiesStayOnTheirExistingPaths:
    @pytest.mark.parametrize("pack_counted", [False, True], ids=["unit", "pack"])
    def test_every_filing_path_keeps_its_pre_case_rule_quantity(self, pack_counted):
        if pack_counted:
            item = _pack_item(
                case_size=12,
                current_stock=0,
                minimum_stock=100,
                reorder_quantity=5,
                use_case_based_reorder=True,
            )
            existing_filing_quantity = 1200
            reconciliation_quantity = 60
        else:
            item = InventoryItemFactory(
                image=None,
                current_stock=0,
                minimum_stock=100,
                reorder_quantity=5,
                quantity_per_package=1,
            )
            existing_filing_quantity = 100
            reconciliation_quantity = 5

        user = get_user_model().objects.create_user(
            username=get_random_string(8), password=get_random_string(24), is_staff=True
        )
        client = APIClient()
        client.force_authenticate(user=user)

        reorder_data_response = client.get("/api/reorders/purchase-orders/reorder_data/")
        reorder_data_lines = [
            line
            for group in reorder_data_response.data["suppliers"]
            for line in group["items"]
            if line["item_id"] == str(item.id)
        ]
        optimized_response = client.post(
            "/api/reorders/purchase-orders/create_optimized_order/", {}, format="json"
        )
        optimized_quantities = {
            str(line["item_id"]): line["recommended_quantity"]
            for recommendation in optimized_response.data.get("recommendations", [])
            for line in recommendation["items"]
        }
        maintenance_item = MaintenanceItem.objects.create(
            asset=AssetFactory(), title="Quantity guard", description="", interval_days=30
        )
        MaintenanceMaterial.objects.create(
            maintenance_item=maintenance_item,
            name=item.name,
            quantity=Decimal("1.00"),
            inventory_item=item,
        )
        maintenance_response = APIClient().get(
            reverse("maintenanceitem-check-material-stock", args=[maintenance_item.id])
        )

        assert reorder_data_response.status_code == status.HTTP_200_OK
        assert [line["suggested_quantity"] for line in reorder_data_lines] == [
            existing_filing_quantity
        ]
        assert optimized_response.status_code == status.HTTP_200_OK
        assert optimized_quantities[str(item.id)] == existing_filing_quantity
        assert default_quantity(item.item_suppliers.get()) == existing_filing_quantity
        assert reorder_display(item)["order_quantity"] == existing_filing_quantity
        assert maintenance_response.status_code == status.HTTP_200_OK
        assert maintenance_response.data["low_stock_alerts"][0]["reorder_qty"] == (
            existing_filing_quantity
        )

        reconciliation_response = client.post(
            "/api/inventory/reconciliations/batch/",
            {"rows": [{"item_id": str(item.id), "actual_count": 0, "reason": _USED}]},
            format="json",
        )

        assert reconciliation_response.status_code == status.HTTP_201_CREATED
        assert ReorderRequest.objects.get(item=item).quantity == reconciliation_quantity


class TestTheFiledQuantityIsBaseUnitsEndToEnd:
    """The unit claim, proved against the endpoint and the stock it moves.

    Filed with a bare ``APIClient`` — NO credentials — on purpose. Most people
    who scan a shelf QR code are not registered members, and anonymous
    scan-to-reorder is the feature those printed labels exist for, so "an
    anonymous caller can still file" is part of what this module pins rather
    than an accident of the fixture.
    """

    def test_an_anonymous_caller_can_still_file_a_reorder(self):
        """The primary path: no token, no account, request accepted."""
        item = _pack_item(case_size=12, current_stock=35, minimum_stock=2, reorder_quantity=3)

        response = APIClient().post(
            reverse("reorderrequest-list"),
            {"item": str(item.id), "quantity": reorder_display(item)["order_quantity"]},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert ReorderRequest.objects.filter(item=item).count() == 1

    def test_a_scan_filing_order_quantity_receives_exactly_that_many_base_units(
        self, authenticated_client
    ):
        """Files ``order_quantity`` anonymously, then RECEIVES it through the real action.

        This is what establishes which of the two numbers the purchasing side is
        meant to receive: ``mark-received`` adds ``ReorderRequest.quantity`` to
        ``current_stock``, and ``current_stock`` is base units by definition. A
        page that filed the pack count would restock a twelfth of the order.

        The receipt half POSTs to ``reorderrequest-mark-received`` rather than
        doing the addition here. Hand-simulating it (``current_stock +=
        reorder.quantity``, then asserting the difference) is arithmetically
        true for every quantity and cannot fail, so it proved nothing about the
        claim above: were that action changed to convert through ``count_level``
        this test would still have passed.
        """
        item = _pack_item(case_size=12, current_stock=35, minimum_stock=2, reorder_quantity=3)
        order_quantity = reorder_display(item)["order_quantity"]
        client = APIClient()

        response = client.post(
            reverse("reorderrequest-list"),
            {
                "item": str(item.id),
                "quantity": order_quantity,
                "requested_by": "Anonymous",
                "request_notes": "Auto-submitted via QR scan",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        reorder = ReorderRequest.objects.get(pk=response.data["id"])
        assert reorder.quantity == 36

        item.refresh_from_db()
        before = item.current_stock

        staff_client, _ = authenticated_client
        receipt = staff_client.post(
            reverse("reorderrequest-mark-received", args=[reorder.id]),
            {},
            format="json",
        )

        assert receipt.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        # 3 whole cases of 12 bottles arrived, in the unit stock is counted in.
        assert item.current_stock - before == 36


class TestMaintenanceLowStockAlertFilesBaseUnits:
    """``check_material_stock``'s ``reorder_qty`` is POSTed straight through.

    ``MaintenanceDashboard``'s "Create reorder requests & continue" files this
    number verbatim, so it is a filing derivation and not a display one. The
    each-mode cases live in ``test_maintenance_stock_check.py`` beside the rest
    of that action; what is new here is the pack-counting shape it got wrong.
    """

    def _alerts_for(self, api_client, item):
        maintenance_item = MaintenanceItem.objects.create(
            asset=AssetFactory(), title="Monthly inspection", description="", interval_days=30
        )
        MaintenanceMaterial.objects.create(
            maintenance_item=maintenance_item,
            name=item.name,
            quantity=Decimal("1.00"),
            inventory_item=item,
        )
        response = api_client.get(
            reverse("maintenanceitem-check-material-stock", args=[maintenance_item.id])
        )
        assert response.status_code == status.HTTP_200_OK
        return response.data["low_stock_alerts"]

    def test_a_pack_counting_item_is_alerted_in_base_units(self, api_client):
        """The raw column would have filed 3 bottles for a reorder of 3 cases of 12."""
        item = _pack_item(case_size=12, current_stock=0, minimum_stock=2, reorder_quantity=3)

        alerts = self._alerts_for(api_client, item)

        assert len(alerts) == 1
        assert alerts[0]["reorder_qty"] == 36
        assert alerts[0]["reorder_qty"] == base_reorder_quantity(item)

    def test_an_each_item_near_its_minimum_files_its_configured_quantity(self, api_client):
        """The shortage (4) loses to the configured 25, so this shape is unchanged.

        Named for what it pins rather than for the whole ``each`` path: it sits
        on the side of ``max()`` where ``reorder_quantity`` wins, so it cannot
        see the shortage clause. ``test_an_each_item_deeply_short_files_the_shortage``
        below covers the side that does move.
        """
        item = InventoryItemFactory(
            image=None, current_stock=1, minimum_stock=5, reorder_quantity=25
        )

        alerts = self._alerts_for(api_client, item)

        assert alerts[0]["reorder_qty"] == 25

    def test_an_each_item_deeply_short_files_the_shortage(self, api_client):
        """``base_reorder_quantity``'s shortage clause reaches EVERY material.

        Not only pack-counted ones: this item filed 25 — the raw column — before
        the action moved onto that derivation, and files 100 now, which is what
        a purchase-order pad would order for the same shortfall.
        """
        item = InventoryItemFactory(
            image=None, current_stock=0, minimum_stock=100, reorder_quantity=25
        )

        alerts = self._alerts_for(api_client, item)

        assert alerts[0]["reorder_qty"] == 100
        assert alerts[0]["reorder_qty"] == base_reorder_quantity(item)
        assert alerts[0]["reorder_qty"] != item.reorder_quantity


class TestTheApiCarriesThePair:
    """A client cannot read what the serializer does not send."""

    def test_item_detail_serialises_order_quantity_and_order_text(self, authenticated_client):
        client, _ = authenticated_client
        item = _pack_item(case_size=12, current_stock=35, minimum_stock=2, reorder_quantity=3)

        response = client.get(reverse("inventoryitem-detail", kwargs={"pk": str(item.id)}))

        assert response.status_code == status.HTTP_200_OK
        display = response.data["reorder_display"]
        assert display["order_quantity"] == 36
        assert display["order_text"] == "3 cases (36 bottles)"

    def test_an_anonymous_scan_reads_the_pair_and_learns_nothing_new(self, api_client):
        """The QR-scan page is not behind a login, so the pair must reach it.

        LOAD-BEARING, not incidental. The page files nothing when this key is
        absent — it will not invent a quantity — so dropping the pair from the
        anonymous payload would silently switch off anonymous scan-to-reorder,
        which is the feature the printed labels exist for. This test is what
        makes that a build failure instead.

        And it must not be a DISCLOSURE: ``order_quantity`` is a function of
        ``reorder_quantity``, ``minimum_stock``, ``current_stock`` and the
        packaging chain, every one of which this same anonymous response already
        carried and the scan page already rendered. Asserted here so the claim
        is checkable rather than asserted in prose.
        """
        item = _pack_item(case_size=12, current_stock=35, minimum_stock=2, reorder_quantity=3)

        response = api_client.get(reverse("inventoryitem-detail", kwargs={"pk": str(item.id)}))

        assert response.status_code == status.HTTP_200_OK
        display = response.data["reorder_display"]
        assert display["order_quantity"] == 36

        # Every input the derivation reads was already in this payload.
        assert display["reorder_quantity"] == 3
        assert response.data["current_stock"] == 35
        assert response.data["minimum_stock"] == 2
        assert {level["base_units"] for level in response.data["packaging_levels"]} == {12, 1}
