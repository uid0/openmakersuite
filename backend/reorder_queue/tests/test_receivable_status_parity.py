"""Every receivable-status gate agrees with ``PurchaseOrder.RECEIVABLE_STATUSES``.

``RECEIVABLE_STATUSES`` is the ONE definition of which orders can accept a
delivery. Three sites answered the same question from their own spelling of the
same three statuses instead of reading it:

* ``BarcodeReceiptSerializer.validate_purchase_order_id`` — the scan path;
* the ``pending_orders`` delivery worksheet;
* ``inventory.services.item_metrics.ON_ORDER_STATUSES`` — the QOO figure.

Nothing was wrong while the four spellings matched. The failure they set up is
the drift: add a status to the constant and the receive endpoints accept an
order the scan path refuses, the worksheet never lists, and QOO does not count
— with nothing anywhere reporting the contradiction. The operator at the
scanner, refused against a genuinely open order, would be the first to know.

The two arms answer different halves of that, in opposite directions.

:class:`TestGatesFollowTheConstant` is the drift guard. It drives EVERY member
of ``PurchaseOrder.Status`` — enumerated from the enum, never hand-listed —
through each gate and asserts the answer is exactly ``status in
RECEIVABLE_STATUSES``. Widen the constant and a gate that kept its own list
refuses the new status while the predicate accepts it, and that gate's test
fails. This is the arm that sees a divergence the day the constant moves.

:class:`TestNoSecondSpelling` catches the other direction: a FOURTH copy written
today, while the sets still agree, which the parity arm cannot see because it
would not yet disagree with anything. It parses the backend tree and fails on
any literal collection of ``Status`` members equal to the constant, outside the
definition itself.
"""

from __future__ import annotations

import ast
import pathlib
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from inventory.models import ItemSupplier
from inventory.services.item_metrics import compute_item_metrics
from inventory.tests.factories import InventoryItemFactory, SupplierFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.serializers import BarcodeReceiptSerializer

User = get_user_model()

#: Every status in the lifecycle, derived from the enum so a status added later
#: is driven through the gates without anyone remembering to list it here.
ALL_STATUSES = list(PurchaseOrder.Status)

ORDERED_QUANTITY = 7


def _expected(status_value):
    """The one answer every gate must give for ``status_value``."""
    return status_value in PurchaseOrder.RECEIVABLE_STATUSES


@pytest.fixture
def operator(db):
    return User.objects.create_user(
        username="receivable-parity-clerk", password="pw", is_staff=True, is_superuser=True
    )


@pytest.fixture
def client(operator):
    api = APIClient()
    api.force_authenticate(user=operator)
    return api


def _order_at(status_value, operator, *, upc="4006381333931"):
    """A one-line purchase order parked at ``status_value``, with its item.

    The status is written with ``update`` rather than ``save`` because adding a
    line re-derives the order's receipt status; the point here is to hold the
    order at each status, including ones the transitions would move it off.
    """
    supplier = SupplierFactory()
    item = InventoryItemFactory(current_stock=0, minimum_stock=0, image=None)
    ItemSupplier.objects.create(
        item=item,
        supplier=supplier,
        supplier_sku=f"SKU-{status_value}",
        package_upc=upc,
        unit_cost=Decimal("10.00"),
        quantity_per_package=1,
        is_primary=True,
    )
    purchase_order = PurchaseOrder.objects.create(
        supplier=supplier,
        status=PurchaseOrder.Status.DRAFT,
        order_date=timezone.now(),
        created_by=operator,
    )
    PurchaseOrderItem.objects.create(
        purchase_order=purchase_order,
        item_supplier=item.item_suppliers.first(),
        quantity_ordered=ORDERED_QUANTITY,
        unit_cost_ordered=Decimal("10.00"),
    )
    PurchaseOrder.objects.filter(pk=purchase_order.pk).update(status=status_value)
    purchase_order.refresh_from_db()
    assert purchase_order.status == status_value
    return purchase_order, item


class TestGatesFollowTheConstant:
    """Each gate's answer IS ``status in RECEIVABLE_STATUSES``, for every status."""

    @pytest.mark.django_db
    @pytest.mark.parametrize("status_value", ALL_STATUSES)
    def test_scan_path_accepts_exactly_the_receivable_statuses(self, status_value, operator):
        """The defect's own path: what the barcode scanner is allowed to receive against."""
        purchase_order, item = _order_at(status_value, operator)
        serializer = BarcodeReceiptSerializer(
            data={
                "purchase_order_id": purchase_order.pk,
                "scanned_upc": purchase_order.items.first().item_supplier.package_upc,
                "quantity_received": 1,
            }
        )
        accepted = serializer.is_valid()
        assert accepted is _expected(status_value), (
            f"scan path {'accepted' if accepted else 'refused'} a {status_value} order; "
            f"RECEIVABLE_STATUSES says {_expected(status_value)} — "
            f"errors={dict(serializer.errors)}"
        )
        if not accepted:
            assert "purchase_order_id" in serializer.errors

    @pytest.mark.django_db
    @pytest.mark.parametrize("status_value", ALL_STATUSES)
    def test_pending_orders_lists_exactly_the_receivable_statuses(
        self, status_value, operator, client
    ):
        """The delivery worksheet: an order the receive path accepts must be listed."""
        purchase_order, _ = _order_at(status_value, operator)
        response = client.get(reverse("orderdelivery-pending-orders"))
        assert response.status_code == 200, response.data
        listed = any(str(row["id"]) == str(purchase_order.pk) for row in response.data)
        assert listed is _expected(status_value), (
            f"pending_orders {'listed' if listed else 'omitted'} a {status_value} order; "
            f"RECEIVABLE_STATUSES says {_expected(status_value)}"
        )

    @pytest.mark.django_db
    @pytest.mark.parametrize("status_value", ALL_STATUSES)
    def test_quantity_on_order_counts_exactly_the_receivable_statuses(self, status_value, operator):
        """QOO: units committed on an order still in flight with the supplier."""
        _, item = _order_at(status_value, operator)
        on_order = compute_item_metrics(item)["quantity_on_order"]
        expected = ORDERED_QUANTITY if _expected(status_value) else 0
        assert on_order == expected, (
            f"quantity_on_order counted {on_order} units on a {status_value} order, "
            f"expected {expected} from RECEIVABLE_STATUSES"
        )


class TestNoSecondSpelling:
    """No backend site spells the receivable set out as a literal.

    The definition in ``models.py`` is the one exception, because it is the
    definition. Tests and migrations are out of scope: a test that enumerates
    statuses is describing a case, and a migration is a frozen historical record
    that must not follow a constant that moves.
    """

    BACKEND = pathlib.Path(__file__).resolve().parents[2]
    DEFINITION = ("reorder_queue/models.py", "RECEIVABLE_STATUSES")

    @staticmethod
    def _status_members(node):
        """The ``Status`` member names in a literal collection, or ``None``.

        ``None`` means "not a collection of status members" — one element that
        is anything else (a variable, a call, a string) disqualifies the whole
        node, because the set it denotes is then not readable from the source.
        """
        if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return None
        names = set()
        for element in node.elts:
            if not isinstance(element, ast.Attribute):
                return None
            owner = element.value
            if isinstance(owner, ast.Attribute) and owner.attr == "Status":
                names.add(element.attr)
            elif isinstance(owner, ast.Name) and owner.id == "Status":
                names.add(element.attr)
            else:
                return None
        return names or None

    def _sources(self):
        for path in sorted(self.BACKEND.rglob("*.py")):
            parts = path.relative_to(self.BACKEND).parts
            if "migrations" in parts or "__pycache__" in parts or "tests" in parts:
                continue
            yield path

    def test_backend_sweep_finds_the_definition(self):
        """The sweep can see the thing it is looking for.

        Without this the other test passes just as happily when the walk is
        broken and finds nothing at all anywhere.
        """
        relative, _ = self.DEFINITION
        definition = self.BACKEND / relative
        assert definition.exists(), definition
        assert definition in set(self._sources())
        expected = {member.name for member in PurchaseOrder.RECEIVABLE_STATUSES}
        found = [
            node.lineno
            for node in ast.walk(ast.parse(definition.read_text()))
            if self._status_members(node) == expected
        ]
        assert found, (
            "the sweep could not find RECEIVABLE_STATUSES' own literal in "
            f"{relative}, so it would report a clean tree however many copies exist"
        )

    def test_no_site_respells_the_receivable_set(self):
        expected = {member.name for member in PurchaseOrder.RECEIVABLE_STATUSES}
        offenders = []
        for path in self._sources():
            relative = str(path.relative_to(self.BACKEND))
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if self._status_members(node) != expected:
                    continue
                if relative == self.DEFINITION[0]:
                    continue
                offenders.append(f"{relative}:{node.lineno}")
        assert not offenders, (
            "these sites spell out PurchaseOrder.RECEIVABLE_STATUSES as a literal instead of "
            "reading it, so they will not follow it when it changes: "
            + ", ".join(offenders)
            + ". If a site genuinely answers a DIFFERENT question and only happens to need the "
            "same statuses today, say so in the source by deriving it — "
            "``RECEIVABLE_STATUSES | {...}`` and a comment naming the other question — rather "
            "than by leaving a copy that cannot be told apart from a stale one."
        )
