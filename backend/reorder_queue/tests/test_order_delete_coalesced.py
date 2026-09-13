"""Deleting a purchase order re-derives nothing per line (oms-cascade-delete-not-coalesced).

``PurchaseOrder.delete()`` goes through ``Collector``, which collects the
order's lines through their BASE manager and deletes them with a raw
``DeleteQuery`` — so ``PurchaseOrderItemQuerySet.delete``'s batch never opened
and every line's ``post_delete`` re-derived the order on the spot. Measured on
the code before this change, deleting an order cost ``11 + 6N`` queries for N
lines (17 / 71 / 311 at N = 1 / 10 / 50), and all six per line were spent on
the order being deleted in the same call: a savepoint, a locking re-read of
the order, two re-reads of its (already deleted) lines, a write of the zero
total, and the savepoint release.

Three things are pinned here:

* the cost of an order delete does not grow with its line count, for the
  instance form and for the queryset form the admin's "Delete selected" uses;
* the order being deleted is not written on its way out;
* nothing that SURVIVES the delete ends up different from what the unbatched
  delete left behind. The reference is Django's own ``Model.delete`` /
  ``QuerySet.delete`` called past the override — literally the code path this
  change wrapped — run on an identical copy of the same data.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import pytest

from inventory.tests.factories import ItemSupplierFactory, SupplierFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.settlement_signals import DERIVED_ORDER_VALUES, settlement_batch
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def operator():
    return UserFactory()


def _order(operator, supplier=None, status=PurchaseOrder.Status.SENT):
    return PurchaseOrder.objects.create(
        supplier=supplier or SupplierFactory(),
        created_by=operator,
        status=status,
        order_date=timezone.now(),
    )


def _line(purchase_order, *, ordered=4, received=0, cost="3.25", **fields):
    return PurchaseOrderItem.objects.create(
        purchase_order=purchase_order,
        item_supplier=ItemSupplierFactory(supplier=purchase_order.supplier),
        quantity_ordered=ordered,
        quantity_received=received,
        unit_cost_ordered=Decimal(cost),
        **fields,
    )


def _order_with_lines(operator, count):
    purchase_order = _order(operator)
    for index in range(count):
        _line(purchase_order, received=index % 3, cost=f"{index + 1}.50")
    return purchase_order


def _queries(action) -> list[str]:
    with CaptureQueriesContext(connection) as captured:
        action()
    return [query["sql"] for query in captured.captured_queries]


# --------------------------------------------------------------------------
# cost
# --------------------------------------------------------------------------


class TestOrderDeleteCostDoesNotGrowPerLine:
    """The number that was filed: the delete's cost against its line count."""

    @pytest.mark.parametrize("line_count", [10, 50])
    def test_deleting_an_order_costs_what_a_one_line_order_does(
        self, operator, line_count, django_assert_num_queries
    ):
        one_line = _order_with_lines(operator, 1)
        many_lines = _order_with_lines(operator, line_count)

        cost_of_one = len(_queries(one_line.delete))

        with django_assert_num_queries(cost_of_one):
            many_lines.delete()

        assert not PurchaseOrderItem.objects.filter(purchase_order_id=many_lines.pk).exists()

    @pytest.mark.parametrize("line_count", [10, 50])
    def test_bulk_deleting_orders_costs_what_one_line_orders_do(
        self, operator, line_count, django_assert_num_queries
    ):
        """The queryset form — the admin changelist's "Delete selected"."""
        one_line = [_order_with_lines(operator, 1).pk for _ in range(2)]
        many_lines = [_order_with_lines(operator, line_count).pk for _ in range(2)]

        cost_of_one = len(_queries(PurchaseOrder.objects.filter(pk__in=one_line).delete))

        with django_assert_num_queries(cost_of_one):
            PurchaseOrder.objects.filter(pk__in=many_lines).delete()

        assert not PurchaseOrder.objects.filter(pk__in=many_lines).exists()

    def test_the_order_being_deleted_is_not_written_on_its_way_out(self, operator):
        """The per-line re-derivation was redundant, not merely repeated.

        Every mark a cascading line delete raised was for the order going in
        the same call. Coalescing to one write would still be a write to a row
        about to be deleted; the flush must find nothing to re-derive at all.
        """
        purchase_order = _order_with_lines(operator, 5)
        table = PurchaseOrder._meta.db_table

        writes = [
            sql for sql in _queries(purchase_order.delete) if sql.startswith(f'UPDATE "{table}"')
        ]

        assert writes == []


# --------------------------------------------------------------------------
# correctness against the unbatched delete
# --------------------------------------------------------------------------


def _unbatched_instance_delete(purchase_order):
    """Today's ``PurchaseOrder.delete``: Django's own, past the override."""
    return models.Model.delete(purchase_order)


def _unbatched_queryset_delete(queryset):
    """Today's ``PurchaseOrder.objects.filter(...).delete``, past the override."""
    return models.QuerySet.delete(queryset)


def _both_doomed(shop):
    return PurchaseOrder.objects.filter(pk__in=[shop["doomed"].pk, shop["also_doomed"].pk])


def _build_shop(operator):
    """Orders in every receipt state, keyed by role so two copies line up.

    ``doomed`` and ``also_doomed`` are the ones deleted. Everything else
    survives, and each survivor is shaped to catch a different way the batch
    could leak onto an order it should leave alone: one mid-receipt, one fully
    received, one a draft, and one whose stored total has been left STALE on
    purpose — a delete that re-derived a survivor would silently correct it.
    """
    supplier = SupplierFactory()
    shop = {}

    shop["doomed"] = _order(operator, supplier)
    _line(shop["doomed"], ordered=5, received=2, cost="9.99")
    _line(shop["doomed"], ordered=3, received=3, cost="1.25")
    _line(shop["doomed"], ordered=7, is_voided=True, cost="4.00")
    _line(shop["doomed"], ordered=2, received=1, closed_short_at=timezone.now())

    shop["also_doomed"] = _order(operator, supplier)
    _line(shop["also_doomed"], ordered=6, received=6, cost="2.00")

    shop["partial"] = _order(operator, supplier)
    _line(shop["partial"], ordered=10, received=4, cost="5.50")
    _line(shop["partial"], ordered=1, cost="100.00")

    shop["received"] = _order(operator, supplier)
    _line(shop["received"], ordered=2, received=2, cost="8.00")

    shop["draft"] = _order(operator, supplier, status=PurchaseOrder.Status.DRAFT)
    _line(shop["draft"], ordered=3, cost="7.00")

    shop["stale"] = _order(operator, supplier)
    _line(shop["stale"], ordered=4, received=1, cost="6.00")
    PurchaseOrder.objects.filter(pk=shop["stale"].pk).update(estimated_total=Decimal("999.99"))

    return shop


def _outcome(shop) -> dict:
    """Everything a delete could have moved on every order, read back fresh."""
    outcome = {}
    for role, stale_instance in shop.items():
        purchase_order = PurchaseOrder.objects.filter(pk=stale_instance.pk).first()
        if purchase_order is None:
            outcome[role] = "deleted"
            continue
        outcome[role] = {
            **{
                value.column: getattr(purchase_order, value.column)
                for value in DERIVED_ORDER_VALUES
            },
            "updated_at": purchase_order.updated_at,
            "effective_estimated_total": purchase_order.effective_estimated_total,
            "is_settled": purchase_order.is_settled,
            "total_received_quantity": purchase_order.total_received_quantity,
            "lines": sorted(
                (
                    line.quantity_ordered,
                    line.quantity_received,
                    line.is_settled,
                    line.estimated_cost,
                )
                for line in purchase_order.items.all()
            ),
        }
    return outcome


def _without_timestamps(outcome):
    return {
        role: state if state == "deleted" else {k: v for k, v in state.items() if k != "updated_at"}
        for role, state in outcome.items()
    }


class TestSurvivorsEndWhereTheUnbatchedDeleteLeftThem:
    """Same data, both deletes, the same answer for everything left standing."""

    @pytest.mark.parametrize(
        ("delete", "unbatched_delete"),
        [
            pytest.param(
                lambda shop: shop["doomed"].delete(),
                lambda shop: _unbatched_instance_delete(shop["doomed"]),
                id="instance",
            ),
            pytest.param(
                lambda shop: _both_doomed(shop).delete(),
                lambda shop: _unbatched_queryset_delete(_both_doomed(shop)),
                id="queryset",
            ),
        ],
    )
    def test_every_surviving_order_matches_the_unbatched_delete(
        self, operator, delete, unbatched_delete
    ):
        reference_shop = _build_shop(operator)
        reference_return = unbatched_delete(reference_shop)
        reference = _outcome(reference_shop)

        shop = _build_shop(operator)
        before = _outcome(shop)
        returned = delete(shop)
        after = _outcome(shop)

        assert returned == reference_return
        assert _without_timestamps(after) == _without_timestamps(reference)
        for role, state in after.items():
            if state != "deleted":
                assert (
                    state["updated_at"] == before[role]["updated_at"]
                ), f"deleting another order touched the surviving {role!r} order"

    def test_a_delete_inside_an_outer_batch_keeps_the_marks_queued_before_it(self, operator):
        """Nesting: the order delete must not flush, or drop, the caller's marks.

        A receipt on a surviving order is queued first, then an order is deleted
        inside the same block. The outer flush has to re-derive the survivor
        exactly as the unbatched delete inside the same block would have.
        """

        def scenario(delete_doomed):
            shop = _build_shop(operator)
            with settlement_batch():
                for line in shop["partial"].items.all():
                    line.quantity_received = line.quantity_ordered
                    line.save()
                delete_doomed(shop["doomed"])
            return shop

        before = _outcome(_build_shop(operator))
        reference = _outcome(scenario(_unbatched_instance_delete))
        outcome = _outcome(scenario(lambda order: order.delete()))

        # The queued receipt has to MOVE the survivor, or a dropped mark and a
        # flushed one would leave it in the same place and this proves nothing.
        assert before["partial"]["status"] == PurchaseOrder.Status.PARTIALLY_RECEIVED
        assert reference["partial"]["status"] == PurchaseOrder.Status.RECEIVED
        assert _without_timestamps(outcome) == _without_timestamps(reference)
