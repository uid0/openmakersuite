"""A work order without a PM template still works everywhere (op-svut).

``WorkOrder.maintenance_item`` became nullable so a *corrective* work order —
one raised from a reported problem rather than generated from a preventive
schedule — can exist. Nothing in this file creates one through a product
feature (there is no promote-a-problem action yet); each test builds the shape
directly, ``WorkOrder(maintenance_item=None, asset=…)``, and drives it through
a surface that used to reach the asset via ``maintenance_item.asset``.

Every one of those surfaces would previously have raised ``AttributeError`` or
silently omitted the row, so these tests are the regression net for the whole
refactor. The preventive path is asserted alongside where the two must agree.
"""

import importlib
from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import (
    MaintenanceItem,
    MaintenanceMaterial,
    WorkOrder,
    WorkOrderMaterialUsage,
)
from inventory.serializers import WorkOrderListSerializer, WorkOrderSerializer
from inventory.services.work_order_context import build_work_order_context
from inventory.services.work_order_loto import create_loto_completions
from inventory.services.work_order_omr import dynamic_target_ids
from inventory.tests.factories import AssetFactory
from inventory.utils.work_order_pdf import generate_work_order_omr_pdf, generate_work_order_pdf

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _corrective_wo(asset=None, **kwargs):
    """The shape this whole bead exists for: an asset, no PM template."""
    asset = asset or AssetFactory()
    return WorkOrder.objects.create(maintenance_item=None, asset=asset, **kwargs)


def _preventive_wo(asset=None, *, interval_days=30, title="Monthly inspection", **kwargs):
    """The classic shape: created with only ``maintenance_item=``, as callers do."""
    asset = asset or AssetFactory()
    item = MaintenanceItem.objects.create(
        asset=asset,
        title=title,
        description="Standard monthly checklist",
        interval_days=interval_days,
        estimated_time_minutes=45,
    )
    return WorkOrder.objects.create(maintenance_item=item, **kwargs)


def _staff_client():
    user = get_user_model().objects.create_user(
        username="wo_corrective_staff",
        email="staff@example.com",
        password="pw-not-secret-test",  # noqa: S106 — test fixture
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def _used_material(work_order, *, cost_per_unit="4.00", quantity="3"):
    """A consumed material so the cost/supplies reports have something to report.

    ``MaintenanceMaterial`` hangs off a ``MaintenanceItem``, so the spec lives on
    a template with no work orders of its own — it must not add cost rows of its
    own to the reports under test.
    """
    spec_item = MaintenanceItem.objects.create(
        asset=work_order.asset,
        title="Material spec (no work orders)",
        interval_days=None,
    )
    material = MaintenanceMaterial.objects.create(
        maintenance_item=spec_item,
        name="Filter cartridge",
        quantity=Decimal(quantity),
        unit="ea",
        estimated_cost_per_unit=Decimal(cost_per_unit),
    )
    return WorkOrderMaterialUsage.objects.create(
        work_order=work_order,
        material=material,
        material_name=material.name,
        quantity_planned=Decimal(quantity),
        quantity_used=Decimal(quantity),
        unit="ea",
        was_used=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Model: the asset invariant
# ─────────────────────────────────────────────────────────────────────────────
class TestAssetInvariant:
    def test_save_backfills_asset_from_the_template(self):
        """Every existing caller passes only ``maintenance_item=`` — still fine."""
        wo = _preventive_wo()
        assert wo.asset_id == wo.maintenance_item.asset_id

    def test_backfill_survives_a_reload(self):
        wo = _preventive_wo()
        assert WorkOrder.objects.get(pk=wo.pk).asset_id == wo.maintenance_item.asset_id

    def test_backfill_applies_on_a_partial_save(self):
        """``update_fields`` must not pin the write to a column set without asset."""
        wo = _preventive_wo()
        expected_asset_id = wo.maintenance_item.asset_id
        WorkOrder.objects.filter(pk=wo.pk).update(asset=None)

        stale = WorkOrder.objects.get(pk=wo.pk)
        stale.status = WorkOrder.Status.IN_PROGRESS
        stale.save(update_fields=["status"])

        assert WorkOrder.objects.get(pk=wo.pk).asset_id == expected_asset_id

    def test_explicit_asset_is_not_overwritten(self):
        asset = AssetFactory()
        wo = _corrective_wo(asset)
        assert wo.asset_id == asset.id
        assert wo.maintenance_item_id is None

    def test_deleting_the_template_keeps_the_work_order_and_its_asset(self):
        """SET_NULL, not CASCADE: retiring a PM template must not erase history."""
        wo = _preventive_wo()
        asset_id = wo.asset_id
        wo.maintenance_item.delete()

        wo.refresh_from_db()
        assert wo.maintenance_item_id is None
        assert wo.asset_id == asset_id


class TestDisplayTitle:
    def test_preventive_uses_the_template_title(self):
        wo = _preventive_wo(title="Quarterly belt change")
        assert wo.display_title == "Quarterly belt change"

    def test_corrective_falls_back_to_the_asset_name(self):
        asset = AssetFactory(name="Bridgeport Mill")
        assert _corrective_wo(asset).display_title == "Bridgeport Mill"

    def test_str_uses_display_title(self):
        """``__str__`` used to dereference the template unconditionally."""
        asset = AssetFactory(name="Bridgeport Mill")
        assert "Bridgeport Mill" in str(_corrective_wo(asset))


# ─────────────────────────────────────────────────────────────────────────────
# Serializers
# ─────────────────────────────────────────────────────────────────────────────
class TestSerializers:
    def test_detail_reads_the_asset_off_the_work_order(self):
        asset = AssetFactory(name="Laser Cutter", asset_tag="LC-1")
        data = WorkOrderSerializer(_corrective_wo(asset)).data

        assert data["asset_name"] == "Laser Cutter"
        assert data["asset_tag"] == "LC-1"
        assert data["asset_id"] == str(asset.id)

    def test_detail_nulls_the_template_derived_keys(self):
        """Keys stay present — a client reading them must not KeyError."""
        data = WorkOrderSerializer(_corrective_wo()).data

        assert data["maintenance_item"] is None
        assert data["maintenance_item_title"] is None
        assert data["estimated_time_minutes"] is None
        assert data["tools"] == []

    def test_detail_still_renders_asset_derived_blocks(self):
        data = WorkOrderSerializer(_corrective_wo()).data

        assert data["electrical"] is not None
        assert data["loto"] is not None
        assert data["reference_documents"]["documents"] == []

    def test_detail_keeps_the_preventive_values(self):
        wo = _preventive_wo(title="Quarterly belt change")
        data = WorkOrderSerializer(wo).data

        assert data["maintenance_item_title"] == "Quarterly belt change"
        assert data["estimated_time_minutes"] == 45
        assert data["asset_name"] == wo.asset.name

    def test_display_title_is_exposed_on_both_serializers(self):
        asset = AssetFactory(name="Bridgeport Mill")
        wo = _corrective_wo(asset)

        assert WorkOrderSerializer(wo).data["display_title"] == "Bridgeport Mill"
        assert WorkOrderListSerializer(wo).data["display_title"] == "Bridgeport Mill"

    def test_list_reads_the_asset_off_the_work_order(self):
        asset = AssetFactory(name="Laser Cutter", asset_tag="LC-1")
        data = WorkOrderListSerializer(_corrective_wo(asset)).data

        assert data["asset_name"] == "Laser Cutter"
        assert data["asset_tag"] == "LC-1"
        assert data["asset_id"] == str(asset.id)
        assert data["maintenance_item_title"] is None


# ─────────────────────────────────────────────────────────────────────────────
# API surface
# ─────────────────────────────────────────────────────────────────────────────
class TestWorkOrderApi:
    def test_detail_endpoint_renders_a_corrective_work_order(self):
        client, _ = _staff_client()
        wo = _corrective_wo()

        response = client.get(f"/api/inventory/work-orders/{wo.id}/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["asset_id"] == str(wo.asset_id)

    def test_list_endpoint_renders_a_corrective_work_order(self):
        client, _ = _staff_client()
        wo = _corrective_wo()

        response = client.get("/api/inventory/work-orders/")

        ids = [row["id"] for row in response.data["results"]]
        assert str(wo.id) in ids

    def test_asset_filter_returns_both_kinds(self):
        """A bare ``maintenance_item__asset_id=`` inner-joins corrective WOs away."""
        client, _ = _staff_client()
        asset = AssetFactory()
        preventive = _preventive_wo(asset)
        corrective = _corrective_wo(asset)

        response = client.get(f"/api/inventory/work-orders/?asset={asset.id}")

        ids = {row["id"] for row in response.data["results"]}
        assert ids == {str(preventive.id), str(corrective.id)}

    def test_create_without_a_template_or_asset_is_rejected(self):
        """Nullable FK must not become a way to make a work order about nothing."""
        client, _ = _staff_client()

        response = client.post("/api/inventory/work-orders/", {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_with_only_an_asset_is_accepted(self):
        client, _ = _staff_client()
        asset = AssetFactory()

        response = client.post(
            "/api/inventory/work-orders/", {"asset": str(asset.id)}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["asset_id"] == str(asset.id)
        assert response.data["maintenance_item"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Printed form + OMR + LOTO
# ─────────────────────────────────────────────────────────────────────────────
class TestPrintedSurfaces:
    def test_pdf_renders_without_a_template(self):
        pdf = generate_work_order_pdf(_corrective_wo(), base_url="http://testserver")
        assert pdf.startswith(b"%PDF")

    def test_pdf_renders_with_material_usage_but_no_template(self):
        """The materials block used to fall back to ``item.materials``."""
        wo = _corrective_wo()
        _used_material(wo)

        assert generate_work_order_pdf(wo).startswith(b"%PDF")

    def test_omr_form_and_template_map_build(self):
        pdf, template_map = generate_work_order_omr_pdf(_corrective_wo())

        assert pdf.startswith(b"%PDF")
        assert template_map["regions"]

    def test_omr_target_ids_skip_the_absent_material_spec(self):
        """``materialspec_`` is a template fallback; there is no template here."""
        assert dynamic_target_ids(_corrective_wo()) == []

    def test_loto_rows_materialize_for_a_corrective_work_order(self):
        from loto.models import AssetEnergySource

        asset = AssetFactory()
        AssetEnergySource.objects.create(
            asset=asset,
            source_type=AssetEnergySource.SOURCE_ELECTRICAL,
            isolation_point="Wall disconnect",
        )
        wo = _corrective_wo(asset)

        rows = create_loto_completions(wo)

        assert len(rows) == 1
        assert wo.loto_completions.count() == 1

    def test_parity_context_reads_the_work_order_asset(self):
        asset = AssetFactory(name="Bridgeport Mill")
        context = build_work_order_context(_corrective_wo(asset))

        assert context["tools"] == []
        assert context["loto"] is not None
        assert context["reference_documents"]["documents"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Reports — every one of these walked asset → maintenance_items → work_orders
# ─────────────────────────────────────────────────────────────────────────────
class TestReports:
    def _completed_corrective(self, *, days_ago=2, cost_per_unit="4.00", quantity="3"):
        wo = _corrective_wo(
            status=WorkOrder.Status.COMPLETED,
            completed_at=timezone.now() - timedelta(days=days_ago),
        )
        _used_material(wo, cost_per_unit=cost_per_unit, quantity=quantity)
        return wo

    def test_dashboard_lists_it_as_unscheduled_open_work(self):
        client, _ = _staff_client()
        asset = AssetFactory(name="Bridgeport Mill")
        wo = _corrective_wo(asset)

        response = client.get("/api/inventory/maintenance/dashboard/")

        rows = {row["workorder_id"]: row for row in response.data["unscheduled"]}
        assert str(wo.id) in rows
        assert rows[str(wo.id)]["asset_name"] == "Bridgeport Mill"
        assert rows[str(wo.id)]["problem"] == "Bridgeport Mill"

    def test_dashboard_costs_it_from_used_materials(self):
        client, _ = _staff_client()
        wo = self._completed_corrective()

        response = client.get("/api/inventory/maintenance/dashboard/")

        by_asset = {row["asset_id"]: row for row in response.data["costs"]["by_asset"]}
        assert Decimal(by_asset[str(wo.asset_id)]["total_cost"]) == Decimal("12.00")

    def test_dashboard_does_not_double_count_preventive_work(self):
        """A preventive WO sits in both halves of the union — count it once."""
        client, _ = _staff_client()
        asset = AssetFactory()
        item = MaintenanceItem.objects.create(
            asset=asset, title="Monthly", interval_days=30, estimated_cost=Decimal("25.00")
        )
        WorkOrder.objects.create(
            maintenance_item=item,
            status=WorkOrder.Status.COMPLETED,
            completed_at=timezone.now() - timedelta(days=1),
        )

        response = client.get("/api/inventory/maintenance/dashboard/")

        by_asset = {row["asset_id"]: row for row in response.data["costs"]["by_asset"]}
        assert Decimal(by_asset[str(asset.id)]["total_cost"]) == Decimal("25.00")

    def test_active_report_lists_it(self):
        client, _ = _staff_client()
        asset = AssetFactory(name="Bridgeport Mill")
        wo = _corrective_wo(asset)

        response = client.get("/api/inventory/maintenance/active/")

        rows = {row["id"]: row for row in response.data["results"] if row["kind"] == "work_order"}
        assert rows[str(wo.id)]["title"] == "Bridgeport Mill"
        assert rows[str(wo.id)]["asset_name"] == "Bridgeport Mill"

    def test_tco_counts_its_cost_and_maintenance_days(self):
        # Closed the same day it was opened, so its one-day span lands inside
        # the window (the span runs created_at → completed_at, not backwards).
        client, _ = _staff_client()
        wo = self._completed_corrective(days_ago=0)

        response = client.get("/api/inventory/reports/assets/tco/")

        rows = {row["asset_id"]: row for row in response.data}
        assert Decimal(rows[str(wo.asset_id)]["unscheduled_maintenance_cost"]) == Decimal("12.00")
        assert rows[str(wo.asset_id)]["maintenance_days_last_90"] == 1

    def test_tco_does_not_double_count_preventive_work(self):
        client, _ = _staff_client()
        asset = AssetFactory()
        item = MaintenanceItem.objects.create(
            asset=asset, title="Monthly", interval_days=30, estimated_cost=Decimal("25.00")
        )
        WorkOrder.objects.create(
            maintenance_item=item,
            status=WorkOrder.Status.COMPLETED,
            completed_at=timezone.now() - timedelta(days=1),
        )

        response = client.get("/api/inventory/reports/assets/tco/")

        rows = {row["asset_id"]: row for row in response.data}
        assert Decimal(rows[str(asset.id)]["scheduled_maintenance_cost"]) == Decimal("25.00")

    def test_supplies_used_lists_its_consumables(self):
        client, _ = _staff_client()
        wo = self._completed_corrective()

        response = client.get("/api/inventory/reports/assets/supplies_used/?period=past_month")

        consumables = [
            row
            for row in response.data
            if row["source"] == "consumable" and row.get("work_order_id") == str(wo.id)
        ]
        assert len(consumables) == 1
        assert consumables[0]["item_name"] == "Filter cartridge"

    def test_cost_recovery_includes_it_as_an_internal_service(self):
        client, _ = _staff_client()
        wo = self._completed_corrective()

        response = client.get(
            "/api/inventory/reports/assets/cost_recovery/",
            {"asset_ids": str(wo.asset_id), "period": "past_month"},
        )

        blocks = {b["asset_id"]: b for b in response.data["assets"]}
        services = [s for s in blocks[str(wo.asset_id)]["services"] if s["source"] == "pm"]
        assert len(services) == 1
        assert services[0]["description"] == wo.asset.name
        assert Decimal(services[0]["estimated_cost"]) == Decimal("12.00")


# ─────────────────────────────────────────────────────────────────────────────
# Data migration
# ─────────────────────────────────────────────────────────────────────────────
class TestMigrationBackfill:
    """The 0099 backfill, run against the live registry.

    Deliberately *not* driven through ``MigrationExecutor``: rewinding the graph
    needs ``transaction=True``, and that marker's post-test flush deletes
    migration-seeded rows (the accounting chart of accounts) for every test that
    runs after it in the same session. The backfill query is the part worth
    testing, and calling it directly exercises exactly that.
    """

    @staticmethod
    def _backfill():
        migration = importlib.import_module(
            "inventory.migrations.0099_work_order_nullable_item_direct_asset"
        )
        migration.backfill_work_order_asset(apps, None)

    def test_a_pre_migration_row_ends_up_on_its_templates_asset(self):
        wo = _preventive_wo()
        expected_asset_id = wo.asset_id
        # The pre-migration state: the column exists but was never populated.
        WorkOrder.objects.filter(pk=wo.pk).update(asset=None)

        self._backfill()

        assert WorkOrder.objects.get(pk=wo.pk).asset_id == expected_asset_id

    def test_an_already_populated_row_is_left_alone(self):
        """Idempotent: re-running must not reassign a work order's asset."""
        other_asset = AssetFactory()
        wo = _preventive_wo()
        WorkOrder.objects.filter(pk=wo.pk).update(asset=other_asset)

        self._backfill()

        assert WorkOrder.objects.get(pk=wo.pk).asset_id == other_asset.id

    def test_a_template_less_row_is_skipped(self):
        """Nothing to derive from — and no crash trying."""
        wo = _corrective_wo()
        WorkOrder.objects.filter(pk=wo.pk).update(asset=None)

        self._backfill()

        assert WorkOrder.objects.get(pk=wo.pk).asset_id is None
