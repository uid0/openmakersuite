"""Actual materials + cost capture on a work order (op-768w, B3 of the
corrective-WO epic).

Before this bead a work order's materials were a frozen copy of the PM
template's :class:`MaintenanceMaterial` rows, made once at generation, with no
price on them and no way to add another. That left the two cases this file is
about with nothing at all to record:

* a **corrective** work order — raised from a reported problem, so it has no PM
  template and therefore zero material rows (see
  ``test_work_order_corrective_foundation.py`` for the shape); and
* an **out-of-pocket buy** on any work order — the tech drove to the hardware
  store, and the only artefact is a receipt.

Both are now the same thing: an *ad-hoc* line (``material=None``,
``is_ad_hoc=True``) carrying a real ``unit_cost``, optionally a
``receipt_image``, and optionally a direct ``inventory_item`` link that makes
it behave exactly like a template line when marked used.

The stock half is deliberately NOT re-tested here — it goes through the one
``apply_material_usage`` seam that ``test_work_order_material_usage.py``
already covers. What is asserted below is that an ad-hoc line reaches that seam
(via the new ``inventory_item`` link) and that an unlinked one stays flag-only.
"""

from __future__ import annotations

import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.crypto import get_random_string

import pytest
from PIL import Image as PILImage
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import (
    InventoryItem,
    MaintenanceItem,
    MaintenanceMaterial,
    UsageLog,
    WorkOrder,
    WorkOrderMaterialUsage,
)
from inventory.serializers import WorkOrderSerializer
from inventory.tests.factories import AssetFactory, InventoryItemFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _isolated_media(settings, tmp_path):
    """Keep uploaded receipts out of the tracked ``backend/media`` tree."""
    settings.MEDIA_ROOT = str(tmp_path)


def _staff_client():
    user = User.objects.create_user(
        username=f"staff_{get_random_string(6)}",
        email="staff@example.com",
        password=get_random_string(24),
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def _corrective_wo(asset=None):
    """The case this bead exists for: an asset, no PM template, no materials."""
    return WorkOrder.objects.create(maintenance_item=None, asset=asset or AssetFactory())


def _preventive_wo(*, material_name="Filter", inventory_item=None, quantity=None):
    """A classic PM work order with one template-derived material line."""
    quantity = Decimal("2.00") if quantity is None else quantity
    asset = AssetFactory()
    item = MaintenanceItem.objects.create(asset=asset, title="Monthly PM")
    material = MaintenanceMaterial.objects.create(
        maintenance_item=item,
        name=material_name,
        quantity=quantity,
        inventory_item=inventory_item,
    )
    wo = WorkOrder.objects.create(maintenance_item=item)
    usage = WorkOrderMaterialUsage.objects.create(
        work_order=wo,
        material=material,
        material_name=material.name,
        quantity_planned=quantity,
        quantity_used=quantity,
        unit=material.unit,
    )
    return wo, usage


def _priced_item(unit_cost, **kwargs):
    """An inventory item whose ``unit_cost`` property resolves to ``unit_cost``.

    ``InventoryItem.unit_cost`` reads through to the primary ``ItemSupplier``,
    and ``ItemSupplier.save()`` *derives* the per-unit price from
    ``package_cost`` when there is one — so pinning an exact price means a
    package of one and no package cost. Passing ``unit_cost=None`` gives the
    other case this file needs: a real item nobody has priced.
    """
    return InventoryItemFactory(
        unit_cost=None if unit_cost is None else Decimal(unit_cost),
        quantity_per_package=1,
        **kwargs,
    )


def _add_url(wo):
    return reverse("workorder-add-material", kwargs={"pk": wo.id})


def _remove_url(wo, usage):
    return reverse("workorder-remove-material", kwargs={"pk": wo.id, "material_id": usage.id})


def _toggle_url(wo, usage):
    return reverse("workorder-toggle-material", kwargs={"pk": wo.id, "material_id": usage.id})


def _receipt_file(name="receipt.jpg"):
    buf = io.BytesIO()
    PILImage.new("RGB", (20, 30), color=(200, 200, 200)).save(buf, format="JPEG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/jpeg")


# ─────────────────────────────────────────────────────────────────────────────
# add_material — the corrective case (a WO with no template and no materials)
# ─────────────────────────────────────────────────────────────────────────────
class TestAddMaterialToCorrectiveWorkOrder:
    def test_corrective_wo_starts_with_no_materials(self):
        """The premise: nothing copies materials onto a template-less WO."""
        wo = _corrective_wo()
        assert wo.material_usage.count() == 0

    def test_add_ad_hoc_material_with_cost(self):
        client, _u = _staff_client()
        wo = _corrective_wo()

        resp = client.post(
            _add_url(wo),
            {
                "material_name": "Drive belt A-42",
                "quantity_used": "2",
                "unit": "ea",
                "unit_cost": "14.50",
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_201_CREATED
        body = resp.json()
        assert body["material_name"] == "Drive belt A-42"
        assert body["is_ad_hoc"] is True
        assert body["material"] is None
        assert body["inventory_item"] is None
        assert Decimal(body["quantity_used"]) == Decimal("2.00")
        assert Decimal(body["unit_cost"]) == Decimal("14.50")
        assert Decimal(body["actual_cost"]) == Decimal("29.00")
        # Un-used until someone says otherwise — the decrement is a separate,
        # deliberate step through the one apply seam.
        assert body["was_used"] is False
        assert body["applied_quantity"] is None

        usage = wo.material_usage.get()
        assert usage.is_ad_hoc is True
        assert usage.material_id is None
        # Nothing planned it, so planned == used rather than a bogus 1.00.
        assert usage.quantity_planned == Decimal("2.00")

    def test_add_defaults_quantity_to_one(self):
        client, _u = _staff_client()
        wo = _corrective_wo()

        resp = client.post(
            _add_url(wo), {"material_name": "Zip ties", "unit_cost": "3.00"}, format="json"
        )

        assert resp.status_code == status.HTTP_201_CREATED
        assert Decimal(resp.json()["quantity_used"]) == Decimal("1.00")
        assert Decimal(resp.json()["actual_cost"]) == Decimal("3.00")

    def test_cost_is_optional(self):
        """Shop stock nobody prices at the point of use still records fine."""
        client, _u = _staff_client()
        wo = _corrective_wo()

        resp = client.post(_add_url(wo), {"material_name": "Shop rag"}, format="json")

        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.json()["unit_cost"] is None
        assert resp.json()["actual_cost"] is None

    def test_add_to_preventive_wo_sits_beside_template_rows(self):
        """A PM work order can gain ad-hoc lines too — the template ones stay."""
        client, _u = _staff_client()
        wo, template_usage = _preventive_wo(material_name="Air filter")

        resp = client.post(
            _add_url(wo),
            {"material_name": "Extra gasket", "unit_cost": "6.25"},
            format="json",
        )

        assert resp.status_code == status.HTTP_201_CREATED
        assert wo.material_usage.count() == 2
        template_usage.refresh_from_db()
        assert template_usage.is_ad_hoc is False
        assert wo.material_usage.get(is_ad_hoc=True).material_name == "Extra gasket"

    def test_material_name_is_required(self):
        client, _u = _staff_client()
        wo = _corrective_wo()

        resp = client.post(_add_url(wo), {"unit_cost": "1.00"}, format="json")

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "material_name" in resp.json()
        assert wo.material_usage.count() == 0

    def test_blank_material_name_is_rejected(self):
        client, _u = _staff_client()
        wo = _corrective_wo()

        resp = client.post(_add_url(wo), {"material_name": "   "}, format="json")

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "material_name" in resp.json()

    @pytest.mark.parametrize(
        "payload,bad_field",
        [
            ({"material_name": "X", "quantity_used": "abc"}, "quantity_used"),
            ({"material_name": "X", "quantity_used": "-1"}, "quantity_used"),
            ({"material_name": "X", "unit_cost": "abc"}, "unit_cost"),
            ({"material_name": "X", "unit_cost": "-5.00"}, "unit_cost"),
        ],
    )
    def test_rejects_bad_numbers(self, payload, bad_field):
        client, _u = _staff_client()
        wo = _corrective_wo()

        resp = client.post(_add_url(wo), payload, format="json")

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert bad_field in resp.json()
        assert wo.material_usage.count() == 0

    def test_unknown_inventory_item_is_rejected(self):
        client, _u = _staff_client()
        wo = _corrective_wo()

        resp = client.post(
            _add_url(wo),
            {"material_name": "Belt", "inventory_item": "6b1c3f4e-0000-4000-8000-000000000000"},
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "inventory_item" in resp.json()

    def test_adding_a_material_never_creates_an_inventory_item(self):
        """The explicit non-goal: this is a record of spend, not a stock row."""
        client, _u = _staff_client()
        wo = _corrective_wo()
        before = InventoryItem.objects.count()

        resp = client.post(
            _add_url(wo),
            {"material_name": "Something nobody stocks", "unit_cost": "9.99"},
            format="json",
        )

        assert resp.status_code == status.HTTP_201_CREATED
        assert InventoryItem.objects.count() == before

    def test_requires_authentication(self):
        wo = _corrective_wo()
        resp = APIClient().post(_add_url(wo), {"material_name": "Belt"}, format="json")
        assert resp.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        assert wo.material_usage.count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# add_material — the inventory link
# ─────────────────────────────────────────────────────────────────────────────
class TestAddMaterialInventoryLink:
    def test_links_item_and_defaults_cost_from_it(self):
        client, _u = _staff_client()
        wo = _corrective_wo()
        item = _priced_item("7.25", name="V-belt", current_stock=10)

        resp = client.post(
            _add_url(wo),
            {"material_name": "V-belt", "quantity_used": "2", "inventory_item": str(item.id)},
            format="json",
        )

        assert resp.status_code == status.HTTP_201_CREATED
        body = resp.json()
        assert body["inventory_item"] == str(item.id)
        assert body["inventory_item_name"] == "V-belt"
        assert Decimal(body["unit_cost"]) == Decimal("7.25")
        assert Decimal(body["actual_cost"]) == Decimal("14.50")

    def test_supplied_cost_overrides_the_item_default(self):
        """The default is a convenience — what was actually paid wins."""
        client, _u = _staff_client()
        wo = _corrective_wo()
        item = _priced_item("7.25", current_stock=10)

        resp = client.post(
            _add_url(wo),
            {"material_name": "V-belt", "inventory_item": str(item.id), "unit_cost": "11.00"},
            format="json",
        )

        assert resp.status_code == status.HTTP_201_CREATED
        assert Decimal(resp.json()["unit_cost"]) == Decimal("11.00")

    def test_unpriced_item_leaves_cost_null(self):
        client, _u = _staff_client()
        wo = _corrective_wo()
        item = _priced_item(None, current_stock=5)  # supplier carries no price
        assert item.unit_cost is None

        resp = client.post(
            _add_url(wo),
            {"material_name": "Mystery part", "inventory_item": str(item.id)},
            format="json",
        )

        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.json()["unit_cost"] is None

    def test_stock_item_resolves_for_both_kinds_of_row(self):
        """The one accessor the apply seam consults."""
        linked = InventoryItemFactory(current_stock=5)
        _wo, template_usage = _preventive_wo(inventory_item=linked)
        assert template_usage.stock_item == linked

        ad_hoc = WorkOrderMaterialUsage.objects.create(
            work_order=_corrective_wo(),
            material=None,
            is_ad_hoc=True,
            inventory_item=linked,
            material_name="Ad-hoc",
        )
        assert ad_hoc.stock_item == linked

        flag_only = WorkOrderMaterialUsage.objects.create(
            work_order=_corrective_wo(), material=None, is_ad_hoc=True, material_name="Rag"
        )
        assert flag_only.stock_item is None


# ─────────────────────────────────────────────────────────────────────────────
# toggle_material — an ad-hoc line reaches the same decrement seam
# ─────────────────────────────────────────────────────────────────────────────
class TestToggleAdHocMaterial:
    def test_linked_ad_hoc_line_decrements_then_restores(self):
        client, _u = _staff_client()
        wo = _corrective_wo()
        item = _priced_item("7.25", current_stock=10)
        usage = WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            inventory_item=item,
            material_name="V-belt",
            quantity_used=Decimal("3.00"),
            unit_cost=Decimal("7.25"),
        )

        resp = client.patch(_toggle_url(wo, usage), {"was_used": True}, format="json")
        assert resp.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        usage.refresh_from_db()
        assert item.current_stock == 7
        assert usage.applied_quantity == 3
        assert UsageLog.objects.filter(item=item).count() == 1

        resp = client.patch(_toggle_url(wo, usage), {"was_used": False}, format="json")
        assert resp.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        usage.refresh_from_db()
        assert item.current_stock == 10
        assert usage.applied_quantity is None
        assert UsageLog.objects.filter(item=item).count() == 0

    def test_unlinked_ad_hoc_line_is_flag_only(self):
        """An out-of-pocket buy moves no stock — there is no stock row to move."""
        client, _u = _staff_client()
        wo = _corrective_wo()
        usage = WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            material_name="Misc supplies — Ace Hardware",
            unit_cost=Decimal("18.75"),
        )

        resp = client.patch(_toggle_url(wo, usage), {"was_used": True}, format="json")

        assert resp.status_code == status.HTTP_200_OK
        usage.refresh_from_db()
        assert usage.was_used is True
        assert usage.applied_quantity is None
        assert UsageLog.objects.count() == 0

    def test_toggle_persists_unit_cost(self):
        client, _u = _staff_client()
        wo = _corrective_wo()
        usage = WorkOrderMaterialUsage.objects.create(
            work_order=wo, material=None, is_ad_hoc=True, material_name="Bolts"
        )

        resp = client.patch(
            _toggle_url(wo, usage),
            {"was_used": True, "quantity_used": "4", "unit_cost": "1.25"},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        usage.refresh_from_db()
        assert usage.unit_cost == Decimal("1.25")
        assert usage.quantity_used == Decimal("4.00")
        assert usage.actual_cost == Decimal("5.00")
        assert Decimal(resp.json()["actual_cost"]) == Decimal("5.00")

    def test_toggle_sets_cost_on_a_template_row_too(self):
        """Template rows carry an *estimate*; this is what it really cost."""
        client, _u = _staff_client()
        wo, usage = _preventive_wo(quantity=Decimal("2.00"))

        resp = client.patch(
            _toggle_url(wo, usage), {"was_used": True, "unit_cost": "8.00"}, format="json"
        )

        assert resp.status_code == status.HTTP_200_OK
        usage.refresh_from_db()
        assert usage.unit_cost == Decimal("8.00")
        assert usage.actual_cost == Decimal("16.00")

    def test_cost_is_frozen_once_stock_is_applied(self):
        """Same rule as ``quantity_used`` — un-mark it to change the price."""
        client, _u = _staff_client()
        wo = _corrective_wo()
        item = InventoryItemFactory(current_stock=10)
        usage = WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            inventory_item=item,
            material_name="V-belt",
            quantity_used=Decimal("1.00"),
            unit_cost=Decimal("5.00"),
        )
        client.patch(_toggle_url(wo, usage), {"was_used": True}, format="json")

        resp = client.patch(
            _toggle_url(wo, usage), {"was_used": True, "unit_cost": "99.00"}, format="json"
        )

        assert resp.status_code == status.HTTP_200_OK
        usage.refresh_from_db()
        assert usage.unit_cost == Decimal("5.00")

    def test_explicit_null_clears_the_cost(self):
        client, _u = _staff_client()
        wo = _corrective_wo()
        usage = WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            material_name="Bolts",
            unit_cost=Decimal("2.00"),
        )

        resp = client.patch(
            _toggle_url(wo, usage), {"was_used": False, "unit_cost": None}, format="json"
        )

        assert resp.status_code == status.HTTP_200_OK
        usage.refresh_from_db()
        assert usage.unit_cost is None

    @pytest.mark.parametrize("bad", ["abc", "-1.00"])
    def test_toggle_rejects_bad_cost(self, bad):
        client, _u = _staff_client()
        wo = _corrective_wo()
        usage = WorkOrderMaterialUsage.objects.create(
            work_order=wo, material=None, is_ad_hoc=True, material_name="Bolts"
        )

        resp = client.patch(
            _toggle_url(wo, usage), {"was_used": True, "unit_cost": bad}, format="json"
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        usage.refresh_from_db()
        assert usage.unit_cost is None
        assert usage.was_used is False


# ─────────────────────────────────────────────────────────────────────────────
# remove_material
# ─────────────────────────────────────────────────────────────────────────────
class TestRemoveMaterial:
    def test_removes_an_unused_ad_hoc_line(self):
        client, _u = _staff_client()
        wo = _corrective_wo()
        usage = WorkOrderMaterialUsage.objects.create(
            work_order=wo, material=None, is_ad_hoc=True, material_name="Wrong part"
        )

        resp = client.delete(_remove_url(wo, usage))

        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not WorkOrderMaterialUsage.objects.filter(id=usage.id).exists()

    def test_cannot_remove_a_template_row(self):
        """The frozen PM copy is what the printed sheet says the job is."""
        client, _u = _staff_client()
        wo, usage = _preventive_wo()

        resp = client.delete(_remove_url(wo, usage))

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert WorkOrderMaterialUsage.objects.filter(id=usage.id).exists()

    def test_cannot_remove_a_line_with_stock_applied(self):
        """Deleting it would strand the decremented units with no way back."""
        client, _u = _staff_client()
        wo = _corrective_wo()
        item = InventoryItemFactory(current_stock=10)
        usage = WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            inventory_item=item,
            material_name="V-belt",
            quantity_used=Decimal("2.00"),
        )
        client.patch(_toggle_url(wo, usage), {"was_used": True}, format="json")

        resp = client.delete(_remove_url(wo, usage))

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert WorkOrderMaterialUsage.objects.filter(id=usage.id).exists()
        item.refresh_from_db()
        assert item.current_stock == 8  # decrement untouched

    def test_un_toggling_first_makes_it_removable(self):
        """The documented escape hatch: reverse the stock, then remove."""
        client, _u = _staff_client()
        wo = _corrective_wo()
        item = InventoryItemFactory(current_stock=10)
        usage = WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            inventory_item=item,
            material_name="V-belt",
            quantity_used=Decimal("2.00"),
        )
        client.patch(_toggle_url(wo, usage), {"was_used": True}, format="json")
        client.patch(_toggle_url(wo, usage), {"was_used": False}, format="json")

        resp = client.delete(_remove_url(wo, usage))

        assert resp.status_code == status.HTTP_204_NO_CONTENT
        item.refresh_from_db()
        assert item.current_stock == 10

    def test_flag_only_used_line_is_still_removable(self):
        """No stock moved, so nothing is stranded — ``was_used`` alone is not a lock."""
        client, _u = _staff_client()
        wo = _corrective_wo()
        usage = WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            material_name="Misc supplies",
            was_used=True,
        )

        resp = client.delete(_remove_url(wo, usage))

        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_other_work_orders_line_is_not_found(self):
        client, _u = _staff_client()
        mine = _corrective_wo()
        theirs = _corrective_wo()
        usage = WorkOrderMaterialUsage.objects.create(
            work_order=theirs, material=None, is_ad_hoc=True, material_name="Theirs"
        )

        resp = client.delete(_remove_url(mine, usage))

        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert WorkOrderMaterialUsage.objects.filter(id=usage.id).exists()

    def test_requires_authentication(self):
        wo = _corrective_wo()
        usage = WorkOrderMaterialUsage.objects.create(
            work_order=wo, material=None, is_ad_hoc=True, material_name="Wrong part"
        )

        resp = APIClient().delete(_remove_url(wo, usage))

        assert resp.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        assert WorkOrderMaterialUsage.objects.filter(id=usage.id).exists()


# ─────────────────────────────────────────────────────────────────────────────
# Out-of-pocket receipt
# ─────────────────────────────────────────────────────────────────────────────
class TestReceipt:
    def test_out_of_pocket_line_with_receipt(self):
        """The whole out-of-pocket shape: a name, a price, a photo, no stock."""
        client, _u = _staff_client()
        wo = _corrective_wo()

        resp = client.post(
            _add_url(wo),
            {
                "material_name": "Misc supplies — Ace Hardware",
                "quantity_used": "1",
                "unit_cost": "23.87",
                "receipt_image": _receipt_file(),
            },
            format="multipart",
        )

        assert resp.status_code == status.HTTP_201_CREATED
        body = resp.json()
        assert body["receipt_url"] is not None
        assert body["receipt_url"].endswith(".jpg")
        assert "work_orders/receipts/" in body["receipt_url"]
        assert Decimal(body["actual_cost"]) == Decimal("23.87")
        assert body["inventory_item"] is None

        usage = wo.material_usage.get()
        assert usage.receipt_image
        assert usage.stock_item is None

    def test_receipt_is_null_when_none_uploaded(self):
        client, _u = _staff_client()
        wo = _corrective_wo()

        resp = client.post(_add_url(wo), {"material_name": "Shop rag"}, format="json")

        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.json()["receipt_image"] is None
        assert resp.json()["receipt_url"] is None

    def test_non_image_upload_is_rejected(self):
        """The upload is validated as an image, not trusted on content type."""
        client, _u = _staff_client()
        wo = _corrective_wo()

        resp = client.post(
            _add_url(wo),
            {
                "material_name": "Misc supplies",
                "receipt_image": SimpleUploadedFile(
                    "receipt.jpg", b"not really a jpeg", content_type="image/jpeg"
                ),
            },
            format="multipart",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "receipt_image" in resp.json()
        assert wo.material_usage.count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# Cost roll-up — the handoff to B5 (cost reporting) and B6 (ledger charge)
# ─────────────────────────────────────────────────────────────────────────────
class TestActualCostRollup:
    def test_actual_cost_is_none_without_a_price(self):
        usage = WorkOrderMaterialUsage.objects.create(
            work_order=_corrective_wo(),
            material=None,
            is_ad_hoc=True,
            material_name="Rag",
            quantity_used=Decimal("2.00"),
        )
        assert usage.actual_cost is None

    def test_wo_total_sums_used_lines(self):
        wo, template = _preventive_wo(quantity=Decimal("2.00"))
        template.unit_cost = Decimal("10.00")
        template.was_used = True
        template.save(update_fields=["unit_cost", "was_used"])
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            material_name="Used",
            quantity_used=Decimal("2.00"),
            unit_cost=Decimal("5.00"),
            was_used=True,
        )

        assert wo.actual_material_cost == Decimal("30.00")

    def test_unused_template_line_still_costs_nothing(self):
        """A planned-but-unused PM material is a plan, not a purchase."""
        wo, template = _preventive_wo(quantity=Decimal("5.00"))
        template.unit_cost = Decimal("100.00")
        template.was_used = False
        template.save(update_fields=["unit_cost", "was_used"])

        assert wo.actual_material_cost == Decimal("0.00")

    def test_unpriced_used_line_contributes_zero_not_an_error(self):
        wo = _corrective_wo()
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            material_name="Priced",
            quantity_used=Decimal("1.00"),
            unit_cost=Decimal("4.00"),
            was_used=True,
        )
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            material_name="Unpriced",
            quantity_used=Decimal("3.00"),
            was_used=True,
        )

        assert wo.actual_material_cost == Decimal("4.00")

    def test_empty_work_order_totals_zero(self):
        assert _corrective_wo().actual_material_cost == Decimal("0.00")

    def test_end_to_end_corrective_job_reports_its_cost(self):
        """The bead's verify step, start to finish.

        Add two ad-hoc lines to a corrective work order — one drawn from stock,
        one an out-of-pocket receipt — mark both used, complete the job, then
        read the total off the work order the way B5/B6 will.
        """
        client, _u = _staff_client()
        wo = _corrective_wo()
        item = _priced_item("7.25", current_stock=10)

        stocked = client.post(
            _add_url(wo),
            {"material_name": "V-belt", "quantity_used": "2", "inventory_item": str(item.id)},
            format="json",
        ).json()
        receipted = client.post(
            _add_url(wo),
            {
                "material_name": "Misc supplies — Ace Hardware",
                "unit_cost": "23.87",
                "receipt_image": _receipt_file(),
            },
            format="multipart",
        ).json()

        for line in (stocked, receipted):
            resp = client.patch(
                reverse(
                    "workorder-toggle-material",
                    kwargs={"pk": wo.id, "material_id": line["id"]},
                ),
                {"was_used": True},
                format="json",
            )
            assert resp.status_code == status.HTTP_200_OK

        # Completion is gated on the pre-finalization acknowledgement (412
        # otherwise) — a corrective work order is no exception.
        assert (
            client.post(
                reverse("workorder-validate-checklist", kwargs={"pk": wo.id}),
                {
                    "electrical_acknowledged": True,
                    "loto_acknowledged": True,
                    "required_fields_acknowledged": True,
                },
                format="json",
            ).status_code
            == status.HTTP_201_CREATED
        )

        resp = client.patch(
            reverse("workorder-detail", kwargs={"pk": wo.id}),
            {"status": WorkOrder.Status.COMPLETED},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        wo.refresh_from_db()
        # 2 × 7.25 (from stock, priced off the item) + 23.87 (receipt)
        assert wo.actual_material_cost == Decimal("38.37")
        assert Decimal(resp.json()["actual_material_cost"]) == Decimal("38.37")
        item.refresh_from_db()
        assert item.current_stock == 8


# ─────────────────────────────────────────────────────────────────────────────
# op-4pzp — a freehand (ad-hoc) supply costs the job the moment it is added
# ─────────────────────────────────────────────────────────────────────────────
class TestFreehandSupplyCountsImmediately:
    """The bug: a tech adds an out-of-pocket supply *with a price* and the job's
    Actual Material Cost stays at zero until someone separately marks the line
    "used" — which is neither obvious nor what ``was_used`` is for. That flag
    governs *stock*; the money left the wallet at the hardware store.

    So a priced **ad-hoc** line counts on entry, ``was_used`` or not, and moves
    no stock in the process. A **template** line is still a plan until it is
    marked used.
    """

    def test_priced_ad_hoc_line_counts_before_it_is_marked_used(self):
        wo = _corrective_wo()
        usage = WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            material_name="Misc supplies — Ace Hardware",
            quantity_used=Decimal("1.00"),
            unit_cost=Decimal("23.87"),
        )

        assert usage.was_used is False
        assert wo.actual_material_cost == Decimal("23.87")

    def test_unpriced_ad_hoc_line_still_contributes_nothing(self):
        """Unchanged: no ``unit_cost`` is no cost, however the line got here."""
        wo = _corrective_wo()
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            material_name="Shop rag",
            quantity_used=Decimal("3.00"),
        )

        assert wo.actual_material_cost == Decimal("0.00")

    def test_mixed_work_order_counts_each_kind_by_its_own_rule(self):
        """One job, all four combinations, one total."""
        wo, template_unused = _preventive_wo(quantity=Decimal("5.00"))
        template_unused.unit_cost = Decimal("100.00")  # planned, never used → 0
        template_unused.save(update_fields=["unit_cost"])
        template_used = WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=template_unused.material,
            material_name="Gasket",
            quantity_used=Decimal("2.00"),
            unit_cost=Decimal("3.00"),  # used template line → 6.00
            was_used=True,
        )
        assert template_used.is_ad_hoc is False
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            material_name="Bolts — out of pocket",
            quantity_used=Decimal("4.00"),
            unit_cost=Decimal("1.25"),  # freehand, never toggled → 5.00
        )
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            material_name="Sealant",
            quantity_used=Decimal("1.00"),
            unit_cost=Decimal("9.00"),  # freehand and toggled → 9.00, once
            was_used=True,
        )

        assert wo.actual_material_cost == Decimal("20.00")

    def test_added_over_the_api_shows_up_without_a_toggle(self):
        """Ian's report, end to end: add a freehand supply with a cost, read the
        work order back, and the job's Actual Material Cost is already there."""
        client, _u = _staff_client()
        wo = _corrective_wo()

        created = client.post(
            _add_url(wo),
            {"material_name": "PVC fittings", "quantity_used": "3", "unit_cost": "4.15"},
            format="json",
        )
        assert created.status_code == status.HTTP_201_CREATED
        assert created.json()["was_used"] is False

        resp = client.get(reverse("workorder-detail", kwargs={"pk": wo.id}))

        assert resp.status_code == status.HTTP_200_OK
        assert Decimal(resp.json()["actual_material_cost"]) == Decimal("12.45")

    def test_counting_a_freehand_line_moves_no_stock(self):
        """Cost and stock stay decoupled: the line counts, the shelf does not
        move until someone explicitly marks it used."""
        client, _u = _staff_client()
        wo = _corrective_wo()
        item = _priced_item("7.25", current_stock=10)

        line = client.post(
            _add_url(wo),
            {"material_name": "V-belt", "quantity_used": "2", "inventory_item": str(item.id)},
            format="json",
        ).json()

        wo.refresh_from_db()
        item.refresh_from_db()
        usage = WorkOrderMaterialUsage.objects.get(id=line["id"])
        assert wo.actual_material_cost == Decimal("14.50")
        assert usage.was_used is False
        assert usage.applied_quantity is None
        assert item.current_stock == 10
        assert UsageLog.objects.filter(item=item).count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# consumed_material_cost — the ledger's basis, deliberately a different number
# ─────────────────────────────────────────────────────────────────────────────
class TestConsumedMaterialCost:
    """``actual_material_cost`` is what the job cost; ``consumed_material_cost``
    is what left the shelf. They diverge by exactly the freehand spend, which is
    why the committee charge (which credits *Inventory — supplies on hand*)
    reads the second one. See ``test_work_order_committee_charge.py``.
    """

    def test_used_only_rule_survives_on_the_consumption_basis(self):
        wo = _corrective_wo()
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            material_name="Drawn from stock",
            quantity_used=Decimal("2.00"),
            unit_cost=Decimal("10.00"),
            was_used=True,
        )
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            material_name="Out of pocket",
            quantity_used=Decimal("1.00"),
            unit_cost=Decimal("23.87"),
        )

        assert wo.actual_material_cost == Decimal("43.87")
        assert wo.consumed_material_cost == Decimal("20.00")

    def test_the_two_agree_when_every_line_was_used(self):
        wo, usage = _preventive_wo(quantity=Decimal("2.00"))
        usage.unit_cost = Decimal("6.00")
        usage.was_used = True
        usage.save(update_fields=["unit_cost", "was_used"])

        assert wo.actual_material_cost == wo.consumed_material_cost == Decimal("12.00")


# ─────────────────────────────────────────────────────────────────────────────
# The report mirror — reports and the work-order screen must say the same thing
# ─────────────────────────────────────────────────────────────────────────────
class TestReportMirrorMatchesTheWorkOrder:
    """``work_order_reports.wo_actual_material_cost`` is the same sum with a
    ``None`` for "never priced" so the reports can fall back to the estimate. A
    freehand line has to count there too, or the cost-recovery/TCO reports
    disagree with the screen about what the job cost.
    """

    def test_freehand_line_reaches_the_report_sum(self):
        from inventory.services.work_order_reports import wo_actual_material_cost

        wo = _corrective_wo()
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            material_name="Out of pocket",
            quantity_used=Decimal("2.00"),
            unit_cost=Decimal("11.50"),
        )

        assert wo_actual_material_cost(wo) == wo.actual_material_cost == Decimal("23.00")

    def test_unpriced_job_still_reports_none_and_falls_back(self):
        """The None-vs-zero contract is untouched: an ad-hoc line with no price
        is not "this job cost nothing", it is "nobody priced this job"."""
        from inventory.services.work_order_reports import wo_actual_material_cost

        wo = _corrective_wo()
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=None,
            is_ad_hoc=True,
            material_name="Shop rag",
            quantity_used=Decimal("1.00"),
        )

        assert wo_actual_material_cost(wo) is None

    def test_unused_template_line_is_excluded_from_the_report_sum_too(self):
        from inventory.services.work_order_reports import wo_actual_material_cost

        wo, usage = _preventive_wo(quantity=Decimal("5.00"))
        usage.unit_cost = Decimal("100.00")
        usage.save(update_fields=["unit_cost"])

        assert wo_actual_material_cost(wo) is None
        assert wo.actual_material_cost == Decimal("0.00")


# ─────────────────────────────────────────────────────────────────────────────
# Serializer contract (the web bead op-xl80 consumes these keys)
# ─────────────────────────────────────────────────────────────────────────────
class TestSerializerContract:
    def test_material_usage_keys(self):
        client, _u = _staff_client()
        wo = _corrective_wo()
        item = _priced_item("7.25", name="V-belt", current_stock=10)
        client.post(
            _add_url(wo),
            {"material_name": "V-belt", "inventory_item": str(item.id)},
            format="json",
        )

        resp = client.get(reverse("workorder-detail", kwargs={"pk": wo.id}))

        assert resp.status_code == status.HTTP_200_OK
        line = resp.json()["material_usage"][0]
        for key in (
            "inventory_item",
            "inventory_item_name",
            "is_ad_hoc",
            "unit_cost",
            "actual_cost",
            "receipt_image",
            "receipt_url",
        ):
            assert key in line, f"missing {key}"
        assert "actual_material_cost" in resp.json()

    def test_frozen_fields_are_read_only(self):
        """``material_name``/``unit``/``inventory_item`` are set once, at add."""
        read_only = WorkOrderSerializer().fields["material_usage"].child.Meta.read_only_fields
        for field in ("material_name", "unit", "inventory_item", "is_ad_hoc"):
            assert field in read_only

    def test_serializing_materials_is_not_n_plus_1(self):
        """``inventory_item_name`` rides the prefetch, not a query per line."""
        client, _u = _staff_client()
        wo = _corrective_wo()
        for n in range(5):
            WorkOrderMaterialUsage.objects.create(
                work_order=wo,
                material=None,
                is_ad_hoc=True,
                inventory_item=InventoryItemFactory(current_stock=5),
                material_name=f"Part {n}",
                unit_cost=Decimal("1.00"),
            )
        url = reverse("workorder-detail", kwargs={"pk": wo.id})
        client.get(url)  # warm any lazy caches

        with CaptureQueriesContext(connection) as ctx:
            resp = client.get(url)
        baseline = len(ctx.captured_queries)

        for n in range(5, 15):
            WorkOrderMaterialUsage.objects.create(
                work_order=wo,
                material=None,
                is_ad_hoc=True,
                inventory_item=InventoryItemFactory(current_stock=5),
                material_name=f"Part {n}",
                unit_cost=Decimal("1.00"),
            )
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get(url)

        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.json()["material_usage"]) == 15
        assert len(ctx.captured_queries) == baseline
