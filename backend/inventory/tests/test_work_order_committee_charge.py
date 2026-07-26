"""Committee ledger charge on work-order completion (op-u2g5, Phase 2 · B6).

The work-order half of charge-on-consume: finishing a job on a committee-owned
asset posts one aggregate ``SIG_CHARGE`` for the materials it actually consumed,
and reopening the job reverses it. Postgres/hordak required (``accounting.E001``).
"""

import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils.crypto import get_random_string

import pytest
from hordak.models import Transaction
from PIL import Image as PILImage
from rest_framework import status
from rest_framework.test import APIClient

from accounting.models import EntryMeta, LegDimension, SourceType
from accounting.services import committee_balance, trial_balance
from inventory.models import (
    MaintenanceItem,
    MaintenanceMaterial,
    WorkOrder,
    WorkOrderMaterialUsage,
)
from inventory.services.work_order_ledger import (
    NO_COST_WARNING,
    active_charge,
    charge_committee,
)
from inventory.tests.factories import AssetFactory, InventoryItemFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _isolated_media(settings, tmp_path):
    """Keep uploaded receipts out of the tracked ``backend/media`` tree."""
    settings.MEDIA_ROOT = str(tmp_path)


@pytest.fixture(autouse=True)
def _chart_of_accounts(db):
    """Guarantee the chart exists before a ledger test posts against it.

    The chart is seeded by a *data migration*, and any ``transaction=True`` test
    earlier in the session flushes it back out (its ``TransactionTestCase``
    teardown truncates every table and ``post_migrate`` only restores
    contenttypes/permissions). Re-seeding is idempotent by design — it is the
    same call the migration and the management command make — and it rolls back
    with the test, so this costs one no-op query and makes the module order-proof.
    """
    from accounting.chart import seed_chart_of_accounts

    seed_chart_of_accounts()


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


def _committee(name="Woodshop SIG"):
    return Group.objects.create(name=name)


def _asset(committee=None):
    """An asset owned by ``committee`` (a SIG), or by the space when omitted."""
    if committee is None:
        return AssetFactory()
    return AssetFactory(
        ownership_type="group",
        owning_group=committee,
    )


def _corrective_wo(committee=None, asset=None):
    """A corrective work order: an asset, no PM template, no materials."""
    return WorkOrder.objects.create(maintenance_item=None, asset=asset or _asset(committee))


def _preventive_wo(committee=None):
    """A PM work order whose asset (and therefore committee) comes off the template."""
    asset = _asset(committee)
    item = MaintenanceItem.objects.create(asset=asset, title="Monthly PM")
    material = MaintenanceMaterial.objects.create(
        maintenance_item=item, name="Filter", quantity=Decimal("2.00")
    )
    wo = WorkOrder.objects.create(maintenance_item=item)
    return wo, material


def _line(wo, *, unit_cost, quantity="1.00", was_used=True, name="V-belt", **kwargs):
    """One material line on ``wo`` — priced, used, and ad-hoc unless told otherwise."""
    return WorkOrderMaterialUsage.objects.create(
        work_order=wo,
        material_name=name,
        is_ad_hoc=kwargs.pop("is_ad_hoc", True),
        quantity_planned=Decimal(quantity),
        quantity_used=Decimal(quantity),
        unit_cost=None if unit_cost is None else Decimal(unit_cost),
        was_used=was_used,
        **kwargs,
    )


def _acknowledge(client, wo):
    """Clear the pre-finalization validation gate (a completion 412s without it)."""
    resp = client.post(
        reverse("workorder-validate-checklist", kwargs={"pk": wo.id}),
        {
            "electrical_acknowledged": True,
            "loto_acknowledged": True,
            "required_fields_acknowledged": True,
        },
        format="json",
    )
    assert resp.status_code == status.HTTP_201_CREATED


def _set_status(client, wo, new_status):
    return client.patch(
        reverse("workorder-detail", kwargs={"pk": wo.id}),
        {"status": new_status},
        format="json",
    )


def _complete(client, wo, *, acknowledge=True):
    if acknowledge:
        _acknowledge(client, wo)
    resp = _set_status(client, wo, WorkOrder.Status.COMPLETED)
    assert resp.status_code == status.HTTP_200_OK, resp.data
    return resp


def _charges(wo):
    """Every SIG_CHARGE ``EntryMeta`` posted for this work order, oldest first."""
    return list(
        EntryMeta.objects.filter(
            source_type=SourceType.SIG_CHARGE,
            source_ref__startswith=f"wo_complete:{wo.id}",
        ).order_by("posted_at", "id")
    )


def _receipt_file(name="receipt.jpg"):
    buf = io.BytesIO()
    PILImage.new("RGB", (20, 30), color=(200, 200, 200)).save(buf, format="JPEG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/jpeg")


# ─────────────────────────────────────────────────────────────────────────────
# Completing a job charges its committee
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.integration
class TestCompletionCharge:
    def test_committee_owned_job_posts_one_balanced_sig_charge(self):
        """DR 5100 (sig + asset dimensions) / CR 1300, keyed ``wo_complete:<id>``."""
        client, user = _staff_client()
        committee = _committee()
        wo, _material = _preventive_wo(committee)
        _line(wo, unit_cost="7.25", quantity="2.00")  # 14.50
        _line(wo, unit_cost="3.00", quantity="1.00", name="Grease")  # 3.00

        _complete(client, wo)

        (meta,) = _charges(wo)
        assert meta.source_ref == f"wo_complete:{wo.id}"
        assert meta.created_by_id == user.id
        txn = meta.transaction
        assert txn.legs.count() == 2

        debit_leg = txn.legs.get(debit__isnull=False)
        credit_leg = txn.legs.get(credit__isnull=False)
        assert debit_leg.account.code == "5100"
        assert debit_leg.debit.amount == Decimal("17.50")
        assert credit_leg.account.code == "1300"
        assert credit_leg.credit.amount == Decimal("17.50")

        # The committee is charged and the machine is attributed on the expense leg.
        dim = LegDimension.objects.get(leg=debit_leg)
        assert dim.sig_id == committee.id
        assert dim.asset_id == wo.asset_id

        assert committee_balance(committee) == Decimal("17.50")
        assert trial_balance()["balanced"] is True

    def test_corrective_job_with_ad_hoc_materials_charges(self):
        """No PM template, ad-hoc lines only — including an out-of-pocket receipt."""
        client, _user = _staff_client()
        committee = _committee("Metal SIG")
        wo = _corrective_wo(committee)
        item = InventoryItemFactory(unit_cost=Decimal("7.25"), quantity_per_package=1)

        stocked = client.post(
            reverse("workorder-add-material", kwargs={"pk": wo.id}),
            {"material_name": "V-belt", "quantity_used": "2", "inventory_item": str(item.id)},
            format="json",
        ).json()
        receipted = client.post(
            reverse("workorder-add-material", kwargs={"pk": wo.id}),
            {
                "material_name": "Misc supplies — Ace Hardware",
                "unit_cost": "23.87",
                "receipt_image": _receipt_file(),
            },
            format="multipart",
        ).json()
        for line in (stocked, receipted):
            assert (
                client.patch(
                    reverse(
                        "workorder-toggle-material",
                        kwargs={"pk": wo.id, "material_id": line["id"]},
                    ),
                    {"was_used": True},
                    format="json",
                ).status_code
                == status.HTTP_200_OK
            )

        _complete(client, wo)

        # 2 × 7.25 (stock) + 23.87 (receipt) — the same total the WO reports.
        wo.refresh_from_db()
        assert wo.actual_material_cost == Decimal("38.37")
        assert committee_balance(committee) == Decimal("38.37")
        assert len(_charges(wo)) == 1

    def test_basis_counts_only_used_and_priced_lines(self):
        """Planned-but-unused and unpriced lines contribute nothing."""
        client, _user = _staff_client()
        committee = _committee("Textiles SIG")
        wo = _corrective_wo(committee)
        _line(wo, unit_cost="10.00", quantity="1.00")  # counted
        _line(wo, unit_cost="99.00", was_used=False, name="Spare")  # never used
        _line(wo, unit_cost=None, name="Shop rag")  # used, unpriced

        _complete(client, wo)

        assert committee_balance(committee) == Decimal("10.00")

    def test_freehand_spend_is_job_cost_but_not_a_committee_charge(self):
        """op-4pzp — the audited split, asserted from both sides.

        A priced ad-hoc line counts toward the job's Actual Material Cost the
        moment it is entered. The charge does **not** follow it: this entry
        credits ``1300 Inventory — supplies on hand``, so charging for a
        freehand supply nobody drew from stock would write down inventory that
        was never issued. Job cost ≥ ledger charge, and the gap is exactly the
        out-of-pocket spend.
        """
        client, _user = _staff_client()
        committee = _committee("Metalworking SIG")
        wo = _corrective_wo(committee)
        _line(wo, unit_cost="10.00", quantity="1.00")  # drawn from stock
        _line(wo, unit_cost="23.87", was_used=False, name="Ace Hardware run")  # freehand

        _complete(client, wo)

        wo.refresh_from_db()
        assert wo.actual_material_cost == Decimal("33.87")
        assert wo.consumed_material_cost == Decimal("10.00")
        assert committee_balance(committee) == Decimal("10.00")

    def test_freehand_only_job_charges_nothing_and_warns(self):
        """Nothing left the shelf, so there is nothing to book — even though the
        job did cost money. The committee hears about it through the same
        no-cost warning rather than a silent zero."""
        client, _user = _staff_client()
        committee = _committee("Fiber Arts SIG")
        wo = _corrective_wo(committee)
        _line(wo, unit_cost="23.87", was_used=False, name="Ace Hardware run")

        resp = _complete(client, wo)

        wo.refresh_from_db()
        assert wo.actual_material_cost == Decimal("23.87")
        assert Transaction.objects.count() == 0
        assert resp.data["warning"] == NO_COST_WARNING
        assert committee_balance(committee) == Decimal("0.00")

    def test_space_owned_job_posts_nothing(self):
        """No committee owns the machine — the ledger is untouched, no warning."""
        client, _user = _staff_client()
        wo = _corrective_wo()  # space-owned asset
        _line(wo, unit_cost="42.00")

        resp = _complete(client, wo)

        assert Transaction.objects.count() == 0
        assert EntryMeta.objects.count() == 0
        assert "warning" not in resp.data

    def test_committee_owned_job_with_no_cost_warns_and_posts_nothing(self):
        """The ``log_usage`` shape: committee on file, nothing priced → warning."""
        client, _user = _staff_client()
        committee = _committee("Ceramics SIG")
        wo = _corrective_wo(committee)
        _line(wo, unit_cost=None, name="Shop rag")

        resp = _complete(client, wo)

        assert Transaction.objects.count() == 0
        assert resp.data["warning"] == NO_COST_WARNING
        assert committee_balance(committee) == Decimal("0.00")

    def test_editing_a_completed_job_does_not_charge_again(self):
        """Only the transition into completed charges — later saves are no-ops."""
        client, _user = _staff_client()
        committee = _committee("Electronics SIG")
        wo = _corrective_wo(committee)
        _line(wo, unit_cost="12.00")

        _complete(client, wo)
        resp = client.patch(
            reverse("workorder-detail", kwargs={"pk": wo.id}),
            {"notes": "tightened the belt after all"},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        assert len(_charges(wo)) == 1
        assert committee_balance(committee) == Decimal("12.00")
        assert "warning" not in resp.data

    def test_a_never_completed_job_is_never_charged(self):
        """Moving to in_progress is not a completion."""
        client, _user = _staff_client()
        committee = _committee("Print SIG")
        wo = _corrective_wo(committee)
        _line(wo, unit_cost="5.00")

        assert (
            _set_status(client, wo, WorkOrder.Status.IN_PROGRESS).status_code == status.HTTP_200_OK
        )

        assert Transaction.objects.count() == 0
        assert committee_balance(committee) == Decimal("0.00")


# ─────────────────────────────────────────────────────────────────────────────
# Reopening gives the money back; re-completing takes it again
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.integration
class TestReopenAndRecomplete:
    def test_reopening_posts_an_append_only_reversal(self):
        client, user = _staff_client()
        committee = _committee("Woodshop SIG")
        wo = _corrective_wo(committee)
        _line(wo, unit_cost="20.00")
        _complete(client, wo)
        charge = _charges(wo)[0].transaction

        assert _set_status(client, wo, WorkOrder.Status.OPEN).status_code == status.HTTP_200_OK

        reversal_meta = EntryMeta.objects.get(source_type=SourceType.REVERSAL)
        assert reversal_meta.reverses_id == charge.id
        assert reversal_meta.created_by_id == user.id
        # Append-only: the original charge is still on the books, and the two
        # entries net the committee back to zero.
        assert EntryMeta.objects.filter(transaction=charge).exists()
        assert committee_balance(committee) == Decimal("0.00")
        assert trial_balance()["balanced"] is True
        assert active_charge(wo) is None

    def test_recompleting_restores_the_charge_exactly_once(self):
        """The bead's verify loop: complete → reopen → complete, no double-charge."""
        client, _user = _staff_client()
        committee = _committee("Metal SIG")
        wo = _corrective_wo(committee)
        _line(wo, unit_cost="20.00")

        _complete(client, wo)
        assert committee_balance(committee) == Decimal("20.00")

        _set_status(client, wo, WorkOrder.Status.OPEN)
        assert committee_balance(committee) == Decimal("0.00")

        # The acknowledgement from the first pass still stands, so this is a
        # plain re-completion.
        _complete(client, wo, acknowledge=False)
        assert committee_balance(committee) == Decimal("20.00")

        # Two charges and one reversal — never two live charges at once.
        charges = _charges(wo)
        assert [m.source_ref for m in charges] == [
            f"wo_complete:{wo.id}",
            f"wo_complete:{wo.id}:2",
        ]
        assert EntryMeta.objects.filter(source_type=SourceType.REVERSAL).count() == 1
        assert active_charge(wo) == charges[1].transaction
        assert trial_balance()["balanced"] is True

    def test_recompleting_picks_up_a_corrected_cost(self):
        """Reopen-to-fix-the-numbers is the point of the reversal: the new charge wins."""
        client, _user = _staff_client()
        committee = _committee("Auto SIG")
        wo = _corrective_wo(committee)
        line = _line(wo, unit_cost="20.00")

        _complete(client, wo)
        _set_status(client, wo, WorkOrder.Status.OPEN)

        line.unit_cost = Decimal("32.50")
        line.save(update_fields=["unit_cost"])
        _complete(client, wo, acknowledge=False)

        assert committee_balance(committee) == Decimal("32.50")

    def test_reopening_an_uncharged_job_is_a_no_op(self):
        """Nothing was ever posted, so there is nothing to reverse."""
        client, _user = _staff_client()
        wo = _corrective_wo()  # space-owned
        _line(wo, unit_cost="9.00")
        _complete(client, wo)

        assert (
            _set_status(client, wo, WorkOrder.Status.IN_PROGRESS).status_code == status.HTTP_200_OK
        )

        assert Transaction.objects.count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# The service seam itself
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.integration
class TestChargeCommitteeResolution:
    def test_reads_the_work_orders_own_asset(self):
        """A preventive WO derives its asset from the template on save."""
        committee = _committee("Woodshop SIG")
        wo, _material = _preventive_wo(committee)
        assert wo.asset_id is not None
        assert charge_committee(wo) == committee

    def test_space_and_user_owned_assets_have_no_committee(self):
        owner = User.objects.create_user(username="owner", password=get_random_string(24))
        assert charge_committee(_corrective_wo()) is None
        user_owned = AssetFactory(ownership_type="user", owning_user=owner)
        assert charge_committee(_corrective_wo(asset=user_owned)) is None
