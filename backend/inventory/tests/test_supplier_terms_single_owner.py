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
   (``item_supplier``, ``supplier_link`` …). A save on a differently-named local
   is invisible. This is the loosest of the rules and is why the allowlist
   reasons matter more than the count.
4. A FRAMEWORK writer has no write CALL in it at all — DRF's
   ``ModelSerializer.update`` and Django admin's ``ModelForm`` both do the
   ``setattr`` + ``save()`` themselves, inside the framework. Review found
   exactly that blind spot twice: first the generic ``/item-suppliers/``
   endpoint, then the Django admin. The scan therefore counts all three ways
   this model gets bound to a framework class as write sites — ``model =
   ItemSupplier`` inside a ``class Meta``, the same assignment at CLASS level
   (``ItemSupplierInline(admin.TabularInline)``), and an
   ``@admin.register(ItemSupplier)`` decorator. What it still cannot see: a
   class that reaches the model through a variable or an import alias rather
   than naming it, a ``ModelForm`` declared outside an admin or serializer, and
   any framework that writes the table with no Python-visible model reference
   at all.

**What counts as a write** is deliberately broad, because the defect is a
partial write rather than any particular spelling:

* ``ItemSupplier(...)`` — direct construction.
* ``ItemSupplier.objects.create`` / ``update_or_create`` / ``get_or_create`` /
  ``update`` / ``bulk_create`` / ``bulk_update``.
* ``<supplier-ish>.save(...)``.
* ``model = ItemSupplier`` bound to a framework class — in a ``class Meta``, at
  class level on an admin inline, or via ``@admin.register(ItemSupplier)``.
  Each writes the row through the framework rather than through a call this
  scan can see.

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
        "construction every pair-resolving caller delegates to (its "
        "row-addressed twin `update_supplier_terms` never creates); "
        "`enforce_single_primary` bulk-updates `is_primary` only, which is a "
        "flag rather than a cost.",
    ),
    "inventory/serializers.py": (
        1,
        "`ItemSupplierSerializer` — a `ModelSerializer` on this model, which is "
        "a framework writer (limit 4). Its `create`/`update` are overridden to "
        "delegate to `write_supplier_terms`, so DRF's own partial "
        "`setattr` + `save()` never runs. Allowlisted because it IS routed, not "
        "because a partial write would be safe here — it demonstrably is not.",
    ),
    "inventory/admin.py": (
        2,
        "The Django admin's two bindings — `ItemSupplierInline`'s class-level "
        "`model =` and `@admin.register(ItemSupplier)`. Both write through a "
        "ModelForm, but MEASURED, neither can fight the derivation: `unit_cost` "
        "is in `ItemSupplierAdmin.readonly_fields` and the inline shows the "
        "read-only `unit_cost_display`, so only `package_cost` and "
        "`quantity_per_package` are editable — the direction `save()` prefers "
        "anyway. Allowlisted as safe, not as routed; an admin that made "
        "`unit_cost` editable would need routing.",
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


def _assigns_model(body) -> bool:
    for stmt in body:
        if not isinstance(stmt, ast.Assign):
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name) and target.id == "model":
                value = stmt.value
                name = (
                    value.attr if isinstance(value, ast.Attribute) else getattr(value, "id", None)
                )
                if name == "ItemSupplier":
                    return True
    return False


def _registers_model(node: ast.ClassDef) -> bool:
    """``@admin.register(ItemSupplier)`` — the admin's own binding."""
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        if not _called_name(decorator).endswith("register"):
            continue
        for arg in decorator.args:
            name = arg.attr if isinstance(arg, ast.Attribute) else getattr(arg, "id", None)
            if name == "ItemSupplier":
                return True
    return False


def _binds_model(node: ast.AST) -> bool:
    """This class is bound to ItemSupplier by a framework — so it writes the row.

    Three shapes, because the two frameworks in this tree spell it three ways:
    a serializer's ``class Meta``, an admin inline's CLASS-level ``model =``,
    and an ``@admin.register`` decorator.
    """
    if not isinstance(node, ast.ClassDef):
        return False
    if node.name == "Meta":
        return _assigns_model(node.body)
    return _registers_model(node) or _assigns_model(node.body)


def _writes(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if _binds_model(node):
            found.add(node.lineno)
            continue
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
        "A writer outside inventory.services.suppliers sets "
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


def test_a_model_serializer_on_this_model_is_a_writer():
    """Limit 4: DRF writes the row itself, so there is no call to catch."""
    source = """
class ItemSupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemSupplier
        fields = ["unit_cost", "package_cost"]
"""
    assert _writes_in(source) == 1


def test_a_model_serializer_on_another_model_is_not_a_writer():
    source = """
class PurchaseOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseOrder
        fields = ["unit_cost"]
"""
    assert _writes_in(source) == 0


def test_a_class_level_model_binding_is_a_writer():
    """Limit 4, the admin inline shape: no `class Meta`, no write call."""
    source = """
class ItemSupplierInline(admin.TabularInline):
    model = ItemSupplier
    fields = ["package_cost"]
"""
    assert _writes_in(source) == 1


def test_an_admin_register_decorator_is_a_writer():
    source = """
@admin.register(ItemSupplier)
class ItemSupplierAdmin(admin.ModelAdmin):
    list_display = ["package_cost"]
"""
    assert _writes_in(source) == 1


def test_registering_another_model_is_not_a_writer():
    source = """
@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ["unit_cost"]
"""
    assert _writes_in(source) == 0
