"""Committee chargeback on consume — ``log_usage`` + ledger (Postgres/hordak).

Covers accounting Phase-2 Bead 1: charging supplies to a committee when they are
consumed via the ``InventoryItem.log_usage`` action. The endpoint is otherwise
public and backward compatible — the no-committee path posts nothing to the
ledger and behaves exactly as before.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

import pytest
from hordak.models import Transaction
from rest_framework import status
from rest_framework.test import APIClient

from accounting.adapters import post_supply_consumption
from accounting.models import EntryMeta, LegDimension, SourceType
from accounting.services import trial_balance
from inventory.models import UsageLog
from inventory.tests.factories import InventoryItemFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


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
    user = User.objects.create_user(username="charger", password="pw", is_staff=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def _url(item):
    return reverse("inventoryitem-log-usage", kwargs={"pk": str(item.id)})


@pytest.mark.integration
class TestLogUsageCommitteeCharge:
    """AC coverage for charging a committee on consume."""

    def test_no_committee_decrements_stock_and_posts_no_ledger_entry(self):
        """Unchanged behaviour: stock down, UsageLog created, NO ledger entry."""
        client = APIClient()  # public / anonymous — unchanged permissions
        item = InventoryItemFactory(current_stock=50, unit_cost=Decimal("2.00"))

        resp = client.post(_url(item), {"quantity": 5}, format="json")

        assert resp.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.current_stock == 45

        log = UsageLog.objects.get(item=item)
        assert log.quantity_used == 5
        assert log.charged_group_id is None
        assert log.ledger_transaction_id is None
        # The cost/actor snapshot is still recorded (harmless record-keeping);
        # the anonymous caller leaves charged_by null.
        assert log.unit_cost == Decimal("2.00")
        assert log.total_cost == Decimal("10.00")
        assert log.charged_by_id is None

        # Nothing posted to the ledger.
        assert Transaction.objects.count() == 0
        assert EntryMeta.objects.count() == 0
        assert "warning" not in resp.data

    def test_committee_with_cost_posts_one_balanced_sig_charge(self):
        """UsageLog carries the snapshot + link; exactly one balanced SIG_CHARGE."""
        client, user = _staff_client()
        group = Group.objects.create(name="Woodshop SIG")
        item = InventoryItemFactory(current_stock=50, unit_cost=Decimal("2.00"))

        resp = client.post(_url(item), {"quantity": 5, "charged_group": group.id}, format="json")

        assert resp.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.current_stock == 45

        log = UsageLog.objects.get(item=item)
        assert log.charged_group_id == group.id
        assert log.unit_cost == Decimal("2.00")
        assert log.total_cost == Decimal("10.00")
        assert log.charged_by_id == user.id
        assert log.ledger_transaction_id is not None

        # Exactly one SIG_CHARGE transaction.
        assert EntryMeta.objects.filter(source_type=SourceType.SIG_CHARGE).count() == 1
        txn = log.ledger_transaction

        # DR 5100 with the committee dimension.
        debit_leg = txn.legs.get(debit__isnull=False)
        assert debit_leg.account.code == "5100"
        assert debit_leg.debit.amount == Decimal("10.00")
        assert LegDimension.objects.get(leg=debit_leg).sig_id == group.id

        # CR 1300.
        credit_leg = txn.legs.get(credit__isnull=False)
        assert credit_leg.account.code == "1300"
        assert credit_leg.credit.amount == Decimal("10.00")

        # Response echoes the chargeback fields.
        assert resp.data["charged_group"] == group.id
        assert Decimal(str(resp.data["total_cost"])) == Decimal("10.00")
        assert resp.data["ledger_transaction"] == txn.id
        assert "warning" not in resp.data

        # The books still balance.
        assert trial_balance()["balanced"] is True

    def test_replaying_same_usage_source_ref_does_not_double_post(self):
        """Idempotency: re-posting for the same UsageLog pk returns the same txn."""
        client, user = _staff_client()
        group = Group.objects.create(name="Retry SIG")
        item = InventoryItemFactory(current_stock=20, unit_cost=Decimal("4.00"))

        resp = client.post(_url(item), {"quantity": 2, "charged_group": group.id}, format="json")
        assert resp.status_code == status.HTTP_200_OK
        log = UsageLog.objects.get(item=item)
        original_txn = log.ledger_transaction
        assert original_txn is not None

        # A retry of the same domain event (same source_ref) must not double-post.
        replay = post_supply_consumption(
            committee=group,
            amount=log.total_cost,
            source_ref=f"usage:{log.pk}",
            item=item,
            created_by=user,
        )
        assert replay.pk == original_txn.pk
        assert (
            EntryMeta.objects.filter(
                source_type=SourceType.SIG_CHARGE, source_ref=f"usage:{log.pk}"
            ).count()
            == 1
        )
        assert Transaction.objects.count() == 1

    def test_committee_but_no_unit_cost_records_group_and_warns(self):
        """No cost on file: committee recorded, nothing posted, warning returned."""
        client, _user = _staff_client()
        group = Group.objects.create(name="No-Cost SIG")
        item = InventoryItemFactory(current_stock=10, unit_cost=None)

        resp = client.post(_url(item), {"quantity": 3, "charged_group": group.id}, format="json")

        assert resp.status_code == status.HTTP_200_OK
        log = UsageLog.objects.get(item=item)
        assert log.charged_group_id == group.id
        assert log.unit_cost is None
        assert log.total_cost is None
        assert log.ledger_transaction_id is None

        # Nothing posted.
        assert Transaction.objects.count() == 0
        assert EntryMeta.objects.filter(source_type=SourceType.SIG_CHARGE).count() == 0

        assert "warning" in resp.data
        assert "no unit cost" in resp.data["warning"]

    def test_committee_charge_requires_permission(self):
        """A non-privileged (here anonymous) caller supplying a committee -> 403."""
        client = APIClient()  # anonymous — cannot charge
        group = Group.objects.create(name="Locked SIG")
        item = InventoryItemFactory(current_stock=10, unit_cost=Decimal("1.00"))

        resp = client.post(_url(item), {"quantity": 1, "charged_group": group.id}, format="json")

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        # Nothing was created — the charge gate fires before any write.
        assert UsageLog.objects.count() == 0
        assert Transaction.objects.count() == 0

    def test_invalid_committee_returns_400(self):
        """A charged_group that does not resolve to a real Group -> 400."""
        client, _user = _staff_client()
        item = InventoryItemFactory(current_stock=10, unit_cost=Decimal("1.00"))

        resp = client.post(_url(item), {"quantity": 1, "charged_group": 999999}, format="json")

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert UsageLog.objects.count() == 0
        assert Transaction.objects.count() == 0
