"""
Tests for the "Tools Required" surface (op-67q5).

``MaintenanceTool`` has existed (migration 0063) but was wired only into Django
admin. This covers the plumbing that surfaces it everywhere a maintainer looks
before starting a job:

- ``MaintenanceToolSerializer`` read + write (pinned field contract — ScanTTY
  decodes these exact keys);
- ``MaintenanceToolViewSet`` CRUD + ``?maintenance_item=`` filter;
- the nested ``tools`` on ``MaintenanceItemSerializer``;
- ``WorkOrderSerializer.tools`` (the pinned per-WO shape + ordering);
- the printed work-order PDF's up-front Tools Required table.
"""

import io
from datetime import date, timedelta

from django.urls import reverse

import pytest
from pypdf import PdfReader
from rest_framework import status

from inventory.models import MaintenanceItem, MaintenanceTool, WorkOrder
from inventory.serializers import (
    MaintenanceItemSerializer,
    MaintenanceToolSerializer,
    WorkOrderSerializer,
)
from inventory.tests.factories import AssetFactory, InventoryItemFactory, LocationFactory
from inventory.utils.work_order_pdf import generate_work_order_pdf

pytestmark = pytest.mark.django_db


def _make_item(**kwargs) -> MaintenanceItem:
    return MaintenanceItem.objects.create(
        asset=kwargs.pop("asset", None) or AssetFactory(),
        title=kwargs.pop("title", "Quarterly safety check"),
        description=kwargs.pop("description", "Standard safety inspection."),
        interval_days=kwargs.pop("interval_days", 90),
        **kwargs,
    )


def _pdf_text(pdf_bytes: bytes) -> str:
    """Extract concatenated visible text from a generated work-order PDF."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# ─────────────────────────────────────────────────────────────────────────────
# Serializer — pinned field contract
# ─────────────────────────────────────────────────────────────────────────────


class TestMaintenanceToolSerializer:
    def test_exposes_the_pinned_field_set(self):
        item = _make_item()
        tool = MaintenanceTool.objects.create(
            maintenance_item=item,
            name="Torque wrench",
            quantity=2,
            location_hint="Tool crib, drawer 3",
            notes="Calibrated 2026-01",
        )
        data = MaintenanceToolSerializer(tool).data
        assert set(data) == {
            "id",
            "maintenance_item",
            "inventory_item",
            "inventory_item_detail",
            "name",
            "quantity",
            "location_hint",
            "is_required",
            "notes",
            "created_at",
        }
        assert data["name"] == "Torque wrench"
        assert data["quantity"] == 2
        assert data["location_hint"] == "Tool crib, drawer 3"
        assert data["is_required"] is True
        assert data["notes"] == "Calibrated 2026-01"

    def test_inventory_item_detail_is_null_when_fk_unset(self):
        item = _make_item()
        tool = MaintenanceTool.objects.create(maintenance_item=item, name="Hex key set")
        data = MaintenanceToolSerializer(tool).data
        assert data["inventory_item"] is None
        assert data["inventory_item_detail"] is None

    def test_inventory_item_detail_populated_when_fk_set(self):
        inv = InventoryItemFactory(
            name="Torque wrench",
            current_stock=3,
            minimum_stock=1,
            reorder_quantity=2,
        )
        item = _make_item()
        tool = MaintenanceTool.objects.create(
            maintenance_item=item, name="Torque wrench", inventory_item=inv
        )
        detail = MaintenanceToolSerializer(tool).data["inventory_item_detail"]
        assert detail["id"] == str(inv.id)
        assert detail["name"] == "Torque wrench"
        assert detail["current_stock"] == 3
        assert detail["minimum_stock"] == 1
        assert detail["reorder_quantity"] == 2

    def test_write_creates_a_tool(self):
        item = _make_item()
        serializer = MaintenanceToolSerializer(
            data={
                "maintenance_item": str(item.id),
                "name": "Feeler gauge",
                "quantity": 1,
                "location_hint": "Bench 2",
                "is_required": False,
                "notes": "0.002in blade",
            }
        )
        assert serializer.is_valid(), serializer.errors
        tool = serializer.save()
        assert tool.maintenance_item_id == item.id
        assert tool.name == "Feeler gauge"
        assert tool.is_required is False

    def test_read_only_fields_are_not_writable(self):
        item = _make_item()
        tool = MaintenanceTool.objects.create(maintenance_item=item, name="Pry bar")
        original_created_at = tool.created_at
        serializer = MaintenanceToolSerializer(
            tool,
            data={"created_at": "2000-01-01T00:00:00Z", "name": "Pry bar"},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        tool.refresh_from_db()
        assert tool.created_at == original_created_at


class TestMaintenanceItemSerializerTools:
    def test_nested_tools_are_returned(self):
        item = _make_item()
        MaintenanceTool.objects.create(maintenance_item=item, name="Multimeter")
        data = MaintenanceItemSerializer(item).data
        assert [t["name"] for t in data["tools"]] == ["Multimeter"]

    def test_tools_key_present_and_empty_when_none_defined(self):
        data = MaintenanceItemSerializer(_make_item()).data
        assert data["tools"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Viewset CRUD
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestMaintenanceToolViewSet:
    LIST_URL = "/api/inventory/maintenance-tools/"

    def test_route_is_registered(self):
        assert reverse("maintenancetool-list") == self.LIST_URL

    def test_create_requires_authentication(self, api_client):
        item = _make_item()
        response = api_client.post(
            self.LIST_URL, {"maintenance_item": str(item.id), "name": "Nut driver"}
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert not MaintenanceTool.objects.exists()

    def test_create_and_list(self, authenticated_client):
        client, _user = authenticated_client
        item = _make_item()
        response = client.post(
            self.LIST_URL,
            {
                "maintenance_item": str(item.id),
                "name": "Torque wrench",
                "quantity": 2,
                "location_hint": "Tool crib, drawer 3",
                "is_required": True,
                "notes": "",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert MaintenanceTool.objects.filter(maintenance_item=item).count() == 1

        listing = client.get(self.LIST_URL)
        assert listing.status_code == status.HTTP_200_OK
        results = listing.data["results"] if "results" in listing.data else listing.data
        assert [t["name"] for t in results] == ["Torque wrench"]

    def test_list_filters_by_maintenance_item(self, authenticated_client):
        client, _user = authenticated_client
        mine = _make_item(title="Mine")
        other = _make_item(title="Other")
        MaintenanceTool.objects.create(maintenance_item=mine, name="Mine tool")
        MaintenanceTool.objects.create(maintenance_item=other, name="Other tool")

        response = client.get(self.LIST_URL, {"maintenance_item": str(mine.id)})
        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"] if "results" in response.data else response.data
        assert [t["name"] for t in results] == ["Mine tool"]

    def test_retrieve_and_partial_update(self, authenticated_client):
        client, _user = authenticated_client
        item = _make_item()
        tool = MaintenanceTool.objects.create(maintenance_item=item, name="Old name")
        detail_url = f"{self.LIST_URL}{tool.id}/"

        assert client.get(detail_url).data["name"] == "Old name"

        response = client.patch(
            detail_url, {"name": "New name", "is_required": False}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        tool.refresh_from_db()
        assert tool.name == "New name"
        assert tool.is_required is False

    def test_destroy(self, authenticated_client):
        client, _user = authenticated_client
        item = _make_item()
        tool = MaintenanceTool.objects.create(maintenance_item=item, name="Doomed")
        response = client.delete(f"{self.LIST_URL}{tool.id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not MaintenanceTool.objects.filter(id=tool.id).exists()


# ─────────────────────────────────────────────────────────────────────────────
# WorkOrderSerializer.tools — pinned shape + ordering
# ─────────────────────────────────────────────────────────────────────────────


class TestWorkOrderSerializerTools:
    def test_shape_is_the_pinned_key_set(self):
        item = _make_item()
        tool = MaintenanceTool.objects.create(
            maintenance_item=item,
            name="Torque wrench",
            quantity=2,
            location_hint="Tool crib, drawer 3",
            notes="Calibrated 2026-01",
        )
        wo = WorkOrder.objects.create(maintenance_item=item)
        tools = WorkOrderSerializer(wo).data["tools"]
        assert tools == [
            {
                "id": str(tool.id),
                "name": "Torque wrench",
                "quantity": 2,
                "location_hint": "Tool crib, drawer 3",
                "is_required": True,
                "notes": "Calibrated 2026-01",
            }
        ]

    def test_required_tools_sort_first_then_by_name(self):
        item = _make_item()
        MaintenanceTool.objects.create(maintenance_item=item, name="Zip ties", is_required=False)
        MaintenanceTool.objects.create(maintenance_item=item, name="Wrench", is_required=True)
        MaintenanceTool.objects.create(maintenance_item=item, name="Allen key", is_required=True)
        wo = WorkOrder.objects.create(maintenance_item=item)
        assert [t["name"] for t in WorkOrderSerializer(wo).data["tools"]] == [
            "Allen key",
            "Wrench",
            "Zip ties",
        ]

    def test_empty_when_the_template_defines_no_tools(self):
        wo = WorkOrder.objects.create(maintenance_item=_make_item())
        assert WorkOrderSerializer(wo).data["tools"] == []

    def test_only_the_primary_maintenance_items_tools_are_included(self):
        asset = AssetFactory()
        primary = _make_item(asset=asset, title="Primary")
        extra = _make_item(asset=asset, title="Bundled")
        MaintenanceTool.objects.create(maintenance_item=primary, name="Primary tool")
        MaintenanceTool.objects.create(maintenance_item=extra, name="Bundled tool")
        wo = WorkOrder.objects.create(maintenance_item=primary)
        wo.additional_maintenance_items.add(extra)
        assert [t["name"] for t in WorkOrderSerializer(wo).data["tools"]] == ["Primary tool"]

    def test_list_serializer_stays_lean(self):
        from inventory.serializers import WorkOrderListSerializer

        wo = WorkOrder.objects.create(maintenance_item=_make_item())
        assert "tools" not in WorkOrderListSerializer(wo).data


@pytest.mark.integration
class TestWorkOrderDetailApiTools:
    def test_detail_endpoint_carries_tools(self, authenticated_client):
        client, _user = authenticated_client
        item = _make_item()
        MaintenanceTool.objects.create(
            maintenance_item=item, name="Torque wrench", location_hint="Drawer 3"
        )
        wo = WorkOrder.objects.create(maintenance_item=item)
        response = client.get(f"/api/inventory/work-orders/{wo.id}/")
        assert response.status_code == status.HTTP_200_OK
        assert [t["name"] for t in response.data["tools"]] == ["Torque wrench"]
        assert response.data["tools"][0]["location_hint"] == "Drawer 3"


# ─────────────────────────────────────────────────────────────────────────────
# Printed work-order PDF
# ─────────────────────────────────────────────────────────────────────────────


class TestWorkOrderPdfToolsSection:
    def _work_order(self, item=None) -> WorkOrder:
        return WorkOrder.objects.create(
            maintenance_item=item or _make_item(),
            due_date=date.today() + timedelta(days=14),
        )

    def test_tools_table_lists_name_qty_and_location(self):
        item = _make_item()
        MaintenanceTool.objects.create(
            maintenance_item=item,
            name="Torque wrench",
            quantity=2,
            location_hint="Tool crib, drawer 3",
        )
        text = _pdf_text(generate_work_order_pdf(self._work_order(item), base_url="http://x"))
        assert "Tools Required" in text
        assert "Torque wrench" in text
        assert "Tool crib, drawer 3" in text
        assert "REQ" in text

    def test_tools_section_prints_before_the_lockout_safety_block(self):
        """ "Tools + LOTO up front": the tool list must precede the safety block."""
        asset = AssetFactory(
            breaker_location="A-12",
            lockout_instructions="LOTO breaker A-12 before service.",
        )
        item = _make_item(asset=asset)
        MaintenanceTool.objects.create(maintenance_item=item, name="Torque wrench")
        text = _pdf_text(generate_work_order_pdf(self._work_order(item), base_url="http://x"))
        assert text.index("Asset Information") < text.index("Tools Required")
        assert text.index("Tools Required") < text.index("Lockout Safety")
        assert "LOTO breaker A-12 before service." in text

    def test_optional_tools_are_not_marked_required(self):
        item = _make_item()
        MaintenanceTool.objects.create(maintenance_item=item, name="Shop vacuum", is_required=False)
        text = _pdf_text(generate_work_order_pdf(self._work_order(item), base_url="http://x"))
        assert "Shop vacuum" in text
        assert "REQ" not in text

    def test_falls_back_to_the_linked_inventory_items_location(self):
        inv = InventoryItemFactory(name="Multimeter", location=LocationFactory(name="Bay 4"))
        item = _make_item()
        MaintenanceTool.objects.create(maintenance_item=item, name="Multimeter", inventory_item=inv)
        text = _pdf_text(generate_work_order_pdf(self._work_order(item), base_url="http://x"))
        assert "Bay 4" in text

    def test_renders_a_placeholder_when_no_tools_are_specified(self):
        text = _pdf_text(generate_work_order_pdf(self._work_order(), base_url="http://x"))
        assert "Tools Required" in text
        assert "No tools specified." in text

    def test_tool_text_with_xml_metacharacters_does_not_break_the_pdf(self):
        item = _make_item()
        MaintenanceTool.objects.create(
            maintenance_item=item,
            name="1/2 <drive> & socket",
            location_hint="Bin <A> & <B>",
        )
        text = _pdf_text(generate_work_order_pdf(self._work_order(item), base_url="http://x"))
        assert "1/2 <drive> & socket" in text
        assert "Bin <A> & <B>" in text

    def test_tools_add_no_omr_marks(self):
        """Reference-only: the section must not touch the OMR template map."""
        from inventory.utils.work_order_pdf import RegionCollector

        item = _make_item()
        wo = self._work_order(item)
        without = RegionCollector()
        generate_work_order_pdf(wo, base_url="http://x", region_collector=without)

        MaintenanceTool.objects.create(maintenance_item=item, name="Torque wrench")
        with_tools = RegionCollector()
        generate_work_order_pdf(wo, base_url="http://x", region_collector=with_tools)

        assert {r["target_id"] for r in with_tools.regions} == {
            r["target_id"] for r in without.regions
        }
