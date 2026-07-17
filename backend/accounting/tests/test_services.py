"""Service-layer tests for the double-entry ledger (Postgres/hordak required)."""

from decimal import Decimal

from django.contrib.auth.models import Group

import pytest
from hordak.models import Account, Transaction

from accounting.models import EntryMeta, LegDimension, SourceType
from accounting.services import (
    Line,
    get_account,
    post_entry,
    reverse_entry,
    trial_balance,
    type_word,
)

pytestmark = pytest.mark.django_db


def _sig(name="Woodshop SIG"):
    return Group.objects.create(name=name)


def test_seeded_chart_present_with_correct_types():
    # 0002_seed_chart_of_accounts runs during test-DB migration.
    assert Account.objects.count() >= 10
    ar = get_account("1200")
    assert ar.name == "Accounts Receivable"
    assert ar.currencies == ["USD"]
    assert type_word(ar) == "asset"
    assert type_word(get_account("2000")) == "liability"
    assert type_word(get_account("3000")) == "equity"
    assert type_word(get_account("4000")) == "income"
    assert type_word(get_account("5100")) == "expense"
    # No 1000 Cash account in Phase 1.
    assert not Account.objects.filter(code="1000").exists()


def test_balanced_post_entry_sets_dimension_and_meta():
    sig = _sig()
    txn = post_entry(
        lines=[
            Line(account="1300", debit=Decimal("100.00"), sig=sig),
            Line(account="2000", credit=Decimal("100.00")),
        ],
        source_type=SourceType.PO_RECEIPT,
        description="PO receipt of supplies",
        source_ref="po:123",
    )
    assert isinstance(txn, Transaction)
    assert txn.legs.count() == 2

    meta = EntryMeta.objects.get(transaction=txn)
    assert meta.source_type == SourceType.PO_RECEIPT
    assert meta.source_ref == "po:123"
    assert "PO_RECEIPT" in str(meta)

    debit_leg = txn.legs.get(debit__isnull=False)
    dim = LegDimension.objects.get(leg=debit_leg)
    assert dim.sig_id == sig.id
    assert "sig=" in str(dim)
    # The credit leg had no dimension supplied.
    credit_leg = txn.legs.get(credit__isnull=False)
    assert not LegDimension.objects.filter(leg=credit_leg).exists()


def test_post_entry_with_asset_dimension():
    from inventory.tests.factories import AssetFactory

    asset = AssetFactory()
    txn = post_entry(
        lines=[
            Line(account="1700", debit=Decimal("500.00"), asset=asset),
            Line(account="2000", credit=Decimal("500.00")),
        ],
        source_type=SourceType.ASSET_PURCHASE,
    )
    debit_leg = txn.legs.get(debit__isnull=False)
    assert LegDimension.objects.get(leg=debit_leg).asset_id == asset.id


def test_amounts_are_usd_two_decimal_places():
    txn = post_entry(
        lines=[
            Line(account="1300", debit=Decimal("10")),
            Line(account="2000", credit=Decimal("10")),
        ],
        source_type=SourceType.MANUAL,
    )
    leg = txn.legs.get(debit__isnull=False)
    assert leg.debit.amount == Decimal("10.00")
    assert leg.debit.amount.as_tuple().exponent == -2
    assert str(leg.debit.currency) == "USD"


def test_unbalanced_entry_raises_and_writes_nothing():
    with pytest.raises(ValueError, match="does not balance"):
        post_entry(
            lines=[
                Line(account="1300", debit=Decimal("100.00")),
                Line(account="2000", credit=Decimal("90.00")),
            ],
            source_type=SourceType.MANUAL,
        )
    assert Transaction.objects.count() == 0


def test_fewer_than_two_lines_raises():
    with pytest.raises(ValueError, match="at least two lines"):
        post_entry(
            lines=[Line(account="1300", debit=Decimal("1.00"))],
            source_type=SourceType.MANUAL,
        )


def test_line_must_set_exactly_one_of_debit_or_credit():
    with pytest.raises(ValueError, match="exactly one"):
        post_entry(
            lines=[
                Line(account="1300", debit=Decimal("1.00"), credit=Decimal("1.00")),
                Line(account="2000", credit=Decimal("1.00")),
            ],
            source_type=SourceType.MANUAL,
        )
    with pytest.raises(ValueError, match="exactly one"):
        post_entry(
            lines=[
                Line(account="1300"),
                Line(account="2000", credit=Decimal("1.00")),
            ],
            source_type=SourceType.MANUAL,
        )


def test_amounts_must_be_positive():
    with pytest.raises(ValueError, match="positive"):
        post_entry(
            lines=[
                Line(account="1300", debit=Decimal("-5.00")),
                Line(account="2000", credit=Decimal("-5.00")),
            ],
            source_type=SourceType.MANUAL,
        )


def test_idempotent_double_post_yields_one_transaction():
    kwargs = dict(
        lines=[
            Line(account="1300", debit=Decimal("25.00")),
            Line(account="2000", credit=Decimal("25.00")),
        ],
        source_type=SourceType.PO_RECEIPT,
        source_ref="po:idem",
    )
    first = post_entry(**kwargs)
    second = post_entry(**kwargs)
    assert first.pk == second.pk
    assert Transaction.objects.count() == 1
    assert (
        EntryMeta.objects.filter(source_type=SourceType.PO_RECEIPT, source_ref="po:idem").count()
        == 1
    )


def test_blank_source_ref_is_not_deduplicated():
    for _ in range(2):
        post_entry(
            lines=[
                Line(account="1300", debit=Decimal("1.00")),
                Line(account="2000", credit=Decimal("1.00")),
            ],
            source_type=SourceType.MANUAL,
        )
    assert Transaction.objects.count() == 2


def test_reverse_entry_nets_to_zero_links_and_leaves_original_intact():
    sig = _sig()
    original = post_entry(
        lines=[
            Line(account="1300", debit=Decimal("40.00"), sig=sig),
            Line(account="2000", credit=Decimal("40.00")),
        ],
        source_type=SourceType.PO_RECEIPT,
        source_ref="po:rev",
    )
    reversal = reverse_entry(original)

    rmeta = EntryMeta.objects.get(transaction=reversal)
    assert rmeta.source_type == SourceType.REVERSAL
    assert rmeta.reverses_id == original.id

    # Dimension copied onto the reversal's mirror (now credit) 1300 leg.
    rev_1300 = reversal.legs.get(account__code="1300")
    assert rev_1300.credit.amount == Decimal("40.00")
    assert LegDimension.objects.get(leg=rev_1300).sig_id == sig.id

    # Both touched accounts net to zero.
    by_code = {r["code"]: r for r in trial_balance()["accounts"]}
    assert by_code["1300"]["balance"] == Decimal("0.00")
    assert by_code["2000"]["balance"] == Decimal("0.00")

    # Original untouched (append-only).
    assert Transaction.objects.filter(pk=original.pk).exists()
    assert original.legs.count() == 2


def test_reverse_entry_is_idempotent():
    original = post_entry(
        lines=[
            Line(account="1300", debit=Decimal("5.00")),
            Line(account="2000", credit=Decimal("5.00")),
        ],
        source_type=SourceType.MANUAL,
        source_ref="man:1",
    )
    assert reverse_entry(original).pk == reverse_entry(original).pk


def test_trial_balance_balances_with_correct_columns():
    post_entry(
        lines=[
            Line(account="1300", debit=Decimal("100.00")),
            Line(account="2000", credit=Decimal("100.00")),
        ],
        source_type=SourceType.PO_RECEIPT,
        source_ref="po:tb",
    )
    report = trial_balance()
    assert report["balanced"] is True
    assert report["total_debit"] == Decimal("100.00")
    assert report["total_credit"] == Decimal("100.00")

    by_code = {r["code"]: r for r in report["accounts"]}
    assert by_code["1300"]["debit"] == Decimal("100.00")
    assert by_code["1300"]["credit"] == Decimal("0.00")
    assert by_code["1300"]["type"] == "asset"
    assert by_code["2000"]["debit"] == Decimal("0.00")
    assert by_code["2000"]["credit"] == Decimal("100.00")
    assert by_code["2000"]["type"] == "liability"


def test_trial_balance_as_of_excludes_later_transactions():
    post_entry(
        lines=[
            Line(account="1300", debit=Decimal("9.00")),
            Line(account="2000", credit=Decimal("9.00")),
        ],
        source_type=SourceType.MANUAL,
    )
    # An as-of date before any transaction shows an empty, balanced report.
    import datetime

    report = trial_balance(as_of=datetime.date(2000, 1, 1))
    assert report["balanced"] is True
    assert report["total_debit"] == Decimal("0.00")


def test_get_account_resolves_instances_and_codes_in_lines():
    inv = get_account("1300")
    txn = post_entry(
        lines=[
            Line(account=inv, debit=Decimal("7.00")),
            Line(account="2000", credit=Decimal("7.00")),
        ],
        source_type=SourceType.MANUAL,
    )
    assert txn.legs.count() == 2
