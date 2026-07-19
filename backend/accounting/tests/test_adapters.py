"""Adapter tests: domain events -> ledger entries (Postgres/hordak required)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

import pytest
from hordak.models import Transaction

from accounting.adapters import post_po_receipt, post_supply_consumption
from accounting.models import EntryMeta, LegDimension, SourceType
from accounting.services import trial_balance

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_post_supply_consumption_builds_correct_balanced_entry_and_dimension():
    """DR 5100 (sig dimension) / CR 1300, SIG_CHARGE, and the ledger balances."""
    group = Group.objects.create(name="Woodshop SIG")
    user = User.objects.create_user(username="charger", password="x")

    txn = post_supply_consumption(
        committee=group,
        amount=Decimal("12.50"),
        source_ref="usage:1",
        created_by=user,
    )

    assert txn.legs.count() == 2
    debit_leg = txn.legs.get(debit__isnull=False)
    credit_leg = txn.legs.get(credit__isnull=False)

    # Expense debited, inventory credited.
    assert debit_leg.account.code == "5100"
    assert debit_leg.debit.amount == Decimal("12.50")
    assert str(debit_leg.debit.currency) == "USD"
    assert credit_leg.account.code == "1300"
    assert credit_leg.credit.amount == Decimal("12.50")

    # SIG dimension on the expense leg only; no asset supplied.
    dim = LegDimension.objects.get(leg=debit_leg)
    assert dim.sig_id == group.id
    assert dim.asset_id is None
    assert not LegDimension.objects.filter(leg=credit_leg).exists()

    # Provenance / idempotency key.
    meta = EntryMeta.objects.get(transaction=txn)
    assert meta.source_type == SourceType.SIG_CHARGE
    assert meta.source_ref == "usage:1"
    assert meta.created_by_id == user.id

    # The books still balance.
    assert trial_balance()["balanced"] is True


def test_post_supply_consumption_records_asset_dimension():
    """An optional asset is recorded on the debit line alongside the committee."""
    from inventory.tests.factories import AssetFactory

    group = Group.objects.create(name="Metal SIG")
    asset = AssetFactory()

    txn = post_supply_consumption(
        committee=group,
        amount=Decimal("5.00"),
        source_ref="usage:asset:1",
        asset=asset,
    )

    debit_leg = txn.legs.get(debit__isnull=False)
    dim = LegDimension.objects.get(leg=debit_leg)
    assert dim.sig_id == group.id
    assert dim.asset_id == asset.id


def test_post_supply_consumption_is_idempotent_on_source_ref():
    """Replaying the same source_ref returns the original entry (no double-post)."""
    group = Group.objects.create(name="Idem SIG")

    first = post_supply_consumption(
        committee=group, amount=Decimal("3.00"), source_ref="usage:idem"
    )
    second = post_supply_consumption(
        committee=group, amount=Decimal("3.00"), source_ref="usage:idem"
    )

    assert first.pk == second.pk
    assert Transaction.objects.filter(meta__source_ref="usage:idem").count() == 1


def test_post_po_receipt_builds_correct_balanced_entry_and_dimension():
    """DR 1300 (sig dimension) / CR 2000, PO_RECEIPT, and the ledger balances."""
    group = Group.objects.create(name="Print SIG")
    user = User.objects.create_user(username="receiver", password="x")

    txn = post_po_receipt(
        committee=group,
        amount=Decimal("40.00"),
        source_ref="po_receipt:1",
        created_by=user,
    )

    assert txn.legs.count() == 2
    debit_leg = txn.legs.get(debit__isnull=False)
    credit_leg = txn.legs.get(credit__isnull=False)

    # Inventory debited (stock added on the committee's tab), payable credited.
    assert debit_leg.account.code == "1300"
    assert debit_leg.debit.amount == Decimal("40.00")
    assert str(debit_leg.debit.currency) == "USD"
    assert credit_leg.account.code == "2000"
    assert credit_leg.credit.amount == Decimal("40.00")

    # SIG dimension on the inventory (debit) leg only; the payable is unattributed.
    dim = LegDimension.objects.get(leg=debit_leg)
    assert dim.sig_id == group.id
    assert dim.asset_id is None
    assert not LegDimension.objects.filter(leg=credit_leg).exists()

    # Provenance / idempotency key.
    meta = EntryMeta.objects.get(transaction=txn)
    assert meta.source_type == SourceType.PO_RECEIPT
    assert meta.source_ref == "po_receipt:1"
    assert meta.created_by_id == user.id

    # The books still balance.
    assert trial_balance()["balanced"] is True


def test_post_po_receipt_is_idempotent_on_source_ref():
    """Replaying the same delivery-item key returns the original entry."""
    group = Group.objects.create(name="PO Idem SIG")

    first = post_po_receipt(committee=group, amount=Decimal("7.00"), source_ref="po_receipt:idem")
    second = post_po_receipt(committee=group, amount=Decimal("7.00"), source_ref="po_receipt:idem")

    assert first.pk == second.pk
    assert Transaction.objects.filter(meta__source_ref="po_receipt:idem").count() == 1
