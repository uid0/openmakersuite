"""Work-order-level tools with per-job locations (op-0v4).

``MaintenanceTool`` (op-67q5) hangs off the PM *template*, so a corrective work
order could never list a tool at all, and staging a tool for one job meant
rewriting the recurring template for every future job. :class:`WorkOrderTool`
gives each work order its own rows, mirroring
:class:`WorkOrderMaterialUsage`'s two-kinds-of-row shape: template-derived
copies frozen at generation, and ad-hoc rows added during the job.

One test class per acceptance criterion, named for it.
"""

import io
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import migrations as django_migrations
from django.utils.crypto import get_random_string

import pytest
import yaml
from pypdf import PdfReader
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import (
    MaintenanceItem,
    MaintenanceMaterial,
    MaintenanceTool,
    UsageLog,
    WorkOrder,
    WorkOrderMaterialUsage,
    WorkOrderTool,
    WorkOrderValidation,
)
from inventory.serializers import WorkOrderSerializer
from inventory.services.work_order_context import build_tools_context
from inventory.services.work_order_loto import create_loto_completions
from inventory.services.work_order_omr import compute_template_version, dynamic_target_ids
from inventory.tests.factories import AssetFactory, InventoryItemFactory, LocationFactory
from inventory.utils.work_order_pdf import generate_work_order_pdf
from loto.models import AssetEnergySource

User = get_user_model()

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

#: The pinned ScanTTY key set for one entry of the ``tools`` display payload.
TOOLS_PAYLOAD_KEYS = {"id", "name", "quantity", "location_hint", "is_required", "notes"}


def _staff_client():
    """A staff APIClient — the gate on every work-order write."""
    user = User.objects.create_user(
        username=f"staff_{get_random_string(6)}",
        email="staff@example.com",
        password=get_random_string(24),
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def _pm_item(**kwargs) -> MaintenanceItem:
    return MaintenanceItem.objects.create(
        asset=kwargs.pop("asset", None) or AssetFactory(),
        title=kwargs.pop("title", "Quarterly safety check"),
        interval_days=kwargs.pop("interval_days", 90),
        **kwargs,
    )


def _template_tool(item, **kwargs) -> MaintenanceTool:
    return MaintenanceTool.objects.create(
        maintenance_item=item,
        name=kwargs.pop("name", "Torque wrench"),
        quantity=kwargs.pop("quantity", 1),
        location_hint=kwargs.pop("location_hint", "Tool crib, drawer 3"),
        is_required=kwargs.pop("is_required", True),
        notes=kwargs.pop("notes", ""),
        **kwargs,
    )


def _corrective_wo() -> WorkOrder:
    """A work order raised from a problem: no PM template to copy tools from."""
    return WorkOrder.objects.create(maintenance_item=None, asset=AssetFactory())


def _generate_wo(client, item) -> WorkOrder:
    """Generate a work order through the product path (the copy seam)."""
    resp = client.post(
        f"/api/inventory/maintenance-items/{item.id}/generate_work_order/",
        {},
        format="json",
    )
    assert resp.status_code == status.HTTP_201_CREATED, resp.data
    return WorkOrder.objects.get(id=resp.data["id"])


def _detail_tools(client, wo) -> list[dict]:
    """The pinned ``tools`` display payload off the work-order detail API."""
    resp = client.get(f"/api/inventory/work-orders/{wo.id}/")
    assert resp.status_code == status.HTTP_200_OK, resp.data
    return resp.data["tools"]


def _detail_tool_rows(client, wo) -> list[dict]:
    """The editable ``tool_rows`` payload off the work-order detail API."""
    resp = client.get(f"/api/inventory/work-orders/{wo.id}/")
    assert resp.status_code == status.HTTP_200_OK, resp.data
    return resp.data["tool_rows"]


def _pdf_text(wo) -> str:
    reader = PdfReader(io.BytesIO(generate_work_order_pdf(wo)))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _tools_url(wo) -> str:
    return f"/api/inventory/work-orders/{wo.id}/tools/"


def _tool_url(wo, tool_id) -> str:
    return f"/api/inventory/work-orders/{wo.id}/tools/{tool_id}/"


# ─────────────────────────────────────────────────────────────────────────────
# AC-1: Migration is additive
# ─────────────────────────────────────────────────────────────────────────────


class TestAC1MigrationIsAdditive:
    """One CreateModel, nothing touched on any existing table."""

    @staticmethod
    def _operations():
        import importlib

        module = importlib.import_module("inventory.migrations.0109_workordertool")
        return module.Migration.operations

    def test_exactly_one_create_model_for_work_order_tool(self):
        ops = self._operations()
        assert len(ops) == 1, [type(op).__name__ for op in ops]
        (create,) = ops
        assert isinstance(create, django_migrations.CreateModel)
        assert create.name == "WorkOrderTool"

    def test_creates_every_documented_field(self):
        (create,) = self._operations()
        fields = dict(create.fields)
        assert set(fields) == {
            "id",
            "work_order",
            "tool",
            "inventory_item",
            "is_ad_hoc",
            "name",
            "quantity",
            "location_hint",
            "is_required",
            "notes",
            "created_at",
        }

    def test_declares_ordering_and_the_work_order_index(self):
        (create,) = self._operations()
        options = create.options
        # Required first, then case-folded name — the order every display
        # surface reads in.
        assert options["ordering"][0] == "-is_required"
        assert [index.fields for index in options["indexes"]] == [["work_order"]]

    def test_alters_no_existing_table(self):
        """Nothing renamed, altered, added to or removed from an existing model."""
        forbidden = (
            django_migrations.AddField,
            django_migrations.AlterField,
            django_migrations.RemoveField,
            django_migrations.RenameField,
            django_migrations.AlterModelOptions,
            django_migrations.AlterModelTable,
            django_migrations.AlterUniqueTogether,
            django_migrations.AlterIndexTogether,
            django_migrations.AddIndex,
            django_migrations.RemoveIndex,
            django_migrations.AddConstraint,
            django_migrations.RemoveConstraint,
            django_migrations.RunSQL,
            django_migrations.RunPython,
        )
        assert not [op for op in self._operations() if isinstance(op, forbidden)]

    def test_model_state_matches_the_migrated_database(self):
        """``makemigrations --check`` equivalent: no un-migrated model drift.

        The command exits non-zero (``SystemExit``) only when a model change
        has no migration, so a clean return *is* the assertion.
        """
        call_command("makemigrations", "--check", "--dry-run", verbosity=0)


# ─────────────────────────────────────────────────────────────────────────────
# AC-2: Preventive generation copies template tools
# ─────────────────────────────────────────────────────────────────────────────


class TestAC2GenerationCopiesTemplateTools:
    def test_one_frozen_row_per_template_tool_with_copied_fields(self):
        client, _ = _staff_client()
        item = _pm_item()
        stocked = InventoryItemFactory(location=LocationFactory(name="Shelf A"))
        _template_tool(
            item,
            name="Torque wrench",
            quantity=2,
            location_hint="Tool crib, drawer 3",
            is_required=True,
            notes="Calibrated 2026-01",
            inventory_item=stocked,
        )
        _template_tool(item, name="Feeler gauge", quantity=1, is_required=False, notes="0.05mm")

        wo = _generate_wo(client, item)

        rows = {row.name: row for row in wo.tools.all()}
        assert set(rows) == {"Torque wrench", "Feeler gauge"}

        wrench = rows["Torque wrench"]
        assert wrench.is_ad_hoc is False
        assert wrench.quantity == 2
        assert wrench.location_hint == "Tool crib, drawer 3"
        assert wrench.is_required is True
        assert wrench.notes == "Calibrated 2026-01"
        # Provenance links: the template spec and its inventory item.
        assert wrench.tool_id == item.tools.get(name="Torque wrench").id
        assert wrench.inventory_item_id == stocked.id

        gauge = rows["Feeler gauge"]
        assert gauge.is_ad_hoc is False
        assert gauge.is_required is False
        assert gauge.notes == "0.05mm"

    def test_rows_are_ordered_required_first_then_case_folded_name(self):
        client, _ = _staff_client()
        item = _pm_item()
        _template_tool(item, name="zip ties", is_required=True)
        _template_tool(item, name="Allen keys", is_required=True)
        _template_tool(item, name="Bench light", is_required=False)
        _template_tool(item, name="anti-static mat", is_required=False)

        wo = _generate_wo(client, item)

        assert [t["name"] for t in _detail_tools(client, wo)] == [
            "Allen keys",
            "zip ties",
            "anti-static mat",
            "Bench light",
        ]

    def test_corrective_generation_path_copies_nothing(self):
        """No template, so no rows — not an error, just an empty list."""
        wo = _corrective_wo()
        assert list(wo.tools.all()) == []
        client, _ = _staff_client()
        assert _detail_tools(client, wo) == []


# ─────────────────────────────────────────────────────────────────────────────
# AC-3: Generated tool rows are frozen
# ─────────────────────────────────────────────────────────────────────────────


class TestAC3GeneratedRowsAreFrozen:
    def test_editing_the_template_tool_does_not_rewrite_the_job(self):
        client, _ = _staff_client()
        item = _pm_item()
        tool = _template_tool(
            item,
            name="Torque wrench",
            quantity=2,
            location_hint="Tool crib, drawer 3",
            is_required=True,
            notes="Calibrated 2026-01",
        )
        wo = _generate_wo(client, item)

        tool.name = "Torque wrench (metric)"
        tool.quantity = 9
        tool.location_hint = "Moved to cage"
        tool.is_required = False
        tool.notes = "Recalibrated"
        tool.save()

        (payload,) = _detail_tools(client, wo)
        assert payload["name"] == "Torque wrench"
        assert payload["quantity"] == 2
        assert payload["location_hint"] == "Tool crib, drawer 3"
        assert payload["is_required"] is True
        assert payload["notes"] == "Calibrated 2026-01"

        text = _pdf_text(wo)
        assert "Torque wrench" in text
        assert "Tool crib, drawer 3" in text
        assert "Moved to cage" not in text

    def test_deleting_the_template_tool_leaves_the_job_intact(self):
        """``tool`` is SET_NULL provenance — the display fields are the row's own."""
        client, _ = _staff_client()
        item = _pm_item()
        tool = _template_tool(item, name="Torque wrench", quantity=2, location_hint="Drawer 3")
        wo = _generate_wo(client, item)

        tool.delete()

        row = wo.tools.get()
        assert row.tool_id is None
        assert row.is_ad_hoc is False  # still template-derived, just orphaned

        (payload,) = _detail_tools(client, wo)
        assert payload["name"] == "Torque wrench"
        assert payload["quantity"] == 2
        assert payload["location_hint"] == "Drawer 3"
        assert "Torque wrench" in _pdf_text(wo)


# ─────────────────────────────────────────────────────────────────────────────
# AC-4: Per-job location edits do not mutate the template
# ─────────────────────────────────────────────────────────────────────────────


class TestAC4PerJobLocationDoesNotMutateTemplate:
    def test_restaging_writes_only_to_this_job(self):
        client, _ = _staff_client()
        item = _pm_item()
        tool = _template_tool(item, name="Torque wrench", location_hint="Tool crib, drawer 3")
        wo = _generate_wo(client, item)
        row = wo.tools.get()

        resp = client.patch(_tool_url(wo, row.id), {"location_hint": "Bench 2"}, format="json")
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["location_hint"] == "Bench 2"
        assert resp.data["resolved_location"] == "Bench 2"

        # This job shows the new spot…
        (payload,) = _detail_tools(client, wo)
        assert payload["location_hint"] == "Bench 2"

        # …the recurring template is untouched…
        tool.refresh_from_db()
        assert tool.location_hint == "Tool crib, drawer 3"

        # …and the next work order off it still stages from the template.
        next_wo = _generate_wo(client, item)
        (next_payload,) = _detail_tools(client, next_wo)
        assert next_payload["location_hint"] == "Tool crib, drawer 3"


# ─────────────────────────────────────────────────────────────────────────────
# AC-5: Corrective work orders can add ad-hoc tools
# ─────────────────────────────────────────────────────────────────────────────


class TestAC5CorrectiveCanAddAdHocTools:
    def test_add_tool_creates_an_ad_hoc_row_on_a_template_less_work_order(self):
        client, _ = _staff_client()
        wo = _corrective_wo()
        stocked = InventoryItemFactory(location=LocationFactory(name="Shelf A"))

        resp = client.post(
            _tools_url(wo),
            {
                "name": "Bearing puller",
                "quantity": 1,
                "inventory_item": str(stocked.id),
                "location_hint": "Bench 2",
                "is_required": True,
                "notes": "3-jaw",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.data

        row = WorkOrderTool.objects.get(work_order=wo)
        assert row.is_ad_hoc is True
        assert row.tool_id is None  # no template spec exists to point at
        assert row.name == "Bearing puller"
        assert row.location_hint == "Bench 2"
        assert row.inventory_item_id == stocked.id
        assert row.notes == "3-jaw"

        (payload,) = _detail_tools(client, wo)
        assert payload["name"] == "Bearing puller"
        assert payload["location_hint"] == "Bench 2"

    def test_preventive_work_orders_can_also_gain_ad_hoc_tools(self):
        """Something the tech turned out to need mid-job, alongside the copies."""
        client, _ = _staff_client()
        item = _pm_item()
        _template_tool(item, name="Torque wrench")
        wo = _generate_wo(client, item)

        resp = client.post(_tools_url(wo), {"name": "Pry bar"}, format="json")
        assert resp.status_code == status.HTTP_201_CREATED, resp.data

        assert {(r.name, r.is_ad_hoc) for r in wo.tools.all()} == {
            ("Torque wrench", False),
            ("Pry bar", True),
        }

    def test_defaults_fill_in_for_a_name_only_payload(self):
        client, _ = _staff_client()
        wo = _corrective_wo()

        resp = client.post(_tools_url(wo), {"name": "Pry bar"}, format="json")
        assert resp.status_code == status.HTTP_201_CREATED, resp.data

        row = WorkOrderTool.objects.get(work_order=wo)
        assert row.quantity == 1
        assert row.is_required is True
        assert row.location_hint == ""
        assert row.notes == ""
        assert row.inventory_item_id is None


# ─────────────────────────────────────────────────────────────────────────────
# AC-6: Add-tool validation is enforced
# ─────────────────────────────────────────────────────────────────────────────


class TestAC6AddToolValidation:
    @pytest.mark.parametrize(
        "payload,field",
        [
            ({"name": ""}, "name"),
            ({"name": "   "}, "name"),
            ({}, "name"),
            ({"name": "Pry bar", "quantity": 0}, "quantity"),
            ({"name": "Pry bar", "quantity": -3}, "quantity"),
        ],
    )
    def test_invalid_payload_is_rejected_and_writes_nothing(self, payload, field):
        client, _ = _staff_client()
        wo = _corrective_wo()

        resp = client.post(_tools_url(wo), payload, format="json")

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
        assert field in resp.data
        assert WorkOrderTool.objects.filter(work_order=wo).count() == 0

    def test_an_unknown_inventory_item_is_rejected(self):
        client, _ = _staff_client()
        wo = _corrective_wo()

        resp = client.post(
            _tools_url(wo),
            {"name": "Pry bar", "inventory_item": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
        assert WorkOrderTool.objects.filter(work_order=wo).count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# AC-7: Any work-order tool location can be edited
# ─────────────────────────────────────────────────────────────────────────────


class TestAC7AnyRowLocationIsEditable:
    def test_both_kinds_of_row_persist_a_new_per_job_location(self):
        client, _ = _staff_client()
        item = _pm_item()
        _template_tool(item, name="Torque wrench", location_hint="Tool crib, drawer 3")
        wo = _generate_wo(client, item)
        client.post(_tools_url(wo), {"name": "Pry bar"}, format="json")

        derived = wo.tools.get(is_ad_hoc=False)
        ad_hoc = wo.tools.get(is_ad_hoc=True)

        assert (
            client.patch(
                _tool_url(wo, derived.id), {"location_hint": "Bench 2"}, format="json"
            ).status_code
            == status.HTTP_200_OK
        )
        assert (
            client.patch(
                _tool_url(wo, ad_hoc.id), {"location_hint": "Cart 7"}, format="json"
            ).status_code
            == status.HTTP_200_OK
        )

        locations = {t["name"]: t["location_hint"] for t in _detail_tools(client, wo)}
        assert locations == {"Torque wrench": "Bench 2", "Pry bar": "Cart 7"}

    def test_blank_clears_the_hint_and_lets_the_item_location_stand_in(self):
        client, _ = _staff_client()
        wo = _corrective_wo()
        stocked = InventoryItemFactory(location=LocationFactory(name="Shelf A"))
        created = client.post(
            _tools_url(wo),
            {"name": "Pry bar", "inventory_item": str(stocked.id), "location_hint": "Bench 2"},
            format="json",
        )

        resp = client.patch(_tool_url(wo, created.data["id"]), {"location_hint": ""}, format="json")

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["location_hint"] == ""
        assert resp.data["resolved_location"] == "Shelf A"

    def test_a_tool_on_another_work_order_is_not_reachable_sideways(self):
        client, _ = _staff_client()
        mine = _corrective_wo()
        theirs = _corrective_wo()
        created = client.post(_tools_url(theirs), {"name": "Pry bar"}, format="json")

        resp = client.patch(
            _tool_url(mine, created.data["id"]), {"location_hint": "Bench 2"}, format="json"
        )

        assert resp.status_code == status.HTTP_404_NOT_FOUND, resp.data


# ─────────────────────────────────────────────────────────────────────────────
# AC-8: Ad-hoc tools can be removed
# ─────────────────────────────────────────────────────────────────────────────


class TestAC8AdHocToolsAreRemovable:
    def test_delete_removes_the_row_and_the_detail_api_drops_it(self):
        client, _ = _staff_client()
        wo = _corrective_wo()
        created = client.post(_tools_url(wo), {"name": "Pry bar"}, format="json")

        resp = client.delete(_tool_url(wo, created.data["id"]))

        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not WorkOrderTool.objects.filter(id=created.data["id"]).exists()
        assert _detail_tools(client, wo) == []


# ─────────────────────────────────────────────────────────────────────────────
# AC-9: Template-derived tools cannot be removed
# ─────────────────────────────────────────────────────────────────────────────


class TestAC9TemplateDerivedRowsAreNotRemovable:
    def test_delete_is_refused_and_the_row_stays_visible(self):
        client, _ = _staff_client()
        item = _pm_item()
        _template_tool(item, name="Torque wrench")
        wo = _generate_wo(client, item)
        row = wo.tools.get()

        resp = client.delete(_tool_url(wo, row.id))

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
        assert WorkOrderTool.objects.filter(id=row.id).exists()
        assert [t["name"] for t in _detail_tools(client, wo)] == ["Torque wrench"]

    def test_an_orphaned_template_row_is_still_not_removable(self):
        """Losing the spec does not turn a frozen copy into an ad-hoc row."""
        client, _ = _staff_client()
        item = _pm_item()
        tool = _template_tool(item, name="Torque wrench")
        wo = _generate_wo(client, item)
        tool.delete()
        row = wo.tools.get()

        assert client.delete(_tool_url(wo, row.id)).status_code == status.HTTP_400_BAD_REQUEST
        assert WorkOrderTool.objects.filter(id=row.id).exists()


# ─────────────────────────────────────────────────────────────────────────────
# AC-10: Legacy work orders fall back to template tools
# ─────────────────────────────────────────────────────────────────────────────


class TestAC10LegacyFallsBackToTemplate:
    def test_a_work_order_with_no_rows_renders_the_template_list_verbatim(self):
        client, _ = _staff_client()
        item = _pm_item()
        wrench = _template_tool(
            item,
            name="Torque wrench",
            quantity=2,
            location_hint="Tool crib, drawer 3",
            is_required=True,
            notes="Calibrated 2026-01",
        )
        gauge = _template_tool(
            item,
            name="Feeler gauge",
            quantity=1,
            location_hint="",
            is_required=False,
            notes="0.05mm",
        )
        # A work order generated before per-job tools existed: no rows at all.
        legacy = WorkOrder.objects.create(maintenance_item=item, asset=item.asset)
        assert legacy.tools.count() == 0

        payload = _detail_tools(client, legacy)

        assert payload == [
            {
                "id": str(wrench.id),
                "name": "Torque wrench",
                "quantity": 2,
                "location_hint": "Tool crib, drawer 3",
                "is_required": True,
                "notes": "Calibrated 2026-01",
            },
            {
                "id": str(gauge.id),
                "name": "Feeler gauge",
                "quantity": 1,
                "location_hint": "",
                "is_required": False,
                "notes": "0.05mm",
            },
        ]

    def test_the_ids_are_the_template_tools_own(self):
        client, _ = _staff_client()
        item = _pm_item()
        _template_tool(item, name="Torque wrench")
        legacy = WorkOrder.objects.create(maintenance_item=item, asset=item.asset)

        (payload,) = _detail_tools(client, legacy)

        assert payload["id"] == str(item.tools.get().id)
        assert not WorkOrderTool.objects.filter(work_order=legacy).exists()

    def test_the_printed_form_falls_back_the_same_way(self):
        item = _pm_item()
        _template_tool(item, name="Torque wrench", location_hint="Tool crib, drawer 3")
        legacy = WorkOrder.objects.create(maintenance_item=item, asset=item.asset)

        text = _pdf_text(legacy)

        assert "Tools Required" in text
        assert "Torque wrench" in text
        assert "Tool crib, drawer 3" in text


# ─────────────────────────────────────────────────────────────────────────────
# AC-11: Work-order rows override template fallback
# ─────────────────────────────────────────────────────────────────────────────


class TestAC11RowsOverrideTemplate:
    def test_own_rows_replace_rather_than_merge_with_the_template(self):
        client, _ = _staff_client()
        item = _pm_item()
        _template_tool(item, name="Torque wrench")
        _template_tool(item, name="Feeler gauge")
        wo = WorkOrder.objects.create(maintenance_item=item, asset=item.asset)
        WorkOrderTool.objects.create(work_order=wo, name="Pry bar", is_ad_hoc=True)

        payload = _detail_tools(client, wo)

        assert [t["name"] for t in payload] == ["Pry bar"]
        assert "Torque wrench" not in {t["name"] for t in payload}

    def test_the_printed_form_overrides_the_same_way(self):
        item = _pm_item()
        _template_tool(item, name="Torque wrench")
        wo = WorkOrder.objects.create(maintenance_item=item, asset=item.asset)
        WorkOrderTool.objects.create(work_order=wo, name="Pry bar", is_ad_hoc=True)

        text = _pdf_text(wo)

        assert "Pry bar" in text
        assert "Torque wrench" not in text


# ─────────────────────────────────────────────────────────────────────────────
# AC-12: ScanTTY tool keys stay pinned
# ─────────────────────────────────────────────────────────────────────────────


class TestAC12ScanTtyKeysArePinned:
    """ScanTTY decodes this payload for the e-paper form — the key set is frozen."""

    def _both_branches(self):
        item = _pm_item()
        _template_tool(item, name="Torque wrench", quantity=2, location_hint="Drawer 3")
        legacy = WorkOrder.objects.create(maintenance_item=item, asset=item.asset)

        own = WorkOrder.objects.create(maintenance_item=item, asset=item.asset)
        WorkOrderTool.objects.create(
            work_order=own, name="Pry bar", quantity=3, location_hint="Bench 2", is_ad_hoc=True
        )
        return legacy, own

    def test_both_branches_emit_exactly_the_pinned_keys(self):
        legacy, own = self._both_branches()

        for wo in (legacy, own):
            payload = build_tools_context(wo)
            assert payload, wo
            for entry in payload:
                assert set(entry) == TOOLS_PAYLOAD_KEYS, entry

    def test_the_detail_api_emits_exactly_the_pinned_keys(self):
        client, _ = _staff_client()
        legacy, own = self._both_branches()

        for wo in (legacy, own):
            for entry in _detail_tools(client, wo):
                assert set(entry) == TOOLS_PAYLOAD_KEYS, entry

    def test_id_is_a_string_uuid_in_both_branches(self):
        import uuid as uuid_module

        legacy, own = self._both_branches()

        for wo in (legacy, own):
            for entry in build_tools_context(wo):
                assert isinstance(entry["id"], str)
                uuid_module.UUID(entry["id"])  # raises if not a UUID

    def test_the_serializer_and_the_helper_cannot_drift(self):
        client, _ = _staff_client()
        legacy, own = self._both_branches()

        for wo in (legacy, own):
            assert _detail_tools(client, wo) == build_tools_context(wo)

    def test_the_editable_surface_lives_on_a_separate_key(self):
        """``tool_rows`` carries the richer shape so ``tools`` never grows."""
        client, _ = _staff_client()
        _legacy, own = self._both_branches()

        (row,) = _detail_tool_rows(client, own)

        assert row["is_ad_hoc"] is True
        assert row["resolved_location"] == "Bench 2"
        assert TOOLS_PAYLOAD_KEYS < set(row)


# ─────────────────────────────────────────────────────────────────────────────
# AC-13: Explicit per-job location takes precedence
# ─────────────────────────────────────────────────────────────────────────────


class TestAC13ExplicitHintWins:
    def test_hint_beats_the_linked_inventory_location_everywhere(self):
        client, _ = _staff_client()
        wo = _corrective_wo()
        stocked = InventoryItemFactory(location=LocationFactory(name="Shelf A"))
        row = WorkOrderTool.objects.create(
            work_order=wo,
            name="Bearing puller",
            location_hint="Bench 2",
            inventory_item=stocked,
            is_ad_hoc=True,
        )

        assert row.resolved_location == "Bench 2"

        (payload,) = _detail_tools(client, wo)
        assert payload["location_hint"] == "Bench 2"

        text = _pdf_text(wo)
        assert "Bench 2" in text
        assert "Shelf A" not in text


# ─────────────────────────────────────────────────────────────────────────────
# AC-14: Inventory location is the fallback location
# ─────────────────────────────────────────────────────────────────────────────


class TestAC14InventoryLocationIsTheFallback:
    def test_a_blank_hint_falls_back_to_the_items_location_everywhere(self):
        client, _ = _staff_client()
        wo = _corrective_wo()
        stocked = InventoryItemFactory(location=LocationFactory(name="Shelf A"))
        row = WorkOrderTool.objects.create(
            work_order=wo,
            name="Bearing puller",
            location_hint="",
            inventory_item=stocked,
            is_ad_hoc=True,
        )

        assert row.resolved_location == "Shelf A"

        # Carried under the pinned ``location_hint`` key — the display contract.
        (payload,) = _detail_tools(client, wo)
        assert payload["location_hint"] == "Shelf A"

        assert "Shelf A" in _pdf_text(wo)


# ─────────────────────────────────────────────────────────────────────────────
# AC-15: Missing location resolves to blank
# ─────────────────────────────────────────────────────────────────────────────


class TestAC15MissingLocationIsBlank:
    def test_no_hint_and_no_item_serializes_as_empty_string(self):
        client, _ = _staff_client()
        wo = _corrective_wo()
        row = WorkOrderTool.objects.create(
            work_order=wo, name="Bearing puller", is_ad_hoc=True, location_hint=""
        )

        assert row.resolved_location == ""

        (payload,) = _detail_tools(client, wo)
        assert payload["location_hint"] == ""
        assert payload["location_hint"] is not None

    def test_an_item_without_a_location_also_resolves_blank(self):
        client, _ = _staff_client()
        wo = _corrective_wo()
        WorkOrderTool.objects.create(
            work_order=wo,
            name="Bearing puller",
            is_ad_hoc=True,
            inventory_item=InventoryItemFactory(location=None),
        )

        (payload,) = _detail_tools(client, wo)
        assert payload["location_hint"] == ""

    def test_a_blank_row_does_not_borrow_the_templates_location(self):
        """Own rows win outright — no per-field fallback into the template."""
        client, _ = _staff_client()
        item = _pm_item()
        _template_tool(item, name="Torque wrench", location_hint="Tool crib, drawer 3")
        wo = WorkOrder.objects.create(maintenance_item=item, asset=item.asset)
        WorkOrderTool.objects.create(work_order=wo, name="Torque wrench", location_hint="")

        (payload,) = _detail_tools(client, wo)

        assert payload["location_hint"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# AC-16: Corrective tools render on the printed form
# ─────────────────────────────────────────────────────────────────────────────


class TestAC16CorrectiveToolsPrint:
    def test_the_tools_required_table_lists_ad_hoc_rows(self):
        wo = _corrective_wo()
        WorkOrderTool.objects.create(
            work_order=wo,
            name="Bearing puller",
            quantity=2,
            location_hint="Bench 2",
            is_required=True,
            is_ad_hoc=True,
        )
        WorkOrderTool.objects.create(
            work_order=wo,
            name="Shop rags",
            quantity=5,
            location_hint="Cart 7",
            is_required=False,
            is_ad_hoc=True,
        )

        text = _pdf_text(wo)

        assert "Tools Required" in text
        assert "Bearing puller" in text
        assert "Bench 2" in text
        assert "Shop rags" in text
        assert "Cart 7" in text

    def test_a_corrective_work_order_without_tools_still_renders(self):
        """Regression: the section is guarded, not conditional on a template."""
        assert "Tools Required" in _pdf_text(_corrective_wo())


# ─────────────────────────────────────────────────────────────────────────────
# AC-17: Tools do not affect OMR targets
# ─────────────────────────────────────────────────────────────────────────────


class TestAC17OmrIsUnaffected:
    @staticmethod
    def _marked_work_order():
        """A WO with the three things that DO make OMR targets."""
        item = _pm_item()
        item.tasks.create(title="Inspect belt", order=1)
        MaintenanceMaterial.objects.create(
            maintenance_item=item, name="Filter", quantity=Decimal("1.00")
        )
        asset = item.asset
        AssetEnergySource.objects.create(
            asset=asset, source_type="electrical", isolation_point="Main breaker"
        )
        wo = WorkOrder.objects.create(maintenance_item=item, asset=asset)
        wo.task_completions.create(task=item.tasks.get(), task_title="Inspect belt", task_order=1)
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=item.materials.get(),
            material_name="Filter",
            quantity_planned=Decimal("1.00"),
            quantity_used=Decimal("1.00"),
            unit="ea",
        )
        create_loto_completions(wo)
        return wo

    def test_target_ids_and_drift_signature_are_identical_with_and_without_tools(self):
        wo = self._marked_work_order()
        before_ids = set(dynamic_target_ids(wo))
        before_signature = compute_template_version(wo)
        assert before_ids, "fixture produced no marks — the assertion would be vacuous"

        WorkOrderTool.objects.create(
            work_order=wo, name="Torque wrench", location_hint="Bench 2", is_ad_hoc=True
        )
        WorkOrderTool.objects.create(work_order=wo, name="Feeler gauge")
        wo.refresh_from_db()

        assert set(dynamic_target_ids(wo)) == before_ids
        assert compute_template_version(wo) == before_signature

    def test_no_tool_derived_target_id_is_emitted(self):
        wo = self._marked_work_order()
        WorkOrderTool.objects.create(work_order=wo, name="Torque wrench", is_ad_hoc=True)
        row_id = str(wo.tools.get().id)

        ids = dynamic_target_ids(wo)

        assert not [i for i in ids if i.startswith("tool")]
        assert not [i for i in ids if row_id in i]

    def test_a_corrective_work_orders_tools_add_no_targets(self):
        wo = _corrective_wo()
        create_loto_completions(wo)
        before = set(dynamic_target_ids(wo))

        WorkOrderTool.objects.create(work_order=wo, name="Bearing puller", is_ad_hoc=True)
        wo.refresh_from_db()

        assert set(dynamic_target_ids(wo)) == before

    @staticmethod
    def _collected(spec):
        """The regions the OMR form collects, identified rather than placed.

        Compares *which* marks were collected, not where they landed on the
        page: a tool row is printed text, so it can push a checkbox down the
        sheet without ever becoming a scan target. Adding the geometry here
        would assert layout stability, which is not what AC-17 is about.
        """
        return sorted((r["target_id"], r["kind"], r["page"]) for r in spec["regions"])

    def test_the_omr_form_collects_no_tool_region(self):
        """Tools are reference-only on paper: no AcroCheckbox, no region."""
        from inventory.utils.work_order_pdf import generate_work_order_omr_pdf

        wo = self._marked_work_order()
        _pdf, before = generate_work_order_omr_pdf(wo)
        assert before["regions"], "fixture collected no regions — the check is vacuous"

        tool = WorkOrderTool.objects.create(work_order=wo, name="Torque wrench", is_ad_hoc=True)
        wo.refresh_from_db()
        _pdf, after = generate_work_order_omr_pdf(wo)

        assert self._collected(after) == self._collected(before)
        assert not [r for r in after["regions"] if str(tool.id) in r["target_id"]]


# ─────────────────────────────────────────────────────────────────────────────
# AC-18: Tools are not consumed as inventory
# ─────────────────────────────────────────────────────────────────────────────


class TestAC18ToolsAreNotConsumed:
    """A tool is gathered, used and RETURNED — nothing here touches stock."""

    @staticmethod
    def _counts():
        return {
            "usage_logs": UsageLog.objects.count(),
            "material_usage": WorkOrderMaterialUsage.objects.count(),
        }

    def test_the_whole_lifecycle_leaves_stock_and_ledgers_alone(self):
        client, _ = _staff_client()
        wo = _corrective_wo()
        stocked = InventoryItemFactory(current_stock=10, location=LocationFactory(name="Shelf A"))
        before = self._counts()

        # add …
        created = client.post(
            _tools_url(wo),
            {"name": "Bearing puller", "quantity": 3, "inventory_item": str(stocked.id)},
            format="json",
        )
        assert created.status_code == status.HTTP_201_CREATED, created.data
        stocked.refresh_from_db()
        assert stocked.current_stock == 10

        # … edit …
        client.patch(_tool_url(wo, created.data["id"]), {"location_hint": "Bench 2"}, format="json")
        stocked.refresh_from_db()
        assert stocked.current_stock == 10

        # … complete the job (which needs its own acknowledgement, unrelated
        # to tools — a tool has nothing to validate) …
        WorkOrderValidation.objects.create(
            work_order=wo,
            electrical_acknowledged=True,
            loto_acknowledged=True,
            required_fields_acknowledged=True,
        )
        done = client.patch(
            f"/api/inventory/work-orders/{wo.id}/", {"status": "completed"}, format="json"
        )
        assert done.status_code == status.HTTP_200_OK, done.data
        stocked.refresh_from_db()
        assert stocked.current_stock == 10

        # … and remove.
        client.delete(_tool_url(wo, created.data["id"]))
        stocked.refresh_from_db()
        assert stocked.current_stock == 10

        assert self._counts() == before

    def test_a_generated_tool_row_creates_no_material_usage_row(self):
        client, _ = _staff_client()
        item = _pm_item()
        stocked = InventoryItemFactory(current_stock=10)
        _template_tool(item, name="Torque wrench", inventory_item=stocked)

        wo = _generate_wo(client, item)

        assert wo.tools.count() == 1
        assert wo.material_usage.count() == 0
        stocked.refresh_from_db()
        assert stocked.current_stock == 10
        assert UsageLog.objects.filter(item=stocked).count() == 0

    def test_the_model_carries_no_cost_stock_or_purchase_order_fields(self):
        """The shape itself forecloses consumption — compare material usage."""
        fields = {f.name for f in WorkOrderTool._meta.get_fields()}
        assert not fields & {
            "unit_cost",
            "actual_cost",
            "quantity_used",
            "stock_applied",
            "applied_quantity",
            "was_used",
            "usage_log",
            "receipt_image",
            "purchase_order_item",
        }


# ─────────────────────────────────────────────────────────────────────────────
# AC-19: API schema documents work-order tool actions
# ─────────────────────────────────────────────────────────────────────────────


class TestAC19SchemaDocumentsTheActions:
    ADD_PATH = "/api/inventory/work-orders/{id}/tools/"
    DETAIL_PATH = "/api/inventory/work-orders/{id}/tools/{tool_id}/"

    @pytest.fixture(scope="class")
    def schema(self, tmp_path_factory):
        out = Path(tmp_path_factory.mktemp("schema") / "schema.yaml")
        call_command("spectacular", "--validate", "--file", str(out), verbosity=0)
        return yaml.safe_load(out.read_text())

    def test_both_paths_are_documented(self, schema):
        paths = schema["paths"]
        assert self.ADD_PATH in paths, sorted(p for p in paths if "tools" in p)
        assert self.DETAIL_PATH in paths, sorted(p for p in paths if "tools" in p)

    def test_add_tool_documents_its_request_body_and_responses(self, schema):
        op = schema["paths"][self.ADD_PATH]["post"]
        assert "requestBody" in op
        assert "201" in op["responses"]
        assert "400" in op["responses"]
        assert "403" in op["responses"]
        assert "WorkOrderTool" in yaml.safe_dump(op["responses"]["201"])

    def test_the_documented_add_body_carries_every_input_field(self, schema):
        op = schema["paths"][self.ADD_PATH]["post"]
        ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        component = schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]
        assert {
            "name",
            "quantity",
            "inventory_item",
            "location_hint",
            "is_required",
            "notes",
        } <= set(component["properties"])
        assert component.get("required") == ["name"]

    def test_edit_and_remove_document_their_shapes_and_errors(self, schema):
        entry = schema["paths"][self.DETAIL_PATH]
        assert "patch" in entry and "delete" in entry

        patch = entry["patch"]
        assert "requestBody" in patch
        assert "200" in patch["responses"]
        assert "400" in patch["responses"]

        delete = entry["delete"]
        assert "204" in delete["responses"]
        # The template-derived refusal is part of the documented contract.
        assert "400" in delete["responses"]

    def test_auth_expectations_are_documented(self, schema):
        """Each write documents who may call it — a 403 naming the gate."""
        for path, method in (
            (self.ADD_PATH, "post"),
            (self.DETAIL_PATH, "patch"),
            (self.DETAIL_PATH, "delete"),
        ):
            op = schema["paths"][path][method]
            assert "403" in op["responses"], (path, method)
            described = op["responses"]["403"]["description"].lower()
            assert "staff" in described, (path, method, described)


# ─────────────────────────────────────────────────────────────────────────────
# Permissions — the actions inherit the work-order write gate
# ─────────────────────────────────────────────────────────────────────────────


class TestToolActionPermissions:
    def _member_client(self):
        user = User.objects.create_user(
            username=f"member_{get_random_string(6)}",
            email="member@example.com",
            password=get_random_string(24),
        )
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_a_volunteer_cannot_add_edit_or_remove_a_tool(self):
        staff, _ = _staff_client()
        wo = _corrective_wo()
        created = staff.post(_tools_url(wo), {"name": "Pry bar"}, format="json")
        member = self._member_client()

        assert (
            member.post(_tools_url(wo), {"name": "Sledge"}, format="json").status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert (
            member.patch(
                _tool_url(wo, created.data["id"]), {"location_hint": "X"}, format="json"
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert member.delete(_tool_url(wo, created.data["id"])).status_code == (
            status.HTTP_403_FORBIDDEN
        )
        assert WorkOrderTool.objects.filter(work_order=wo).count() == 1

    def test_a_volunteer_can_still_read_the_tool_list(self):
        staff, _ = _staff_client()
        wo = _corrective_wo()
        staff.post(_tools_url(wo), {"name": "Pry bar"}, format="json")

        payload = _detail_tools(self._member_client(), wo)

        assert [t["name"] for t in payload] == ["Pry bar"]


# ─────────────────────────────────────────────────────────────────────────────
# Query budget — the detail view must not go N+1 on tools
# ─────────────────────────────────────────────────────────────────────────────


class TestToolsAreNotNPlusOne:
    def test_resolving_many_tool_locations_costs_no_extra_queries(
        self, django_assert_max_num_queries
    ):
        client, _ = _staff_client()
        wo = _corrective_wo()
        for n in range(6):
            WorkOrderTool.objects.create(
                work_order=wo,
                name=f"Tool {n}",
                is_ad_hoc=True,
                inventory_item=InventoryItemFactory(location=LocationFactory(name=f"Shelf {n}")),
            )

        with django_assert_max_num_queries(30):
            payload = _detail_tools(client, wo)

        assert {t["location_hint"] for t in payload} == {f"Shelf {n}" for n in range(6)}


# ─────────────────────────────────────────────────────────────────────────────
# The service seam used by every generation path
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateWorkOrderToolsService:
    def test_calling_twice_does_not_duplicate_rows(self):
        """PM bundling / regeneration can re-run the copy safely."""
        from inventory.services.work_order_tools import create_work_order_tools

        item = _pm_item()
        _template_tool(item, name="Torque wrench")
        wo = WorkOrder.objects.create(maintenance_item=item, asset=item.asset)

        create_work_order_tools(wo)
        create_work_order_tools(wo)

        assert wo.tools.count() == 1

    def test_a_template_less_work_order_yields_no_rows(self):
        from inventory.services.work_order_tools import create_work_order_tools

        assert create_work_order_tools(_corrective_wo()) == []


class TestWorkOrderSerializerTools:
    def test_the_serializer_reads_the_same_two_branches(self):
        item = _pm_item()
        _template_tool(item, name="Torque wrench")
        wo = WorkOrder.objects.create(maintenance_item=item, asset=item.asset)

        assert [t["name"] for t in WorkOrderSerializer(wo).data["tools"]] == ["Torque wrench"]

        WorkOrderTool.objects.create(work_order=wo, name="Pry bar", is_ad_hoc=True)
        wo.refresh_from_db()

        assert [t["name"] for t in WorkOrderSerializer(wo).data["tools"]] == ["Pry bar"]
