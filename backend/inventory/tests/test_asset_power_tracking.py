"""
Tests for the Asset power / electrical / lockout tracking fields (oms-sxi).

Covers:
- New fields persist on the model and round-trip through the serializer.
- The `is_forgekey_managed` flag picks up both explicit ForgeKey wiring
  (interlock_type / lockout_type) and the implicit signal from having a
  ForgeKey AssetDevice attached.
- The printed work order PDF surfaces the electrical / lockout safety
  section when the asset has data, and labels the asset as ForgeKey-managed
  vs. not so the tech knows where to cut power before service.
"""

import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from pypdf import PdfReader

from inventory.models import (
    Asset,
    MaintenanceItem,
    MaintenanceTask,
    WorkOrder,
    WorkOrderTaskCompletion,
)
from inventory.serializers import AssetSerializer
from inventory.tests.factories import AssetFactory
from inventory.utils.work_order_pdf import _build_electrical_rows, generate_work_order_pdf

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────────────
# Model field persistence
# ─────────────────────────────────────────────────────────────────────────────


class TestAssetPowerFields:
    def test_defaults_are_unknown_and_blank(self):
        asset = AssetFactory()
        asset.refresh_from_db()
        assert asset.power_draw_watts is None
        assert asset.wiring_type == Asset.WiringType.UNKNOWN
        assert asset.has_interlock is False
        assert asset.interlock_type == Asset.InterlockType.UNKNOWN
        assert asset.lockout_type == Asset.LockoutType.UNKNOWN
        assert asset.has_network_drop is False
        assert asset.suite == ""
        assert asset.electrical_box == ""
        assert asset.breaker_location == ""
        assert asset.lockout_instructions == ""
        assert asset.lockout_responsible == ""
        assert asset.interlock_responsible == ""
        assert asset.network_drop_location == ""

    def test_full_power_record_persists(self):
        asset = AssetFactory(
            power_draw_watts=Decimal("1800.00"),
            wiring_type=Asset.WiringType.PLUG_240V,
            suite="North Bay",
            electrical_box="Panel A",
            breaker_location="A-12",
            has_interlock=True,
            interlock_type=Asset.InterlockType.KEY_SWITCH,
            interlock_responsible="Wood Shop SIG Lead",
            lockout_type=Asset.LockoutType.BREAKER,
            lockout_instructions="1. Open panel A. 2. Throw breaker A-12. 3. Apply LOTO tag.",
            lockout_responsible="Logistics on duty",
            has_network_drop=True,
            network_drop_location="Jack WS-04, Switch port 12",
        )
        asset.full_clean()
        asset.refresh_from_db()
        assert asset.power_draw_watts == Decimal("1800.00")
        assert asset.wiring_type == Asset.WiringType.PLUG_240V
        assert asset.has_interlock is True
        assert asset.interlock_responsible == "Wood Shop SIG Lead"
        assert asset.lockout_instructions.startswith("1. Open panel A.")
        assert asset.network_drop_location.startswith("Jack WS-04")


# ─────────────────────────────────────────────────────────────────────────────
# Serializer round-trip
# ─────────────────────────────────────────────────────────────────────────────


class TestAssetPowerSerializer:
    def test_serializer_round_trips_power_fields(self):
        asset = AssetFactory(
            power_draw_watts=Decimal("750.50"),
            wiring_type=Asset.WiringType.PLUG_120V,
            suite="Suite 200",
            electrical_box="Sub-panel B",
            breaker_location="B-7",
            has_interlock=True,
            interlock_type=Asset.InterlockType.E_STOP,
            lockout_type=Asset.LockoutType.PLUG,
            lockout_instructions="Unplug at outlet O-14, apply plug lockout, tag.",
            has_network_drop=False,
        )
        data = AssetSerializer(asset).data
        assert Decimal(str(data["power_draw_watts"])) == Decimal("750.50")
        assert data["wiring_type"] == Asset.WiringType.PLUG_120V
        assert data["suite"] == "Suite 200"
        assert data["electrical_box"] == "Sub-panel B"
        assert data["breaker_location"] == "B-7"
        assert data["has_interlock"] is True
        assert data["interlock_type"] == Asset.InterlockType.E_STOP
        assert data["lockout_type"] == Asset.LockoutType.PLUG
        assert "Unplug at outlet O-14" in data["lockout_instructions"]
        assert data["has_network_drop"] is False
        assert data["is_forgekey_managed"] is False

    def test_serializer_writes_power_fields(self):
        asset = AssetFactory()
        payload = {
            "name": asset.name,
            "power_draw_watts": "1200.00",
            "wiring_type": Asset.WiringType.HARDWIRED,
            "suite": "Main Floor",
            "breaker_location": "Main-3",
            "has_interlock": True,
            "interlock_type": Asset.InterlockType.CONTACTOR,
            "lockout_type": Asset.LockoutType.LOTO,
            "lockout_instructions": "LOTO at main breaker.",
        }
        serializer = AssetSerializer(asset, data=payload, partial=True)
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        asset.refresh_from_db()
        assert asset.power_draw_watts == Decimal("1200.00")
        assert asset.wiring_type == Asset.WiringType.HARDWIRED
        assert asset.suite == "Main Floor"
        assert asset.lockout_type == Asset.LockoutType.LOTO
        assert asset.has_interlock is True


# ─────────────────────────────────────────────────────────────────────────────
# is_forgekey_managed
# ─────────────────────────────────────────────────────────────────────────────


class TestIsForgekeyManaged:
    def test_explicit_interlock_type_marks_managed(self):
        asset = AssetFactory(interlock_type=Asset.InterlockType.FORGEKEY)
        assert asset.is_forgekey_managed is True

    def test_explicit_lockout_type_marks_managed(self):
        asset = AssetFactory(lockout_type=Asset.LockoutType.FORGEKEY)
        assert asset.is_forgekey_managed is True

    def test_unmanaged_asset_returns_false(self):
        asset = AssetFactory()
        assert asset.is_forgekey_managed is False

    def test_attached_forgekey_device_marks_managed(self):
        from forgekey.models import AssetDevice, DeviceType, ESP32Device

        asset = AssetFactory()
        device_type, _ = DeviceType.objects.get_or_create(
            code=DeviceType.TYPE_AC_RELAY,
            defaults={"name": "AC Relay (test)"},
        )
        device = ESP32Device.objects.create(
            mac_address="AA:BB:CC:DD:EE:01",
            device_type=device_type,
        )
        AssetDevice.objects.create(asset=asset, device=device, role="power_control")
        assert asset.is_forgekey_managed is True


# ─────────────────────────────────────────────────────────────────────────────
# _build_electrical_rows helper
# ─────────────────────────────────────────────────────────────────────────────


class TestElectricalRowsHelper:
    def test_no_data_returns_empty_list(self):
        asset = AssetFactory()
        # Default field values should produce no rows so non-powered fixtures
        # don't get an empty "Power & Lockout Safety" section on their forms.
        assert _build_electrical_rows(asset) == []

    def test_rows_include_breaker_and_lockout(self):
        asset = AssetFactory(
            wiring_type=Asset.WiringType.PLUG_240V,
            power_draw_watts=Decimal("3000"),
            suite="North Bay",
            electrical_box="Panel A",
            breaker_location="A-12",
            has_interlock=True,
            interlock_type=Asset.InterlockType.KEY_SWITCH,
            lockout_type=Asset.LockoutType.BREAKER,
            lockout_responsible="Logistics",
        )
        rows = _build_electrical_rows(asset)
        labels = [r[0] for r in rows]
        assert "Wiring" in labels
        assert "Rated Power Draw" in labels
        assert "Suite" in labels
        assert "Electrical Box" in labels
        assert "Breaker" in labels
        assert "Interlock" in labels
        assert "Lockout Type" in labels
        assert "Lockout Responsible" in labels

    def test_breaker_derived_circuit_used_when_no_breaker_location(self):
        # The free-text ``circuit`` field was removed in #880; ``asset.circuit``
        # is now a read-only shim that falls back to a breaker-derived label
        # when ``breaker_location`` is blank. That fallback still renders as the
        # "Circuit" row.
        from electrical_circuits.models import PowerBreaker, PowerPanel

        asset = AssetFactory(wiring_type=Asset.WiringType.PLUG_120V)
        panel = PowerPanel.objects.create(location=asset.location, name="Panel Z")
        breaker = PowerBreaker.objects.create(panel=panel, position="7", amperage=15)
        # Write-through the compat setter; no breaker_location is set.
        asset.breaker = breaker
        rows = _build_electrical_rows(asset)
        labels = [r[0] for r in rows]
        assert "Circuit" in labels
        assert "Breaker" not in labels


# ─────────────────────────────────────────────────────────────────────────────
# Work order PDF integration
# ─────────────────────────────────────────────────────────────────────────────


def _make_work_order(asset: Asset) -> WorkOrder:
    item = MaintenanceItem.objects.create(
        asset=asset,
        title="Quarterly safety check",
        description="Standard safety inspection.",
        interval_days=90,
    )
    task = MaintenanceTask.objects.create(
        maintenance_item=item,
        order=0,
        title="Inspect electrical connections",
        is_required=True,
    )
    wo = WorkOrder.objects.create(
        maintenance_item=item,
        due_date=date.today() + timedelta(days=14),
    )
    WorkOrderTaskCompletion.objects.create(
        work_order=wo,
        task=task,
        task_title=task.title,
        task_order=task.order,
        is_required=task.is_required,
    )
    return wo


def _pdf_text(pdf_bytes: bytes) -> str:
    """Extract concatenated visible text from a generated work-order PDF."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


class TestWorkOrderPdfElectricalSection:
    def test_pdf_includes_breaker_and_lockout_text(self):
        asset = AssetFactory(
            name="CNC Router",
            power_draw_watts=Decimal("2400"),
            wiring_type=Asset.WiringType.PLUG_240V,
            suite="North Bay",
            electrical_box="Panel A",
            breaker_location="A-12",
            lockout_type=Asset.LockoutType.BREAKER,
            lockout_instructions="LOTO breaker A-12 before service.",
            lockout_responsible="Wood Shop SIG Lead",
        )
        wo = _make_work_order(asset)
        text = _pdf_text(generate_work_order_pdf(wo, base_url="http://example.com"))
        assert "Power" in text and "Lockout Safety" in text
        assert "A-12" in text
        assert "Panel A" in text
        assert "North Bay" in text
        assert "Wood Shop SIG Lead" in text
        assert "LOTO breaker A-12 before service." in text

    def test_pdf_marks_forgekey_managed_assets(self):
        asset = AssetFactory(
            name="Laser Cutter",
            wiring_type=Asset.WiringType.PLUG_240V,
            breaker_location="B-3",
            interlock_type=Asset.InterlockType.FORGEKEY,
            lockout_type=Asset.LockoutType.FORGEKEY,
        )
        wo = _make_work_order(asset)
        text = _pdf_text(generate_work_order_pdf(wo, base_url="http://example.com"))
        assert "managed by ForgeKey" in text

    def test_pdf_marks_non_forgekey_asset_with_responsible_party(self):
        asset = AssetFactory(
            name="Old Drill Press",
            wiring_type=Asset.WiringType.PLUG_120V,
            breaker_location="C-7",
            has_interlock=True,
            interlock_type=Asset.InterlockType.KEY_SWITCH,
            interlock_responsible="Metal Shop SIG",
            lockout_type=Asset.LockoutType.PLUG,
            lockout_responsible="Metal Shop SIG",
        )
        wo = _make_work_order(asset)
        text = _pdf_text(generate_work_order_pdf(wo, base_url="http://example.com"))
        assert "Not managed by ForgeKey" in text
        assert "Metal Shop SIG" in text

    def test_pdf_skips_section_when_asset_has_no_electrical_data(self):
        # A non-powered asset (e.g., shelving) shouldn't get an empty
        # safety section on its work order.
        asset = AssetFactory(name="Storage Shelf")
        wo = _make_work_order(asset)
        text = _pdf_text(generate_work_order_pdf(wo, base_url="http://example.com"))
        assert "Lockout Safety" not in text
