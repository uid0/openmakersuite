"""Committee settlement / period-close (Phase 2 · Bead 4).

Covers the balance helper (:func:`accounting.services.committee_balance`), the
append-only settlement service (:func:`accounting.adapters.settle_committee`),
and the staff-only ``POST /api/accounting/committee-settlement/`` endpoint.

Postgres/hordak required (see :mod:`accounting`).
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group

import pytest
from hordak.models import Transaction

from accounting.adapters import post_supply_consumption, settle_committee
from accounting.models import EntryMeta, LegDimension, SourceType
from accounting.services import committee_balance, reverse_entry, trial_balance

pytestmark = pytest.mark.django_db

SETTLEMENT_URL = "/api/accounting/committee-settlement/"


def _charge(committee, amount, ref, *, on=None):
    """Post a SIG_CHARGE (DR 5100 [sig] / CR 1300) for ``committee``."""
    return post_supply_consumption(
        committee=committee,
        amount=Decimal(amount),
        source_ref=ref,
        date=on,
    )


def _settlement_count():
    return Transaction.objects.filter(meta__source_type=SourceType.SETTLEMENT).count()


# ─────────────────────────────────────────────────────────────────────────
# committee_balance
# ─────────────────────────────────────────────────────────────────────────
def test_committee_balance_is_net_of_that_committee_on_5100():
    a = Group.objects.create(name="Balance A SIG")
    b = Group.objects.create(name="Balance B SIG")
    _charge(a, "10.00", "usage:a1")
    _charge(a, "2.50", "usage:a2")
    _charge(b, "99.00", "usage:b1")  # another committee must not leak in

    assert committee_balance(a) == Decimal("12.50")
    assert committee_balance(b) == Decimal("99.00")


def test_committee_balance_is_zero_for_a_committee_with_no_ledger():
    group = Group.objects.create(name="Fresh SIG")
    assert committee_balance(group) == Decimal("0.00")


def test_committee_balance_respects_as_of():
    group = Group.objects.create(name="Timed SIG")
    _charge(group, "5.00", "usage:t1", on=date(2026, 1, 1))
    _charge(group, "7.00", "usage:t2", on=date(2026, 6, 1))

    assert committee_balance(group, as_of=date(2026, 3, 1)) == Decimal("5.00")
    assert committee_balance(group, as_of=date(2026, 6, 1)) == Decimal("12.00")
    assert committee_balance(group) == Decimal("12.00")


def test_committee_balance_reflects_charge_reversal():
    """An append-only reversal of a charge drives the balance back down."""
    group = Group.objects.create(name="Reversal SIG")
    charge = _charge(group, "8.00", "usage:rev")
    assert committee_balance(group) == Decimal("8.00")

    reverse_entry(charge)
    assert committee_balance(group) == Decimal("0.00")


# ─────────────────────────────────────────────────────────────────────────
# settle_committee — the append-only close
# ─────────────────────────────────────────────────────────────────────────
def test_settle_absorbed_zeroes_balance_and_keeps_books_balanced():
    group = Group.objects.create(name="Woodshop SIG")
    _charge(group, "12.50", "usage:1")
    _charge(group, "7.50", "usage:2")
    assert committee_balance(group) == Decimal("20.00")

    txn = settle_committee(committee=group, note="Q2 close")

    assert txn is not None
    assert _settlement_count() == 1

    credit_leg = txn.legs.get(credit__isnull=False)
    debit_leg = txn.legs.get(debit__isnull=False)
    # CR 5100 removes the accrued expense; DR 3000 absorbs it (default).
    assert credit_leg.account.code == "5100"
    assert credit_leg.credit.amount == Decimal("20.00")
    assert debit_leg.account.code == "3000"
    assert debit_leg.debit.amount == Decimal("20.00")

    # Committee dimension on BOTH legs.
    assert LegDimension.objects.get(leg=credit_leg).sig_id == group.id
    assert LegDimension.objects.get(leg=debit_leg).sig_id == group.id

    # Balance zeroed; the trial balance still balances.
    assert committee_balance(group) == Decimal("0.00")
    assert trial_balance()["balanced"] is True

    meta = EntryMeta.objects.get(transaction=txn)
    assert meta.source_type == SourceType.SETTLEMENT
    assert meta.source_ref.startswith(f"settle:{group.id}:")
    assert txn.description == "Q2 close"


def test_settle_reimbursed_debits_accounts_receivable():
    group = Group.objects.create(name="Metal SIG")
    _charge(group, "30.00", "usage:r1")

    txn = settle_committee(committee=group, reimbursed=True)

    debit_leg = txn.legs.get(debit__isnull=False)
    assert debit_leg.account.code == "1200"  # AR, not 3000
    assert debit_leg.debit.amount == Decimal("30.00")
    assert LegDimension.objects.get(leg=debit_leg).sig_id == group.id
    assert committee_balance(group) == Decimal("0.00")
    assert trial_balance()["balanced"] is True


def test_settle_default_description_names_the_committee():
    group = Group.objects.create(name="Named SIG")
    _charge(group, "3.00", "usage:n1")
    txn = settle_committee(committee=group)
    assert txn.description == "Committee settlement: Named SIG"


def test_settle_is_idempotent_per_committee_and_date():
    group = Group.objects.create(name="Idem SIG")
    _charge(group, "9.00", "usage:i1", on=date(2026, 6, 15))
    as_of = date(2026, 6, 30)

    first = settle_committee(committee=group, as_of=as_of)
    second = settle_committee(committee=group, as_of=as_of)

    assert first is not None
    # The second close finds a zero balance and does not double-post.
    assert second is None
    assert _settlement_count() == 1
    # The settlement is dated at the close date.
    assert first.date == as_of
    assert committee_balance(group, as_of=as_of) == Decimal("0.00")


def test_settle_with_zero_balance_returns_none_and_posts_nothing():
    group = Group.objects.create(name="Empty SIG")
    assert settle_committee(committee=group) is None
    assert _settlement_count() == 0


def test_settle_only_settles_balance_accrued_on_or_before_as_of():
    group = Group.objects.create(name="Partial SIG")
    _charge(group, "5.00", "usage:p1", on=date(2026, 1, 1))
    _charge(group, "40.00", "usage:p2", on=date(2026, 12, 1))  # after the close

    txn = settle_committee(committee=group, as_of=date(2026, 6, 30))

    assert txn.legs.get(credit__isnull=False).credit.amount == Decimal("5.00")
    # As of the close date, the committee is settled; all-time still owes the rest.
    assert committee_balance(group, as_of=date(2026, 6, 30)) == Decimal("0.00")
    assert committee_balance(group) == Decimal("40.00")


def test_fresh_charge_after_settlement_accumulates_from_zero():
    group = Group.objects.create(name="Cycle SIG")
    _charge(group, "15.00", "usage:c1")
    settle_committee(committee=group)
    assert committee_balance(group) == Decimal("0.00")

    _charge(group, "4.00", "usage:c2")
    assert committee_balance(group) == Decimal("4.00")


# ─────────────────────────────────────────────────────────────────────────
# Endpoint: POST /api/accounting/committee-settlement/  (staff only)
# ─────────────────────────────────────────────────────────────────────────
def test_settlement_endpoint_rejects_anonymous(api_client):
    group = Group.objects.create(name="Anon SIG")
    resp = api_client.post(SETTLEMENT_URL, {"committee": group.id}, format="json")
    assert resp.status_code in (401, 403)


def test_settlement_endpoint_rejects_non_staff_sig_admin(api_client):
    """A SIG admin of the committee may VIEW its statement but must NOT settle."""
    from membership.models import SIGAdmin
    from membership.tests.factories import UserFactory

    group = Group.objects.create(name="Owned SIG")
    user = UserFactory(is_staff=False)
    SIGAdmin.objects.create(user=user, group=group, is_active=True)

    api_client.force_authenticate(user)
    resp = api_client.post(SETTLEMENT_URL, {"committee": group.id}, format="json")
    assert resp.status_code == 403


def test_settlement_endpoint_staff_settles(api_client, admin_user):
    group = Group.objects.create(name="Staff-settle SIG")
    _charge(group, "25.00", "usage:api1")

    api_client.force_authenticate(admin_user)
    resp = api_client.post(SETTLEMENT_URL, {"committee": group.id}, format="json")

    assert resp.status_code == 200
    assert resp.data["settled_amount"] == "25.00"
    assert resp.data["new_balance"] == "0.00"
    assert resp.data["reimbursed"] is False
    assert resp.data["transaction"] is not None
    assert resp.data["committee"] == {"id": group.id, "name": "Staff-settle SIG"}
    assert committee_balance(group) == Decimal("0.00")


def test_settlement_endpoint_reimbursed(api_client, admin_user):
    group = Group.objects.create(name="Reimb SIG")
    _charge(group, "40.00", "usage:api2")

    api_client.force_authenticate(admin_user)
    resp = api_client.post(
        SETTLEMENT_URL, {"committee": group.id, "reimbursed": True}, format="json"
    )

    assert resp.status_code == 200
    assert resp.data["reimbursed"] is True
    txn = Transaction.objects.get(uuid=resp.data["transaction"])
    assert txn.legs.get(debit__isnull=False).account.code == "1200"


def test_settlement_endpoint_as_of_dates_the_entry(api_client, admin_user):
    group = Group.objects.create(name="Dated SIG")
    _charge(group, "11.00", "usage:api3", on=date(2026, 5, 1))

    api_client.force_authenticate(admin_user)
    resp = api_client.post(
        SETTLEMENT_URL, {"committee": group.id, "as_of": "2026-05-31"}, format="json"
    )

    assert resp.status_code == 200
    assert resp.data["settled_amount"] == "11.00"
    txn = Transaction.objects.get(uuid=resp.data["transaction"])
    assert txn.date == date(2026, 5, 31)


def test_settlement_endpoint_nothing_to_settle(api_client, admin_user):
    group = Group.objects.create(name="Zero SIG")

    api_client.force_authenticate(admin_user)
    resp = api_client.post(SETTLEMENT_URL, {"committee": group.id}, format="json")

    assert resp.status_code == 200
    assert resp.data["settled_amount"] == "0.00"
    assert resp.data["new_balance"] == "0.00"
    assert resp.data["transaction"] is None
    assert "detail" in resp.data
    assert _settlement_count() == 0


def test_settlement_endpoint_unknown_committee_404(api_client, admin_user):
    api_client.force_authenticate(admin_user)
    resp = api_client.post(SETTLEMENT_URL, {"committee": 999999}, format="json")
    assert resp.status_code == 404


def test_settlement_endpoint_missing_committee_400(api_client, admin_user):
    api_client.force_authenticate(admin_user)
    resp = api_client.post(SETTLEMENT_URL, {}, format="json")
    assert resp.status_code == 400
