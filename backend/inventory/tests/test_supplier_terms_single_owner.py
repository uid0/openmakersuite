"""A writer that hand-rolls an ItemSupplier cost write fails the build (op-9m2v).

"Write purchase terms onto a supplier link" has ONE owner,
:func:`inventory.services.suppliers.write_supplier_terms`. Before it, each
caller spelled its own ``ItemSupplier.objects.update_or_create(defaults=...)``
with a PARTIAL dict, against a model whose ``save()`` derives ``unit_cost`` and
``package_cost`` from each other — and a partial write always lost that fight:
the stale sibling column recomputed the old price over the operator's typed one,
and a column ``save()`` had just derived was dropped by ``update_or_create``'s
``update_fields`` restriction. Three consecutive review rounds landed on the same
function for three different faces of that one cause.

The write-side twin of ``test_price_single_owner.py``, which gates READS of the
same columns. This one walks every non-test, non-migration module under
``backend/`` with the AST and counts WRITES that could set a cost column outside
the owner: a construction of ``ItemSupplier`` or a manager call that takes a
``defaults=`` / field kwargs, plus a ``.save()`` on something named like an item
supplier.

**Scope, narrowed honestly — three limits, all narrower than "no writer can
bypass the owner" sounds:**

1. The walk stops at the Python tree. A write issued from a management command
   shelled out to, from raw SQL, or from a data migration is not seen. Data
   migrations are deliberately excluded: they run against historical model
   states where the owner may not exist.
2. It matches on NAMES, not types. ``ItemSupplier.objects.update_or_create(...)``
   is caught because the manager's owner is spelled out; a queryset held in a
   local (``qs = ItemSupplier.objects.filter(...)`` then ``qs.update(...)``) is
   not, because the AST cannot tell what ``qs`` is. The same limit the
   pack-size and price gates carry.
3. A ``.save()`` is counted only when the receiver READS like an item supplier
   (``link``, ``item_supplier``, ``supplier_link``, ``rel`` …). A save on a
   differently-named local is invisible. This is the loosest of the three and is
   why the allowlist reasons matter more than the count.

**What counts as a write** is deliberately broad, because the defect is a
partial write rather than any particular spelling:

* ``ItemSupplier(...)`` — direct construction.
* ``ItemSupplier.objects.create`` / ``update_or_create`` / ``get_or_create`` /
  ``update`` / ``bulk_create`` / ``bulk_update``.
* ``<supplier-ish>.save(...)``.

Naming a cost column in a serializer ``fields`` list, an admin column or prose
is NOT a write: naming a field cannot store a number.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Optional

BACKEND = pathlib.Path(__file__).resolve().parents[2]

#: Manager methods that persist rows.
_WRITE_METHODS = frozenset(
    {"create", "update_or_create", "get_or_create", "update", "bulk_create", "bulk_update"}
)

#: Receiver names that read as "an ItemSupplier row" for the ``.save()`` rule.
#: Deliberately NARROW. A generic ``link`` / ``rel`` would match unrelated models
#: — ``maintenance_orders.transitions`` saves a ``link.allocated_cost`` that has
#: nothing to do with supplier pricing — and an allowlist entry for another app's
#: code would be a misleading claim rather than a gate.
_SUPPLIER_RECEIVERS = frozenset({"item_supplier", "supplier_link", "item_supplier_obj"})

ALLOWED: dict[str, tuple[int, str]] = {
    "inventory/services/suppliers.py": (
        2,
        "THE OWNER, plus its neighbour. `write_supplier_terms` performs the one "
        "construction every caller now delegates to; `enforce_single_primary` "
        "bulk-updates `is_primary` only, which is a flag rather than a cost.",
    ),
    "inventory/tasks.py": (
        1,
        "`update_lead_times` sets `average_lead_time` with "
        "`update_fields=['average_lead_time']`, so no cost column is written and "
        "the derivation in `save()` cannot reach the database. Not a price write.",
    ),
    "inventory/views.py": (
        1,
        "`ItemSupplierViewSet.mark_discontinued` sets `is_discontinued` / "
        "`is_active` on a row it just loaded. Flags only; the costs it re-saves "
        "are the consistent pair already stored, so the derivation is a no-op.",
    ),
    "reorder_queue/services/purchase_orders.py": (
        1,
        "`void_line_item` marks the link discontinued when its line is struck "
        "off. Flags only, on a freshly loaded row — same reasoning as "
        "`mark_discontinued` above.",
    ),
}


def _modules():
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if "/migrations/" in rel or "/tests/" in rel or rel.startswith("tests/"):
            continue
        if path.name.startswith("test_") or path.name == "conftest.py":
            continue
        yield path, rel


def _called_name(node: ast.Call) -> str:
    """Dotted name of a call, seeing THROUGH intermediate calls.

    ``ItemSupplier.objects.filter(...).update(...)`` has a ``Call`` in the middle
    of its attribute chain; stopping there would miss the queryset-update
    spelling entirely, so the walk unwraps a call back to its own ``func``.
    """
    parts: list[str] = []
    cur: ast.AST = node.func
    while True:
        if isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        elif isinstance(cur, ast.Call):
            cur = cur.func
        else:
            break
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _writes(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _called_name(node)
        parts = dotted.split(".")
        if not parts:
            continue
        if parts[-1] == "save" and len(parts) >= 2 and parts[-2] in _SUPPLIER_RECEIVERS:
            found.add(node.lineno)
            continue
        if "ItemSupplier" not in parts:
            continue
        if dotted.endswith("ItemSupplier"):
            found.add(node.lineno)
            continue
        if parts[-1] in _WRITE_METHODS:
            found.add(node.lineno)
    return found


def _writes_in(source: str, filename: str = "<snippet>") -> int:
    return len(_writes(ast.parse(source, filename=filename)))


def _writes_for(path: pathlib.Path) -> Optional[int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return None
    return len(_writes(tree))


def test_every_supplier_terms_writer_goes_through_the_one_owner():
    """The gate. A new hand-rolled cost write fails until it is justified here."""
    actual: dict[str, int] = {}
    for path, rel in _modules():
        count = _writes_for(path)
        if count:
            actual[rel] = count

    expected = {rel: count for rel, (count, _) in ALLOWED.items() if count}

    unexpected = {rel: n for rel, n in actual.items() if rel not in expected}
    assert not unexpected, (
        "A writer outside inventory.services.suppliers.write_supplier_terms sets "
        f"ItemSupplier rows: {unexpected}. Route it through the owner, or add it "
        "to ALLOWED with a reason saying why a partial write is safe there."
    )

    drifted = {
        rel: (expected[rel], actual.get(rel, 0))
        for rel in expected
        if actual.get(rel, 0) != expected[rel]
    }
    assert not drifted, (
        f"Allowlisted write counts drifted (expected, actual): {drifted}. "
        "Update ALLOWED and say why the new site is safe."
    )


def test_a_hand_rolled_update_or_create_is_caught():
    assert (
        _writes_in(
            "ItemSupplier.objects.update_or_create(item=i, supplier=s, defaults={'unit_cost': c})"
        )
        == 1
    )


def test_the_partial_defaults_shape_that_caused_this_is_caught():
    source = """
defaults = {"supplier_sku": sku, "unit_cost": cost}
ItemSupplier.objects.update_or_create(item=item, supplier_id=sid, defaults=defaults)
"""
    assert _writes_in(source) == 1


def test_a_direct_construction_is_caught():
    assert _writes_in("link = ItemSupplier(item=i, supplier=s)") == 1


def test_a_plain_create_is_caught():
    assert _writes_in("ItemSupplier.objects.create(item=i, supplier=s, unit_cost=c)") == 1


def test_a_queryset_update_is_caught():
    assert _writes_in("ItemSupplier.objects.filter(item=i).update(unit_cost=c)") == 1


def test_a_save_on_a_supplier_shaped_receiver_is_caught():
    assert _writes_in("item_supplier.save(update_fields=['unit_cost'])") == 1
    assert _writes_in("line_item.item_supplier.save()") == 1


def test_a_save_on_an_unrelated_receiver_is_not_a_write():
    assert _writes_in("purchase_order.save()") == 0


def test_a_generically_named_receiver_is_invisible_limit_three():
    """The honest limit: a save on a generic local is NOT seen."""
    assert _writes_in("link.save()") == 0


def test_naming_the_column_without_writing_it_is_not_a_write():
    source = """
fields = ["unit_cost", "package_cost"]
list_display = ("unit_cost_display",)
"""
    assert _writes_in(source) == 0


def test_reading_a_supplier_is_not_a_write():
    assert _writes_in("ItemSupplier.objects.filter(item=i).first()") == 0
    assert _writes_in("cost = link.unit_cost") == 0


def test_an_unrelated_model_never_trips_the_gate():
    assert _writes_in("PurchaseOrder.objects.update_or_create(defaults={'x': 1})") == 0
