"""A reader that collapses a real ``estimated_cost`` of zero fails the build (op-9m2v).

``ReorderRequest.estimated_cost`` is the money face of
:mod:`inventory.services.pricing`: it extends the orderable supplier's unit
price by the requested quantity, so it returns ``Decimal("0.00")`` for an item a
vendor gives away and ``None`` ONLY when the price is genuinely unknown. Base
returned ``None`` for both, and every reader was written against that — which
is why ``if obj.estimated_cost:`` and ``float(x) if x else None`` are still the
shape a new reader arrives in, and why each one re-collapses the two facts the
derivation exists to keep apart.

**Why this gate exists rather than a third hand-enumeration.** The reader set
was enumerated by hand TWICE and was wrong BOTH times: four readers were named
(the outbound reorder webhook and three admin displays) and recorded in
AGENTS.md as complete, and review then found three more on the PUBLIC
``AllowAny`` transparency endpoint that neither sweep had seen. A hand-counted
list is a claim that goes stale the moment somebody adds a reader; this walk is
the claim, re-derived on every run. It is the third such gate, after
``inventory/tests/test_pack_size_single_owner.py`` and
``inventory/tests/test_price_single_owner.py``, and it deliberately mirrors the
second's mechanics rather than inventing a new shape.

**Scope: ``backend/`` ONLY, and the name alone.** Three honest limits, all
narrower than "a reader that collapses a zero fails the build" sounds:

1. The walk stops at the Python tree. A FRONTEND reader of ``estimated_cost``
   is not gated, exactly as the price gate's frontend readers are not.
2. ``estimated_cost`` is a name on THREE models here and the AST cannot tell
   which one an attribute access lands on:

   * ``ReorderRequest.estimated_cost`` — the property this branch changed.
     ``Decimal("0.00")`` for a free item, ``None`` when unpriced.
   * ``PurchaseOrderItem.estimated_cost`` — NON-nullable, always a ``Decimal``
     (``0.00`` when the line has no quantity or no ordered cost). A falsy guard
     on this one has ALWAYS been wrong for a free line, because ``None`` was
     never one of its answers.
   * ``MaintenanceItem.estimated_cost`` — a nullable column holding what a
     maintenance TASK is budgeted at. A different fact with different owners,
     and outside this branch's invariant.

   The allowlist therefore states, per entry, WHICH model the read is on and
   why the site is not a falsy collapse.
3. It counts reads, not correctness. An entry is a decision somebody made and
   wrote down, not a proof.

**What counts as a read** is the set ``test_price_single_owner`` justifies, for
the same reason — a collapse can be written in Python or in SQL:

* ``obj.estimated_cost`` — attribute access, LOAD context only. A Store is a
  WRITE and cannot turn "we do not know" into a number.
* ``getattr(obj, "estimated_cost")`` — the same read, spelled dynamically.
* the name inside a read-only ORM call (``values``, ``only``, ``annotate``,
  ``filter``, ``order_by`` …), as a string, a keyword, a ``"-"`` ordering or a
  ``__`` lookup path.
* the name inside a bare expression wrapper — ``F``/``Q``/``Value``/``When``/
  ``Case``/``Subquery``/``OuterRef`` plus ``Coalesce``/``Min``/``Max``/``Avg``/
  ``Sum``. ``Coalesce("estimated_cost", Value(0))`` is ``x or 0`` written in
  SQL and would be exactly how a future bypass arrived.

Bare string occurrences outside those calls — payload keys, serializer
``fields`` lists, admin column tuples, prose — are NOT counted: naming a field
cannot fabricate a number.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Optional

COLUMN = "estimated_cost"

BACKEND = pathlib.Path(__file__).resolve().parents[2]

#: Reads of ``estimated_cost`` that are NOT a falsy collapse, with why. Every
#: entry names the MODEL the read is on, because the AST cannot.
ALLOWED: dict[str, tuple[int, str]] = {
    "reorder_queue/views.py": (
        7,
        "ReorderRequest.estimated_cost on the PUBLIC AllowAny transparency "
        "action (6): the order payload's ``estimated_cost`` (2) and "
        "``cost_variance`` (2), and the ledger block's ``estimated_cost`` (2). "
        "All spelled ``is not None`` — a free request publishes ``0.0``, and "
        "``null`` means only that no price is on file. Plus one "
        "PurchaseOrderItem.estimated_cost read (1), ``str(line_item."
        "estimated_cost)`` on the line-edit response: that property is "
        "non-nullable, and this is a verbatim copy with no guard at all.",
    ),
    "reorder_queue/admin.py": (
        6,
        "``estimated_cost_display`` on the ReorderRequest changelist (2), the "
        "PurchaseOrderItem inline (2) and the PurchaseOrderItem changelist "
        "(2) — an ``is not None`` guard and the format beside it. The em dash "
        "is reserved for the genuine absence, so a comped line renders "
        "``$0.00``.",
    ),
    "reorder_queue/tasks.py": (
        2,
        "ReorderRequest.estimated_cost in the OUTBOUND reorder webhook "
        "payload: an ``is not None`` guard and the ``float()`` beside it. "
        "Discord/Slack are told a donated item costs ``0.0``, not that its "
        "cost is unknown.",
    ),
    "reorder_queue/models.py": (
        2,
        "PurchaseOrderItem.estimated_cost summed into the order totals — the "
        "voided-line subtotal and ``estimated_total``. NON-nullable, so there "
        "is no unknown to collapse and both sites add it unguarded.",
    ),
    "reorder_queue/services/purchase_orders.py": (
        5,
        "PurchaseOrderItem.estimated_cost: three order-total accumulations, "
        "one quantity-edit re-roll, and one copy onto "
        "``ReorderRequest.actual_cost`` at receipt. NON-nullable, all "
        "unguarded arithmetic or verbatim copies.",
    ),
    "inventory/views.py": (
        3,
        "MaintenanceItem.estimated_cost — a DIFFERENT model: what a "
        "maintenance TASK is budgeted at, not what a supplier charges. One "
        "verbatim copy onto a cloned template, and two "
        "``or Decimal('0.00')`` fallbacks in the PM cost report which are "
        "INERT: the fallback IS ``0.00``, so a recorded zero and a NULL "
        "produce the same number either way.",
    ),
    "inventory/services/work_order_reports.py": (
        1,
        "MaintenanceItem.estimated_cost, same different model as "
        "``inventory/views.py`` above. ``or Decimal('0.00')`` is INERT here "
        "for the same reason — both branches yield ``0.00`` at a zero "
        "estimate.",
    ),
    "inventory/utils/work_order_pdf.py": (
        2,
        "MaintenanceItem.estimated_cost on the work-order PDF: ``if "
        "item.estimated_cost:`` and the format beside it, so a task budgeted "
        "at a recorded ``0.00`` prints no 'Est. Cost' line. REPORTED, NOT "
        "FIXED — it is the same shape on a value this branch did not change "
        "and does not own (a maintenance budget, not a supplier price), so "
        "repairing it would move output for a reason that is not 'base "
        "presented an unknown price as a real number'. Recorded in AGENTS.md.",
    ),
    "analytics/services/aggregation.py": (
        2,
        "MaintenanceItem.estimated_cost reached in SQL as "
        "``_money_sum('maintenance_item__estimated_cost')``, twice. Same "
        "different model; the ``Coalesce(..., 0)`` inside ``_money_sum`` is "
        "INERT at a zero estimate for the reason the two above are.",
    ),
}

#: Directories whose modules are never scanned — the same set the price gate
#: skips, and the virtualenv names matter for the same reason.
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


def _names_column(text: str) -> bool:
    """True when an ORM field reference resolves to :data:`COLUMN`.

    Splitting on ``__`` is what keeps ``estimated_cost_per_unit`` — a genuinely
    different column on ``MaintenanceMaterial`` — from tripping the gate, while
    still seeing it through a ``maintenance_item__estimated_cost`` join.
    """
    return COLUMN in text.lstrip("-").split("__")


def _called_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _reads(tree: ast.AST) -> set[int]:
    """Node ids of every site that READS :data:`COLUMN`, deduped by identity.

    Deduping by ``id`` is what makes chained querysets safe to scan: the
    subtree of ``qs.filter(...).values(COLUMN)`` is visited from both calls and
    the same string node is recorded once.
    """
    found: set[int] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == COLUMN:
            # LOAD only. ``request.estimated_cost = x`` is a write.
            if isinstance(node.ctx, ast.Load):
                found.add(id(node))
            continue

        if not isinstance(node, ast.Call):
            continue

        name = _called_name(node)

        if name == "getattr" and len(node.args) >= 2:
            attr = node.args[1]
            if isinstance(attr, ast.Constant) and attr.value == COLUMN:
                found.add(id(attr))
            continue

        if name in _ORM_READ_CALLS or name in _EXPRESSION_CALLS:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    if _names_column(inner.value):
                        found.add(id(inner))
                elif isinstance(inner, ast.keyword) and inner.arg:
                    if _names_column(inner.arg):
                        found.add(id(inner))

    return found


def _reads_in(source: str, filename: str = "<snippet>") -> int:
    """How many sites in ``source`` read :data:`COLUMN`."""
    return len(_reads(ast.parse(source, filename=filename)))


def _reads_at(path: pathlib.Path) -> Optional[int]:
    """How many sites in ``path`` read the column, or ``None`` if unreadable.

    A file this Python cannot decode or parse is SKIPPED rather than raised
    through: an unparsable module is not a reader of anything, and failing the
    gate on one would report a decoding problem under a message about
    ``estimated_cost``.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    try:
        return _reads_in(source, filename=str(path))
    except SyntaxError:
        return None


def test_every_estimated_cost_reader_is_a_decision_somebody_wrote_down():
    found = {}
    for path in _modules():
        count = _reads_at(path)
        if count:
            found[str(path.relative_to(BACKEND))] = count

    assert found, (
        f"The scan found no reads of {COLUMN} anywhere under {BACKEND} — not "
        "even the order totals that are built from it. The walk itself is "
        "broken (a bad _SKIP_PARTS entry, or a moved tree), so this gate is "
        "passing vacuously rather than guarding the derivation."
    )

    expected = {name: count for name, (count, _reason) in ALLOWED.items()}

    new_readers = {k: v for k, v in found.items() if k not in expected}
    assert not new_readers, (
        f"{sorted(new_readers)} reads {COLUMN} and no entry here says why. "
        "ReorderRequest.estimated_cost returns Decimal('0.00') for an item a "
        "vendor gives away and None ONLY when no price is on file, so spell "
        "the guard 'is not None' and render a real $0.00; 'if cost:' and "
        "'float(x) if x else None' cannot tell the two apart. If the read is "
        "on PurchaseOrderItem.estimated_cost (non-nullable) or "
        "MaintenanceItem.estimated_cost (a different fact), add it to ALLOWED "
        "saying WHICH model and why it is not a collapse."
    )

    assert found == expected, (
        f"The set of reads of {COLUMN} changed: {found} != {expected}. This "
        "list is the derivation — it exists because the reader set was "
        "hand-counted twice and was incomplete both times. Update ALLOWED "
        "with the model the read is on and why the site is not a falsy "
        "collapse."
    )


# ── The detector itself, exercised on constructed sources ────────────────────
#
# A gate that cannot see the bypass it was built to catch passes vacuously, so
# each shape below is run through ``_reads_in`` as an ordinary function with
# test-owned input. None of these assert anything about the repository's own
# source.


def test_a_plain_attribute_read_is_caught():
    assert _reads_in("cost = request.estimated_cost") == 1


def test_the_three_collapses_that_actually_occurred_are_caught():
    """The exact expressions review found, twice, after two hand sweeps."""
    assert _reads_in("if obj.estimated_cost:\n    pass") == 1
    assert _reads_in("x = float(o.estimated_cost) if o.estimated_cost else None") == 2
    assert _reads_in("if (o.actual_cost and o.estimated_cost):\n    pass") == 1


def test_the_repaired_spelling_is_still_a_read():
    """Fixing a site must not hide it — the gate tracks reads, not guards."""
    assert _reads_in("x = None if o.estimated_cost is None else float(o.estimated_cost)") == 2


def test_assigning_the_field_is_a_write_not_a_read():
    assert _reads_in("self.estimated_cost = 5") == 0
    assert _reads_in("obj.estimated_cost, obj.other = 5, 6") == 0


def test_a_dynamic_read_is_caught():
    assert _reads_in('cost = getattr(request, "estimated_cost")') == 1


def test_a_sql_side_read_cannot_slip_past():
    assert _reads_in('PurchaseOrderItem.objects.values("estimated_cost")') == 1
    assert _reads_in('qs.only("estimated_cost")') == 1
    assert _reads_in("qs.filter(estimated_cost__isnull=False)") == 1
    assert _reads_in('qs.order_by("-estimated_cost")') == 1
    assert _reads_in('qs.annotate(n=Sum("items__estimated_cost"))') == 1
    assert _reads_in('qs.aggregate(t=Sum("maintenance_item__estimated_cost"))') == 1


def test_the_sql_twin_of_an_or_zero_cannot_slip_past():
    assert _reads_in('Coalesce("estimated_cost", Value(0))') == 1
    assert _reads_in('Sum(Coalesce("estimated_cost", Value(0)))') == 1
    assert _reads_in("Q(estimated_cost=0) | Q(estimated_cost__isnull=True)") == 2
    assert _reads_in("When(estimated_cost__isnull=True, then=Value(0))") == 1


def test_a_chained_queryset_counts_each_site_once():
    assert _reads_in('qs.filter(estimated_cost__isnull=False).values("estimated_cost")') == 2
    assert _reads_in('qs.filter(estimated_cost__isnull=False).order_by("id")') == 1


def test_naming_the_field_without_reading_it_is_not_a_read():
    """Payload keys, serializer field lists, admin columns and prose."""
    assert _reads_in('data = {"estimated_cost": total}') == 0
    assert _reads_in('fields = ["estimated_cost", "actual_cost"]') == 0
    assert _reads_in('list_display = ("estimated_cost_display",)') == 0
    assert _reads_in('"""What the estimated_cost property means."""') == 0
    assert _reads_in('Item.objects.create(estimated_cost=Decimal("5"))') == 0


def test_a_neighbouring_column_never_trips_the_gate():
    """``estimated_cost_per_unit`` and ``total_estimated_cost`` are other facts."""
    assert _reads_in("cost = material.estimated_cost_per_unit") == 0
    assert _reads_in("cost = material.total_estimated_cost") == 0
    assert _reads_in('qs.values("estimated_cost_per_unit")') == 0
    assert _reads_in("cost = order.actual_cost") == 0
