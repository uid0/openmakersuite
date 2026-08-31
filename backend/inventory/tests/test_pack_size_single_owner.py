"""A reader that bypasses the pack-size derivation fails the build (op-c1ke).

"How many base units are in one package" has ONE owner,
:mod:`inventory.services.pack_size`. Before it there were seven independent
readers of ``ItemSupplier.quantity_per_package`` across three apps, several
carrying their own ``or 1``, and they disagreed on the same item — which is how
a case-based item at a tenth of its reorder point stopped being flagged.

The lesson op-2rsp's four reverted rounds recorded is that "how many places read
this?" must be DERIVED, never recalled. This test derives it: it walks every
non-test, non-migration module under ``backend/`` with the AST and counts real
attribute reads of the column. A new one anywhere — a new module, or one more in
a module already listed — fails until it either goes through the derivation or
is added here deliberately, with a reason.

String occurrences (serializer ``fields`` lists, admin columns, help text,
docstrings, and the write path in ``InventoryItemViewSet``, which addresses the
column by name through ``request.data`` and ``_meta.get_field``) are NOT
counted: naming the field cannot fabricate a number, and counting prose would
make every doc edit a failure.
"""

from __future__ import annotations

import ast
import pathlib

COLUMN = "quantity_per_package"

BACKEND = pathlib.Path(__file__).resolve().parents[2]

#: Reads of the column that are NOT a pack-size derivation, with why. Every
#: entry is either the column's own definition, a verbatim copy of what a row
#: records, or a write path — none of them turn "we do not know" into a number.
#: A DERIVATION belongs in ``inventory.services.pack_size`` instead.
ALLOWED: dict[str, tuple[int, str]] = {
    "inventory/services/pack_size.py": (
        1,
        "THE OWNER — the single place the column is interpreted.",
    ),
    "inventory/models/core.py": (
        6,
        "The column's own definition plus ItemSupplier's local arithmetic on its "
        "OWN row (unit_weight, the unit_cost/package_cost derivation in save). "
        "Each already tests '> 0' explicitly rather than for truthiness.",
    ),
    "inventory/services/suppliers.py": (
        3,
        "Copies the recorded value verbatim into PriceHistory and compares it to "
        "the previous row. A snapshot of what was recorded, not a reading of it.",
    ),
    "reorder_queue/services/line_entry.py": (
        1,
        "Reports the recorded value verbatim on the line-entry candidate payload. "
        "The rounding beside it goes through declares_a_case.",
    ),
    "reorder_queue/views.py": (
        2,
        "Reports the recorded value verbatim on the order-pad and recommendation "
        "payloads. The rounding beside both goes through declares_a_case.",
    ),
}

#: Directories whose modules are never scanned.
_SKIP_PARTS = {"migrations", "tests", "__pycache__", "node_modules", "staticfiles"}


def _modules():
    """Every non-test, non-migration Python module under ``backend/``."""
    for path in sorted(BACKEND.rglob("*.py")):
        if _SKIP_PARTS & set(path.parts):
            continue
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            continue
        yield path


def _column_reads(path: pathlib.Path) -> int:
    """Attribute accesses of ``x.quantity_per_package`` in ``path``."""
    tree = ast.parse(path.read_text(), filename=str(path))
    return sum(
        1 for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == COLUMN
    )


def test_every_pack_size_reader_goes_through_the_one_derivation():
    found = {}
    for path in _modules():
        count = _column_reads(path)
        if count:
            found[str(path.relative_to(BACKEND))] = count

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


def test_the_allowlist_explains_itself():
    """Every exemption carries a reason a reviewer can check."""
    for name, (count, reason) in ALLOWED.items():
        assert count >= 1, name
        assert len(reason) > 40, f"{name} needs a real reason, not a label"


def test_the_owner_reads_the_column_exactly_once():
    """One interpretation, in one function — that is what "single owner" means."""
    owner = BACKEND / "inventory" / "services" / "pack_size.py"

    assert _column_reads(owner) == 1
