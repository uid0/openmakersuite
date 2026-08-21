"""Resolve a typed/scanned identifier to a PO line, and add it (oms-po-add-item).

An operator working a **draft** purchase order wants to say "add this thing"
without first knowing which ``ItemSupplier`` row backs it. They identify the
item however it is in front of them — the item's name, the item's own SKU, the
barcode on the box or on the unit, or the vendor's part number off the
catalogue page — and the order's supplier decides whether that is even a legal
line.

Two operations live here:

* :func:`lookup_candidates` — pure read. Resolves one identifier *in the
  context of one purchase order's supplier*.
* :func:`add_line_item` — the write. Owns the draft-only and
  supplier-supplies-it guards, the defaults, and the already-on-the-order rule.

**The supplier is the validation boundary.** ``ItemSupplier`` *is* the "this
supplier supplies this item" relationship, so scoping every lookup to
``purchase_order.supplier`` means the check cannot be forgotten: a row that is
not in that scope simply is not a candidate. :func:`add_line_item` re-checks it
even when the caller names an ``ItemSupplier`` directly, because a non-browser
client (ScanTTY) can post any id it likes.

**Match tiers and ambiguity.** One string can name more than one thing — an
exact vendor SKU for one item is a substring of another item's name. Candidates
are therefore grouped into ordered tiers (see :data:`MATCH_TIERS`) and the
caller is told the tier of every match. The add path resolves an identifier
**only when the strongest tier that matched holds exactly one candidate**;
anything else comes back as an ambiguity with the full candidate set so the
operator picks, rather than the server guessing. Exact identifiers therefore
still resolve in one shot even when a partial name match rides alongside them,
which is what keeps the scan-and-Enter path a single round trip.

**Discontinued links are not candidates.** Voiding a line marks the
``ItemSupplier`` discontinued/inactive (``void_line_item``), which is the
system's way of recording "this vendor stopped carrying it". Such a row is
reported as *unavailable* with its own reason rather than silently missing, so
the operator gets told why instead of "no match".

**Already on the order → the line grows by one package.** A second line is
impossible (``(purchase_order, item_supplier)`` is unique) and scanning the same
box twice plainly means "two of those", so a repeat add *increments* the line
already there. Without an explicit quantity the increment is one package
(:func:`repeat_quantity`) — the unit the operator physically picked up — not the
full reorder suggestion a fresh line lands on, so an accidental re-scan costs
one package rather than doubling the order. What counts as a package is
:func:`~reorder_queue.services.purchase_orders.order_package_size`, the same
ladder ``order_in_packages`` is derived through, so the grown line's quantity
and package count always describe the same order. An explicit quantity is
honoured verbatim on either path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from django.db import IntegrityError, transaction
from django.db.models import Q

from inventory.models import InventoryItem, ItemSupplier
from inventory.services.kits import build_kit_snapshot
from inventory.services.packaging import base_reorder_quantity, counts_in_packs

from ..models import PurchaseOrder, PurchaseOrderItem
from .purchase_orders import (
    order_package_size,
    order_packages_for_line,
    recalculate_estimated_total,
)

# Match tiers, strongest first. The three barcode/vendor identifiers come ahead
# of the item's own fields because they are what the *supplier* calls the thing,
# and the scanner path only ever produces those. ``partial_*`` tiers are the
# typed-a-few-letters fallback and are deliberately the weakest, so a typed
# string that exactly matches one identifier is never dragged into an ambiguity
# by an unrelated item whose name happens to contain it.
MATCH_UNIT_BARCODE = "unit_barcode"
MATCH_PACKAGE_BARCODE = "package_barcode"
MATCH_VENDOR_SKU = "vendor_sku"
MATCH_ITEM_SKU = "item_sku"
MATCH_ITEM_NAME = "item_name"
MATCH_PARTIAL_VENDOR_SKU = "partial_vendor_sku"
MATCH_PARTIAL_ITEM_SKU = "partial_item_sku"
MATCH_PARTIAL_ITEM_NAME = "partial_item_name"

MATCH_TIERS = (
    MATCH_UNIT_BARCODE,
    MATCH_PACKAGE_BARCODE,
    MATCH_VENDOR_SKU,
    MATCH_ITEM_SKU,
    MATCH_ITEM_NAME,
    MATCH_PARTIAL_VENDOR_SKU,
    MATCH_PARTIAL_ITEM_SKU,
    MATCH_PARTIAL_ITEM_NAME,
)

# Human labels for the tiers above — used in the "what matched" block the API
# hands back so the operator can see *why* a candidate came up.
MATCH_TIER_LABELS = {
    MATCH_UNIT_BARCODE: "unit barcode",
    MATCH_PACKAGE_BARCODE: "package barcode",
    MATCH_VENDOR_SKU: "supplier SKU",
    MATCH_ITEM_SKU: "item SKU",
    MATCH_ITEM_NAME: "item name",
    MATCH_PARTIAL_VENDOR_SKU: "supplier SKU (partial)",
    MATCH_PARTIAL_ITEM_SKU: "item SKU (partial)",
    MATCH_PARTIAL_ITEM_NAME: "item name (partial)",
}

_EXACT_TIERS = frozenset(
    {
        MATCH_UNIT_BARCODE,
        MATCH_PACKAGE_BARCODE,
        MATCH_VENDOR_SKU,
        MATCH_ITEM_SKU,
        MATCH_ITEM_NAME,
    }
)

# Reasons an identifier resolved to a real item that still cannot be ordered on
# THIS purchase order. Each one names the item and the supplier in its message.
UNAVAILABLE_NOT_SUPPLIED = "not_supplied"
UNAVAILABLE_DISCONTINUED = "discontinued"

DEFAULT_CANDIDATE_LIMIT = 20


class LineEntryError(Exception):
    """A line could not be added, with an operator-facing reason.

    ``code`` lets a non-browser client branch without parsing prose;
    ``candidates`` carries the choice set for the ambiguous case.

    Every argument goes to ``super().__init__`` so the exception survives
    ``copy``/``pickle``; ``str(exc)`` is therefore the tuple — read
    :attr:`message` for the operator-facing text.
    """

    def __init__(self, message: str, code: str, candidates: Optional[List[dict]] = None):
        super().__init__(message, code, candidates)
        self.message = message
        self.code = code
        self.candidates = candidates or []


@dataclass
class Candidate:
    """One orderable ``ItemSupplier`` this supplier carries, and why it matched."""

    item_supplier: ItemSupplier
    match_kind: str
    matched_value: str
    existing_line: Optional[PurchaseOrderItem] = None


@dataclass
class Unavailable:
    """An item the identifier named that this order still cannot carry."""

    item: InventoryItem
    reason: str
    message: str


@dataclass
class LookupResult:
    candidates: List[Candidate] = field(default_factory=list)
    unavailable: List[Unavailable] = field(default_factory=list)
    #: How many candidates matched BEFORE ``limit`` capped the list, in total
    #: and within :attr:`best_tier`. A capped list must never be presented as
    #: the whole story: the operator is told the real count and that there is
    #: more, rather than being quietly handed the cap as if it were the total.
    total_candidates: int = 0
    best_tier_total: int = 0
    truncated: bool = False
    #: The same accounting for :attr:`unavailable`, which is capped by the same
    #: ``limit`` on the nothing-this-supplier-carries path and would otherwise
    #: hand back 20 explanations for 50 matching items as if that were all of
    #: them. Discontinued rows are never capped, so they always count in full.
    total_unavailable: int = 0
    unavailable_truncated: bool = False

    @property
    def best_tier(self) -> Optional[str]:
        """Strongest tier that actually matched, or ``None`` for no candidates."""
        for tier in MATCH_TIERS:
            if any(candidate.match_kind == tier for candidate in self.candidates):
                return tier
        return None

    def best_tier_candidates(self) -> List[Candidate]:
        """Candidates at :attr:`best_tier` — the set the add path resolves over."""
        tier = self.best_tier
        if tier is None:
            return []
        return [candidate for candidate in self.candidates if candidate.match_kind == tier]


def _matched_value(item_supplier: ItemSupplier, kind: str) -> str:
    if kind in (MATCH_UNIT_BARCODE,):
        return item_supplier.unit_upc
    if kind in (MATCH_PACKAGE_BARCODE,):
        return item_supplier.package_upc
    if kind in (MATCH_VENDOR_SKU, MATCH_PARTIAL_VENDOR_SKU):
        return item_supplier.supplier_sku
    if kind in (MATCH_ITEM_SKU, MATCH_PARTIAL_ITEM_SKU):
        return item_supplier.item.sku
    return item_supplier.item.name


def _classify(item_supplier: ItemSupplier, query: str) -> Optional[str]:
    """Strongest tier ``query`` matches this row at, or ``None``.

    Case-insensitive throughout: an operator typing a vendor SKU rarely matches
    the catalogue's capitalisation, and a scanner emits exactly what is printed.
    """
    needle = query.casefold()
    item = item_supplier.item

    def eq(value: Optional[str]) -> bool:
        return bool(value) and value.casefold() == needle

    def contains(value: Optional[str]) -> bool:
        return bool(value) and needle in value.casefold()

    if eq(item_supplier.unit_upc):
        return MATCH_UNIT_BARCODE
    if eq(item_supplier.package_upc):
        return MATCH_PACKAGE_BARCODE
    if eq(item_supplier.supplier_sku):
        return MATCH_VENDOR_SKU
    if eq(item.sku):
        return MATCH_ITEM_SKU
    if eq(item.name):
        return MATCH_ITEM_NAME
    if contains(item_supplier.supplier_sku):
        return MATCH_PARTIAL_VENDOR_SKU
    if contains(item.sku):
        return MATCH_PARTIAL_ITEM_SKU
    if contains(item.name):
        return MATCH_PARTIAL_ITEM_NAME
    return None


def lookup_candidates(purchase_order, query, limit=DEFAULT_CANDIDATE_LIMIT):
    """Resolve ``query`` against what this order's supplier carries.

    Returns a :class:`LookupResult`. ``candidates`` are orderable rows sorted
    strongest tier first (and by item name within a tier, so a partial-name
    choice list reads alphabetically); ``unavailable`` explains items the
    identifier really does name but that this order cannot carry — the supplier
    supplies them no longer, or (only when nothing else matched, since that is
    the case where the operator is owed an explanation rather than a list) does
    not supply them at all.

    A blank query resolves to nothing rather than to everything: an empty scan
    is a mis-scan, not a request for the whole catalogue.
    """
    query = (query or "").strip()
    result = LookupResult()
    if not query:
        return result

    supplier = purchase_order.supplier
    # One query for the whole tier ladder: the OR below is the union of every
    # tier's predicate, and _classify picks the strongest per row afterwards.
    # Partial predicates subsume their exact twins, so unit/package UPC are the
    # only ones needing an explicit equality arm.
    matches = (
        ItemSupplier.objects.filter(
            Q(unit_upc__iexact=query)
            | Q(package_upc__iexact=query)
            | Q(supplier_sku__icontains=query)
            | Q(item__sku__icontains=query)
            | Q(item__name__icontains=query),
            supplier=supplier,
        )
        .select_related("item", "item__count_level", "supplier")
        .order_by("item__name")
    )

    existing_lines = {
        line.item_supplier_id: line
        for line in PurchaseOrderItem.objects.filter(
            purchase_order=purchase_order, item_supplier__isnull=False
        ).select_related("item_supplier__item")
    }

    for item_supplier in matches:
        kind = _classify(item_supplier, query)
        if kind is None:  # pragma: no cover - the SQL filter already implies one
            continue
        if item_supplier.is_discontinued or not item_supplier.is_active:
            result.unavailable.append(
                Unavailable(
                    item=item_supplier.item,
                    reason=UNAVAILABLE_DISCONTINUED,
                    message=(
                        f"{supplier.name} no longer supplies "
                        f"{item_supplier.item.name} (marked discontinued)."
                    ),
                )
            )
            continue
        result.candidates.append(
            Candidate(
                item_supplier=item_supplier,
                match_kind=kind,
                matched_value=_matched_value(item_supplier, kind),
                existing_line=existing_lines.get(item_supplier.pk),
            )
        )

    result.candidates.sort(
        key=lambda candidate: (
            MATCH_TIERS.index(candidate.match_kind),
            candidate.item_supplier.item.name.casefold(),
        )
    )
    # Counted before the cap, so "matches N items" is the number of matches and
    # not the number we happened to keep. Sorting put the strongest tier at the
    # head, so capping never changes which tier is best — only how much of it
    # the caller sees.
    result.total_candidates = len(result.candidates)
    best_tier = result.best_tier
    result.best_tier_total = sum(
        1 for candidate in result.candidates if candidate.match_kind == best_tier
    )
    if limit is not None and len(result.candidates) > limit:
        result.candidates = result.candidates[:limit]
        result.truncated = True

    # Nothing this supplier carries matched — say who *does* carry it, which is
    # the difference between "no such item" and "wrong supplier for this order".
    discontinued = len(result.unavailable)
    elsewhere, elsewhere_total = (
        _items_supplied_elsewhere(supplier, query, limit) if not result.candidates else ([], 0)
    )
    result.unavailable.extend(elsewhere)
    result.total_unavailable = discontinued + elsewhere_total
    result.unavailable_truncated = elsewhere_total > len(elsewhere)

    return result


def _items_supplied_elsewhere(supplier, query, limit):
    """Items the identifier names that this supplier does not supply at all.

    Searched over ``InventoryItem`` plus every *other* supplier's identifiers,
    so scanning a competitor's barcode or typing a rival part number produces
    "Acme does not supply M3 hex bolt" rather than a bare miss.

    Returns ``(entries, total)`` — the capped explanations and how many items
    actually matched — so a caller can never present a shortened list as the
    whole answer.
    """
    matching = (
        InventoryItem.objects.filter(
            Q(sku__icontains=query)
            | Q(name__icontains=query)
            | Q(item_suppliers__supplier_sku__icontains=query)
            | Q(item_suppliers__unit_upc__iexact=query)
            | Q(item_suppliers__package_upc__iexact=query)
        )
        .exclude(item_suppliers__supplier=supplier)
        .distinct()
    )
    total = matching.count()
    items = matching.order_by("name")[: limit or DEFAULT_CANDIDATE_LIMIT]
    entries = [
        Unavailable(
            item=item,
            reason=UNAVAILABLE_NOT_SUPPLIED,
            message=(
                f"{supplier.name} does not supply {item.name}. "
                f"Add {supplier.name} as a supplier for that item, or order it "
                "on a purchase order for a supplier that carries it."
            ),
        )
        for item in items
    ]
    return entries, total


def default_quantity(item_supplier):
    """Quantity a freshly added line should land on, in BASE units.

    Same derivation ``create_optimized_order`` uses to fill a pad — the item's
    own reorder maths (:func:`base_reorder_quantity`), rounded up to a whole
    supplier package for the ``each`` items where the vendor's case size is the
    binding constraint. Never zero: an item whose maths produces nothing still
    gets one unit, because an operator who asked to add a line meant to buy
    something.
    """
    item = item_supplier.item
    quantity = base_reorder_quantity(item)

    if not counts_in_packs(item):
        case_size = item_supplier.quantity_per_package or 1
        if case_size > 1:
            quantity = -(-quantity // case_size) * case_size

    return max(1, quantity)


def repeat_quantity(item_supplier):
    """Quantity a REPEAT add grows an existing line by, in BASE units.

    One package — the unit the operator physically picked up and scanned. Not
    :func:`default_quantity`: that is the whole reorder suggestion, and
    re-running it would double an order the moment a box got scanned twice,
    which is the one mistake this path invites.

    "One package" is resolved by :func:`order_package_size`, the same ladder
    ``order_in_packages`` is derived through, and deliberately not by reading
    ``ItemSupplier.quantity_per_package`` directly. That column DEFAULTS to 1,
    so it cannot tell "this vendor sells singles" from "nobody filled in the
    case size": taken literally it would add a single loose unit to a
    case-counted item whose supplier simply never declared a case, and the line
    would then record more packages than its quantity actually represents —
    an order pad asking the vendor for two cases while costing and receiving
    work off one case plus one unit. Going through the shared ladder still
    degrades to +1 for a genuine single.
    """
    return max(1, order_package_size(item_supplier))


def default_unit_cost(item_supplier):
    """Unit cost a freshly added line should land on.

    The supplier relationship's own ``unit_cost`` first — that is the price this
    vendor quotes and what ``create_purchase_order`` writes. When the
    relationship carries no price, fall back to what this item last actually
    cost on a purchase order from this supplier, mirroring the
    ``last_po_unit_cost`` derivation in ``inventory.services.item_metrics``
    (newest ``PurchaseOrderItem`` first). Only a brand-new relationship with no
    price and no purchase history lands at zero.
    """
    if item_supplier.unit_cost is not None:
        return Decimal(item_supplier.unit_cost)

    last = (
        PurchaseOrderItem.objects.filter(item_supplier=item_supplier)
        .order_by("-created_at")
        .values_list("unit_cost_actual", "unit_cost_ordered")
        .first()
    )
    if last is not None:
        actual, ordered = last
        if actual is not None:
            return Decimal(actual)
        if ordered is not None:
            return Decimal(ordered)

    return Decimal("0.00")


def _coerce_quantity(quantity):
    try:
        value = int(str(quantity))
    except (TypeError, ValueError):
        raise LineEntryError(
            f"Quantity must be a whole number, got {quantity!r}.", "invalid_quantity"
        )
    if value < 1:
        raise LineEntryError("Quantity must be at least 1.", "invalid_quantity")
    return value


def _coerce_unit_cost(unit_cost):
    try:
        value = Decimal(str(unit_cost))
    except (InvalidOperation, ValueError, TypeError):
        raise LineEntryError(f"Unit cost must be numeric, got {unit_cost!r}.", "invalid_unit_cost")
    if value < 0:
        raise LineEntryError("Unit cost cannot be negative.", "invalid_unit_cost")
    return value


def assert_addable(purchase_order):
    """Guard: lines may only be added while the order is still a draft.

    Once an order has gone to the supplier, what it contains is a matter of
    record — growing it is a new order, not an edit. Raised server-side so a
    non-browser client cannot skip it.
    """
    if purchase_order.status != PurchaseOrder.Status.DRAFT:
        label = PurchaseOrder.Status(purchase_order.status).label
        raise LineEntryError(
            f"Line items can only be added while a purchase order is a draft. "
            f"{purchase_order.po_number or 'This order'} is {label}.",
            "not_draft",
        )


def resolve_item_supplier(purchase_order, item_supplier_id):
    """Load an explicitly named ``ItemSupplier`` and prove this supplier carries it.

    The supplier check is repeated here — not only in :func:`lookup_candidates`
    — because a client may post an id it obtained anywhere. A row belonging to
    another vendor gets the item-and-supplier message, not a bare 404.
    """
    try:
        item_supplier = ItemSupplier.objects.select_related(
            "item", "item__count_level", "supplier"
        ).get(pk=item_supplier_id)
    except (ItemSupplier.DoesNotExist, ValueError, TypeError):
        raise LineEntryError(
            f"No supplier catalogue entry with id {item_supplier_id!r} exists.",
            "not_found",
        )

    supplier = purchase_order.supplier
    if item_supplier.supplier_id != supplier.pk:
        raise LineEntryError(
            f"{supplier.name} does not supply {item_supplier.item.name} — that item "
            f"is supplied by {item_supplier.supplier.name}. Add {supplier.name} as a "
            f"supplier for {item_supplier.item.name}, or order it on a purchase "
            f"order for {item_supplier.supplier.name}.",
            "supplier_mismatch",
        )
    if item_supplier.is_discontinued or not item_supplier.is_active:
        raise LineEntryError(
            f"{supplier.name} no longer supplies {item_supplier.item.name} "
            "(marked discontinued).",
            "discontinued",
        )
    return item_supplier


def resolve_identifier(purchase_order, identifier):
    """Resolve a typed/scanned identifier to exactly one candidate, or explain why not.

    Resolves when the strongest tier that matched holds a single candidate —
    an exact barcode or vendor SKU therefore still resolves in one shot even
    when weaker partial-name matches came back alongside it. Two candidates in
    the same tier raise ``ambiguous`` carrying the whole choice set; the
    operator picks and re-posts by ``item_supplier``.

    The count in the ambiguity message is the number of matches, not the number
    of candidates that survived :data:`DEFAULT_CANDIDATE_LIMIT`, and the message
    says so when the choice set attached to it is only part of them — being told
    "20" when 63 matched would send the operator hunting for an item that was
    never in the list.
    """
    identifier = (identifier or "").strip()
    if not identifier:
        raise LineEntryError("Enter or scan an item to add.", "empty_identifier")

    result = lookup_candidates(purchase_order, identifier)
    best = result.best_tier_candidates()

    if len(best) == 1:
        return best[0]

    if not best:
        if result.unavailable:
            first = result.unavailable[0]
            raise LineEntryError(first.message, first.reason)
        raise LineEntryError(
            f'Nothing matching "{identifier}" is supplied by ' f"{purchase_order.supplier.name}.",
            "no_match",
        )

    total = max(result.best_tier_total, len(best))
    shown = (
        ""
        if total == len(best)
        else f" The first {len(best)} are offered here — narrow the search to see the rest."
    )
    raise LineEntryError(
        f'"{identifier}" matches {total} items {purchase_order.supplier.name} '
        f"supplies. Choose which one to add.{shown}",
        "ambiguous",
        candidates=[serialize_candidate(candidate) for candidate in best],
    )


def _locked_existing_line(purchase_order, item_supplier):
    """This order's line for ``item_supplier``, locked for the rest of the transaction.

    ``select_for_update`` for the same reason ``mark_received`` takes it: the
    read decides whether the next statement grows a line or inserts one, and two
    adds in flight at once (two operators on one order, a re-posted scan from a
    non-browser client) would otherwise both read the old quantity and one of
    the two writes would be lost.

    ``order_by()`` clears the model's default ordering, which joins the nullable
    ``item_supplier``/``asset`` sides — Postgres refuses ``FOR UPDATE`` over an
    outer join.
    """
    return (
        PurchaseOrderItem.objects.select_for_update()
        .filter(purchase_order=purchase_order, item_supplier=item_supplier)
        .order_by()
        .first()
    )


def _tag_label(tagged):
    """Operator-facing name for a work order or committee, for conflict messages."""
    return getattr(tagged, "name", None) or str(tagged)


def _apply_tag(existing, attr, supplied, noun, item_name):
    """Set a line-level ``work_order``/``owning_group`` on a line being grown.

    Untagged line → the supplied tag lands on it. Same tag → nothing to do.
    *Different* tag → refused, naming both, because either silent outcome is
    wrong: dropping the tag reports success for a request only half applied,
    and overwriting silently moves an existing line's attribution to another
    job. Each field is decided on its own current value.

    The refusal offers only remedies that exist. "Put it on a second line for
    the other job" is not one of them: ``(purchase_order, item_supplier)`` is
    unique, so this order has exactly one line for this item and no endpoint can
    make another.
    """
    if supplied is None:
        return
    current = getattr(existing, attr)
    if current is None:
        setattr(existing, attr, supplied)
        return
    if current.pk != supplied.pk:
        raise LineEntryError(
            f"{item_name} is already on this order for {noun} "
            f"{_tag_label(current)}; this request names {noun} "
            f"{_tag_label(supplied)}. Clear this line's {noun} first, or order "
            f"it on a separate purchase order for {noun} "
            f"{_tag_label(supplied)}.",
            f"{attr}_conflict",
        )


def _grow_existing_line(
    purchase_order,
    item_supplier,
    existing,
    *,
    quantity,
    explicit_cost,
    notes,
    work_order,
    owning_group,
):
    """Increment a line already on the order — see :func:`add_line_item`."""
    if existing.is_voided:
        raise LineEntryError(
            f"{item_supplier.item.name} is already on "
            f"{purchase_order.po_number or 'this order'} as a voided line. "
            "Restore or remove that line before ordering it again.",
            "line_voided",
        )

    # Before any mutation, so a refused tag leaves the line exactly as it was.
    item_name = item_supplier.item.name
    _apply_tag(existing, "work_order", work_order, "work order", item_name)
    _apply_tag(existing, "owning_group", owning_group, "committee", item_name)

    grow_by = repeat_quantity(item_supplier) if quantity is None else quantity
    existing.quantity_ordered = (existing.quantity_ordered or 0) + grow_by
    existing.order_in_packages = order_packages_for_line(item_supplier, existing.quantity_ordered)
    if explicit_cost is not None:
        existing.unit_cost_ordered = explicit_cost
    if notes:
        existing.notes = f"{existing.notes}\n{notes}".strip() if existing.notes else notes
    existing.save()
    recalculate_estimated_total(purchase_order)
    return existing, False


@transaction.atomic
def add_line_item(
    purchase_order,
    item_supplier,
    *,
    quantity=None,
    unit_cost=None,
    notes="",
    work_order=None,
    owning_group=None,
):
    """Add ``item_supplier`` to ``purchase_order`` as a line, or grow its line.

    Returns ``(line_item, created)``.

    **Already on the order → the existing line grows.** ``PurchaseOrderItem``
    carries a ``(purchase_order, item_supplier)`` unique constraint, so a second
    line is not merely undesirable, it is impossible; and scanning the same box
    twice plainly means "two of those". An explicit ``quantity`` is added
    verbatim; without one the line grows by **one package**
    (:func:`repeat_quantity`, resolved through the same
    :func:`~reorder_queue.services.purchase_orders.order_package_size` ladder
    that derives ``order_in_packages``), which is what the operator just picked
    up — not the full reorder suggestion :func:`default_quantity` gives a fresh
    line, so a stray second scan costs one package rather than doubling the
    order, and never leaves the line recording more packages than its quantity
    represents. The
    line's cost is left alone unless an explicit ``unit_cost`` came with the
    request. A **voided** existing line is refused instead: the constraint
    blocks a replacement line, and quietly resurrecting something an operator
    deliberately struck off the order would be worse than saying so.

    **Tags on a line being grown.** ``work_order`` and ``owning_group`` are
    applied to the existing line when it carries none for that field, and the
    request is *refused* (``work_order_conflict`` / ``owning_group_conflict``,
    naming both values) when the line already carries a different one. Neither
    silent outcome is acceptable: dropping the tag would report success for a
    request only half applied, and overwriting would move an existing line's
    attribution to another job behind the operator's back. Each field is judged
    independently on its own current value. ``notes`` are appended, as always.

    Defaults for a *new* line come from the supplier relationship and purchase
    history — see :func:`default_quantity` / :func:`default_unit_cost` — so a
    line never lands at zero just because the operator only scanned a barcode.

    The caller owns the draft guard (:func:`assert_addable`) and the audit
    event; both are applied at the view boundary alongside the other PO
    actions.
    """
    explicit_quantity = None if quantity is None else _coerce_quantity(quantity)
    explicit_cost = None if unit_cost is None else _coerce_unit_cost(unit_cost)
    grow_kwargs = {
        "quantity": explicit_quantity,
        "explicit_cost": explicit_cost,
        "notes": notes,
        "work_order": work_order,
        "owning_group": owning_group,
    }

    existing = _locked_existing_line(purchase_order, item_supplier)
    if existing is not None:
        return _grow_existing_line(purchase_order, item_supplier, existing, **grow_kwargs)

    new_quantity = (
        default_quantity(item_supplier) if explicit_quantity is None else explicit_quantity
    )
    try:
        # Nested so a losing race rolls back only the failed INSERT: an
        # IntegrityError would otherwise poison the whole transaction and there
        # would be nothing left to grow.
        with transaction.atomic():
            line_item = PurchaseOrderItem.objects.create(
                purchase_order=purchase_order,
                item_supplier=item_supplier,
                quantity_ordered=new_quantity,
                unit_cost_ordered=(
                    explicit_cost if explicit_cost is not None else default_unit_cost(item_supplier)
                ),
                order_in_packages=order_packages_for_line(item_supplier, new_quantity),
                notes=notes or "",
                work_order=work_order,
                owning_group=owning_group,
                # Same reason create_purchase_order freezes it: the BOM is
                # editable and receipt is weeks away, so the line has to carry
                # what it bought.
                kit_snapshot=build_kit_snapshot(item_supplier.item),
            )
    except IntegrityError:
        # A concurrent add won the (purchase_order, item_supplier) constraint
        # between our locked read and this insert. Its row is committed by the
        # time the constraint fires, so re-read and take the documented
        # grow-the-line path rather than surfacing a 500.
        existing = _locked_existing_line(purchase_order, item_supplier)
        if existing is None:
            raise
        return _grow_existing_line(purchase_order, item_supplier, existing, **grow_kwargs)

    recalculate_estimated_total(purchase_order)
    return line_item, True


def serialize_candidate(candidate):
    """API shape for one candidate — enough to render a choice row and re-post it.

    ``suggested_*`` are what adding this candidate would land on, so a client can
    show the operator the quantity and price before they commit. The cost
    suggestion costs one extra query per candidate whose supplier relationship
    carries no price (see :func:`default_unit_cost`); the candidate list is
    capped at :data:`DEFAULT_CANDIDATE_LIMIT`, which bounds that.
    """
    item_supplier = candidate.item_supplier
    item = item_supplier.item
    existing = candidate.existing_line
    return {
        "item_supplier": item_supplier.pk,
        "match_kind": candidate.match_kind,
        "match_label": MATCH_TIER_LABELS.get(candidate.match_kind, candidate.match_kind),
        "matched_value": candidate.matched_value,
        "is_exact": candidate.match_kind in _EXACT_TIERS,
        "item": {
            "id": str(item.pk),
            "name": item.name,
            "sku": item.sku,
            "is_kit": item.is_kit,
        },
        "supplier_sku": item_supplier.supplier_sku,
        "package_upc": item_supplier.package_upc,
        "unit_upc": item_supplier.unit_upc,
        "quantity_per_package": item_supplier.quantity_per_package,
        "suggested_quantity": default_quantity(item_supplier),
        "suggested_unit_cost": str(default_unit_cost(item_supplier)),
        "already_on_order": (
            None
            if existing is None
            else {
                "line_item": str(existing.pk),
                "quantity_ordered": existing.quantity_ordered,
                "is_voided": existing.is_voided,
            }
        ),
    }


def serialize_unavailable(entry):
    """API shape for one item the identifier named but this order cannot carry."""
    return {
        "item": {"id": str(entry.item.pk), "name": entry.item.name, "sku": entry.item.sku},
        "reason": entry.reason,
        "message": entry.message,
    }


def serialize_lookup(purchase_order, query, result):
    """Full lookup payload: who we searched for, what matched, what cannot be added.

    Both lists are capped at :data:`DEFAULT_CANDIDATE_LIMIT`, so the payload
    carries the pre-cap accounting for each: ``total_candidates`` /
    ``best_match_total`` / ``truncated`` for ``candidates``, and
    ``total_unavailable`` / ``unavailable_truncated`` for ``unavailable``. A
    client rendering either capped list has to be able to tell the operator that
    more matched, rather than presenting the cap as the complete answer.
    """
    best_tier = result.best_tier
    return {
        "query": query,
        "supplier": {"id": purchase_order.supplier_id, "name": purchase_order.supplier.name},
        "purchase_order": {
            "id": purchase_order.pk,
            "po_number": purchase_order.po_number,
            "status": purchase_order.status,
            "can_add_items": purchase_order.status == PurchaseOrder.Status.DRAFT,
        },
        "best_match_kind": best_tier,
        # True when a client may add straight from this lookup without asking
        # the operator anything — exactly the rule resolve_identifier applies.
        "resolves": result.best_tier_total == 1,
        "candidates": [serialize_candidate(candidate) for candidate in result.candidates],
        # Pre-cap counts, so a shortened list is never mistaken for all of them.
        "total_candidates": result.total_candidates,
        "best_match_total": result.best_tier_total,
        "truncated": result.truncated,
        "unavailable": [serialize_unavailable(entry) for entry in result.unavailable],
        "total_unavailable": result.total_unavailable,
        "unavailable_truncated": result.unavailable_truncated,
    }
