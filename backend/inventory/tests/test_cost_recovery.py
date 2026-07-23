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
            "service_date",
            "source",
            "description",
            "estimated_cost",
            "actual_cost",
        ]
        rows = [r for r in reader if r["asset_tag"] == "A-CSV"]
        assert len(rows) == 1
        row = rows[0]
        assert row["serial_number"] == "SN-CSV"
        assert row["source"] == "vendor"
        assert row["actual_cost"] == "321.00"
        assert row["estimated_cost"] == ""

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
