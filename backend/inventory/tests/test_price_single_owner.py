"""A reader that bypasses the price derivation fails the build (op-9m2v).

"What does this cost, and do we know?" has ONE owner,
:mod:`inventory.services.pricing`. Before it, every reader spelled the guard
with ``or`` — ``unit_cost or 0``, ``unit_cost or Decimal("0.00")``,
``if unit_cost:`` — and ``or`` cannot tell a recorded ``0.00`` from a ``NULL``,
so each of those sites got one of the two cases wrong: an unpriced supplier
costed a purchase-order line at nothing, and a supplier that genuinely charges
nothing was read as having no price on file.

This is the money sibling of ``test_pack_size_single_owner.py`` and works the
same way: it walks every non-test, non-migration module under ``backend/`` with
the AST and counts real READS of ``unit_cost`` and ``package_cost``. A new one
anywhere — a new module, or one more in a module already listed — fails until it
either goes through the derivation or is added here deliberately, with a reason.

**Scope: ``backend/`` ONLY, and the columns by NAME.** Two honest limits, both
narrower than "a reader that bypasses the derivation fails the build" sounds:

1. The walk stops at the Python tree. A FRONTEND reader of a price is not gated,
   exactly as ``quantity_per_package``'s is not. The frontend price sites this
   branch changed (``PurchaseOrderFormPage``'s pad totals, the reports' money
   columns) were found by derivation and review, not by this test. Extending the
   scan to frontend sources is the same filed follow-up.
2. ``unit_cost`` is a column name on FIVE models here —
   ``ItemSupplier``, ``PriceHistory``, ``UsageLog``,
   ``WorkOrderMaterialUsage`` and ``MaintenanceItem`` — and the AST cannot tell
   which one an attribute access lands on. The scan therefore counts them all
   and the allowlist says, per entry, which model the read is on and why it is
   not a supplier-price derivation. That makes the gate slightly noisier than
   ``pack_size``'s and does not weaken it: an entry has to be justified either
   way.

**What counts as a read** is the same set ``test_pack_size_single_owner``
justifies, and for the same reason — a derivation can reach a price from Python
or from SQL:

* ``link.unit_cost`` — attribute access, LOAD context only. A Store is a WRITE:
  it cannot turn "we do not know" into a number.
* ``getattr(link, "unit_cost")`` — the same read, spelled dynamically.
* the column named inside a read-only ORM call (``values``, ``only``,
  ``annotate``, ``filter``, ``order_by`` …), as a string, a keyword, a ``"-"``
  ordering, or a ``__`` lookup path.
* the column named inside a bare expression wrapper — ``F``/``Q``/``Value``/
  ``When``/``Case``/``Subquery``/``OuterRef`` plus the aggregate and
  null-handling functions ``Coalesce``/``Min``/``Max``/``Avg``/``Sum``.
  ``Coalesce`` is the load-bearing addition over the pack-size scan:
  ``Sum(F("current_stock") * Coalesce("unit_cost_value", Value(0)))`` in
  ``inventory.views``'s stock-value reports is ``unit_cost or 0`` written in
  SQL, and it is exactly the shape a future bypass would take.

Bare string occurrences outside those calls (serializer ``fields`` lists, admin
columns, help text, prose) are NOT counted: naming a field cannot fabricate a
number.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Optional

COLUMNS = ("unit_cost", "package_cost")

BACKEND = pathlib.Path(__file__).resolve().parents[2]

#: Reads of a price column that are NOT a supplier-price derivation, with why.
#: Every entry is the column's own definition, a read on a DIFFERENT model's
#: cost column, a verbatim copy of what a row records, an explicit ``is None``
#: guard that already keeps the states apart, or a read of the owner-routed
#: ``InventoryItem.unit_cost`` property rather than of the column. A DERIVATION
#: belongs in ``inventory.services.pricing`` instead.
ALLOWED: dict[str, tuple[int, str]] = {
    "inventory/services/pricing.py": (
        2,
        "THE OWNER. ``lowest_unit_price`` reads the column directly; the "
        "per-row ``_price_of`` reaches it through ``getattr(link, column)`` so "
        "one function can serve both columns, which is why this count is 2 and "
        "not more.",
    ),
    "inventory/models/core.py": (
        6,
        "``ItemSupplier.save()`` handing the pair to and from the derivation (4): "
        "it reads the SUPPLIED pair once into locals, and reads the DERIVED pair "
        "back when deciding which columns a restricted ``update_fields`` must "
        "gain — without which a cost the derivation computes is dropped by "
        "``update_or_create``. The arithmetic itself is not here; it is "
        "``services.suppliers.derive_costs``, which takes the values as "
        "parameters. Plus ``average_unit_cost``'s "
        "``filter(unit_cost__isnull=False)`` + ``Avg('unit_cost')`` (2), where "
        "the SQL aggregate already skips the NULLs and the explicit isnull "
        "filter says so. No item-level answer is produced from a raw column "
        "here; ``unit_cost`` / ``package_cost`` / ``lowest_unit_cost`` / "
        "``total_value`` all read the derivation.",
    ),
    "inventory/services/supplier_selection.py": (
        3,
        "The yardstick and the row order, and nothing else. "
        "``average_orderable_unit_cost`` (2) is explicitly ``is not None``, "
        "exactly as SQL ``AVG`` skips NULLs; ``Meta``-order "
        "``order_by('unit_cost')`` (1) is a row ordering, not a price. Was 5: "
        "``score_candidate``'s cost guard used to be "
        "``if link.unit_cost and average_unit_cost``, this same falsy-zero "
        "mistake, and it now reads the price through ``unit_price_of`` like "
        "every other reader (oms-supplier-scoring-weight-flaws). A free link is "
        "PRICED AT ZERO in the scoring, so it wins on being free instead of "
        "being graded as the dearest candidate on file.",
    ),
    "inventory/services/suppliers.py": (
        6,
        "THE OWNER. ``stored_pricing``'s ``.values('unit_cost', 'package_cost', "
        "...)`` reads the pre-save row the derivation is a delta against (2); "
        "``pricing_changed`` compares that row to the instance with ``!=`` (2); "
        "``record_price_history`` snapshots the recorded values verbatim (2). "
        "Both comparisons separate None from 0.00 correctly. ``derive_costs`` "
        "itself contributes NONE of these six — it takes the pair as parameters "
        "and never touches a model attribute, which is what makes it a pure "
        "definition of the columns rather than a reading of them.",
    ),
    "inventory/serializers.py": (
        4,
        "``latest_cost`` / ``previous_cost`` on the price-trend summary, on "
        "BOTH of its known-price branches: the one that computed a percentage "
        "and the ``no_baseline`` one that could not. Verbatim copies of two "
        "PriceHistory snapshots, emitted only after "
        "``unit_price_of(...).is_known`` has gated the pair — which is why the "
        "``no_baseline`` payload can keep the two prices it does know instead "
        "of dropping them.",
    ),
    "inventory/views.py": (
        7,
        "Two reads of the owner-routed ``InventoryItem.unit_cost`` PROPERTY "
        "(the consume-time cost snapshot, and the ad-hoc work-order material "
        "default) which the AST cannot tell from a column; one "
        "``order_by('-is_primary', 'unit_cost')`` that orders the ItemSupplier "
        "LIST endpoint; and the two stock-value reports' "
        "``filter(unit_cost__isnull=False)`` + ``Avg('unit_cost')`` subqueries "
        "(4), which skip the NULLs explicitly. The ``Coalesce(..., Value(0))`` "
        "beside those is the SQL twin this scan exists to see, and it is "
        "reported to the operator as ``items_without_price`` rather than "
        "hidden.",
    ),
    "inventory/admin.py": (
        2,
        "``unit_cost_display`` on the ItemSupplier admin: an ``is None`` guard "
        "rendering an em dash, then the format. Already keeps unknown apart "
        "from 0.0000.",
    ),
    "inventory/services/pack_transitions.py": (
        1,
        "Reads the owner-routed ``InventoryItem.unit_cost`` PROPERTY to "
        "snapshot cost onto a UsageLog, guarded ``is not None``. An attribute "
        "read the AST cannot distinguish from a column read.",
    ),
    "inventory/models/maintenance.py": (
        2,
        "``WorkOrderMaterialUsage.unit_cost`` — a DIFFERENT column on a "
        "different model: what a tech actually paid at the hardware store, not "
        "what a vendor quotes. Its ``actual_cost`` already returns ``None`` "
        "when no cost was recorded.",
    ),
    "inventory/management/commands/report_duplicate_work_orders.py": (
        1,
        "``WorkOrderMaterialUsage.unit_cost is not None`` as one term in a "
        "'does this row carry any data?' predicate. Not a price read at all — "
        "the value is never used.",
    ),
    "reorder_queue/views.py": (
        2,
        "``Min('unit_cost')`` / ``Max('unit_cost')`` over PriceHistory in the "
        "price-trend report. SQL MIN/MAX skip NULLs, so the aggregate is "
        "already 'the cheapest/dearest price ON FILE'; the endpoint sends "
        "``null`` rather than 0 when there is none.",
    ),
}

#: Directories whose modules are never scanned — see the twin in
#: ``test_pack_size_single_owner.py`` for why the virtualenv names matter.
_SKIP_PARTS = {
    "migrations",
    "tests",
    "__pycache__",
    "node_modules",
    "staticfiles",
    "site-packages",
    ".venv",
    "venv",
    ".env",
    "env",
    ".tox",
    ".eggs",
}

#: ORM calls that READ rows. A field named inside one of these reaches the
#: column in SQL. Row-writing calls are deliberately absent.
_ORM_READ_CALLS = frozenset(
    {
        "values",
        "values_list",
        "only",
        "defer",
        "annotate",
        "aggregate",
        "order_by",
        "filter",
        "exclude",
    }
)

#: Expression / aggregate wrappers that name a column directly.
#:
#: ``Coalesce`` is the one this scan adds over the pack-size gate, and it is
#: load-bearing: ``Coalesce("unit_cost_value", Value(0))`` IS ``unit_cost or 0``
#: expressed in SQL, and the stock-value reports really do write it. ``Min`` /
#: ``Max`` / ``Avg`` / ``Sum`` are here because a future "average price"
#: derivation is far likelier to arrive as an aggregate than as a loop.
_EXPRESSION_CALLS = frozenset(
    {
        "F",
        "Q",
        "Value",
        "When",
        "Case",
        "Subquery",
        "OuterRef",
        "Coalesce",
        "Min",
        "Max",
        "Avg",
        "Sum",
    }
)


def _modules():
    """Every non-test, non-migration Python module under ``backend/``."""
    for path in sorted(BACKEND.rglob("*.py")):
        if _SKIP_PARTS & set(path.parts):
            continue
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            continue
        yield path


def _names_column(text: str, column: str) -> bool:
    """True when an ORM field reference resolves to ``column``."""
    return column in text.lstrip("-").split("__")


def _called_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _reads(tree: ast.AST, column: str) -> set[int]:
    """Node ids of every site that READS ``column``, deduped by identity.

    Deduping by ``id`` is what makes chained querysets safe to scan: the
    subtree of ``qs.filter(...).values(COLUMN)`` is visited from both calls and
    the same string node is recorded once.
    """
    found: set[int] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == column:
            # LOAD only. ``self.unit_cost = x`` is a write.
            if isinstance(node.ctx, ast.Load):
                found.add(id(node))
            continue

        if not isinstance(node, ast.Call):
            continue

        name = _called_name(node)

        if name == "getattr" and len(node.args) >= 2:
            attr = node.args[1]
            if isinstance(attr, ast.Constant) and attr.value == column:
                found.add(id(attr))
            continue

        if name in _ORM_READ_CALLS or name in _EXPRESSION_CALLS:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    if _names_column(inner.value, column):
                        found.add(id(inner))
                elif isinstance(inner, ast.keyword) and inner.arg:
                    if _names_column(inner.arg, column):
                        found.add(id(inner))

    return found


def _column_reads_in(source: str, filename: str = "<snippet>") -> int:
    """How many sites in ``source`` read either price column."""
    tree = ast.parse(source, filename=filename)
    return sum(len(_reads(tree, column)) for column in COLUMNS)


def _column_reads(path: pathlib.Path) -> Optional[int]:
    """How many sites in ``path`` read a price column, or ``None`` if unreadable.

    A file this Python cannot decode or parse is SKIPPED rather than raised
    through: an unparsable module is not a price reader, and failing the gate on
    one would report a decoding problem under a message about ``unit_cost``.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    try:
        return _column_reads_in(source, filename=str(path))
    except SyntaxError:
        return None


def test_every_price_reader_goes_through_the_one_derivation():
    found = {}
    for path in _modules():
        count = _column_reads(path)
        if count:
            found[str(path.relative_to(BACKEND))] = count
    assert found, (
        f"The scan found no reads of {' / '.join(COLUMNS)} anywhere under "
        f"{BACKEND} — not even the owner. The walk itself is broken (a bad "
        "_SKIP_PARTS entry, or a moved tree), so this gate is passing "
        "vacuously rather than guarding inventory.services.pricing."
    )

    expected = {name: count for name, (count, _reason) in ALLOWED.items()}

    new_readers = {k: v for k, v in found.items() if k not in expected}
    assert not new_readers, (
        f"{sorted(new_readers)} reads a price column directly. 'What does this "
        "cost, and do we know?' has ONE owner: read it through "
        "inventory.services.pricing (unit_price_of / package_price_of / "
        "order_unit_price / order_package_price / lowest_unit_price), and sum "
        "with PriceRollup so a total that omitted a line says so. A guard "
        "spelled 'or 0' or 'if cost:' cannot tell a recorded 0.00 from a NULL, "
        "and getting that wrong in either direction is what this derivation "
        "exists to stop."
    )

    assert found == expected, (
        f"The set of direct reads of {' / '.join(COLUMNS)} changed: "
        f"{found} != {expected}. If you added a derivation, route it through "
        "inventory.services.pricing instead. If you genuinely added the "
        "column's own definition, a verbatim copy, a read on a DIFFERENT "
        "model's cost column, or a write path, update ALLOWED here with the "
        "reason — and say which model the column is on and which of the four "
        "price states the site handles."
    )


# ── The detector itself, exercised on constructed sources ────────────────────
#
# A gate that cannot see the bypass it was built to catch passes vacuously, so
# each shape below is run through ``_column_reads_in`` as an ordinary function
# with test-owned input. None of these assert anything about the repository's
# own source.


def test_a_plain_attribute_read_is_caught():
    assert _column_reads_in("cost = link.unit_cost") == 1
    assert _column_reads_in("cost = link.package_cost") == 1


def test_the_original_falsy_guards_are_caught():
    """The exact expressions this branch removed."""
    assert _column_reads_in("total = qty * (best_supplier.unit_cost or 0)") == 1
    assert _column_reads_in('cost = item_supplier.unit_cost or Decimal("0.00")') == 1
    assert _column_reads_in("if link.unit_cost:\n    pass") == 1


def test_assigning_the_column_is_a_write_not_a_read():
    """``self.unit_cost = x`` cannot turn an unknown into a number."""
    assert _column_reads_in("self.unit_cost = 5") == 0
    assert _column_reads_in("obj.unit_cost, obj.other = 5, 6") == 0


def test_a_dynamic_read_is_caught():
    assert _column_reads_in('cost = getattr(link, "unit_cost")') == 1


def test_a_sql_side_derivation_cannot_slip_past():
    assert _column_reads_in('ItemSupplier.objects.values("unit_cost")') == 1
    assert _column_reads_in('qs.only("package_cost")') == 1
    assert _column_reads_in("qs.filter(unit_cost__isnull=False)") == 1
    assert _column_reads_in('qs.order_by("-unit_cost")') == 1
    assert _column_reads_in('qs.annotate(n=Avg("unit_cost"))') == 1
    assert _column_reads_in('qs.annotate(n=F("item_suppliers__unit_cost"))') == 1


def test_the_sql_twin_of_an_or_zero_cannot_slip_past():
    """``Coalesce(price, Value(0))`` IS ``unit_cost or 0``, written in SQL.

    The stock-value reports build exactly this, which is why ``Coalesce`` and
    the aggregates are in ``_EXPRESSION_CALLS`` and why scanning only queryset
    methods would not have been enough.
    """
    assert _column_reads_in('Coalesce("unit_cost", Value(0))') == 1
    assert _column_reads_in('Sum(F("stock") * Coalesce("unit_cost", Value(0)))') == 1
    assert _column_reads_in('Min("unit_cost") + Max("unit_cost")') == 2


def test_a_bare_q_twin_cannot_slip_past():
    assert _column_reads_in("Q(item_suppliers__unit_cost__gt=0)") == 1
    assert _column_reads_in("Q(unit_cost=0) | Q(unit_cost__isnull=True)") == 2
    assert (
        _column_reads_in("def priced_q():\n    return Q(item_suppliers__unit_cost__isnull=False)\n")
        == 1
    )
    assert _column_reads_in("When(unit_cost__isnull=True, then=Value(0))") == 1


def test_a_chained_queryset_counts_each_site_once():
    assert _column_reads_in('qs.filter(unit_cost__isnull=False).values("unit_cost")') == 2
    assert _column_reads_in('qs.filter(unit_cost__isnull=False).order_by("name")') == 1


def test_naming_the_field_without_reading_it_is_not_a_read():
    """Serializer field lists, admin columns and prose must not trip the gate."""
    assert _column_reads_in('fields = ["unit_cost", "package_cost"]') == 0
    assert _column_reads_in('"""What the unit_cost column means."""') == 0
    assert _column_reads_in('ItemSupplier.objects.create(unit_cost=Decimal("5"))') == 0
    assert _column_reads_in('link.save(update_fields=["unit_cost"])') == 0


def test_an_unrelated_column_never_trips_the_gate():
    assert _column_reads_in('qs.values("unit_cost_ordered")') == 0
    assert _column_reads_in("cost = link.unit_weight") == 0
    assert _column_reads_in("cost = link.quantity_per_package") == 0
