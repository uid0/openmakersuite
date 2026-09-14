"""List what "ordering by cases" changes for every legacy case-based item. READ-ONLY.

Captain's decision, 2026-09-05: "We are ordering by cases and counting by
items." A legacy ``use_case_based_reorder`` item that is not counted in packs
now orders enough whole cases to cover both ``reorder_cases`` and the current
shortage (:func:`inventory.services.packaging.case_order`). When the order pack
size is unknown it keeps the pre-change shortage calculation. Before that
decision every filing path used that calculation while every screen showed
``reorder_cases``.

That changes ordered quantities on live data, so this command prints the change
set — one CSV row per supplier link on every governed item — for the captain to
read before it takes effect. An item with no supplier link gets one row with a
blank link. It writes nothing: no
stored column is rewritten, and it is not a data migration.

Columns:

* ``supplier`` / ``case_size`` / ``case_size_state`` — each link and the pack
  size it records, in
  :mod:`inventory.services.pack_size`'s vocabulary. ``case_size`` is blank when
  unknown.
* ``currently_selected`` — whether the ordering path currently selects this
  link through ``primary_item_supplier`` / ``order_pack_size``.
* ``reorder_quantity`` / ``reorder_cases`` — the two stored columns, as stored.
* ``files_today`` — what a filing path (QR scan, maintenance alert, fresh PO
  line before rounding) ordered under the rule before the decision:
  ``max(minimum_stock - current_stock, reorder_quantity)`` base units.
* ``po_line_today`` — ``files_today`` rounded up to whole cases of that link, as
  the purchase-order pad rounds it.
* ``new_rule_orders`` — what the case rule orders, in base units. Already whole
  cases when the size is known, including enough whole cases to cover the
  shortage, so the pad's rounding leaves it alone. When the size is unknown it
  is the unchanged pre-rule quantity, shortage term included.
* ``change`` — ``new_rule_orders - po_line_today``.
* ``columns_disagree`` — ``yes`` when ``reorder_quantity`` names a different
  base-unit amount from ``reorder_cases × case_size``; ``unknown`` when the case
  size is unknown and there is nothing to compare against.
* ``finding`` — ``orders_more`` / ``orders_less`` / ``unchanged``, or
  ``case_size_unknown``: the item CANNOT be ordered by the case under the stated
  rule and keeps ordering as before until its supplier link records a pack size.
  That is a fact for the operator, not a number to invent.

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
from inventory.services.pack_size import declares_a_case, pack_size_of
from inventory.services.packaging import CaseOrder, case_order, counts_in_packs
from inventory.services.supplier_selection import item_suppliers_prefetch

COLUMNS = [
    "item_id",
    "item",
    "sku",
    "is_active",
    "is_retired",
    "supplier",
    "currently_selected",
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


def change_set_row(item: InventoryItem, link, selected_link) -> dict:
    """The report row for one supplier link on a governed item."""
    case = CaseOrder(
        reorder_cases=item.reorder_cases,
        reorder_quantity=item.reorder_quantity,
        minimum_stock=item.minimum_stock,
        current_stock=item.current_stock,
        pack=pack_size_of(link),
    )

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

    return {
        "item_id": str(item.id),
        "item": item.name,
        "sku": item.sku,
        "is_active": "yes" if item.is_active else "no",
        "is_retired": "yes" if item.is_retired else "no",
        "supplier": link.supplier.name if link is not None else "",
        "currently_selected": (
            "yes"
            if link is not None
            and selected_link is not None
            and link.pk == selected_link.pk
            else "no"
        ),
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
        governed_items = 0
        for item in items:
            selected_case = case_order(item)
            if selected_case is None:
                if counts_in_packs(item):
                    bridged += 1
                continue
            governed_items += 1
            selected_link = selected_case.pack.link
            links = list(item.item_suppliers.all()) or [None]
            for link in links:
                row = change_set_row(item, link, selected_link)
                writer.writerow(row)
                findings[row["finding"]] = findings.get(row["finding"], 0) + 1
                if row["columns_disagree"] == "yes":
                    disagreements += 1

        total = sum(findings.values())
        self.stderr.write(
            f"{governed_items} case-based item(s) governed by the case rule; "
            f"{total} supplier-link row(s)."
        )
        for finding, count in sorted(findings.items()):
            self.stderr.write(f"  {finding}: {count}")
        self.stderr.write(
            f"  reorder_quantity disagrees with reorder_cases x case size: {disagreements}"
        )
        self.stderr.write(
            f"{bridged} bridged item(s) skipped: counted in packs, the case rule does not apply."
        )
        self.stderr.write("Read-only: nothing was written.")
