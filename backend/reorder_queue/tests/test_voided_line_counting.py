"""Every value derived from purchase history treats a voided line the same way.

The bug this pins was a DISAGREEMENT, not a wrong number: one response about one
item carried quantity metrics that excluded a struck-off purchase-order line and,
beside them, money that priced the item from it. ``last_po_unit_cost`` and
``cost_trend`` read the whole history, and so did ``default_unit_cost`` — the
price a scan-to-add suggests and ``add_line_item`` writes onto a new line. An
item whose newest line had been voided was quoted at a figure nobody agreed to
pay.

So the tests here assert the CLASS —

    a voided line changes no derived value, and an item whose only line is
    voided reads exactly like an item that was never bought

— over the values currently derived from purchase-order lines. The purchase
history endpoint is the deliberate opposite: it displays provenance rather than
deriving a current value, so its executable test proves that a voided line stays
visible there.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Callable, Optional

from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.reverse import reverse

from inventory.services.demand_forecast_engine import build_restock_events
from inventory.services.item_metrics import compute_item_metrics
from inventory.tests.factories import InventoryItemFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.services.line_entry import default_unit_cost
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

#: The price on the supplier link for the priced scenarios. Above
#: :data:`STANDING_COST` and below :data:`VOIDED_COST`, so ``cost_trend`` reads
#: ``"up"`` against the line that stands and would flip to ``"down"`` the moment
#: the struck-off one were counted — the disagreement is visible in the value,
#: not only in a number moving.
LINK_COST = Decimal("5.00")

#: What the item actually cost on the purchase that STANDS.
STANDING_COST = Decimal("3.0000")

#: What the struck-off line said it would cost. Nobody agreed to pay this.
VOIDED_COST = Decimal("9.0000")


def _item(*, link_unit_cost):
    """An inventory item whose primary supplier link is priced (or is not)."""
    return InventoryItemFactory(
        image=None,
        current_stock=0,
        reorder_quantity=1,
        unit_cost=link_unit_cost,
    )


def _line(item, *, po_status, order_days_ago, created_days_ago, **kwargs):
    """One purchase-order line for ``item``'s primary supplier, on its own order.

    ``order_date`` and ``created_at`` are both stamped explicitly: the restock
    cadence buckets by order DAY and the cost derivations order by ``created_at``,
    and ``auto_now_add`` ties every line built in one test to the same
    microsecond.
    """
    now = timezone.now()
    item_supplier = item.primary_item_supplier
    purchase_order = PurchaseOrder.objects.create(
        supplier=item_supplier.supplier,
        created_by=UserFactory(),
        status=po_status,
        order_date=now - timedelta(days=order_days_ago),
    )
    line = PurchaseOrderItem.objects.create(
        purchase_order=purchase_order,
        item_supplier=item_supplier,
        **kwargs,
    )
    PurchaseOrderItem.objects.filter(pk=line.pk).update(
        created_at=now - timedelta(days=created_days_ago)
    )
    return line


def _standing_line(item):
    """The purchase that happened: part-received, so it feeds QOO and QIT too."""
    return _line(
        item,
        po_status=PurchaseOrder.Status.PARTIALLY_RECEIVED,
        order_days_ago=30,
        created_days_ago=30,
        quantity_ordered=10,
        quantity_received=4,
        unit_cost_ordered=STANDING_COST,
    )


def _voided_line(item):
    """A struck-off line that is NEWER, dearer and larger than the one that stands.

    Newer so it wins every "most recent purchase" derivation, dearer so counting
    it moves a price and flips a trend, larger so counting it moves a quantity,
    and on its own order day so counting it invents a shopping trip. Every
    derivation in :data:`DERIVATIONS` therefore has something to get wrong.

    The order carries a second, standing line for an unrelated item so it keeps a
    live status once this one is struck off — a real order that lost a line, not
    an order that became empty. That line is for another item, so it contributes
    to nothing being asserted here.
    """
    line = _line(
        item,
        po_status=PurchaseOrder.Status.SENT,
        order_days_ago=5,
        created_days_ago=1,
        quantity_ordered=100,
        quantity_received=0,
        unit_cost_ordered=VOIDED_COST,
        is_voided=True,
    )
    other = InventoryItemFactory(image=None, current_stock=0, reorder_quantity=1)
    PurchaseOrderItem.objects.create(
        purchase_order=line.purchase_order,
        item_supplier=other.primary_item_supplier,
        quantity_ordered=1,
        unit_cost_ordered=Decimal("1.0000"),
    )
    return line


@dataclass(frozen=True)
class Derivation:
    """One value derived from an item's purchase-order lines.

    ``link_unit_cost`` is the price to put on the supplier link for this
    derivation's scenario, because the link price decides whether purchase
    history is consulted at all: ``default_unit_cost`` returns the link's own
    price when it has one and only falls through to history when it does not,
    while ``cost_trend`` has nothing to compare against unless the link is
    priced. Passing the wrong one here makes the test pass for the wrong reason.
    """

    name: str
    link_unit_cost: Optional[Decimal]
    read: Callable[[object], object]


def _metric(field):
    return lambda item: compute_item_metrics(item)[field]


#: Every value derived from purchase-order lines about an ITEM. Add a row when
#: you add a derivation; the tests below then hold it to the same rule as the
#: rest. The money entries are the ones that used to disagree with the rest.
DERIVATIONS = [
    Derivation("quantity_on_order", LINK_COST, _metric("quantity_on_order")),
    Derivation("quantity_in_transit", LINK_COST, _metric("quantity_in_transit")),
    Derivation("last_po_unit_cost", LINK_COST, _metric("last_po_unit_cost")),
    Derivation("cost_trend", LINK_COST, _metric("cost_trend")),
    Derivation(
        "default_unit_cost",
        None,
        lambda item: default_unit_cost(item.primary_item_supplier),
    ),
    Derivation(
        "restock_events",
        LINK_COST,
        lambda item: build_restock_events(item, end=timezone.now().date()),
    ),
]

_by_name = pytest.mark.parametrize("derivation", DERIVATIONS, ids=lambda d: d.name)


@_by_name
def test_a_voided_line_changes_no_derived_value(derivation):
    """Striking a line off leaves every derived value exactly where it was.

    The comparison is against a CONTROL item built the same way minus the voided
    line, rather than against a literal: what is being asserted is that the two
    items are indistinguishable, which is the property, and it does not have to
    be restated as an expected number for each new derivation.
    """
    control = _item(link_unit_cost=derivation.link_unit_cost)
    _standing_line(control)

    subject = _item(link_unit_cost=derivation.link_unit_cost)
    _standing_line(subject)
    _voided_line(subject)

    assert derivation.read(subject) == derivation.read(control)


@_by_name
def test_an_item_whose_only_line_was_voided_reads_as_never_bought(derivation):
    """No history at all is what a struck-off purchase leaves behind.

    The edge the equality above cannot reach: with a standing line to fall back
    on, a derivation that wrongly counted the voided one still answers with *a*
    price. With nothing else on file it has to answer "unknown" — and for
    ``default_unit_cost`` that ``None`` is what makes ``add_line_item`` refuse
    rather than write a struck-off figure onto a new line.
    """
    never_bought = _item(link_unit_cost=derivation.link_unit_cost)

    subject = _item(link_unit_cost=derivation.link_unit_cost)
    _voided_line(subject)

    assert derivation.read(subject) == derivation.read(never_bought)


def test_purchase_history_keeps_a_voided_line_visible(authenticated_client):
    """Provenance displays a struck-off line even though derivations exclude it."""
    client, _user = authenticated_client
    item = _item(link_unit_cost=LINK_COST)
    line = _voided_line(item)

    response = client.get(reverse("inventoryitem-purchase-history", kwargs={"pk": str(item.id)}))

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["order_costs"] == [
        {
            "purchase_order": line.purchase_order_id,
            "po_number": line.purchase_order.po_number,
            "order_date": response.json()["order_costs"][0]["order_date"],
            "status": PurchaseOrder.Status.SENT,
            "quantity_ordered": 100,
            "unit_cost_ordered": "9.0000",
            "unit_cost_actual": None,
        }
    ]
