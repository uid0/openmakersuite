"""
Tests for the Asset Cost-Recovery report endpoint.

Covers the cost walk (internal PM estimate vs vendor/manual actual, correct
columns), period presets + custom range filtering, asset selection AND
category expansion, the all-assets + ownership (Space/Committee) filters, an
asset with no in-window services, the recoverable grand-total, and the CSV +
PDF exports.
"""

import csv
import io
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group
from django.utils import timezone

import pytest
from rest_framework import status

from inventory.models import (
    Asset,
    MaintenanceItem,
    MaintenanceMaterial,
    MaintenanceRecord,
    WorkOrder,
    WorkOrderMaterialUsage,
)
from inventory.tests.factories import AssetFactory, CategoryFactory

URL = "/api/inventory/reports/assets/cost_recovery/"


def _completed_wo(maintenance_item, *, completed_at):
    return WorkOrder.objects.create(
        maintenance_item=maintenance_item,
        status=WorkOrder.Status.COMPLETED,
        completed_at=completed_at,
    )


def _priced_usage(work_order, *, name, quantity_used, unit_cost, material=None):
    """An ad-hoc material line with a real ``unit_cost`` (the op-768w capture)."""
    return WorkOrderMaterialUsage.objects.create(
        work_order=work_order,
        material=material,
        is_ad_hoc=material is None,
        material_name=name,
        quantity_planned=quantity_used,
        quantity_used=quantity_used,
        was_used=True,
        unit_cost=unit_cost,
    )


def _closed_vendor_link(asset, *, allocated_cost, closed_days_ago=5, title="Vendor work"):
    """Create a closed ThirdPartyWorkOrder + per-asset link (bypasses the state
    machine) so vendor allocated_cost rolls into the Actual column."""
    from maintenance_orders.models import ThirdPartyWorkOrder, ThirdPartyWorkOrderAsset
    from vendors.models import Vendor

    vendor, _ = Vendor.objects.get_or_create(
        name="CR Test Vendor", defaults={"vendor_kind": Vendor.KIND_HVAC}
    )
    wo = ThirdPartyWorkOrder.objects.create(title=title, vendor=vendor, asset=asset)
    wo.status = ThirdPartyWorkOrder.STATUS_CLOSED
    wo.closed_at = timezone.now() - timedelta(days=closed_days_ago)
    wo.save(update_fields=["status", "closed_at"])
    return ThirdPartyWorkOrderAsset.objects.create(
        work_order=wo,
        asset=asset,
        share_pct=Decimal("100"),
        allocated_cost=allocated_cost,
    )


def _assets_by_id(response):
    return {block["asset_id"]: block for block in response.data["assets"]}


def _error_details(response):
    """Per-field details from the project's wrapped DRF error envelope
    (``{"error": {"code", "message", "details": {...}}}``)."""
    return response.data.get("error", {}).get("details", {})


@pytest.mark.integration
class TestCostRecoveryAccessAndParams:
    def test_requires_auth(self, api_client):
        response = api_client.get(URL, {"asset_ids": "x", "period": "past_month"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_requires_selection(self, authenticated_client):
        """A bare request still 400s — all_assets=true is the explicit opt-in
        for an unbounded run."""
        client, _ = authenticated_client
        response = client.get(URL, {"period": "past_month"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_empty_selection_params_still_rejected(self, authenticated_client):
        """Blank/false values are not a selection."""
        client, _ = authenticated_client
        response = client.get(
            URL,
            {
                "period": "past_month",
                "asset_ids": "",
                "category_ids": "",
                "all_assets": "false",
                "ownership_type": "",
                "owning_group": "",
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_ownership_type_rejected(self, authenticated_client):
        client, _ = authenticated_client
        response = client.get(URL, {"ownership_type": "landlord", "period": "past_month"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "ownership_type" in _error_details(response)

    def test_invalid_owning_group_rejected(self, authenticated_client):
        client, _ = authenticated_client
        response = client.get(URL, {"owning_group": "not-an-int", "period": "past_month"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "owning_group" in _error_details(response)

    def test_requires_period(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory()
        response = client.get(URL, {"asset_ids": str(asset.id)})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_period_rejected(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory()
        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_decade"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_asset_id_rejected(self, authenticated_client):
        client, _ = authenticated_client
        response = client.get(URL, {"asset_ids": "not-a-uuid", "period": "past_month"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_start_after_end_rejected(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory()
        response = client.get(
            URL,
            {
                "asset_ids": str(asset.id),
                "start_date": "2026-05-01",
                "end_date": "2026-04-01",
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
class TestCostRecoveryCostWalk:
    def test_internal_pm_is_estimated_only(self, authenticated_client):
        """Scheduled PM estimate = MaintenanceItem.estimated_cost; unscheduled
        estimate = used-material sum. Both feed Estimated with actual null."""
        client, _ = authenticated_client
        asset = AssetFactory(name="Mill", asset_tag="A-PM")
        now = timezone.now()

        scheduled = MaintenanceItem.objects.create(
            asset=asset,
            title="Quarterly oil change",
            interval_days=90,
            estimated_cost=Decimal("75.00"),
        )
        _completed_wo(scheduled, completed_at=now - timedelta(days=10))

        unscheduled = MaintenanceItem.objects.create(
            asset=asset,
            title="Belt replacement",
            interval_days=None,
            estimated_cost=Decimal("0.00"),
        )
        wo = _completed_wo(unscheduled, completed_at=now - timedelta(days=5))
        belt = MaintenanceMaterial.objects.create(
            maintenance_item=unscheduled,
            name="Drive belt",
            quantity=Decimal("1.00"),
            estimated_cost_per_unit=Decimal("42.50"),
        )
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=belt,
            material_name=belt.name,
            quantity_planned=Decimal("2.00"),
            was_used=True,
        )

        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK

        block = _assets_by_id(response)[str(asset.id)]
        by_desc = {svc["description"]: svc for svc in block["services"]}

        oil = by_desc["Quarterly oil change"]
        assert oil["source"] == "pm"
        assert Decimal(oil["estimated_cost"]) == Decimal("75.00")
        assert oil["actual_cost"] is None

        belt_line = by_desc["Belt replacement"]
        assert belt_line["source"] == "pm"
        assert Decimal(belt_line["estimated_cost"]) == Decimal("85.00")
        assert belt_line["actual_cost"] is None

        assert Decimal(block["subtotal_estimated"]) == Decimal("160.00")
        assert Decimal(block["subtotal_actual"]) == Decimal("0.00")
        # Internal PM never contributes to the recoverable total.
        assert Decimal(response.data["grand_total_actual"]) == Decimal("0.00")

    def test_vendor_actual_is_recoverable(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Compressor", asset_tag="A-VEN")
        _closed_vendor_link(asset, allocated_cost=Decimal("250.00"), closed_days_ago=5)

        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK

        block = _assets_by_id(response)[str(asset.id)]
        assert len(block["services"]) == 1
        svc = block["services"][0]
        assert svc["source"] == "vendor"
        assert svc["estimated_cost"] is None
        assert Decimal(svc["actual_cost"]) == Decimal("250.00")
        assert Decimal(block["subtotal_actual"]) == Decimal("250.00")
        assert Decimal(response.data["grand_total_actual"]) == Decimal("250.00")

    def test_manual_record_actual(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Boiler", asset_tag="A-MAN")
        MaintenanceRecord.objects.create(
            asset=asset,
            title="Annual HVAC service",
            description="Vendor annual service",
            completed_on=(timezone.now() - timedelta(days=3)).date(),
            cost=Decimal("410.00"),
        )

        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK

        block = _assets_by_id(response)[str(asset.id)]
        svc = block["services"][0]
        assert svc["source"] == "manual"
        assert svc["estimated_cost"] is None
        assert Decimal(svc["actual_cost"]) == Decimal("410.00")
        assert Decimal(response.data["grand_total_actual"]) == Decimal("410.00")

    def test_services_sorted_by_date(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(asset_tag="A-SORT")
        now = timezone.now()
        mi = MaintenanceItem.objects.create(
            asset=asset, title="Older PM", interval_days=30, estimated_cost=Decimal("5.00")
        )
        _completed_wo(mi, completed_at=now - timedelta(days=20))
        _closed_vendor_link(asset, allocated_cost=Decimal("10.00"), closed_days_ago=2)

        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_month"})
        block = _assets_by_id(response)[str(asset.id)]
        dates = [svc["date"] for svc in block["services"]]
        assert dates == sorted(dates)


@pytest.mark.integration
class TestCostRecoveryWindow:
    def test_period_preset_filters_window(self, authenticated_client):
        """past_week keeps a 3-day-old service and drops a 20-day-old one."""
        client, _ = authenticated_client
        asset = AssetFactory(asset_tag="A-WIN")
        now = timezone.now()
        mi = MaintenanceItem.objects.create(
            asset=asset, title="Check", interval_days=7, estimated_cost=Decimal("9.00")
        )
        _completed_wo(mi, completed_at=now - timedelta(days=3))  # inside past_week
        _completed_wo(mi, completed_at=now - timedelta(days=20))  # outside past_week

        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_week"})
        block = _assets_by_id(response)[str(asset.id)]
        assert len(block["services"]) == 1
        assert response.data["period"] == "past_week"

    def test_custom_range_filters_window(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(asset_tag="A-RANGE")
        inside = MaintenanceRecord.objects.create(
            asset=asset,
            title="Inside",
            description="in range",
            completed_on=timezone.datetime(2026, 4, 15).date(),
            cost=Decimal("100.00"),
        )
        MaintenanceRecord.objects.create(
            asset=asset,
            title="Outside",
            description="out of range",
            completed_on=timezone.datetime(2026, 6, 1).date(),
            cost=Decimal("999.00"),
        )

        response = client.get(
            URL,
            {
                "asset_ids": str(asset.id),
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        block = _assets_by_id(response)[str(asset.id)]
        assert [svc["description"] for svc in block["services"]] == [inside.title]
        assert response.data["period"] is None
        assert response.data["start_date"] == "2026-04-01"
        assert response.data["end_date"] == "2026-04-30"


@pytest.mark.integration
class TestCostRecoverySelection:
    def test_category_expansion(self, authenticated_client):
        client, _ = authenticated_client
        category = CategoryFactory(name="HVAC Units")
        a1 = AssetFactory(category=category, asset_tag="A-CAT1")
        a2 = AssetFactory(category=category, asset_tag="A-CAT2")
        other = AssetFactory(asset_tag="A-OTHER")

        response = client.get(URL, {"category_ids": str(category.id), "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK
        ids = set(_assets_by_id(response))
        assert {str(a1.id), str(a2.id)} <= ids
        assert str(other.id) not in ids
        assert response.data["category_ids"] == [category.id]

    def test_asset_and_category_union_dedupes(self, authenticated_client):
        client, _ = authenticated_client
        category = CategoryFactory(name="Pumps")
        a1 = AssetFactory(category=category, asset_tag="A-U1")
        a2 = AssetFactory(asset_tag="A-U2")

        response = client.get(
            URL,
            {
                "asset_ids": f"{a1.id},{a2.id}",
                "category_ids": str(category.id),
                "period": "past_month",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        ids = list(_assets_by_id(response))
        # a1 is in both the explicit list and the category — must appear once.
        assert ids.count(str(a1.id)) == 1
        assert {str(a1.id), str(a2.id)} == set(ids)
        assert response.data["asset_count"] == 2

    def test_asset_with_no_services_appears_zero(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Quiet", asset_tag="A-QUIET")

        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK
        block = _assets_by_id(response)[str(asset.id)]
        assert block["services"] == []
        assert Decimal(block["subtotal_estimated"]) == Decimal("0.00")
        assert Decimal(block["subtotal_actual"]) == Decimal("0.00")
        assert response.data["service_count"] == 0

    def test_grand_total_actual_is_recoverable_sum(self, authenticated_client):
        client, _ = authenticated_client
        a1 = AssetFactory(asset_tag="A-G1")
        a2 = AssetFactory(asset_tag="A-G2")
        _closed_vendor_link(a1, allocated_cost=Decimal("120.00"), closed_days_ago=3)
        MaintenanceRecord.objects.create(
            asset=a2,
            title="Repair",
            description="manual",
            completed_on=(timezone.now() - timedelta(days=2)).date(),
            cost=Decimal("80.00"),
        )

        response = client.get(URL, {"asset_ids": f"{a1.id},{a2.id}", "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK
        assert Decimal(response.data["grand_total_actual"]) == Decimal("200.00")
        assert response.data["asset_count"] == 2
        assert response.data["service_count"] == 2


@pytest.mark.integration
class TestCostRecoveryAllAssetsAndOwnership:
    """all_assets escape hatch + the Space/Committee ownership filters."""

    def test_all_assets_returns_every_asset(self, authenticated_client):
        client, _ = authenticated_client
        a1 = AssetFactory(asset_tag="A-ALL1")
        a2 = AssetFactory(asset_tag="A-ALL2")
        a3 = AssetFactory(asset_tag="A-ALL3")

        response = client.get(URL, {"all_assets": "true", "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK
        ids = set(_assets_by_id(response))
        assert {str(a1.id), str(a2.id), str(a3.id)} <= ids
        assert response.data["all_assets"] is True
        assert response.data["asset_count"] == len(ids)

    def test_all_assets_accepts_1_as_true(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(asset_tag="A-ALL-1S")

        response = client.get(URL, {"all_assets": "1", "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK
        assert str(asset.id) in _assets_by_id(response)

    def test_ownership_type_scopes_selection(self, authenticated_client):
        """ownership_type alone = all assets with that ownership."""
        client, _ = authenticated_client
        sig = Group.objects.create(name="CR Robotics Committee")
        space_asset = AssetFactory(asset_tag="A-OWN-SPACE")
        group_asset = AssetFactory(
            asset_tag="A-OWN-GROUP",
            ownership_type=Asset.OwnershipType.GROUP,
            owning_group=sig,
        )

        response = client.get(URL, {"ownership_type": "space", "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK
        ids = set(_assets_by_id(response))
        assert str(space_asset.id) in ids
        assert str(group_asset.id) not in ids
        assert response.data["ownership_type"] == "space"

    def test_owning_group_scopes_to_that_committee(self, authenticated_client):
        client, _ = authenticated_client
        committee = Group.objects.create(name="CR Woodshop Committee")
        other = Group.objects.create(name="CR Metal Committee")
        mine = AssetFactory(
            asset_tag="A-OG-MINE",
            ownership_type=Asset.OwnershipType.GROUP,
            owning_group=committee,
        )
        theirs = AssetFactory(
            asset_tag="A-OG-THEIRS",
            ownership_type=Asset.OwnershipType.GROUP,
            owning_group=other,
        )
        space_asset = AssetFactory(asset_tag="A-OG-SPACE")

        response = client.get(URL, {"owning_group": str(committee.id), "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK
        ids = set(_assets_by_id(response))
        assert ids == {str(mine.id)}
        assert str(theirs.id) not in ids
        assert str(space_asset.id) not in ids
        assert response.data["owning_group"] == committee.id

    def test_all_assets_plus_ownership_type_combine(self, authenticated_client):
        client, _ = authenticated_client
        sig = Group.objects.create(name="CR Combined Committee")
        space_asset = AssetFactory(asset_tag="A-COMBO-SPACE")
        group_asset = AssetFactory(
            asset_tag="A-COMBO-GROUP",
            ownership_type=Asset.OwnershipType.GROUP,
            owning_group=sig,
        )

        response = client.get(
            URL,
            {"all_assets": "true", "ownership_type": "group", "period": "past_month"},
        )
        assert response.status_code == status.HTTP_200_OK
        ids = set(_assets_by_id(response))
        assert str(group_asset.id) in ids
        assert str(space_asset.id) not in ids

    def test_ownership_type_and_owning_group_combine(self, authenticated_client):
        client, _ = authenticated_client
        committee = Group.objects.create(name="CR Both-Filters Committee")
        matching = AssetFactory(
            asset_tag="A-BOTH-MATCH",
            ownership_type=Asset.OwnershipType.GROUP,
            owning_group=committee,
        )
        # Same committee, but owned by a user — excluded by ownership_type.
        AssetFactory(
            asset_tag="A-BOTH-USER",
            ownership_type=Asset.OwnershipType.USER,
            owning_group=committee,
        )

        response = client.get(
            URL,
            {
                "ownership_type": "group",
                "owning_group": str(committee.id),
                "period": "past_month",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert set(_assets_by_id(response)) == {str(matching.id)}

    def test_ownership_filter_widens_past_explicit_asset_ids(self, authenticated_client):
        """An ownership filter starts from every asset, so it is not narrowed
        by a stray asset_ids list (the UI disables the pickers in that mode)."""
        client, _ = authenticated_client
        committee = Group.objects.create(name="CR Widening Committee")
        picked = AssetFactory(
            asset_tag="A-WIDE-PICKED",
            ownership_type=Asset.OwnershipType.GROUP,
            owning_group=committee,
        )
        sibling = AssetFactory(
            asset_tag="A-WIDE-SIBLING",
            ownership_type=Asset.OwnershipType.GROUP,
            owning_group=committee,
        )

        response = client.get(
            URL,
            {
                "asset_ids": str(picked.id),
                "owning_group": str(committee.id),
                "period": "past_month",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert set(_assets_by_id(response)) == {str(picked.id), str(sibling.id)}

    def test_costs_still_walk_under_ownership_selection(self, authenticated_client):
        """The new selection feeds the same cost walk — vendor actual still
        rolls into the recoverable grand total."""
        client, _ = authenticated_client
        committee = Group.objects.create(name="CR Billing Committee")
        asset = AssetFactory(
            asset_tag="A-OWN-COST",
            ownership_type=Asset.OwnershipType.GROUP,
            owning_group=committee,
        )
        _closed_vendor_link(asset, allocated_cost=Decimal("175.00"), closed_days_ago=3)

        response = client.get(URL, {"owning_group": str(committee.id), "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK
        assert Decimal(response.data["grand_total_actual"]) == Decimal("175.00")
        assert response.data["service_count"] == 1

    def test_unknown_owning_group_returns_no_assets(self, authenticated_client):
        client, _ = authenticated_client
        AssetFactory(asset_tag="A-OG-NONE")

        response = client.get(URL, {"owning_group": "99999999", "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["assets"] == []
        assert response.data["asset_count"] == 0

    def test_explicit_selection_unaffected_by_new_params(self, authenticated_client):
        """Without all_assets/ownership params the base set is still the
        id + category union (regression guard on the default path)."""
        client, _ = authenticated_client
        picked = AssetFactory(asset_tag="A-DEFAULT-IN")
        other = AssetFactory(asset_tag="A-DEFAULT-OUT")

        response = client.get(URL, {"asset_ids": str(picked.id), "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK
        ids = set(_assets_by_id(response))
        assert ids == {str(picked.id)}
        assert str(other.id) not in ids
        assert response.data["all_assets"] is False
        assert response.data["ownership_type"] is None
        assert response.data["owning_group"] is None


@pytest.mark.integration
class TestCostRecoveryRecoverableInHouseWork:
    """op-srrv (B5) — in-house actual cost is landlord-billable only on an asset
    flagged ``is_cost_recoverable``; everywhere else it is internal cost only."""

    def test_recoverable_asset_bills_in_house_actual(self, authenticated_client):
        """A flagged asset (the HVAC case) puts the work order's real material
        spend into the recoverable Actual total."""
        client, _ = authenticated_client
        asset = AssetFactory(name="Rooftop Unit", asset_tag="A-REC", is_cost_recoverable=True)
        mi = MaintenanceItem.objects.create(
            asset=asset,
            title="Compressor swap",
            interval_days=None,
            estimated_cost=Decimal("0.00"),
        )
        wo = _completed_wo(mi, completed_at=timezone.now() - timedelta(days=4))
        _priced_usage(
            wo, name="Compressor", quantity_used=Decimal("1.00"), unit_cost=Decimal("640.00")
        )
        _priced_usage(
            wo, name="Refrigerant", quantity_used=Decimal("3.00"), unit_cost=Decimal("22.50")
        )

        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK

        block = _assets_by_id(response)[str(asset.id)]
        assert block["is_cost_recoverable"] is True
        svc = block["services"][0]
        assert svc["source"] == "pm"
        # 640.00 + (3 × 22.50)
        assert Decimal(svc["internal_cost"]) == Decimal("707.50")
        assert Decimal(svc["actual_cost"]) == Decimal("707.50")
        assert Decimal(block["subtotal_internal"]) == Decimal("707.50")
        assert Decimal(block["subtotal_actual"]) == Decimal("707.50")
        assert Decimal(response.data["grand_total_actual"]) == Decimal("707.50")
        assert Decimal(response.data["grand_total_internal"]) == Decimal("707.50")

    def test_non_recoverable_asset_shows_internal_only(self, authenticated_client):
        """Same work order, unflagged asset: the figure is reported as internal
        cost and stays out of the billable Actual column."""
        client, _ = authenticated_client
        asset = AssetFactory(name="Shop Lathe", asset_tag="A-NOREC")
        assert asset.is_cost_recoverable is False  # default
        mi = MaintenanceItem.objects.create(
            asset=asset, title="Bearing swap", interval_days=None, estimated_cost=Decimal("0.00")
        )
        wo = _completed_wo(mi, completed_at=timezone.now() - timedelta(days=4))
        _priced_usage(wo, name="Bearing", quantity_used=Decimal("2.00"), unit_cost=Decimal("35.00"))

        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_month"})
        assert response.status_code == status.HTTP_200_OK

        block = _assets_by_id(response)[str(asset.id)]
        assert block["is_cost_recoverable"] is False
        svc = block["services"][0]
        assert Decimal(svc["internal_cost"]) == Decimal("70.00")
        assert svc["actual_cost"] is None
        assert Decimal(block["subtotal_internal"]) == Decimal("70.00")
        assert Decimal(block["subtotal_actual"]) == Decimal("0.00")
        assert Decimal(response.data["grand_total_actual"]) == Decimal("0.00")
        assert Decimal(response.data["grand_total_internal"]) == Decimal("70.00")

    def test_unpriced_work_order_keeps_old_estimated_numbers(self, authenticated_client):
        """No ``unit_cost`` anywhere — a work order that predates actual-cost
        capture reports exactly what it reported before, on a *recoverable*
        asset too: an estimate is never billed as an actual."""
        client, _ = authenticated_client
        asset = AssetFactory(name="Old Boiler", asset_tag="A-OLD", is_cost_recoverable=True)
        now = timezone.now()

        scheduled = MaintenanceItem.objects.create(
            asset=asset,
            title="Quarterly service",
            interval_days=90,
            estimated_cost=Decimal("75.00"),
        )
        _completed_wo(scheduled, completed_at=now - timedelta(days=10))

        unscheduled = MaintenanceItem.objects.create(
            asset=asset, title="Belt swap", interval_days=None, estimated_cost=Decimal("0.00")
        )
        wo = _completed_wo(unscheduled, completed_at=now - timedelta(days=5))
        belt = MaintenanceMaterial.objects.create(
            maintenance_item=unscheduled,
            name="Drive belt",
            quantity=Decimal("1.00"),
            estimated_cost_per_unit=Decimal("42.50"),
        )
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=belt,
            material_name=belt.name,
            quantity_planned=Decimal("2.00"),
            was_used=True,
        )

        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_month"})
        block = _assets_by_id(response)[str(asset.id)]
        by_desc = {svc["description"]: svc for svc in block["services"]}

        for desc, expected in (("Quarterly service", "75.00"), ("Belt swap", "85.00")):
            svc = by_desc[desc]
            assert Decimal(svc["estimated_cost"]) == Decimal(expected)
            # The internal column falls back to that same estimate...
            assert Decimal(svc["internal_cost"]) == Decimal(expected)
            # ...and nothing unpriced reaches the billable column.
            assert svc["actual_cost"] is None

        assert Decimal(block["subtotal_estimated"]) == Decimal("160.00")
        assert Decimal(block["subtotal_actual"]) == Decimal("0.00")
        assert Decimal(response.data["grand_total_actual"]) == Decimal("0.00")

    def test_actual_replaces_estimate_for_the_internal_figure(self, authenticated_client):
        """Where a real cost exists it wins over the template estimate — the
        Estimated column still reports the old number for reference."""
        client, _ = authenticated_client
        asset = AssetFactory(asset_tag="A-BOTH", is_cost_recoverable=True)
        mi = MaintenanceItem.objects.create(
            asset=asset,
            title="Quarterly filter",
            interval_days=90,
            estimated_cost=Decimal("100.00"),
        )
        wo = _completed_wo(mi, completed_at=timezone.now() - timedelta(days=3))
        _priced_usage(wo, name="Filter", quantity_used=Decimal("1.00"), unit_cost=Decimal("128.40"))

        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_month"})
        svc = _assets_by_id(response)[str(asset.id)]["services"][0]
        assert Decimal(svc["estimated_cost"]) == Decimal("100.00")
        assert Decimal(svc["internal_cost"]) == Decimal("128.40")
        assert Decimal(svc["actual_cost"]) == Decimal("128.40")

    def test_partially_priced_work_order_uses_the_recorded_lines(self, authenticated_client):
        """A job where only some lines were priced bills what is known — the
        unpriced line contributes nothing rather than blocking the total."""
        client, _ = authenticated_client
        asset = AssetFactory(asset_tag="A-PART", is_cost_recoverable=True)
        mi = MaintenanceItem.objects.create(
            asset=asset, title="Mixed job", interval_days=None, estimated_cost=Decimal("0.00")
        )
        wo = _completed_wo(mi, completed_at=timezone.now() - timedelta(days=3))
        _priced_usage(
            wo, name="Contactor", quantity_used=Decimal("1.00"), unit_cost=Decimal("48.00")
        )
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material_name="Shop rag",
            is_ad_hoc=True,
            quantity_planned=Decimal("4.00"),
            quantity_used=Decimal("4.00"),
            was_used=True,
        )

        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_month"})
        svc = _assets_by_id(response)[str(asset.id)]["services"][0]
        assert Decimal(svc["actual_cost"]) == Decimal("48.00")

    def test_planned_but_unused_priced_line_costs_nothing(self, authenticated_client):
        """``was_used=False`` means the material was never consumed, so it is
        not billable even with a price on it."""
        client, _ = authenticated_client
        asset = AssetFactory(asset_tag="A-UNUSED", is_cost_recoverable=True)
        mi = MaintenanceItem.objects.create(
            asset=asset, title="Aborted swap", interval_days=None, estimated_cost=Decimal("0.00")
        )
        wo = _completed_wo(mi, completed_at=timezone.now() - timedelta(days=3))
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material_name="Spare motor",
            is_ad_hoc=True,
            quantity_planned=Decimal("1.00"),
            quantity_used=Decimal("1.00"),
            was_used=False,
            unit_cost=Decimal("900.00"),
        )

        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_month"})
        svc = _assets_by_id(response)[str(asset.id)]["services"][0]
        assert svc["actual_cost"] is None
        assert Decimal(svc["internal_cost"]) == Decimal("0.00")
        assert Decimal(response.data["grand_total_actual"]) == Decimal("0.00")

    def test_corrective_work_order_on_recoverable_asset(self, authenticated_client):
        """A corrective work order has no maintenance item — it reaches the
        asset by direct FK and is billed the same way."""
        client, _ = authenticated_client
        asset = AssetFactory(name="Exhaust Fan", asset_tag="A-CORR", is_cost_recoverable=True)
        wo = WorkOrder.objects.create(
            asset=asset,
            status=WorkOrder.Status.COMPLETED,
            completed_at=timezone.now() - timedelta(days=2),
        )
        _priced_usage(
            wo, name="Fan motor", quantity_used=Decimal("1.00"), unit_cost=Decimal("212.75")
        )

        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_month"})
        block = _assets_by_id(response)[str(asset.id)]
        svc = block["services"][0]
        assert svc["source"] == "pm"
        assert Decimal(svc["actual_cost"]) == Decimal("212.75")
        assert Decimal(response.data["grand_total_actual"]) == Decimal("212.75")

    def test_vendor_actual_recoverable_regardless_of_flag(self, authenticated_client):
        """The flag gates *in-house* cost only — a vendor invoice is billable on
        an unflagged asset exactly as it was before."""
        client, _ = authenticated_client
        asset = AssetFactory(asset_tag="A-VENFLAG")
        _closed_vendor_link(asset, allocated_cost=Decimal("250.00"), closed_days_ago=5)

        response = client.get(URL, {"asset_ids": str(asset.id), "period": "past_month"})
        block = _assets_by_id(response)[str(asset.id)]
        svc = block["services"][0]
        assert Decimal(svc["actual_cost"]) == Decimal("250.00")
        assert svc["internal_cost"] is None
        assert Decimal(block["subtotal_internal"]) == Decimal("0.00")
        assert Decimal(response.data["grand_total_actual"]) == Decimal("250.00")

    def test_mixed_fleet_totals_split_correctly(self, authenticated_client):
        """One recoverable and one non-recoverable asset with identical in-house
        spend: the recoverable total carries one, the internal total both."""
        client, _ = authenticated_client
        recoverable = AssetFactory(asset_tag="A-MIX-REC", is_cost_recoverable=True)
        plain = AssetFactory(asset_tag="A-MIX-NO")
        for asset in (recoverable, plain):
            mi = MaintenanceItem.objects.create(
                asset=asset, title="Service", interval_days=None, estimated_cost=Decimal("0.00")
            )
            wo = _completed_wo(mi, completed_at=timezone.now() - timedelta(days=3))
            _priced_usage(
                wo, name="Part", quantity_used=Decimal("1.00"), unit_cost=Decimal("100.00")
            )

        response = client.get(
            URL,
            {"asset_ids": f"{recoverable.id},{plain.id}", "period": "past_month"},
        )
        assert Decimal(response.data["grand_total_actual"]) == Decimal("100.00")
        assert Decimal(response.data["grand_total_internal"]) == Decimal("200.00")


@pytest.mark.integration
class TestCostRecoveryExports:
    def test_csv_export(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Exporter", asset_tag="A-CSV", serial_number="SN-CSV")
        _closed_vendor_link(asset, allocated_cost=Decimal("321.00"), closed_days_ago=4)

        response = client.get(
            URL, {"asset_ids": str(asset.id), "period": "past_month", "format": "csv"}
        )
        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "text/csv"
        assert "asset_cost_recovery.csv" in response["Content-Disposition"]

        reader = csv.DictReader(io.StringIO(response.content.decode("utf-8")))
        assert reader.fieldnames == [
            "asset_tag",
            "serial_number",
            "status",
            "date_received",
            "cost_recoverable",
            "service_date",
            "source",
            "description",
            "estimated_cost",
            "internal_cost",
            "actual_cost",
        ]
        rows = [r for r in reader if r["asset_tag"] == "A-CSV"]
        assert len(rows) == 1
        row = rows[0]
        assert row["serial_number"] == "SN-CSV"
        assert row["source"] == "vendor"
        assert row["actual_cost"] == "321.00"
        assert row["estimated_cost"] == ""
        # Vendor work is not in-house, so it carries no internal figure.
        assert row["internal_cost"] == ""
        assert row["cost_recoverable"] == "no"

    def test_csv_empty_asset_emits_blank_service_row(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(asset_tag="A-CSV0")

        response = client.get(
            URL, {"asset_ids": str(asset.id), "period": "past_month", "format": "csv"}
        )
        assert response.status_code == status.HTTP_200_OK
        reader = csv.DictReader(io.StringIO(response.content.decode("utf-8")))
        rows = [r for r in reader if r["asset_tag"] == "A-CSV0"]
        assert len(rows) == 1
        assert rows[0]["service_date"] == ""
        assert rows[0]["actual_cost"] == ""

    def test_pdf_export(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Statement Asset", asset_tag="A-PDF")
        _closed_vendor_link(asset, allocated_cost=Decimal("555.00"), closed_days_ago=4)

        response = client.get(
            URL, {"asset_ids": str(asset.id), "period": "past_month", "format": "pdf"}
        )
        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "application/pdf"
        assert "asset_cost_recovery.pdf" in response["Content-Disposition"]
        content = response.content
        assert content.startswith(b"%PDF")

        # Extract text so we can assert a known value is present in the statement.
        from pypdf import PdfReader

        text = "".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
        assert "Cost-Recovery" in text
        assert "Amount to recover" in text
        assert "A-PDF" in text

    def test_csv_honors_ownership_selection(self, authenticated_client):
        """CSV flows through the same selection — the committee's asset is in,
        the space-owned one is out."""
        client, _ = authenticated_client
        committee = Group.objects.create(name="CR CSV Committee")
        owned = AssetFactory(
            asset_tag="A-CSV-OWNED",
            ownership_type=Asset.OwnershipType.GROUP,
            owning_group=committee,
        )
        _closed_vendor_link(owned, allocated_cost=Decimal("42.00"), closed_days_ago=4)
        AssetFactory(asset_tag="A-CSV-SPACE")

        response = client.get(
            URL,
            {"owning_group": str(committee.id), "period": "past_month", "format": "csv"},
        )
        assert response.status_code == status.HTTP_200_OK
        reader = csv.DictReader(io.StringIO(response.content.decode("utf-8")))
        tags = {row["asset_tag"] for row in reader}
        assert tags == {"A-CSV-OWNED"}

    def test_csv_all_assets(self, authenticated_client):
        client, _ = authenticated_client
        a1 = AssetFactory(asset_tag="A-CSVALL1")
        a2 = AssetFactory(asset_tag="A-CSVALL2")

        response = client.get(URL, {"all_assets": "true", "period": "past_month", "format": "csv"})
        assert response.status_code == status.HTTP_200_OK
        reader = csv.DictReader(io.StringIO(response.content.decode("utf-8")))
        tags = {row["asset_tag"] for row in reader}
        assert {a1.asset_tag, a2.asset_tag} <= tags

    def test_pdf_honors_ownership_selection(self, authenticated_client):
        client, _ = authenticated_client
        committee = Group.objects.create(name="CR PDF Committee")
        owned = AssetFactory(
            name="Committee Lathe",
            asset_tag="A-PDF-OWNED",
            ownership_type=Asset.OwnershipType.GROUP,
            owning_group=committee,
        )
        _closed_vendor_link(owned, allocated_cost=Decimal("99.00"), closed_days_ago=4)
        AssetFactory(name="Space Router", asset_tag="A-PDF-SPACE")

        response = client.get(
            URL,
            {"owning_group": str(committee.id), "period": "past_month", "format": "pdf"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "application/pdf"

        from pypdf import PdfReader

        text = "".join(
            page.extract_text() or "" for page in PdfReader(io.BytesIO(response.content)).pages
        )
        assert "A-PDF-OWNED" in text
        assert "A-PDF-SPACE" not in text
        # The header summarizes the ownership scope for the landlord.
        assert "CR PDF Committee" in text

    def test_pdf_all_assets_header(self, authenticated_client):
        client, _ = authenticated_client
        AssetFactory(asset_tag="A-PDFALL")

        response = client.get(URL, {"all_assets": "true", "period": "past_month", "format": "pdf"})
        assert response.status_code == status.HTTP_200_OK

        from pypdf import PdfReader

        text = "".join(
            page.extract_text() or "" for page in PdfReader(io.BytesIO(response.content)).pages
        )
        assert "All assets" in text
        assert "A-PDFALL" in text

    def test_csv_carries_the_recoverable_internal_split(self, authenticated_client):
        """Both columns land in the CSV: the flagged asset bills its in-house
        spend, the unflagged one reports it as internal cost only."""
        client, _ = authenticated_client
        recoverable = AssetFactory(asset_tag="A-CSV-REC", is_cost_recoverable=True)
        plain = AssetFactory(asset_tag="A-CSV-NOREC")
        for asset in (recoverable, plain):
            mi = MaintenanceItem.objects.create(
                asset=asset, title="Repair", interval_days=None, estimated_cost=Decimal("0.00")
            )
            wo = _completed_wo(mi, completed_at=timezone.now() - timedelta(days=3))
            _priced_usage(
                wo, name="Part", quantity_used=Decimal("2.00"), unit_cost=Decimal("60.00")
            )

        response = client.get(
            URL,
            {
                "asset_ids": f"{recoverable.id},{plain.id}",
                "period": "past_month",
                "format": "csv",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        rows = {
            row["asset_tag"]: row
            for row in csv.DictReader(io.StringIO(response.content.decode("utf-8")))
        }

        rec_row = rows["A-CSV-REC"]
        assert rec_row["cost_recoverable"] == "yes"
        assert rec_row["internal_cost"] == "120.00"
        assert rec_row["actual_cost"] == "120.00"

        plain_row = rows["A-CSV-NOREC"]
        assert plain_row["cost_recoverable"] == "no"
        assert plain_row["internal_cost"] == "120.00"
        assert plain_row["actual_cost"] == ""

    def test_pdf_carries_the_recoverable_internal_split(self, authenticated_client):
        client, _ = authenticated_client
        recoverable = AssetFactory(
            name="Recoverable RTU", asset_tag="A-PDF-REC", is_cost_recoverable=True
        )
        plain = AssetFactory(name="Plain Press", asset_tag="A-PDF-NOREC")
        for asset in (recoverable, plain):
            mi = MaintenanceItem.objects.create(
                asset=asset, title="Repair", interval_days=None, estimated_cost=Decimal("0.00")
            )
            wo = _completed_wo(mi, completed_at=timezone.now() - timedelta(days=3))
            _priced_usage(
                wo, name="Part", quantity_used=Decimal("1.00"), unit_cost=Decimal("77.00")
            )

        response = client.get(
            URL,
            {
                "asset_ids": f"{recoverable.id},{plain.id}",
                "period": "past_month",
                "format": "pdf",
            },
        )
        assert response.status_code == status.HTTP_200_OK

        from pypdf import PdfReader

        # Long info lines wrap inside the flowable, so compare on a single line.
        text = " ".join(
            "".join(
                page.extract_text() or "" for page in PdfReader(io.BytesIO(response.content)).pages
            ).split()
        )
        # Both cost columns are rendered, and each asset says which side it is on.
        assert "Internal" in text
        assert "Grand total internal" in text
        assert "Cost recovery: Recoverable (in-house work billable)" in text
        assert "Cost recovery: Not recoverable (in-house work internal only)" in text
        # Only the flagged asset's spend reaches the recoverable grand total.
        assert "Grand total internal (in-house) $154.00" in text  # 77.00 × 2 assets
        assert "Amount to recover (Actual) $77.00" in text  # the flagged asset only

    def test_unknown_format_404s_in_negotiation(self, authenticated_client):
        """``format`` is DRF's reserved content-negotiation param; an
        unregistered value (not json/csv/pdf) is rejected by negotiation
        with a 404 before the view body runs."""
        client, _ = authenticated_client
        asset = AssetFactory()
        response = client.get(
            URL, {"asset_ids": str(asset.id), "period": "past_month", "format": "xml"}
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
