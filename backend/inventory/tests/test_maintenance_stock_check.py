"""
Tests for MaintenanceMaterial ↔ InventoryItem linkage and the
check_material_stock DRF action on MaintenanceItemViewSet.
"""

from decimal import Decimal

from django.urls import reverse

import pytest
from rest_framework import status

from inventory.models import (
    InventoryItem,
    MaintenanceItem,
    MaintenanceMaterial,
    PackagingLevel,
)
from inventory.serializers import MaintenanceMaterialSerializer
from inventory.services.packaging import base_reorder_quantity
from inventory.tests.factories import AssetFactory, InventoryItemFactory

pytestmark = pytest.mark.django_db


def _make_item_with_material(*, inventory_item=None):
    asset = AssetFactory()
    item = MaintenanceItem.objects.create(
        asset=asset,
        title="Monthly inspection",
        description="Standard monthly checklist",
        interval_days=30,
    )
    material = MaintenanceMaterial.objects.create(
        maintenance_item=item,
        name=(inventory_item.name if inventory_item else "Generic material"),
        quantity=Decimal("1.00"),
        inventory_item=inventory_item,
    )
    return item, material


class TestMaintenanceMaterialInventoryItemFK:
    """AC #1: nullable FK to InventoryItem."""

    def test_fk_is_nullable(self):
        item, material = _make_item_with_material()
        assert material.inventory_item is None

    def test_fk_can_be_set(self):
        inv = InventoryItemFactory(current_stock=10, minimum_stock=2)
        item, material = _make_item_with_material(inventory_item=inv)
        material.refresh_from_db()
        assert material.inventory_item_id == inv.id

    def test_inventory_item_delete_sets_null(self):
        inv = InventoryItemFactory()
        item, material = _make_item_with_material(inventory_item=inv)
        inv.delete()
        material.refresh_from_db()
        assert material.inventory_item is None


class TestMaintenanceMaterialSerializerInventoryDetail:
    """AC #2: serializer exposes inventory_item_detail when FK is set."""

    def test_detail_is_null_when_fk_unset(self):
        _, material = _make_item_with_material()
        data = MaintenanceMaterialSerializer(material).data
        assert data["inventory_item"] is None
        assert data["inventory_item_detail"] is None

    def test_detail_populated_when_fk_set(self):
        inv = InventoryItemFactory(
            name="Widget",
            current_stock=5,
            minimum_stock=10,
            reorder_quantity=20,
        )
        _, material = _make_item_with_material(inventory_item=inv)
        data = MaintenanceMaterialSerializer(material).data
        detail = data["inventory_item_detail"]
        assert detail["id"] == str(inv.id)
        assert detail["name"] == "Widget"
        assert detail["current_stock"] == 5
        assert detail["minimum_stock"] == 10
        assert detail["reorder_quantity"] == 20


@pytest.mark.integration
class TestCheckMaterialStockAction:
    """AC #3: GET /api/inventory/maintenance-items/<id>/check_material_stock/."""

    def _url(self, item):
        return reverse("maintenanceitem-check-material-stock", args=[item.id])

    def test_no_linked_items_returns_empty(self, api_client):
        item, _ = _make_item_with_material()
        response = api_client.get(self._url(item))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"low_stock_alerts": []}

    def test_item_above_minimum_returns_no_alert(self, api_client):
        inv = InventoryItemFactory(current_stock=50, minimum_stock=10)
        item, _ = _make_item_with_material(inventory_item=inv)
        response = api_client.get(self._url(item))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["low_stock_alerts"] == []

    def test_item_below_minimum_returns_alert(self, api_client):
        inv = InventoryItemFactory(
            name="Filter",
            current_stock=1,
            minimum_stock=5,
            reorder_quantity=10,
        )
        item, material = _make_item_with_material(inventory_item=inv)
        response = api_client.get(self._url(item))
        assert response.status_code == status.HTTP_200_OK
        alerts = response.data["low_stock_alerts"]
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert["material_id"] == str(material.id)
        assert alert["item_id"] == str(inv.id)
        assert alert["name"] == "Filter"
        assert alert["current"] == 1
        assert alert["minimum"] == 5
        assert alert["reorder_qty"] == 10

    def test_item_at_minimum_returns_no_alert(self, api_client):
        inv = InventoryItemFactory(current_stock=5, minimum_stock=5)
        item, _ = _make_item_with_material(inventory_item=inv)
        response = api_client.get(self._url(item))
        assert response.data["low_stock_alerts"] == []

    def test_mixed_materials_only_low_stock_reported(self, api_client):
        asset = AssetFactory()
        item = MaintenanceItem.objects.create(
            asset=asset,
            title="Mixed",
            description="",
            interval_days=30,
        )
        healthy = InventoryItemFactory(current_stock=50, minimum_stock=10)
        low = InventoryItemFactory(current_stock=1, minimum_stock=5, name="LowOne")
        MaintenanceMaterial.objects.create(
            maintenance_item=item, name="healthy", quantity=Decimal("1.00"), inventory_item=healthy
        )
        MaintenanceMaterial.objects.create(
            maintenance_item=item, name="low", quantity=Decimal("1.00"), inventory_item=low
        )
        MaintenanceMaterial.objects.create(
            maintenance_item=item, name="unlinked", quantity=Decimal("1.00")
        )

        response = api_client.get(self._url(item))
        alerts = response.data["low_stock_alerts"]
        assert len(alerts) == 1
        assert alerts[0]["name"] == "LowOne"


# ---------------------------------------------------------------------------
# Shape builders for the unit-correctness suite below. Module-local, which is
# this suite's convention for them (``test_reorder_filing.py``,
# ``test_reorder_at_level.py``, ``test_po_at_level.py`` each carry their own).
# ---------------------------------------------------------------------------


def _pack_item(*, mode=InventoryItem.CountMode.BY_LEVEL, case_size=12, **kwargs):
    """An item COUNTED in whole cases of ``case_size`` base units."""
    kwargs.setdefault("image", None)
    kwargs.setdefault("base_unit", "bottle")
    item = InventoryItemFactory(**kwargs)
    case = PackagingLevel.objects.create(item=item, name="case", sort_order=0, base_units=case_size)
    PackagingLevel.objects.create(item=item, name="bottle", sort_order=1, base_units=1)
    item.count_mode = mode
    item.count_level = case
    item.save(update_fields=["count_mode", "count_level"])
    return item


def _half_configured_pack_item(**kwargs):
    """A pack ``count_mode`` with NO ``count_level`` — ``counts_in_packs`` is False.

    ``save()`` does not run ``full_clean()``, so the combination the model
    rejects on a form can still reach this action out of the database — which is
    why the shape is in the catalogue rather than assumed impossible.
    """
    kwargs.setdefault("image", None)
    kwargs.setdefault("base_unit", "bottle")
    item = InventoryItemFactory(**kwargs)
    item.count_mode = InventoryItem.CountMode.BY_LEVEL
    item.save(update_fields=["count_mode"])
    return item


def _legacy_case_item(**kwargs):
    """A LEGACY ``use_case_based_reorder`` item — cases, no packaging chain."""
    kwargs.setdefault("image", None)
    return InventoryItemFactory(
        use_case_based_reorder=True,
        minimum_cases=kwargs.pop("minimum_cases", 1),
        reorder_cases=kwargs.pop("reorder_cases", 4),
        **kwargs,
    )


def _every_counting_shape():
    """One material per shape this action can meet, each positioned deliberately.

    Derived from what the model supports rather than from the reported bug:
    every ``InventoryItem.CountMode`` (including a pack mode left
    half-configured), the legacy ``use_case_based_reorder`` columns with a KNOWN
    and an UNKNOWN case size, the bridged shape carrying both at once, and the
    two populations ``needs_reorder`` short-circuits on (``is_kit``,
    ``is_retired``). Keys say where each one sits relative to its minimum.
    """
    return {
        # ``each`` — both sides are base units, so nothing here was ever mixed.
        "each_below_minimum": InventoryItemFactory(
            image=None, current_stock=1, minimum_stock=5, reorder_quantity=10
        ),
        "each_at_minimum": InventoryItemFactory(
            image=None, current_stock=5, minimum_stock=5, reorder_quantity=10
        ),
        "each_above_minimum": InventoryItemFactory(
            image=None, current_stock=50, minimum_stock=10, reorder_quantity=10
        ),
        # THE REPORTED DEFECT: 24 bottles is 2 cases, under a 10-CASE minimum.
        "pack_units_above_cases_below": _pack_item(
            case_size=12, current_stock=24, minimum_stock=10, reorder_quantity=3
        ),
        "pack_below_in_both_units": _pack_item(
            case_size=12, current_stock=0, minimum_stock=2, reorder_quantity=3
        ),
        "pack_above_cases_minimum": _pack_item(
            case_size=12, current_stock=240, minimum_stock=10, reorder_quantity=3
        ),
        # The same mixing, in the other pack-counting mode.
        "open_closed_units_above_sealed_below": _pack_item(
            mode=InventoryItem.CountMode.OPEN_CLOSED,
            case_size=12,
            current_stock=24,
            open_container_count=1,
            minimum_stock=10,
            reorder_quantity=3,
        ),
        # Pack mode, no level: ``counts_in_packs`` is False, so base units —
        # identical numbers to the shape above, and it must NOT be alerted.
        "half_configured_pack": _half_configured_pack_item(
            current_stock=24, minimum_stock=10, reorder_quantity=3
        ),
        # THE SHAPE THAT BROKE LAST TIME. ``needs_reorder`` is False here — 3
        # cases against a 1-case minimum — while the base-unit floor it is also
        # configured with reads low (30 < 50). Its alert must survive.
        "legacy_case_known_size": _legacy_case_item(
            current_stock=30,
            minimum_stock=50,
            minimum_cases=1,
            quantity_per_package=10,
            reorder_quantity=25,
        ),
        # Same columns, but nothing records the case size, so ``current_cases``
        # is None and the item is judged in base units.
        "legacy_case_unknown_size": _legacy_case_item(
            current_stock=30,
            minimum_stock=50,
            minimum_cases=1,
            quantity_per_package=0,
            reorder_quantity=25,
        ),
        # BRIDGED — the legacy columns AND a chain; ``count_mode`` is the source
        # of truth, so this reads as 2 cases under a 10-case minimum.
        "bridged_case": _pack_item(
            case_size=12,
            current_stock=24,
            minimum_stock=10,
            reorder_quantity=3,
            use_case_based_reorder=True,
            minimum_cases=2,
            reorder_cases=4,
        ),
        # ``needs_reorder`` is False for a kit; the base comparison alerts.
        "kit": InventoryItemFactory(
            image=None, is_kit=True, current_stock=0, minimum_stock=5, reorder_quantity=10
        ),
        # Dropped before the predicate today, and stays dropped.
        "retired": InventoryItemFactory(
            image=None, is_retired=True, current_stock=0, minimum_stock=5, reorder_quantity=10
        ),
    }


def _maintenance_item_for(shapes):
    """One maintenance item carrying every shape in ``shapes`` as a material."""
    maintenance_item = MaintenanceItem.objects.create(
        asset=AssetFactory(), title="Quarterly service", description="", interval_days=90
    )
    for label, inv in shapes.items():
        MaintenanceMaterial.objects.create(
            maintenance_item=maintenance_item,
            name=label,
            quantity=Decimal("1.00"),
            inventory_item=inv,
        )
    return maintenance_item


@pytest.mark.integration
class TestLowStockAlertsAreUnitCorrect:
    """The predicate compares one unit, and the payload reports that unit.

    ``check_material_stock`` decided a warning with a raw
    ``current_stock >= minimum_stock``. For a pack-counting material —
    ``minimum_stock`` is a threshold in its COUNT unit for those modes — that
    read 24 bottles against a 10-CASE minimum and dropped a material holding a
    fifth of its minimum, and the payload paired the same two numbers, so a
    screen rendered "24/10" under a "below minimum stock" banner.

    The invariant the fix is held to, and the reason the mode-aware
    ``needs_reorder`` is NOT the fix: the set of materials alerted on must be a
    SUPERSET of what the raw comparison alerted on, for every shape. Ordering is
    by the case and counting is by the item; this surface is a counting one.
    """

    def _url(self, maintenance_item):
        return reverse("maintenanceitem-check-material-stock", args=[maintenance_item.id])

    def _alerts_by_shape(self, api_client, shapes):
        response = api_client.get(self._url(_maintenance_item_for(shapes)))
        assert response.status_code == status.HTTP_200_OK
        label_of = {str(inv.id): label for label, inv in shapes.items()}
        return {label_of[alert["item_id"]]: alert for alert in response.data["low_stock_alerts"]}

    def _one_alert(self, api_client, inv):
        maintenance_item, _ = _make_item_with_material(inventory_item=inv)
        response = api_client.get(self._url(maintenance_item))
        assert response.status_code == status.HTTP_200_OK
        return response.data["low_stock_alerts"]

    def test_alerts_are_a_superset_of_the_raw_base_unit_comparison(self, api_client):
        """THE INVARIANT: this predicate may ADD alerts and must remove none.

        The baseline is recomputed here from the columns rather than copied from
        a list of expected labels, so a shape added to the catalogue is held to
        it automatically. ``is_retired`` is part of the baseline because the
        skip for it already sat in front of the raw comparison.
        """
        shapes = _every_counting_shape()
        alerts = self._alerts_by_shape(api_client, shapes)

        raw_low = {
            label
            for label, inv in shapes.items()
            if not inv.is_retired and inv.current_stock < inv.minimum_stock
        }
        assert raw_low, "the catalogue must hold shapes the raw comparison alerted on"
        assert raw_low - set(alerts) == set()

    def test_the_pack_counting_shapes_are_what_the_fix_adds(self, api_client):
        """The other half of the invariant: exactly which shapes are new.

        Pinned as a set so a future change that adds a *further* alert has to
        come here and say so, rather than arriving unremarked.
        """
        shapes = _every_counting_shape()
        alerts = self._alerts_by_shape(api_client, shapes)

        raw_low = {
            label
            for label, inv in shapes.items()
            if not inv.is_retired and inv.current_stock < inv.minimum_stock
        }
        assert set(alerts) - raw_low == {
            "pack_units_above_cases_below",
            "open_closed_units_above_sealed_below",
            "bridged_case",
        }

    def test_every_alert_pairs_current_and_minimum_in_one_named_unit(self, api_client):
        """No emitted alert may pair two numbers a reader cannot compare.

        ``current < minimum`` in the unit ``unit`` names — the two operands of
        the comparison that raised the alert. "24/10" satisfied neither half.
        """
        shapes = _every_counting_shape()
        alerts = self._alerts_by_shape(api_client, shapes)

        assert alerts
        for label, alert in alerts.items():
            assert alert["unit"], label
            assert alert["current"] < alert["minimum"], label

    def test_a_pack_counted_material_below_its_case_minimum_is_alerted(self, api_client):
        """24 bottles is 2 cases under a 10-CASE minimum — the reported defect."""
        inv = _pack_item(
            case_size=12, name="Widget", current_stock=24, minimum_stock=10, reorder_quantity=3
        )

        alerts = self._one_alert(api_client, inv)

        assert len(alerts) == 1
        assert (alerts[0]["current"], alerts[0]["minimum"], alerts[0]["unit"]) == (2, 10, "case")

    def test_an_open_closed_material_below_its_sealed_minimum_is_alerted(self, api_client):
        """The same mixing in the other pack-counting mode; the open pack is not counted."""
        inv = _pack_item(
            mode=InventoryItem.CountMode.OPEN_CLOSED,
            case_size=12,
            current_stock=24,
            open_container_count=1,
            minimum_stock=10,
            reorder_quantity=3,
        )

        alerts = self._one_alert(api_client, inv)

        assert len(alerts) == 1
        assert (alerts[0]["current"], alerts[0]["minimum"], alerts[0]["unit"]) == (2, 10, "case")

    def test_a_legacy_case_based_material_with_no_chain_keeps_its_alert(self, api_client):
        """The shape a ``needs_reorder`` swap silently dropped last time.

        3 cases against a 1-case minimum, so the mode-aware property says fine,
        while the base-unit floor the same item carries (30 against 50) says low
        — and that is the comparison ``low_stock_q`` still applies to this shape.
        The alert is reported in the unit that decided it.
        """
        inv = _legacy_case_item(
            current_stock=30,
            minimum_stock=50,
            minimum_cases=1,
            quantity_per_package=10,
            reorder_quantity=25,
        )
        assert inv.current_cases == 3.0
        assert inv.needs_reorder is False

        alerts = self._one_alert(api_client, inv)

        assert len(alerts) == 1
        assert (alerts[0]["current"], alerts[0]["minimum"], alerts[0]["unit"]) == (30, 50, "unit")

    def test_a_kit_material_keeps_its_alert(self, api_client):
        """A kit is the other shape ``needs_reorder`` short-circuits away."""
        inv = InventoryItemFactory(
            image=None, is_kit=True, current_stock=0, minimum_stock=5, reorder_quantity=10
        )
        assert inv.needs_reorder is False

        alerts = self._one_alert(api_client, inv)

        assert len(alerts) == 1
        assert (alerts[0]["current"], alerts[0]["minimum"]) == (0, 5)

    def test_a_half_configured_pack_material_is_judged_in_base_units(self, api_client):
        """No usable ``count_level`` means no conversion — today's numbers, unchanged.

        Same columns as the alerted pack shape above; the only difference is
        that nothing records what a pack holds, so 24 is not low against 10.
        """
        inv = _half_configured_pack_item(current_stock=24, minimum_stock=10, reorder_quantity=3)

        assert self._one_alert(api_client, inv) == []

    def test_a_retired_material_is_never_alerted(self, api_client):
        """Pre-existing skip, in front of the predicate; the fix does not reach it."""
        inv = InventoryItemFactory(
            image=None, is_retired=True, current_stock=0, minimum_stock=5, reorder_quantity=10
        )

        assert self._one_alert(api_client, inv) == []

    def test_the_filed_reorder_quantity_stays_in_base_units(self, api_client):
        """``reorder_qty`` is the one number here NOT in the counting unit.

        The dashboard POSTs it verbatim as a ``ReorderRequest.quantity``, which
        is stored in base units, so making the displayed pair comparable must not
        drag this number into cases. Unchanged by this fix, and pinned beside the
        pair so the two cannot be conflated: the 8 cases that carry 2 up to the
        10-case minimum are 96 bottles, reported next to "2/10 case".
        """
        inv = _pack_item(
            case_size=12, current_stock=24, minimum_stock=10, reorder_quantity=3, base_unit="bottle"
        )

        alerts = self._one_alert(api_client, inv)

        assert len(alerts) == 1
        assert alerts[0]["reorder_qty"] == base_reorder_quantity(inv) == 96
        assert (alerts[0]["current"], alerts[0]["unit"]) == (2, "case")
