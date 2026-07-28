"""Mode-aware reordering: trigger + suggested quantity at the count level (op-es7c, phase 2a).

Phase 1 (op-hzji) added the packaging chain and ``count_mode`` without letting
any quantity flow read them. Phase 2a lets exactly two things read them — when
an item needs reordering, and how much to suggest ordering — and nothing else.

The load-bearing assertion of the whole module is the FIRST class: an item in
``count_mode=each`` (which is every item that exists today, including the legacy
``use_case_based_reorder`` ones) must come out of these code paths exactly where
it went in. Everything after that covers items deliberately opted into a
pack-counting mode, where ``minimum_stock``/``reorder_quantity`` are read in the
item's OWN count unit rather than base units.
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models import F
from django.utils.crypto import get_random_string

import pytest
from rest_framework import status
from rest_framework.reverse import reverse
from rest_framework.test import APIClient

from inventory.models import InventoryItem, PackagingLevel
from inventory.services.packaging import (
    base_reorder_quantity,
    count_at_level,
    counts_in_packs,
    low_stock_q,
    reorder_display,
    reorder_threshold,
)
from inventory.tests.factories import InventoryItemFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_api_client():
    """Reconciliation is staff/SIG-admin gated (``_can_reconcile``)."""
    user = get_user_model().objects.create_user(
        username=get_random_string(8),
        email=f"{get_random_string(6)}@example.com",
        password=get_random_string(24),
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# A case of 12 bottles: the case is the counting rung, the bottle is the base
# unit ``current_stock`` is always kept in.
def _pack_item(mode=InventoryItem.CountMode.BY_LEVEL, case_size=12, **kwargs):
    """An item counted in whole cases of ``case_size`` base units."""
    kwargs.setdefault("image", None)
    kwargs.setdefault("base_unit", "bottle")
    item = InventoryItemFactory(**kwargs)
    case = PackagingLevel.objects.create(item=item, name="case", sort_order=0, base_units=case_size)
    PackagingLevel.objects.create(item=item, name="bottle", sort_order=1, base_units=1)
    item.count_mode = mode
    # ``each`` must not name a level (InventoryItem._clean_count_mode), so an
    # item built in that mode keeps the chain but is still counted in base units.
    item.count_level = None if mode == InventoryItem.CountMode.EACH else case
    item.save(update_fields=["count_mode", "count_level"])
    return item


class TestEachItemsAreUntouched:
    """The regression guard: nothing changes for an item in ``count_mode=each``."""

    @pytest.mark.parametrize(
        "current_stock,minimum_stock,expected",
        [(4, 10, True), (10, 10, True), (11, 10, False), (0, 0, True), (1, 0, False)],
    )
    def test_plain_unit_reorder_point_unchanged(self, current_stock, minimum_stock, expected):
        item = InventoryItemFactory(
            image=None, current_stock=current_stock, minimum_stock=minimum_stock
        )

        assert item.count_mode == InventoryItem.CountMode.EACH
        assert item.needs_reorder is expected

    def test_case_based_item_still_compares_fractional_cases(self):
        """``use_case_based_reorder`` keeps its own columns and its own arithmetic.

        24 units at 10 per case is 2.4 cases, which is above a 2-case minimum —
        the legacy comparison is fractional, and phase 2a must not round it.
        """
        item = InventoryItemFactory(
            image=None,
            current_stock=24,
            minimum_stock=0,
            use_case_based_reorder=True,
            minimum_cases=2,
            quantity_per_package=10,
        )

        assert item.current_cases == 2.4
        assert item.needs_reorder is False

        item.current_stock = 20
        assert item.needs_reorder is True

    def test_case_based_item_without_supplier_packaging_falls_back_to_units(self):
        """qpp of 1 leaves ``current_cases`` as the raw stock — unchanged behaviour."""
        item = InventoryItemFactory(
            image=None,
            current_stock=3,
            use_case_based_reorder=True,
            minimum_cases=5,
            quantity_per_package=1,
        )

        assert item.current_cases == 3
        assert item.needs_reorder is True

    def test_retired_item_never_needs_reorder(self):
        item = InventoryItemFactory(image=None, current_stock=0, minimum_stock=10, is_retired=True)

        assert item.needs_reorder is False

    def test_packaging_levels_alone_do_not_opt_an_item_in(self):
        """A chain is inert until ``count_mode`` says to count with it.

        This is the shape a half-migrated item takes: the rungs exist (maybe the
        admin added them for display) but the item is still counted in base
        units, and the legacy case columns still drive it.
        """
        item = _pack_item(
            mode=InventoryItem.CountMode.EACH,
            case_size=12,
            current_stock=24,
            minimum_stock=100,
            use_case_based_reorder=True,
            minimum_cases=1,
            quantity_per_package=12,
        )

        assert item.packaging_levels.count() == 2
        assert counts_in_packs(item) is False
        # 2 cases against a 1-case minimum: the LEGACY comparison, not
        # minimum_stock=100, which would have said True.
        assert item.needs_reorder is False

    def test_suggested_quantity_unchanged_for_each_items(self):
        """``max(shortage, reorder_quantity)`` in base units, exactly as before."""
        plenty = InventoryItemFactory(
            image=None, current_stock=8, minimum_stock=10, reorder_quantity=25
        )
        depleted = InventoryItemFactory(
            image=None, current_stock=0, minimum_stock=100, reorder_quantity=25
        )

        assert base_reorder_quantity(plenty) == 25
        assert base_reorder_quantity(depleted) == 100


class TestCountAtLevel:
    """``count_at_level`` — the whole count in the unit the item is counted in."""

    def test_each_returns_base_units(self):
        item = InventoryItemFactory(image=None, current_stock=37)

        assert counts_in_packs(item) is False
        assert count_at_level(item) == 37

    def test_by_level_returns_whole_packs(self):
        """29 bottles at 12 per case is 2 whole cases — the partial one is not counted."""
        item = _pack_item(case_size=12, current_stock=29)

        assert counts_in_packs(item) is True
        assert count_at_level(item) == 2

    def test_open_closed_counts_sealed_packs_only(self):
        item = _pack_item(
            mode=InventoryItem.CountMode.OPEN_CLOSED,
            case_size=12,
            current_stock=29,
            open_container_count=1,
        )

        assert count_at_level(item) == 2

    def test_exact_multiple_has_no_remainder(self):
        item = _pack_item(case_size=12, current_stock=36)

        assert count_at_level(item) == 3

    def test_missing_count_level_falls_back_to_base_units(self):
        """A half-configured item must degrade, never crash."""
        item = _pack_item(case_size=12, current_stock=29)
        item.count_level = None

        assert counts_in_packs(item) is False
        assert count_at_level(item) == 29

    def test_zero_base_units_level_falls_back_to_base_units(self):
        """A rung written around the validators is treated as unusable, not fatal."""
        item = _pack_item(case_size=12, current_stock=29)
        PackagingLevel.objects.filter(pk=item.count_level_id).update(base_units=0)
        item.refresh_from_db()

        assert counts_in_packs(item) is False
        assert count_at_level(item) == 29


class TestNeedsReorderAtLevel:
    """The trigger for pack-counting items: whole packs vs ``minimum_stock``."""

    @pytest.mark.parametrize(
        "current_stock,expected",
        # minimum_stock=2 cases of 12: 2 whole cases or fewer trips it.
        [(0, True), (23, True), (24, True), (35, True), (36, False), (100, False)],
    )
    def test_by_level_trips_on_whole_pack_count(self, current_stock, expected):
        item = _pack_item(case_size=12, current_stock=current_stock, minimum_stock=2)

        assert item.needs_reorder is expected

    def test_open_closed_ignores_the_open_container(self):
        """36 bottles is 3 sealed cases; opening one leaves 2 sealed + the open one.

        The sealed count is what trips the reorder point, so the same stock reads
        differently once a case is opened — which is the whole point of the mode.
        """
        item = _pack_item(
            mode=InventoryItem.CountMode.OPEN_CLOSED,
            case_size=12,
            current_stock=36,
            minimum_stock=2,
        )
        assert item.needs_reorder is False

        # A case is opened and half-used: 30 bottles = 2 sealed + 1 open.
        item.current_stock = 30
        item.open_container_count = 1
        assert count_at_level(item) == 2
        assert item.needs_reorder is True

    def test_count_mode_is_the_source_of_truth_not_the_legacy_columns(self):
        """A pack-counting item never consults ``use_case_based_reorder``.

        The legacy columns are set to values that would say the OPPOSITE, so a
        path that still read them would fail this.
        """
        item = _pack_item(
            case_size=12,
            current_stock=24,
            minimum_stock=2,
            use_case_based_reorder=True,
            minimum_cases=1,
            quantity_per_package=12,
        )

        # Legacy math: 24/12 = 2.0 cases > 1 minimum case -> False.
        assert item.current_cases == 2.0
        # Pack-counting math: 2 whole cases <= 2 minimum -> True.
        assert item.needs_reorder is True

    def test_retired_pack_counting_item_never_needs_reorder(self):
        item = _pack_item(case_size=12, current_stock=0, minimum_stock=2, is_retired=True)

        assert item.needs_reorder is False


class TestSuggestedQuantityAtLevel:
    """``base_reorder_quantity`` — still base units, derived at the count level."""

    def test_new_mode_derives_base_from_reorder_quantity_times_level(self):
        item = _pack_item(case_size=12, current_stock=24, minimum_stock=2, reorder_quantity=3)

        # 3 cases of 12 bottles.
        assert base_reorder_quantity(item) == 36

    def test_new_mode_covers_a_deep_shortage(self):
        """The count-level twin of the ``each`` shortage clause."""
        item = _pack_item(case_size=12, current_stock=0, minimum_stock=10, reorder_quantity=3)

        # 10 cases short beats the 3-case standing quantity.
        assert base_reorder_quantity(item) == 120

    def test_open_closed_uses_the_sealed_count_for_the_shortage(self):
        item = _pack_item(
            mode=InventoryItem.CountMode.OPEN_CLOSED,
            case_size=12,
            current_stock=30,
            open_container_count=1,
            minimum_stock=6,
            reorder_quantity=1,
        )

        # 2 sealed cases against a 6-case minimum -> 4 cases short.
        assert base_reorder_quantity(item) == 48

    def test_half_configured_item_uses_base_unit_math(self):
        item = _pack_item(case_size=12, current_stock=5, minimum_stock=10, reorder_quantity=3)
        item.count_level = None

        assert base_reorder_quantity(item) == 5


class TestLowStockQuerysetFilter:
    """``low_stock_q`` — the SQL twin, and its ``each`` parity guarantee."""

    def test_each_selection_is_byte_for_byte_the_old_filter(self):
        """Every shape of ``each`` item, selected by both predicates, must match."""
        InventoryItemFactory(image=None, current_stock=1, minimum_stock=10)
        InventoryItemFactory(image=None, current_stock=10, minimum_stock=10)
        InventoryItemFactory(image=None, current_stock=50, minimum_stock=10)
        InventoryItemFactory(
            image=None,
            current_stock=3,
            minimum_stock=10,
            use_case_based_reorder=True,
            minimum_cases=2,
            quantity_per_package=12,
        )
        # A chain present but not opted into: still an ``each`` item.
        _pack_item(mode=InventoryItem.CountMode.EACH, current_stock=5, minimum_stock=10)

        legacy = set(
            InventoryItem.objects.filter(current_stock__lte=F("minimum_stock")).values_list(
                "id", flat=True
            )
        )
        current = set(InventoryItem.objects.filter(low_stock_q()).values_list("id", flat=True))

        assert legacy == current
        assert legacy  # the comparison would be vacuous on an empty set

    def test_pack_counting_item_selected_on_whole_packs(self):
        """The item the old base-unit filter could never have found.

        35 bottles is far above a ``minimum_stock`` of 2, so
        ``current_stock <= minimum_stock`` says no — but it is only 2 whole
        cases, which is at the reorder point.
        """
        low = _pack_item(case_size=12, current_stock=35, minimum_stock=2)
        stocked = _pack_item(case_size=12, current_stock=36, minimum_stock=2)

        selected = set(InventoryItem.objects.filter(low_stock_q()).values_list("id", flat=True))

        assert low.id in selected
        assert stocked.id not in selected

    def test_filter_agrees_with_the_property_across_modes(self):
        """The SQL predicate and ``needs_reorder`` must not drift.

        Retirement is excluded from the comparison: the property short-circuits
        on it and the callers add ``is_retired=False`` alongside the filter.
        Legacy case-based items are excluded too — their SQL filter has ALWAYS
        been the base-unit comparison while the property compares cases, a
        pre-existing divergence phase 2a deliberately preserves.
        """
        items = [
            InventoryItemFactory(image=None, current_stock=1, minimum_stock=10),
            InventoryItemFactory(image=None, current_stock=99, minimum_stock=10),
            _pack_item(case_size=12, current_stock=35, minimum_stock=2),
            _pack_item(case_size=12, current_stock=36, minimum_stock=2),
            _pack_item(
                mode=InventoryItem.CountMode.OPEN_CLOSED,
                case_size=12,
                current_stock=30,
                minimum_stock=2,
                open_container_count=1,
            ),
        ]
        selected = set(InventoryItem.objects.filter(low_stock_q()).values_list("id", flat=True))

        for item in items:
            assert (item.id in selected) is item.needs_reorder, item.id


class TestReorderDisplay:
    """The presentation helper P3 will label thresholds with."""

    def test_pack_counting_item_reads_in_its_own_unit(self):
        item = _pack_item(case_size=12, current_stock=35, minimum_stock=2, reorder_quantity=3)

        assert reorder_threshold(item) == (2, "case")
        display = reorder_display(item)
        assert display["unit"] == "case"
        assert display["threshold"] == 2
        assert display["current"] == 2
        assert display["reorder_quantity"] == 3
        assert display["needs_reorder"] is True
        assert display["text"] == "2 cases on hand · reorder at 2 cases"

    def test_case_based_item_reads_in_cases(self):
        item = InventoryItemFactory(
            image=None,
            current_stock=24,
            use_case_based_reorder=True,
            minimum_cases=2,
            reorder_cases=4,
            quantity_per_package=10,
        )

        assert reorder_threshold(item) == (2, "case")
        display = reorder_display(item)
        assert display["current"] == 2.4
        assert display["reorder_quantity"] == 4

    def test_plain_item_reads_in_base_units(self):
        item = InventoryItemFactory(
            image=None, current_stock=5, minimum_stock=10, reorder_quantity=25
        )

        assert reorder_threshold(item) == (10, "unit")
        display = reorder_display(item)
        assert display["mode"] == InventoryItem.CountMode.EACH
        assert display["current"] == 5
        assert display["threshold"] == 10
        assert display["reorder_quantity"] == 25

    def test_serializer_exposes_needs_reorder_and_reorder_display(self, authenticated_client):
        client, _ = authenticated_client
        item = _pack_item(case_size=12, current_stock=35, minimum_stock=2, reorder_quantity=3)

        response = client.get(reverse("inventoryitem-detail", kwargs={"pk": str(item.id)}))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["needs_reorder"] is True
        assert response.data["reorder_display"]["unit"] == "case"
        assert response.data["reorder_display"]["threshold"] == 2
        assert response.data["reorder_display"]["current"] == 2


class TestBridgeCaseReorderCommand:
    """``bridge_case_reorder_to_packaging`` — opt-in migration of legacy case items."""

    def _run(self, *args):
        out = StringIO()
        call_command("bridge_case_reorder_to_packaging", *args, stdout=out)
        return out.getvalue()

    def _eligible_item(self, **kwargs):
        kwargs.setdefault("image", None)
        kwargs.setdefault("current_stock", 24)
        kwargs.setdefault("minimum_stock", 999)
        kwargs.setdefault("reorder_quantity", 999)
        kwargs.setdefault("quantity_per_package", 12)
        return InventoryItemFactory(
            use_case_based_reorder=True,
            minimum_cases=2,
            reorder_cases=4,
            **kwargs,
        )

    def test_dry_run_reports_and_writes_nothing(self):
        item = self._eligible_item()

        output = self._run()

        assert "Would bridge 1 item(s)." in output
        assert "1 case = 12 unit" in output
        assert "minimum_stock 999 -> 2" in output
        assert "reorder_quantity 999 -> 4" in output
        assert "Dry run" in output

        item.refresh_from_db()
        assert item.count_mode == InventoryItem.CountMode.EACH
        assert item.count_level is None
        assert item.minimum_stock == 999
        assert not item.packaging_levels.exists()

    def test_apply_builds_the_chain_and_maps_the_thresholds(self):
        item = self._eligible_item()

        output = self._run("--apply")

        assert "Bridged 1 item(s)." in output
        item.refresh_from_db()
        assert item.count_mode == InventoryItem.CountMode.BY_LEVEL
        assert item.count_level.name == "case"
        assert item.count_level.base_units == 12
        assert item.minimum_stock == 2
        assert item.reorder_quantity == 4
        assert list(
            item.packaging_levels.order_by("sort_order").values_list("name", "base_units")
        ) == [("case", 12), ("unit", 1)]
        # The legacy columns stay put — deprecating them is a later step.
        assert item.use_case_based_reorder is True
        assert item.minimum_cases == 2
        assert item.reorder_cases == 4

    def test_bridged_item_keeps_its_reorder_verdict(self):
        """Equivalence: a whole number of cases trips identically before and after."""
        item = self._eligible_item(current_stock=24)
        before = item.needs_reorder

        self._run("--apply")

        item.refresh_from_db()
        assert before is True
        assert item.needs_reorder is before

    def test_bridged_item_keeps_a_negative_verdict_too(self):
        item = self._eligible_item(current_stock=36)
        before = item.needs_reorder

        self._run("--apply")

        item.refresh_from_db()
        assert before is False
        assert item.needs_reorder is before

    def test_partial_case_at_the_threshold_is_reported_as_changing(self):
        """The one documented shift: fractional cases vs whole packs.

        30 units at 12 per case is 2.5 cases — above a 2-case minimum by the
        legacy comparison, but only 2 WHOLE cases once the item counts packs.
        The command has to say so before anyone applies it.
        """
        item = self._eligible_item(current_stock=30)
        assert item.needs_reorder is False

        output = self._run()

        assert "needs reorder: False -> True" in output
        assert "CHANGES" in output
        assert "1 item(s) change whether they currently need reordering" in output

        self._run("--apply")
        item.refresh_from_db()
        assert item.needs_reorder is True

    def test_is_idempotent(self):
        item = self._eligible_item()
        self._run("--apply")

        output = self._run("--apply")

        assert "Bridged 0 item(s)." in output
        assert "already has packaging levels" in output
        item.refresh_from_db()
        assert item.packaging_levels.count() == 2
        assert item.minimum_stock == 2

    def test_skips_items_without_a_supplier_case_size(self):
        item = self._eligible_item(quantity_per_package=1)

        output = self._run("--apply")

        assert "Bridged 0 item(s)." in output
        assert "supplier quantity_per_package is not more than 1" in output
        item.refresh_from_db()
        assert item.count_mode == InventoryItem.CountMode.EACH

    def test_ignores_items_that_are_not_case_based(self):
        item = InventoryItemFactory(image=None, quantity_per_package=12)

        output = self._run("--apply")

        assert "Bridged 0 item(s)." in output
        assert item.name not in output

    def test_names_the_base_rung_after_the_items_base_unit(self):
        item = self._eligible_item(base_unit="bottle")

        self._run("--apply")

        item.refresh_from_db()
        assert list(
            item.packaging_levels.order_by("sort_order").values_list("name", flat=True)
        ) == ["case", "bottle"]


class TestReconciliationAutoReorderIsModeAware:
    """A counted-stock reconciliation raises its auto-reorder at the count level.

    Reconciliation duplicates the reorder trigger inline rather than calling
    ``needs_reorder``, and stores ``reorder_quantity`` straight into the
    request. For a pack-counting item both of those read pack amounts, so both
    have to convert (op-es7c); for an ``each`` item both are identities.
    """

    URL = "/api/inventory/reconciliations/batch/"

    def _post(self, client, item, actual_count):
        from inventory.models import StockReconciliation

        return client.post(
            self.URL,
            {
                "rows": [
                    {
                        "item_id": str(item.id),
                        "actual_count": actual_count,
                        "reason": StockReconciliation.ReasonCode.USED_WITHOUT_SCAN,
                    }
                ]
            },
            format="json",
        )

    def test_each_item_reorder_quantity_unchanged(self, staff_api_client):
        from reorder_queue.models import ReorderRequest

        item = InventoryItemFactory(
            image=None, current_stock=20, minimum_stock=10, reorder_quantity=25
        )

        response = self._post(staff_api_client, item, 3)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["reorders_created"] == 1
        assert ReorderRequest.objects.get(item=item).quantity == 25

    def test_pack_counted_item_converts_to_base_units(self, staff_api_client):
        """Counting 35 bottles leaves 2 whole cases against a 2-case minimum."""
        from reorder_queue.models import ReorderRequest

        item = _pack_item(case_size=12, current_stock=100, minimum_stock=2, reorder_quantity=3)

        response = self._post(staff_api_client, item, 35)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["reorders_created"] == 1
        # 3 cases of 12 bottles — NOT the bare 3 that the pack count would be.
        assert ReorderRequest.objects.get(item=item).quantity == 36

    def test_pack_counted_item_above_its_reorder_point_raises_nothing(self, staff_api_client):
        """36 bottles is 3 whole cases, above the 2-case minimum.

        The old base-unit comparison (36 <= 2) also said "no", so this pins the
        other half of the trigger: raw base units must not be compared against a
        pack threshold in EITHER direction.
        """
        from reorder_queue.models import ReorderRequest

        item = _pack_item(case_size=12, current_stock=100, minimum_stock=2, reorder_quantity=3)

        response = self._post(staff_api_client, item, 36)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["reorders_created"] == 0
        assert not ReorderRequest.objects.filter(item=item).exists()
