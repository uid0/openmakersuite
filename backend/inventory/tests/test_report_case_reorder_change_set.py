"""``report_case_reorder_change_set``: the captain's view of what "ordering by cases" changes.

Read-only by contract. Each row names one supplier link on a legacy case-based
item, both stored columns, what the rule before 2026-09-05 ordered and
what the case rule orders instead — plus the two facts the captain asked to be
said rather than guessed: the columns disagree, or the case size is unknown.
"""

import csv
import io

from django.core.management import call_command

import pytest

from inventory.models import InventoryItem, ItemSupplier, PackagingLevel
from inventory.services.pack_size import PACK_SIZE_KNOWN, PACK_SIZE_RECORDED_ZERO
from inventory.tests.factories import InventoryItemFactory, ItemSupplierFactory
from reorder_queue.services.line_entry import default_quantity

pytestmark = pytest.mark.django_db


def _case_item(**kwargs):
    kwargs.setdefault("image", None)
    kwargs.setdefault("current_stock", 24)
    kwargs.setdefault("minimum_stock", 0)
    return InventoryItemFactory(use_case_based_reorder=True, **kwargs)


def _run():
    out, err = io.StringIO(), io.StringIO()
    call_command("report_case_reorder_change_set", stdout=out, stderr=err)
    return list(csv.DictReader(io.StringIO(out.getvalue()))), err.getvalue()


def _row(rows, item):
    matching = [row for row in rows if row["item_id"] == str(item.id)]
    assert len(matching) == 1, f"{item.name} should appear exactly once"
    return matching[0]


def test_a_known_case_size_reports_the_old_order_the_new_order_and_the_disagreement():
    """25 units rounded to the pad's 30 before; 4 cases of 10 is 40 now."""
    item = _case_item(
        name="Trash bags", reorder_cases=4, reorder_quantity=25, quantity_per_package=10
    )

    rows, _ = _run()
    row = _row(rows, item)

    assert row["item"] == "Trash bags"
    assert row["supplier"] == item.item_suppliers.get().supplier.name
    assert row["case_size_state"] == PACK_SIZE_KNOWN
    assert row["case_size"] == "10"
    assert row["reorder_quantity"] == "25"
    assert row["reorder_cases"] == "4"
    assert row["files_today"] == "25"
    assert row["po_line_today"] == "30"
    assert row["new_rule_orders"] == "40"
    assert row["change"] == "10"
    assert row["columns_disagree"] == "yes"
    assert row["finding"] == "orders_more"


def test_agreeing_columns_are_reported_unchanged():
    item = _case_item(reorder_cases=4, reorder_quantity=40, quantity_per_package=10)

    row = _row(_run()[0], item)

    assert row["columns_disagree"] == "no"
    assert row["finding"] == "unchanged"
    assert row["change"] == "0"


def test_an_order_that_shrinks_is_reported_as_orders_less():
    item = _case_item(reorder_cases=1, reorder_quantity=50, quantity_per_package=10)

    row = _row(_run()[0], item)

    assert row["new_rule_orders"] == "10"
    assert row["change"] == "-40"
    assert row["finding"] == "orders_less"


def test_an_unknown_case_size_is_reported_as_a_fact_not_given_a_number():
    item = _case_item(
        reorder_cases=3,
        reorder_quantity=7,
        quantity_per_package=0,
        current_stock=0,
        minimum_stock=100,
    )

    row = _row(_run()[0], item)

    assert row["case_size_state"] == PACK_SIZE_RECORDED_ZERO
    assert row["case_size"] == ""
    assert row["new_rule_orders"] == row["files_today"] == "100"
    assert row["columns_disagree"] == "unknown"
    assert row["finding"] == "case_size_unknown"


def test_a_known_case_size_rounds_the_shortage_up_to_whole_cases():
    item = _case_item(
        reorder_cases=2,
        reorder_quantity=5,
        quantity_per_package=10,
        current_stock=0,
        minimum_stock=95,
    )

    row = _row(_run()[0], item)

    assert row["files_today"] == "95"
    assert row["po_line_today"] == "100"
    assert row["new_rule_orders"] == "100"


def test_an_item_with_no_supplier_link_is_listed_with_a_blank_supplier():
    item = _case_item(reorder_cases=3, reorder_quantity=7)
    item.item_suppliers.all().delete()

    row = _row(_run()[0], item)

    assert row["supplier"] == ""
    assert row["currently_selected"] == "no"
    assert row["finding"] == "case_size_unknown"


def test_every_supplier_link_matches_live_line_sizing():
    item = _case_item(reorder_cases=4, reorder_quantity=25, quantity_per_package=10)
    selected = item.item_suppliers.get()
    links = [
        selected,
        ItemSupplierFactory(item=item, is_primary=False, quantity_per_package=12),
        ItemSupplierFactory(item=item, is_primary=False, quantity_per_package=6),
        ItemSupplierFactory(item=item, is_primary=False, quantity_per_package=1),
        ItemSupplierFactory(item=item, is_primary=False, quantity_per_package=0),
    ]

    rows, _ = _run()
    matching = [row for row in rows if row["item_id"] == str(item.id)]

    assert len(matching) == len(links)
    by_supplier = {row["supplier"]: row for row in matching}
    for link in links:
        row = by_supplier[link.supplier.name]
        assert int(row["new_rule_orders"]) == default_quantity(link)
        assert row["currently_selected"] == ("yes" if link == selected else "no")

    assert [default_quantity(link) for link in links] == [40, 48, 42, 40, 40]


def test_items_the_case_rule_does_not_govern_are_not_listed():
    """Plain items never; bridged items only in the summary count."""
    plain = InventoryItemFactory(image=None, reorder_cases=4, quantity_per_package=10)
    bridged = _case_item(reorder_cases=4, reorder_quantity=3)
    case = PackagingLevel.objects.create(item=bridged, name="case", sort_order=0, base_units=12)
    PackagingLevel.objects.create(item=bridged, name="unit", sort_order=1, base_units=1)
    bridged.count_mode = InventoryItem.CountMode.BY_LEVEL
    bridged.count_level = case
    bridged.save(update_fields=["count_mode", "count_level"])

    rows, summary = _run()

    listed = {row["item_id"] for row in rows}
    assert str(plain.id) not in listed
    assert str(bridged.id) not in listed
    assert "1 bridged item(s) skipped" in summary


def test_the_summary_counts_findings_and_disagreements():
    _case_item(reorder_cases=4, reorder_quantity=25, quantity_per_package=10)
    _case_item(reorder_cases=4, reorder_quantity=40, quantity_per_package=10)
    _case_item(reorder_cases=4, reorder_quantity=40, quantity_per_package=0)

    _, summary = _run()

    assert "3 case-based item(s) governed by the case rule; 3 supplier-link row(s)." in summary
    assert "  case_size_unknown: 1" in summary
    assert "  orders_more: 1" in summary
    assert "  unchanged: 1" in summary
    assert "reorder_quantity disagrees with reorder_cases x case size: 1" in summary


def test_it_writes_nothing():
    item = _case_item(reorder_cases=4, reorder_quantity=25, quantity_per_package=10)
    before_item = InventoryItem.objects.filter(pk=item.pk).values().get()
    before_links = list(ItemSupplier.objects.filter(item=item).values())

    _run()

    assert InventoryItem.objects.filter(pk=item.pk).values().get() == before_item
    assert list(ItemSupplier.objects.filter(item=item).values()) == before_links
