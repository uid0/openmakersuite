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

import pytest
from rest_framework import status
from rest_framework.reverse import reverse
from rest_framework.test import APIClient

from inventory.models import (
    InventoryItem,
    MaintenanceItem,
    MaintenanceMaterial,
    PackagingLevel,
)
from inventory.services.packaging import (
    base_reorder_quantity,
    counts_in_packs,
    order_quantity_text,
    reorder_display,
)
from inventory.tests.factories import AssetFactory, InventoryItemFactory
from reorder_queue.models import ReorderRequest

pytestmark = pytest.mark.django_db


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


class TestLegacyCaseBasedItemsAreRecordedAsTheyBehave:
    """The one shape whose display and filing derivation name different amounts.

    ``base_reorder_quantity`` routes a legacy ``use_case_based_reorder`` item
    through the ``each`` branch — a deliberate preservation recorded in
    ``inventory.services.packaging``'s module docstring ("``each`` items —
    including legacy ``use_case_based_reorder`` ones — route through the same
    helpers and come out byte-for-byte where they were"). So an operator's
    ``reorder_cases`` sizes the DISPLAY while ``reorder_quantity`` sizes the
    ORDER, and nothing relates the two columns.

    Pinned here so the divergence is a recorded fact with a number on it rather
    than a surprise, and so closing it is a visible decision rather than a quiet
    edit. ``order_quantity`` reports what is actually filed either way, which is
    what lets the scan page stay honest while the columns disagree.
    """

    def test_display_reads_reorder_cases_while_the_order_reads_reorder_quantity(self):
        item = _case_item(reorder_cases=4, reorder_quantity=25, quantity_per_package=10)

        display = reorder_display(item)

        assert display["unit"] == "case"
        assert display["reorder_quantity"] == 4  # four cases, i.e. 40 base units
        assert display["order_quantity"] == 25  # what any filing path orders
        assert display["order_text"] == "25 units"

    def test_an_unknown_case_size_names_both_halves_in_base_units(self):
        """With no case size there is nothing to convert, and both halves agree."""
        item = _case_item(reorder_cases=2, reorder_quantity=40, quantity_per_package=0)

        display = reorder_display(item)

        assert display["unit"] == "unit"
        assert display["reorder_quantity"] == 40
        assert display["order_quantity"] == 40


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

    def test_a_scan_filing_order_quantity_receives_exactly_that_many_base_units(self):
        """Files ``order_quantity`` anonymously, then receives it, and counts stock.

        This is what establishes which of the two numbers the purchasing side is
        meant to receive: ``mark-received`` adds ``ReorderRequest.quantity`` to
        ``current_stock``, and ``current_stock`` is base units by definition. A
        page that filed the pack count would restock a twelfth of the order.
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
        item.current_stock += reorder.quantity
        item.save(update_fields=["current_stock"])

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

    def test_an_each_item_is_unchanged(self, api_client):
        item = InventoryItemFactory(
            image=None, current_stock=1, minimum_stock=5, reorder_quantity=25
        )

        alerts = self._alerts_for(api_client, item)

        assert alerts[0]["reorder_qty"] == 25


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
