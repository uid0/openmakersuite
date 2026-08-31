"""A reader that bypasses the pack-size derivation fails the build (op-c1ke).

"How many base units are in one package" has ONE owner,
:mod:`inventory.services.pack_size`. Before it there were seven independent
readers of ``ItemSupplier.quantity_per_package`` across three apps, several
carrying their own ``or 1``, and they disagreed on the same item — which is how
a case-based item at a tenth of its reorder point stopped being flagged.

The lesson op-2rsp's four reverted rounds recorded is that "how many places read
this?" must be DERIVED, never recalled. This test derives it: it walks every
non-test, non-migration module under ``backend/`` with the AST and counts real
READS of the column. A new one anywhere — a new module, or one more in a module
already listed — fails until it either goes through the derivation or is added
here deliberately, with a reason.

**What counts as a read**, and why the set is wider than attribute access. A
derivation can reach this column from Python or from SQL, and ``low_stock_q``
proves this codebase does write SQL twins of exactly that kind. So the scan
catches all of:

* ``link.quantity_per_package`` — attribute access, in LOAD context only. A
  Store (``self.quantity_per_package = x``) is a WRITE: it cannot turn "we do
  not know" into a number, which is the whole failure mode.
* ``getattr(link, "quantity_per_package")`` — the same read, spelled dynamically.
* the column named inside a read-only ORM call — ``values``, ``values_list``,
  ``only``, ``defer``, ``annotate``, ``aggregate``, ``order_by``, ``filter``,
  ``exclude`` — whether as a string, a keyword, a ``"-"``-prefixed ordering, a
  ``__`` lookup path such as ``"item_suppliers__quantity_per_package"``, or
  wrapped in ``F()`` / an aggregate. Row-writing calls (``create``, ``update``,
  ``update_or_create``) are deliberately not in that set.

Bare string occurrences OUTSIDE those calls (serializer ``fields`` lists, admin
columns, help text, docstrings, and the write path in ``InventoryItemViewSet``,
which addresses the column by name through ``request.data`` and
``_meta.get_field``) are NOT counted: naming the field cannot fabricate a
number, and counting prose would make every doc edit a failure.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Optional

COLUMN = "quantity_per_package"

BACKEND = pathlib.Path(__file__).resolve().parents[2]

#: Reads of the column that are NOT a pack-size derivation, with why. Every
#: entry is either the column's own definition, a verbatim copy of what a row
#: records, or a write path — none of them turn "we do not know" into a number.
#: A DERIVATION belongs in ``inventory.services.pack_size`` instead.
ALLOWED: dict[str, tuple[int, str]] = {
    "inventory/services/pack_size.py": (
        1,
        "THE OWNER — the single place the column is interpreted, and the only "
        "code that decides between KNOWN, NOT_RECORDED and RECORDED_ZERO.",
    ),
    "inventory/models/core.py": (
        6,
        "ItemSupplier's own arithmetic on its OWN row: unit_weight, and the "
        "unit_cost/package_cost derivation in save(). All six guard on '> 0' "
        "explicitly rather than for truthiness, so a RECORDED_ZERO takes its own "
        "branch and is never read as one-unit-per-package. No item-level answer "
        "is produced here — nothing crosses from a row to 'how many units are in "
        "this item's box', which is the question pack_size owns.",
    ),
    "inventory/services/suppliers.py": (
        3,
        "Copies the recorded value verbatim into PriceHistory and compares it to "
        "the previous row. A snapshot of what was recorded, not a reading of it: "
        "a NOT_RECORDED or RECORDED_ZERO row is snapshotted as what it says.",
    ),
    "reorder_queue/services/line_entry.py": (
        1,
        "Reports the recorded value verbatim on the line-entry candidate payload "
        "— what the row says, including a zero. The rounding beside it asks the "
        "KNOWN/case question through declares_a_case.",
    ),
    "reorder_queue/views.py": (
        2,
        "Reports the recorded value verbatim on the order-pad and recommendation "
        "payloads — what the row says, including a zero. The rounding beside both "
        "asks the KNOWN/case question through declares_a_case.",
    ),
}

#: Directories whose modules are never scanned. The virtualenv names matter:
#: ``backend/.venv`` is an expected in-tree location here (CI prunes it by name,
#: and ``.gitignore``'s ``venv/`` / ``env/`` match at any depth), and walking a
#: contributor's site-packages would cost tens of thousands of parses to answer
#: a question about THIS codebase.
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
#: column in SQL, which is exactly how a future derivation would bypass a
#: Python-only scan. Row-writing calls are deliberately absent.
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

#: Expression wrappers that name a column directly, e.g. ``F("...__column")``.
_EXPRESSION_CALLS = frozenset({"F"})


def _modules():
    """Every non-test, non-migration Python module under ``backend/``."""
    for path in sorted(BACKEND.rglob("*.py")):
        if _SKIP_PARTS & set(path.parts):
            continue
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            continue
        yield path


def _names_column(text: str) -> bool:
    """True when an ORM field reference resolves to this column.

    Handles the plain name, an ordering's ``-`` prefix, a join path
    (``item_suppliers__quantity_per_package``) and a lookup suffix
    (``quantity_per_package__gt``).
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
    """Node ids of every site that READS the column, deduped by identity.

    Deduping by ``id`` is what makes chained querysets safe to scan: the
    subtree of ``qs.filter(...).values(COLUMN)`` is visited from both calls,
    and the same string node is recorded once.
    """
    found: set[int] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == COLUMN:
            # LOAD only. ``self.quantity_per_package = x`` is a write.
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
                elif isinstance(inner, ast.keyword) and inner.arg and _names_column(inner.arg):
                    found.add(id(inner))

    return found


def _column_reads_in(source: str, filename: str = "<snippet>") -> int:
    """How many sites in ``source`` read the column."""
    return len(_reads(ast.parse(source, filename=filename)))


def _column_reads(path: pathlib.Path) -> Optional[int]:
    """How many sites in ``path`` read the column, or ``None`` if unreadable.

    A file this Python cannot decode as UTF-8 or cannot parse is SKIPPED rather
    than raised through: an unparsable module is not a pack-size reader, and
    failing the gate on one would report a decoding problem under a message
    about ``quantity_per_package``, pointing a future author at neither.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    try:
        return _column_reads_in(source, filename=str(path))
    except SyntaxError:
        return None


def test_every_pack_size_reader_goes_through_the_one_derivation():
    found = {}
    for path in _modules():
        count = _column_reads(path)
        if count:
            found[str(path.relative_to(BACKEND))] = count
    assert found, (
        "The scan found no reads of "
        f"ItemSupplier.{COLUMN} anywhere under {BACKEND} — not even the owner. "
        "The walk itself is broken (a bad _SKIP_PARTS entry, or a moved tree), "
        "so this gate is passing vacuously rather than guarding "
        "inventory.services.pack_size."
    )

    expected = {name: count for name, (count, _reason) in ALLOWED.items()}

    new_readers = {k: v for k, v in found.items() if k not in expected}
    assert not new_readers, (
        f"{sorted(new_readers)} reads ItemSupplier.{COLUMN} directly. "
        "'How many units are in a box' has ONE owner: read it through "
        "inventory.services.pack_size (pack_size_of / shelf_pack_size / "
        "order_pack_size / declares_a_case), which keeps a KNOWN pack size, a "
        "pack size nobody recorded, and a recorded ZERO apart. A guard spelled "
        "'or 1' cannot, and that collapse is what suppressed the low-stock "
        "alert this derivation exists to close."
    )

    assert found == expected, (
        "The set of direct reads of ItemSupplier."
        f"{COLUMN} changed: {found} != {expected}. If you added a derivation, "
        "route it through inventory.services.pack_size instead. If you genuinely "
        "added a verbatim copy or a write path, update ALLOWED here with the "
        "reason — and say which of the three pack-size states it handles."
    )


# ── The detector itself, exercised on constructed sources ────────────────────
#
# A gate that cannot see the bypass it was built to catch passes vacuously, so
# each shape below is run through ``_column_reads_in`` as an ordinary function
# with test-owned input. None of these assert anything about the repository's
# own source.


def test_a_plain_attribute_read_is_caught():
    assert _column_reads_in("units = link.quantity_per_package") == 1


def test_assigning_the_column_is_a_write_not_a_read():
    """``self.quantity_per_package = x`` cannot turn an unknown into a number."""
    assert _column_reads_in("self.quantity_per_package = 5") == 0
    assert _column_reads_in("obj.quantity_per_package, obj.other = 5, 6") == 0


def test_a_dynamic_read_is_caught():
    assert _column_reads_in('units = getattr(link, "quantity_per_package")') == 1


def test_a_sql_side_derivation_cannot_slip_past():
    """The shapes a future ``low_stock_q``-style twin would actually take."""
    assert _column_reads_in('ItemSupplier.objects.values("quantity_per_package")') == 1
    assert _column_reads_in('qs.only("quantity_per_package")') == 1
    assert _column_reads_in("qs.filter(quantity_per_package__gt=0)") == 1
    assert _column_reads_in("qs.exclude(quantity_per_package=0)") == 1
    assert _column_reads_in('qs.order_by("-quantity_per_package")') == 1
    assert _column_reads_in('qs.annotate(n=Sum("quantity_per_package"))') == 1
    assert _column_reads_in('qs.annotate(n=F("item_suppliers__quantity_per_package"))') == 1
    assert (
        _column_reads_in('Item.objects.filter(cases=F("item_suppliers__quantity_per_package"))')
        == 1
    )


def test_a_chained_queryset_counts_each_site_once():
    """Chained calls re-walk the same subtree; identity dedupes it."""
    assert (
        _column_reads_in('qs.filter(quantity_per_package__gt=0).values("quantity_per_package")')
        == 2
    )
    assert _column_reads_in('qs.filter(quantity_per_package__gt=0).order_by("name")') == 1


def test_naming_the_field_without_reading_it_is_not_a_read():
    """Serializer field lists, admin columns and prose must not trip the gate."""
    assert _column_reads_in('fields = ["quantity_per_package", "unit_cost"]') == 0
    assert _column_reads_in('"""How many units are in a quantity_per_package."""') == 0
    assert _column_reads_in("ItemSupplier.objects.create(quantity_per_package=5)") == 0
    assert _column_reads_in('link.save(update_fields=["quantity_per_package"])') == 0


def test_an_unrelated_column_never_trips_the_gate():
    assert _column_reads_in('qs.values("quantity_per_pallet")') == 0
    assert _column_reads_in("units = link.package_cost") == 0
