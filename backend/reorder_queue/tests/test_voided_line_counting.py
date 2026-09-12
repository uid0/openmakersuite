"""Every value derived from purchase history treats a voided line the same way.

The bug this pins was a DISAGREEMENT, not a wrong number: one response about one
item carried quantity metrics that excluded a struck-off purchase-order line and,
beside them, money that priced the item from it. ``last_po_unit_cost`` and
``cost_trend`` read the whole history, and so did ``default_unit_cost`` — the
price a scan-to-add suggests and ``add_line_item`` writes onto a new line. An
item whose newest line had been voided was quoted at a figure nobody agreed to
pay.

So the tests here are deliberately NOT one per field. A test per field is what
lets the next inconsistency in: it pins the field that was fixed and says nothing
about the one added next to it. These assert the CLASS instead —

    a voided line changes no derived value, and an item whose only line is
    voided reads exactly like an item that was never bought

— over a registry (:data:`DERIVATIONS`) that every value derived from
purchase-order lines is expected to join. Adding a derived value means adding one
row there, and the two tests below then hold it to the same rule as its siblings.

:func:`test_no_item_derivation_reads_purchase_lines_outside_the_rule` is the
other half: the registry is hand-written, so it cannot notice a derivation nobody
added to it. That test goes to the SOURCE and requires every query that groups or
filters purchase-order lines by their item to pass through
``PurchaseOrderItem.objects.standing()`` (or ``.outstanding()``, which subsumes
it), with its exemptions named in :data:`_COUNTS_VOIDED_ON_PURPOSE`.
"""

import ast
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

from django.conf import settings
from django.utils import timezone

import pytest

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


# --- the registry cannot see a derivation nobody added to it ----------------

#: Trees searched for item-derived reads of purchase-order lines.
_SEARCHED = ("inventory/services", "reorder_queue/services", "inventory", "reorder_queue")

#: Chain members that mean this is not a derived READ: a write stores what the
#: caller already decided, and ``select_for_update`` locks one known line.
_NOT_A_READ = frozenset(
    {"create", "get_or_create", "update_or_create", "bulk_create", "update", "select_for_update"}
)

#: ``item_supplier__isnull`` asks whether a line is an inventory line at all —
#: a shape test, not a question about a particular item's history.
_SHAPE_ONLY = frozenset({"item_supplier__isnull"})

#: ``(path, function)`` pairs that read purchase lines by item and count voided
#: ones ON PURPOSE. Each needs a reason, because each is a place the rule does
#: not reach.
_COUNTS_VOIDED_ON_PURPOSE = {
    # ``purchase_history`` SHOWS the item's order history rather than deriving a
    # number from it, and what it should show for a voided line is a separate,
    # open question (oms-voided-line-in-purchase-history). It is listed here so
    # that stays a decision somebody made rather than a site nobody looked at.
    ("inventory/views.py", "purchase_history"),
}


def _chain(node):
    """Unwind a call chain into ``(method names, filter keywords, root node)``."""
    attrs, keywords, current = [], [], node
    while True:
        if isinstance(current, ast.Call):
            keywords.extend(kw.arg for kw in current.keywords if kw.arg)
            current = current.func
        elif isinstance(current, ast.Attribute):
            attrs.append(current.attr)
            current = current.value
        else:
            return attrs, keywords, current


class _SiteVisitor(ast.NodeVisitor):
    """Records every item-derived ``PurchaseOrderItem.objects`` read in one module.

    A class rather than a closure so the enclosing-function stack and the
    module's path are attributes rather than variables captured from a loop.
    """

    def __init__(self, sites, relative_path):
        self.sites = sites
        self.relative_path = relative_path
        self.enclosing = []

    def visit_FunctionDef(self, node):
        self.enclosing.append(node.name)
        self.generic_visit(node)
        self.enclosing.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        attrs, keywords, chain_root = _chain(node)
        named = isinstance(chain_root, ast.Name) and chain_root.id == "PurchaseOrderItem"
        by_item = any(kw.startswith("item_supplier") and kw not in _SHAPE_ONLY for kw in keywords)
        if named and "objects" in attrs and by_item and _NOT_A_READ.isdisjoint(attrs):
            # Keyed on the CHAIN, by the line its ``PurchaseOrderItem`` root sits
            # on, and not on the enclosing function: one function routinely holds
            # several of these — the inventory metrics hold three — and a key any
            # coarser lets a compliant sibling vouch for an offender, which is
            # the exact defect this whole change is about. One chain still
            # contributes several nested ``Call`` nodes, so ``or`` folds them
            # together and the site counts as compliant if the derivation appears
            # anywhere in it.
            site = (self.relative_path, self.enclosing[-1] if self.enclosing else "<module>")
            key = site + (chain_root.lineno,)
            self.sites[key] = self.sites.get(key, False) or bool(
                {"standing", "outstanding"} & set(attrs)
            )
        self.generic_visit(node)


def _sites_reading_lines_by_item():
    """``{(path, function, line): goes_through_the_rule}`` for every such read.

    One entry per query CHAIN — see the comment in :class:`_SiteVisitor` — so two
    queries in one function are judged separately.
    """
    root = Path(settings.BASE_DIR)
    paths = sorted(
        {
            path
            for tree in _SEARCHED
            for path in (root / tree).glob("*.py")
            if "tests" not in path.parts and "migrations" not in path.parts
        }
    )
    sites = {}
    for path in paths:
        module = ast.parse(path.read_text())
        _SiteVisitor(sites, str(path.relative_to(root))).visit(module)
    return sites


def test_no_item_derivation_reads_purchase_lines_outside_the_rule():
    """A new derivation cannot quietly count struck-off lines.

    The registry above is hand-written and so is blind to a derivation nobody
    added. This reads the SOURCE instead: any query that filters or groups
    purchase-order lines by their inventory item is deriving something about that
    item, and must ask "does this line count?" through
    ``PurchaseOrderItem.objects.standing()`` — or ``.outstanding()``, which
    already excludes voided lines by way of ``q_settled``.

    Its limits are worth stating, because a guard that implies a completeness it
    does not have is worse than one that names its edges: it sees keyword filters
    on a chain rooted at ``PurchaseOrderItem.objects``, and therefore not raw SQL,
    not ``Q`` objects passed positionally, not a related manager
    (``item.purchase_order_items``), and not history pulled into memory and
    filtered in Python. Those are covered by the equality tests above for the
    derivations that exist today, not by this.
    """
    offenders = sorted(
        key
        for key, goes_through in _sites_reading_lines_by_item().items()
        if not goes_through and key[:2] not in _COUNTS_VOIDED_ON_PURPOSE
    )
    assert offenders == [], (
        "These read purchase-order lines by item without asking whether the line "
        "still stands. Route them through PurchaseOrderItem.objects.standing(), "
        "or add them to _COUNTS_VOIDED_ON_PURPOSE with a reason: "
        f"{offenders}"
    )


def test_the_exemptions_still_exist():
    """An exemption whose site has gone is a stale claim, not a safe one."""
    found = {key[:2] for key in _sites_reading_lines_by_item()}
    assert _COUNTS_VOIDED_ON_PURPOSE <= found, (
        "_COUNTS_VOIDED_ON_PURPOSE names sites the scan no longer finds: "
        f"{sorted(_COUNTS_VOIDED_ON_PURPOSE - found)}"
    )
