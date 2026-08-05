"""PO receipt → committee purchasing ledger wiring (Phase 2 · Bead 5).

``receive_delivery`` posts ``DR 1300`` (dim=committee) / ``CR 2000`` for each
committee-owned line that carries a unit cost, atomically inside the receive
transaction. Non-committee lines and zero-cost lines post nothing, leaving the
receipt behaviour unchanged. The accounting engine is Postgres/hordak-only.
"""

from decimal import Decimal

from django.contrib.auth.models import Group
from django.utils import timezone

import pytest
from hordak.models import Transaction

from accounting.adapters import post_po_receipt
from accounting.models import LegDimension
from accounting.services import trial_balance
from inventory.tests.factories import ItemSupplierFactory, SupplierFactory
from reorder_queue import services
from reorder_queue.models import DeliveryItem, PurchaseOrder, PurchaseOrderItem
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PO_RECEIPT = "PO_RECEIPT"


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


def _committee_po(user, *, group=None, qty=10, unit_cost=None, stock=0):
    """A SENT PO with one inventory line whose item is owned by ``group``.

    ``group=None`` leaves the item Space-owned (no committee). ``unit_cost``
    defaults to ``2.00`` and sets the line's ordered cost (the charge driver);
    pass ``Decimal("0")`` for the no-cost case. The item_supplier's own unit cost
    is irrelevant to the hook — it reads the PO line's actual-or-ordered cost —
    so it just gets a valid positive value.
    """
    if unit_cost is None:
        unit_cost = Decimal("2.00")
    supplier = SupplierFactory()
    po = PurchaseOrder.objects.create(
        supplier=supplier,
        status=PurchaseOrder.Status.SENT,
        created_by=user,
        sent_at=timezone.now(),
    )
    item_supplier = ItemSupplierFactory(
        supplier=supplier, unit_cost=unit_cost or Decimal("1.00"), average_lead_time=5
    )
    item = item_supplier.item
    item.current_stock = stock
    if group is not None:
        item.owning_group = group
        item.ownership_type = "group"
    item.save()
    line = PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=item_supplier,
        quantity_ordered=qty,
        unit_cost_ordered=unit_cost,
    )
    return po, line, item


def test_committee_receipt_posts_one_balanced_po_receipt_entry():
    """Committee item + unit cost → one DR 1300 (dim=committee) / CR 2000 entry."""
    user = UserFactory()
    group = Group.objects.create(name="Laser SIG")
    po, line, item = _committee_po(user, group=group, qty=5, unit_cost=Decimal("4.00"), stock=0)

    services.receive_delivery(po, [(line, 5)], received_by=user, delivery_datetime=timezone.now())

    # Stock still moved (the receipt behaviour is unchanged).
    item.refresh_from_db()
    assert item.current_stock == 5

    entries = Transaction.objects.filter(meta__source_type=PO_RECEIPT)
    assert entries.count() == 1
    txn = entries.get()

    debit_leg = txn.legs.get(debit__isnull=False)
    credit_leg = txn.legs.get(credit__isnull=False)
    assert debit_leg.account.code == "1300"
    assert debit_leg.debit.amount == Decimal("20.00")  # 5 × 4.00
    assert credit_leg.account.code == "2000"
    assert credit_leg.credit.amount == Decimal("20.00")

    # Committee attribution on the inventory (debit) leg.
    dim = LegDimension.objects.get(leg=debit_leg)
    assert dim.sig_id == group.id

    # The books still balance.
    assert trial_balance()["balanced"] is True


def test_receipt_prefers_actual_unit_cost_over_ordered():
    """unit_cost_actual, when present, drives the charge instead of ordered."""
    user = UserFactory()
    group = Group.objects.create(name="CNC SIG")
    po, line, _item = _committee_po(user, group=group, qty=3, unit_cost=Decimal("2.00"), stock=0)
    line.unit_cost_actual = Decimal("2.50")
    line.save()

    services.receive_delivery(po, [(line, 3)], received_by=user, delivery_datetime=timezone.now())

    txn = Transaction.objects.get(meta__source_type=PO_RECEIPT)
    assert txn.legs.get(debit__isnull=False).debit.amount == Decimal("7.50")  # 3 × 2.50


def test_receipt_is_idempotent_on_delivery_item():
    """Re-driving the same delivery_item does not double-post."""
    user = UserFactory()
    group = Group.objects.create(name="Idem SIG")
    po, line, item = _committee_po(user, group=group, qty=4, unit_cost=Decimal("3.00"), stock=0)

    delivery = services.receive_delivery(
        po, [(line, 4)], received_by=user, delivery_datetime=timezone.now()
    )
    assert Transaction.objects.filter(meta__source_type=PO_RECEIPT).count() == 1

    # Replay the post for the very same DeliveryItem (its id is the source key).
    delivery_item = DeliveryItem.objects.get(delivery=delivery, purchase_order_item=line)
    post_po_receipt(
        committee=group,
        amount=Decimal("12.00"),
        source_ref=f"po_receipt:{delivery_item.id}",
        item=item,
    )
    assert Transaction.objects.filter(meta__source_type=PO_RECEIPT).count() == 1


def test_non_committee_item_posts_nothing_but_still_increments_stock():
    """Space-owned item (no committee) → no ledger entry; stock still moves."""
    user = UserFactory()
    po, line, item = _committee_po(user, group=None, qty=6, unit_cost=Decimal("2.00"), stock=1)

    services.receive_delivery(po, [(line, 6)], received_by=user, delivery_datetime=timezone.now())

    item.refresh_from_db()
    assert item.current_stock == 7
    assert not Transaction.objects.filter(meta__source_type=PO_RECEIPT).exists()


def test_committee_item_without_unit_cost_posts_nothing():
    """Committee item with a zero unit cost → no entry; stock still moves."""
    user = UserFactory()
    group = Group.objects.create(name="Free SIG")
    po, line, item = _committee_po(user, group=group, qty=2, unit_cost=Decimal("0"), stock=0)

    services.receive_delivery(po, [(line, 2)], received_by=user, delivery_datetime=timezone.now())

    item.refresh_from_db()
    assert item.current_stock == 2
    assert not Transaction.objects.filter(meta__source_type=PO_RECEIPT).exists()
