"""List what "ordering by cases" changes for every legacy case-based item. READ-ONLY.

Captain's decision, 2026-09-05: "We are ordering by cases and counting by
items." A legacy ``use_case_based_reorder`` item that is not counted in packs
now orders ``reorder_cases × order_pack_size`` base units
(:func:`inventory.services.packaging.case_order`), falling back to
``reorder_quantity`` when the order pack size is unknown. Before that decision
every filing path ordered ``reorder_quantity`` for it while every screen showed
``reorder_cases``.

That changes ordered quantities on live data, so this command prints the change
set — one CSV row per governed item, named by the supplier link that sizes its
order — for the captain to read before it takes effect. It writes nothing: no
stored column is rewritten, and it is not a data migration.

Columns:

* ``supplier`` / ``case_size`` / ``case_size_state`` — the link the next order
  goes through and the pack size it records, in
  :mod:`inventory.services.pack_size`'s vocabulary. ``case_size`` is blank when
  unknown.
* ``reorder_quantity`` / ``reorder_cases`` — the two stored columns, as stored.
* ``files_today`` — what a filing path (QR scan, maintenance alert, fresh PO
  line before rounding) ordered under the rule before the decision:
  ``max(minimum_stock - current_stock, reorder_quantity)`` base units.
* ``po_line_today`` — ``files_today`` rounded up to whole cases of that link, as
  the purchase-order pad rounds it.
* ``new_rule_orders`` — what the case rule orders, in base units. Already whole
  cases, so the pad's rounding leaves it alone.
* ``change`` — ``new_rule_orders - po_line_today``.
* ``columns_disagree`` — ``yes`` when ``reorder_quantity`` names a different
  base-unit amount from ``reorder_cases × case_size``; ``unknown`` when the case
  size is unknown and there is nothing to compare against.
* ``finding`` — ``orders_more`` / ``orders_less`` / ``unchanged``, or
  ``case_size_unknown``: the item CANNOT be ordered by the case under the stated
  rule and keeps ordering ``reorder_quantity`` until its supplier link records a
  pack size. That is a fact for the operator, not a number to invent.

Bridged items — the legacy flag plus a packaging chain — are counted in packs,
are not governed by the case rule, and are only counted in the summary.

Usage::

    python manage.py report_case_reorder_change_set > case-reorder-change-set.csv

The summary goes to stderr so the CSV on stdout stays clean.
"""

from __future__ import annotations

import csv

from django.core.management.base import BaseCommand

from inventory.models import InventoryItem
from inventory.services.pack_size import declares_a_case
from inventory.services.packaging import case_order, counts_in_packs
from inventory.services.supplier_selection import item_suppliers_prefetch

COLUMNS = [
    "item_id",
    "item",
    "sku",
    "is_active",
    "is_retired",
    "supplier",
    "case_size_state",
    "case_size",
    "reorder_quantity",
    "reorder_cases",
    "files_today",
    "po_line_today",
    "new_rule_orders",
    "change",
    "columns_disagree",
    "finding",
]

FINDING_CASE_SIZE_UNKNOWN = "case_size_unknown"


def files_before_the_case_rule(item: InventoryItem) -> int:
    """What ``base_reorder_quantity`` returned for an unpacked item before 2026-09-05.

    Spelled out rather than called, because after the change ships the live
    function answers with the new rule and this report must still name the old
    one.
    """
    shortage = max(0, item.minimum_stock - item.current_stock)
    return max(shortage, item.reorder_quantity)


def change_set_row(item: InventoryItem) -> dict | None:
    """The report row for ``item``, or ``None`` when the case rule does not govern it."""
    case = case_order(item)
    if case is None:
        return None

    files_today = files_before_the_case_rule(item)
    declared = declares_a_case(case.pack.link)
    po_line_today = files_today if declared is None else -(-files_today // declared) * declared

    if case.quantity is None:
        new_rule_orders = files_today
        finding = FINDING_CASE_SIZE_UNKNOWN
        disagree = "unknown"
    else:
        new_rule_orders = case.quantity
        change = new_rule_orders - po_line_today
        finding = "orders_more" if change > 0 else "orders_less" if change < 0 else "unchanged"
        disagree = "yes" if case.columns_disagree else "no"

    link = case.pack.link
    return {
        "item_id": str(item.id),
        "item": item.name,
        "sku": item.sku,
        "is_active": "yes" if item.is_active else "no",
        "is_retired": "yes" if item.is_retired else "no",
        "supplier": link.supplier.name if link is not None else "",
        "case_size_state": case.pack.state,
        "case_size": "" if case.pack.units is None else case.pack.units,
        "reorder_quantity": item.reorder_quantity,
        "reorder_cases": item.reorder_cases,
        "files_today": files_today,
        "po_line_today": po_line_today,
        "new_rule_orders": new_rule_orders,
        "change": new_rule_orders - po_line_today,
        "columns_disagree": disagree,
        "finding": finding,
    }


class Command(BaseCommand):
    help = (
        "READ-ONLY: list every legacy case-based item whose order the 'order by "
        "cases' rule changes, as CSV."
    )

    def handle(self, *args, **options):
        items = (
            InventoryItem.objects.filter(use_case_based_reorder=True)
            .select_related("count_level")
            .prefetch_related(item_suppliers_prefetch())
            .order_by("name", "id")
        )

        writer = csv.DictWriter(self.stdout, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()

        findings: dict[str, int] = {}
        disagreements = 0
        bridged = 0
        for item in items:
            row = change_set_row(item)
            if row is None:
                if counts_in_packs(item):
                    bridged += 1
                continue
            writer.writerow(row)
            findings[row["finding"]] = findings.get(row["finding"], 0) + 1
            if row["columns_disagree"] == "yes":
                disagreements += 1

        total = sum(findings.values())
        self.stderr.write(f"{total} case-based item(s) governed by the case rule.")
        for finding, count in sorted(findings.items()):
            self.stderr.write(f"  {finding}: {count}")
        self.stderr.write(
            f"  reorder_quantity disagrees with reorder_cases x case size: {disagreements}"
        )
        self.stderr.write(
            f"{bridged} bridged item(s) skipped: counted in packs, the case rule does not apply."
        )
        self.stderr.write("Read-only: nothing was written.")
