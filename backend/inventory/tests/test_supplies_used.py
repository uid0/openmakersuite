"""
Tests for the Asset "Supplies Used" report endpoint.

Covers ``AssetReportViewSet.supplies_used`` (serialized ComponentUsageEvent
usage + consumable WorkOrderMaterialUsage from PM work orders, date-windowed)
and its CSV ``export`` branch.
"""

import csv
import io
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

import pytest
from rest_framework import status

from inventory.models import (
    ComponentUsageEvent,
    MaintenanceItem,
    MaintenanceMaterial,
    SerializedComponent,
    WorkOrderMaterialUsage,
)
from inventory.tests.factories import AssetFactory, SerializedComponentFactory

URL = "/api/inventory/reports/assets/supplies_used/"
EXPORT_URL = "/api/inventory/reports/assets/export/"


def _event(asset, *, action, at, item_name="Ball bearing", serial="SN-A", actor=None):
    """Create a SerializedComponent + one ComponentUsageEvent tied to ``asset``."""
    component = SerializedComponentFactory(item__name=item_name, serial_number=serial)
    return ComponentUsageEvent.objects.create(
        component=component,
        asset=asset,
        action=action,
        at=at,
        actor=actor,
    )


def _completed_pm_with_material(
    asset,
    *,
    completed_at,
    material_name="Motor oil",
    quantity_planned=None,
    cost_per_unit=None,
    unit="qt",
    was_used=True,
    interval_days=90,
):
    """Create a MaintenanceItem + completed WorkOrder + one material-usage row."""
    from inventory.models import WorkOrder

    if quantity_planned is None:
        quantity_planned = Decimal("3.00")
    if cost_per_unit is None:
        cost_per_unit = Decimal("2.50")

    mi = MaintenanceItem.objects.create(
        asset=asset,
        title="Quarterly PM",
        interval_days=interval_days,
        estimated_cost=Decimal("0.00"),
    )
    wo = WorkOrder.objects.create(
        maintenance_item=mi,
        status=WorkOrder.Status.COMPLETED,
        completed_at=completed_at,
    )
    material = MaintenanceMaterial.objects.create(
        maintenance_item=mi,
        name=material_name,
        quantity=Decimal("1.00"),
        estimated_cost_per_unit=cost_per_unit,
    )
    usage = WorkOrderMaterialUsage.objects.create(
        work_order=wo,
        material=material,
        material_name=material_name,
        quantity_planned=quantity_planned,
        unit=unit,
        was_used=was_used,
    )
    return mi, wo, usage


def _rows_for_asset(data, asset):
    return [row for row in data if row["asset_id"] == str(asset.id)]


@pytest.mark.integration
class TestSuppliesUsedAuth:
    def test_requires_auth(self, api_client):
        response = api_client.get(URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.integration
class TestSuppliesUsedSerialized:
    """Serialized usage rows come from ComponentUsageEvent (install/consume)."""

    def test_install_and_consume_in_window_included(self, authenticated_client):
        client, user = authenticated_client
        asset = AssetFactory(name="Mill", asset_tag="A-1")
        now = timezone.now()
        _event(
            asset,
            action=SerializedComponent.Action.INSTALL,
            at=now - timedelta(days=3),
            item_name="Spindle bearing",
            serial="SN-INST",
            actor=user,
        )
        _event(
            asset,
            action=SerializedComponent.Action.CONSUME,
            at=now - timedelta(days=4),
            item_name="Cutting fluid pod",
            serial="SN-CONS",
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        rows = _rows_for_asset(response.data, asset)
        assert len(rows) == 2
        by_serial = {r["serial_number"]: r for r in rows}

        install = by_serial["SN-INST"]
        assert install["source"] == "serialized"
        assert install["asset_name"] == "Mill"
        assert install["item_name"] == "Spindle bearing"
        assert install["action"] == SerializedComponent.Action.INSTALL
        assert install["action_display"] == "Install"
        assert install["actor"] == user.username
        assert "used_at" in install

        consume = by_serial["SN-CONS"]
        assert consume["action"] == SerializedComponent.Action.CONSUME
        assert consume["action_display"] == "Consume"
        # No actor recorded on this event.
        assert consume["actor"] is None

    def test_excludes_out_of_window(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Lathe", asset_tag="A-2")
        now = timezone.now()
        # 45 days ago — outside the default 30-day window.
        _event(
            asset,
            action=SerializedComponent.Action.INSTALL,
            at=now - timedelta(days=45),
            serial="SN-OLD",
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        assert _rows_for_asset(response.data, asset) == []

    @pytest.mark.parametrize(
        "excluded_action",
        [
            SerializedComponent.Action.RECEIVE,
            SerializedComponent.Action.REMOVE,
            SerializedComponent.Action.RETIRE,
            SerializedComponent.Action.DISPOSE,
        ],
    )
    def test_excludes_non_install_consume_actions(self, authenticated_client, excluded_action):
        client, _ = authenticated_client
        asset = AssetFactory(name="Router", asset_tag="A-3")
        _event(
            asset,
            action=excluded_action,
            at=timezone.now() - timedelta(days=2),
            serial=f"SN-{excluded_action}",
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        assert _rows_for_asset(response.data, asset) == []

    def test_excludes_events_without_asset(self, authenticated_client):
        """A pure stock event (no asset) must not appear in any asset's rows."""
        client, _ = authenticated_client
        now = timezone.now()
        component = SerializedComponentFactory(item__name="Loose bolt", serial_number="SN-STOCK")
        ComponentUsageEvent.objects.create(
            component=component,
            asset=None,
            action=SerializedComponent.Action.CONSUME,
            at=now - timedelta(days=1),
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        assert all(row["source"] != "serialized" for row in response.data)


@pytest.mark.integration
class TestSuppliesUsedConsumable:
    """Consumable rows come from was_used WorkOrderMaterialUsage on completed PMs."""

    def test_used_material_in_window_included(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="HVAC", asset_tag="A-4")
        _, wo, _ = _completed_pm_with_material(
            asset,
            completed_at=timezone.now() - timedelta(days=5),
            material_name="Motor oil",
            quantity_planned=Decimal("3.00"),
            cost_per_unit=Decimal("2.50"),
            unit="qt",
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        rows = _rows_for_asset(response.data, asset)
        assert len(rows) == 1
        row = rows[0]
        assert row["source"] == "consumable"
        assert row["asset_name"] == "HVAC"
        assert row["item_name"] == "Motor oil"
        assert row["quantity"] == "3.00"
        assert row["unit"] == "qt"
        assert row["work_order_id"] == str(wo.id)
        # 3.00 × 2.50 = 7.50
        assert row["estimated_cost"] == "7.50"
        # Nothing was priced, so the reported cost falls back to the estimate.
        assert row["actual_cost"] is None
        assert row["cost"] == "7.50"
        assert "used_at" in row

    def test_excludes_material_not_used(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Press", asset_tag="A-5")
        _completed_pm_with_material(
            asset,
            completed_at=timezone.now() - timedelta(days=5),
            was_used=False,
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        assert _rows_for_asset(response.data, asset) == []

    def test_excludes_work_order_out_of_window(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Saw", asset_tag="A-6")
        _completed_pm_with_material(
            asset,
            completed_at=timezone.now() - timedelta(days=45),
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        assert _rows_for_asset(response.data, asset) == []

    def test_excludes_incomplete_work_order(self, authenticated_client):
        """A work order with no completion date cannot be windowed → excluded."""
        from inventory.models import WorkOrder

        client, _ = authenticated_client
        asset = AssetFactory(name="Kiln", asset_tag="A-7")
        mi = MaintenanceItem.objects.create(
            asset=asset, title="PM", interval_days=30, estimated_cost=Decimal("0.00")
        )
        wo = WorkOrder.objects.create(
            maintenance_item=mi,
            status=WorkOrder.Status.OPEN,
            completed_at=None,
        )
        material = MaintenanceMaterial.objects.create(
            maintenance_item=mi,
            name="Grease",
            quantity=Decimal("1.00"),
            estimated_cost_per_unit=Decimal("5.00"),
        )
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=material,
            material_name="Grease",
            quantity_planned=Decimal("1.00"),
            was_used=True,
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        assert _rows_for_asset(response.data, asset) == []

    def test_estimated_cost_null_when_material_deleted(self, authenticated_client):
        """material FK is SET_NULL; a used row with no material has no cost."""
        from inventory.models import WorkOrder

        client, _ = authenticated_client
        asset = AssetFactory(name="Compressor", asset_tag="A-8")
        mi = MaintenanceItem.objects.create(
            asset=asset, title="PM", interval_days=30, estimated_cost=Decimal("0.00")
        )
        wo = WorkOrder.objects.create(
            maintenance_item=mi,
            status=WorkOrder.Status.COMPLETED,
            completed_at=timezone.now() - timedelta(days=2),
        )
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            material_name="Deleted filter",
            quantity_planned=Decimal("2.00"),
            was_used=True,
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        rows = _rows_for_asset(response.data, asset)
        assert len(rows) == 1
        assert rows[0]["item_name"] == "Deleted filter"
        assert rows[0]["quantity"] == "2.00"
        assert rows[0]["estimated_cost"] is None
        assert rows[0]["actual_cost"] is None
        assert rows[0]["cost"] is None


@pytest.mark.integration
class TestSuppliesUsedFilteringAndSorting:
    def test_date_filter_honored(self, authenticated_client):
        """Explicit start_date/end_date narrows to events inside the window."""
        client, _ = authenticated_client
        asset = AssetFactory(name="Grinder", asset_tag="A-9")
        now = timezone.now()
        _event(
            asset,
            action=SerializedComponent.Action.INSTALL,
            at=now - timedelta(days=2),
            serial="SN-RECENT",
        )
        _event(
            asset,
            action=SerializedComponent.Action.INSTALL,
            at=now - timedelta(days=20),
            serial="SN-EARLIER",
        )

        # Window covering only the last 5 days.
        start = (now - timedelta(days=5)).date().isoformat()
        end = now.date().isoformat()
        response = client.get(URL, {"start_date": start, "end_date": end})
        assert response.status_code == status.HTTP_200_OK

        serials = {r["serial_number"] for r in _rows_for_asset(response.data, asset)}
        assert serials == {"SN-RECENT"}

    def test_rows_sorted_by_asset_name_then_used_at(self, authenticated_client):
        client, _ = authenticated_client
        alpha = AssetFactory(name="Alpha", asset_tag="A-A")
        beta = AssetFactory(name="Beta", asset_tag="A-B")
        now = timezone.now()

        # Alpha: a later event and an earlier event (should come out ascending).
        _event(
            alpha,
            action=SerializedComponent.Action.INSTALL,
            at=now - timedelta(days=2),
            serial="SN-ALPHA-NEW",
        )
        _event(
            alpha,
            action=SerializedComponent.Action.CONSUME,
            at=now - timedelta(days=8),
            serial="SN-ALPHA-OLD",
        )
        _event(
            beta,
            action=SerializedComponent.Action.INSTALL,
            at=now - timedelta(days=1),
            serial="SN-BETA",
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        names = [r["asset_name"] for r in response.data]
        # All Alpha rows precede all Beta rows.
        assert names == sorted(names)
        alpha_rows = _rows_for_asset(response.data, alpha)
        used_at_values = [r["used_at"] for r in alpha_rows]
        assert used_at_values == sorted(used_at_values)


@pytest.mark.integration
class TestSuppliesUsedExport:
    def test_export_csv_contains_both_sources(self, authenticated_client):
        client, user = authenticated_client
        asset = AssetFactory(name="Exporter", asset_tag="A-X")
        now = timezone.now()
        _event(
            asset,
            action=SerializedComponent.Action.INSTALL,
            at=now - timedelta(days=3),
            item_name="Bearing",
            serial="SN-EXP",
            actor=user,
        )
        _completed_pm_with_material(
            asset,
            completed_at=now - timedelta(days=4),
            material_name="Motor oil",
            quantity_planned=Decimal("2.00"),
            cost_per_unit=Decimal("4.00"),
            unit="qt",
        )

        response = client.get(EXPORT_URL, {"type": "supplies_used"})
        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "text/csv"
        assert "assets_supplies_used.csv" in response["Content-Disposition"]

        reader = csv.DictReader(io.StringIO(response.content.decode("utf-8")))
        assert reader.fieldnames == [
            "asset_id",
            "asset_name",
            "source",
            "item_name",
            "serial_number",
            "action",
            "action_display",
            "quantity",
            "unit",
            "used_at",
            "work_order_id",
            "estimated_cost",
            "actual_cost",
            "cost",
            "actor",
        ]
        rows = [r for r in reader if r["asset_id"] == str(asset.id)]
        assert len(rows) == 2
        by_source = {r["source"]: r for r in rows}

        serialized = by_source["serialized"]
        assert serialized["serial_number"] == "SN-EXP"
        assert serialized["action"] == SerializedComponent.Action.INSTALL
        assert serialized["actor"] == user.username
        # Consumable-only columns are blank on a serialized row.
        assert serialized["quantity"] == ""
        assert serialized["unit"] == ""
        assert serialized["work_order_id"] == ""
        assert serialized["estimated_cost"] == ""

        consumable = by_source["consumable"]
        assert consumable["item_name"] == "Motor oil"
        assert consumable["quantity"] == "2.00"
        assert consumable["unit"] == "qt"
        assert consumable["estimated_cost"] == "8.00"
        # Serialized-only columns are blank on a consumable row.
        assert consumable["serial_number"] == ""
        assert consumable["action"] == ""
        assert consumable["actor"] == ""

    def test_export_invalid_type_returns_error(self, authenticated_client):
        client, _ = authenticated_client
        response = client.get(EXPORT_URL, {"type": "not_a_report"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
class TestSuppliesUsedActualCost:
    """op-srrv (B5) — a consumable row reports what the material really cost
    (``WorkOrderMaterialUsage.unit_cost``, op-768w) where that was captured."""

    def test_actual_cost_wins_over_the_estimate(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Priced Unit", asset_tag="A-SUACT")
        _, _, usage = _completed_pm_with_material(
            asset,
            completed_at=timezone.now() - timedelta(days=3),
            material_name="Contactor",
            quantity_planned=Decimal("2.00"),
            cost_per_unit=Decimal("10.00"),
        )
        usage.quantity_used = Decimal("2.00")
        usage.unit_cost = Decimal("17.25")
        usage.save(update_fields=["quantity_used", "unit_cost"])

        rows = _rows_for_asset(client.get(URL).data, asset)
        assert len(rows) == 1
        row = rows[0]
        # The estimate is untouched for reference; cost reports the real spend.
        assert row["estimated_cost"] == "20.00"
        assert row["actual_cost"] == "34.50"
        assert row["cost"] == "34.50"

    def test_ad_hoc_out_of_pocket_line_reports_its_cost(self, authenticated_client):
        """An ad-hoc line has no template material, so it has no estimate — its
        receipt price is the only figure, and it is reported."""
        from inventory.models import WorkOrder

        client, _ = authenticated_client
        asset = AssetFactory(name="Corrective Unit", asset_tag="A-SUADHOC")
        wo = WorkOrder.objects.create(
            asset=asset,
            status=WorkOrder.Status.COMPLETED,
            completed_at=timezone.now() - timedelta(days=2),
        )
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material_name="Hardware-store fitting",
            is_ad_hoc=True,
            quantity_planned=Decimal("1.00"),
            quantity_used=Decimal("1.00"),
            was_used=True,
            unit_cost=Decimal("8.99"),
        )

        rows = _rows_for_asset(client.get(URL).data, asset)
        assert len(rows) == 1
        assert rows[0]["estimated_cost"] is None
        assert rows[0]["actual_cost"] == "8.99"
        assert rows[0]["cost"] == "8.99"

    def test_csv_export_carries_the_cost_columns(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="CSV Priced", asset_tag="A-SUCSV")
        _, _, usage = _completed_pm_with_material(
            asset,
            completed_at=timezone.now() - timedelta(days=3),
            material_name="Belt",
            quantity_planned=Decimal("1.00"),
            cost_per_unit=Decimal("5.00"),
        )
        usage.quantity_used = Decimal("1.00")
        usage.unit_cost = Decimal("6.50")
        usage.save(update_fields=["quantity_used", "unit_cost"])

        response = client.get("/api/inventory/reports/assets/export/", {"type": "supplies_used"})
        assert response.status_code == status.HTTP_200_OK
        reader = csv.DictReader(io.StringIO(response.content.decode("utf-8")))
        assert "actual_cost" in reader.fieldnames
        assert "cost" in reader.fieldnames
        rows = [r for r in reader if r["item_name"] == "Belt"]
        assert len(rows) == 1
        assert rows[0]["estimated_cost"] == "5.00"
        assert rows[0]["actual_cost"] == "6.50"
        assert rows[0]["cost"] == "6.50"
