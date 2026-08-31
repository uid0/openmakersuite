"""A price we do not know must never be presented, summed or compared (op-9m2v).

The rule, in one sentence: **a price the system does not know must never be
presented, summed, or compared as a real number; a recorded price of zero is a
KNOWN price and must be treated as one.**

The money sibling of ``test_alert_suppression.py``. Same discipline, different
fact: that class inverted a boolean and hid an alert, this one distorts money.
The branch invariant these pin: *no money figure changes versus base EXCEPT
where base was presenting an unknown price as a real number.* Every test is
labelled BEFORE/AFTER where a figure moves and CONTROL where it must not.

**Both halves of the sentence had failures**, and they point in opposite
directions, which is why every site needed both a BEFORE/AFTER and a CONTROL:

* ``unit_cost or 0`` costed an UNPRICED line at nothing and summed it. An order
  read as cheaper than it was, with nothing on the payload to say a line had
  been costed at zero.
* ``if unit_cost:`` read a supplier that charges NOTHING as one with no price on
  file. A makerspace runs on donated stock, free samples and internal
  transfers, so that population is real and not rare.

``or`` cannot tell the two apart. That is why the derivation is spelled
``is None`` and why ``test_price_single_owner.py`` fails the build on a new
reader that is not.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from inventory.models import InventoryItem, ItemSupplier, PriceHistory, Supplier
from inventory.services.pricing import (
    PRICE_KNOWN,
    PRICE_NO_ORDERABLE_LINK,
    PRICE_NO_SUPPLIER_LINK,
    PRICE_NOT_RECORDED,
    PriceRollup,
    explain,
    extended,
    lowest_unit_price,
    order_unit_price,
    package_price_of,
    unit_price_of,
)
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.services import line_entry

pytestmark = pytest.mark.django_db

User = get_user_model()

OPTIMIZED_URL = "/api/reorders/purchase-orders/create_optimized_order/"
REORDER_DATA_URL = "/api/reorders/purchase-orders/reorder_data/"
PRICE_TRENDS_URL = "/api/reorders/reports/purchasing/price_trends/"
STOCK_BY_CATEGORY_URL = "/api/inventory/reports/inventory/stock_by_category/"
VALUE_BY_LOCATION_URL = "/api/inventory/reports/inventory/value_by_location/"
INVENTORY_EXPORT_URL = "/api/inventory/reports/inventory/export/"
PURCHASING_EXPORT_URL = "/api/reorders/reports/purchasing/export/"
BY_SUPPLIER_URL = "/api/reorders/requests/by_supplier/"
DASHBOARD_SUMMARY_URL = "/api/dashboard/inventory-summary/"
TRANSPARENCY_URL = "/api/reorders/analytics/transparency/"


def _item(name="Widget", **kwargs):
    defaults = dict(
        name=name,
        description="x",
        sku=f"SKU-{name}",
        reorder_quantity=5,
        current_stock=0,
        minimum_stock=10,
        is_active=True,
    )
    defaults.update(kwargs)
    return InventoryItem.objects.create(**defaults)


def _link(item, name, *, unit_cost="1.00", package_cost=None, pack=1, **flags):
    """One supplier link. ``unit_cost=None`` means NO price recorded."""
    return ItemSupplier.objects.create(
        item=item,
        supplier=Supplier.objects.create(name=name, supplier_type=Supplier.SupplierType.LOCAL),
        supplier_sku=f"{name}-sku",
        unit_cost=None if unit_cost is None else Decimal(unit_cost),
        package_cost=None if package_cost is None else Decimal(package_cost),
        quantity_per_package=pack,
        average_lead_time=7,
        is_primary=flags.get("is_primary", False),
        is_active=flags.get("is_active", True),
        is_discontinued=flags.get("is_discontinued", False),
    )


def _fresh(item):
    return InventoryItem.objects.get(pk=item.pk)


@pytest.fixture
def api():
    client = APIClient()
    client.force_authenticate(
        user=User.objects.create_user(
            username="purchaser", password="pw", is_staff=True, is_superuser=True
        )
    )
    return client


def _line_for(recommendations, item):
    for rec in recommendations:
        for line in rec["items"]:
            if line["item_id"] == item.id:
                return rec, line
    raise AssertionError(f"{item.name} is not in the recommendations")


def _pad_line_for(suppliers, item):
    for group in suppliers:
        for line in group["items"]:
            if line["item_id"] == str(item.id):
                return group, line
    raise AssertionError(f"{item.name} is not on the order pad")


# ── The derivation itself: four states, and a zero that is a price ───────────


def test_a_recorded_zero_is_a_known_price():
    """The load-bearing judgement. ``or`` cannot express this, which is the point."""
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00")

    price = unit_price_of(link)
    assert price.is_known
    assert bool(price) is True
    assert price.amount == Decimal("0.00")
    assert price.state == PRICE_KNOWN


def test_a_null_price_is_not_recorded_and_is_not_a_number():
    item = _item("Mystery")
    link = _link(item, "Acme", unit_cost=None)

    price = unit_price_of(link)
    assert price.is_known is False
    assert price.amount is None
    assert price.state == PRICE_NOT_RECORDED


def test_the_three_ways_of_having_no_price_stay_apart():
    """ "Nobody priced it", "no vendor at all" and "no vendor you can buy from".

    Different facts, different screens, different operator actions — the same
    ``NO_SUPPLIERS`` / ``NONE_ORDERABLE`` split ``supplier_selection`` keeps,
    and the one whose collapse cost op-2rsp four rounds.
    """
    unpriced = _item("Unpriced")
    _link(unpriced, "Acme", unit_cost=None)
    assert order_unit_price(_fresh(unpriced)).state == PRICE_NOT_RECORDED

    orphan = _item("Orphan")
    assert order_unit_price(_fresh(orphan)).state == PRICE_NO_SUPPLIER_LINK

    unbuyable = _item("Unbuyable")
    _link(unbuyable, "Dead", unit_cost="5.00", is_discontinued=True)
    assert order_unit_price(_fresh(unbuyable)).state == PRICE_NO_ORDERABLE_LINK


def test_every_unknown_state_carries_the_operator_a_remedy():
    """A refusal an operator cannot act on is not a fix."""
    item = _item("Unpriced")
    link = _link(item, "Acme", unit_cost=None)

    detail = explain(unit_price_of(link), item_name="Unpriced", supplier_name="Acme")
    assert detail and "Acme" in detail and "supplier link" in detail

    orphan = _item("Orphan")
    assert "Add" in explain(order_unit_price(_fresh(orphan)), item_name="Orphan")

    unbuyable = _item("Unbuyable")
    _link(unbuyable, "Dead", unit_cost="5.00", is_discontinued=True)
    assert "Reactivate" in explain(order_unit_price(_fresh(unbuyable)), item_name="Unbuyable")

    # Nothing to say about a price we have.
    assert explain(unit_price_of(_link(_item("Fine"), "Ok")), item_name="Fine") is None


def test_an_unknown_price_extends_to_an_unknown_total_and_a_zero_to_zero():
    item = _item("W")
    assert extended(unit_price_of(_link(item, "Acme", unit_cost=None)), 10) is None
    assert extended(unit_price_of(_link(item, "Free", unit_cost="0.00")), 10) == Decimal("0.00")
    assert extended(unit_price_of(_link(item, "Paid", unit_cost="2.50")), 10) == Decimal("25.00")


def test_a_rollup_sums_what_it_can_and_counts_what_it_cannot():
    item = _item("W")
    rollup = PriceRollup()

    assert rollup.add(unit_price_of(_link(item, "Paid", unit_cost="2.00")), 3) == Decimal("6.00")
    assert rollup.add(unit_price_of(_link(item, "Free", unit_cost="0.00")), 3) == Decimal("0.00")
    assert rollup.add(unit_price_of(_link(item, "Acme", unit_cost=None)), 3) is None

    assert rollup.amount == Decimal("6.00")
    assert rollup.unpriced_count == 1
    assert rollup.is_complete is False


def test_a_rollup_of_only_priced_lines_claims_to_be_complete():
    """CONTROL. A total with nothing missing must not be labelled partial."""
    item = _item("W")
    rollup = PriceRollup()
    rollup.add(unit_price_of(_link(item, "Free", unit_cost="0.00")), 4)
    assert rollup.amount == Decimal("0.00")
    assert rollup.is_complete is True


def test_package_price_reads_its_own_column():
    """The two columns are separate facts and separate answers.

    ``ItemSupplier.save()`` derives each from the other, so the population where
    they disagree is exactly the one ``pack_size`` calls ``RECORDED_ZERO``: a
    link recording ``quantity_per_package`` of 0 runs neither derivation, and
    the package price genuinely stays unrecorded while the unit price is known.
    """
    item = _item("W")
    link = _link(item, "Acme", unit_cost="1.00", package_cost=None, pack=0)
    assert unit_price_of(link).is_known
    assert package_price_of(link).is_known is False
    assert package_price_of(link).state == PRICE_NOT_RECORDED


def test_the_cheapest_price_on_file_ignores_orderability_but_not_nulls():
    """``lowest_unit_price`` values the SHELF, so a dead vendor's price counts.

    Filtering this for orderability would revalue a shelf when a vendor's status
    changed — the mistake ``pack_size.shelf_pack_size`` records from op-2rsp
    round 1.
    """
    item = _item("W")
    _link(item, "Dead", unit_cost="1.00", is_discontinued=True)
    _link(item, "Live", unit_cost="9.00")
    _link(item, "Silent", unit_cost=None)

    assert lowest_unit_price(_fresh(item)).amount == Decimal("1.00")


# ── Site 1: create_optimized_order's line and order totals ───────────────────


def test_an_unpriced_recommendation_line_reports_no_total_and_says_why(api):
    """BEFORE/AFTER. ``estimated_line_total`` was ``0``; it is now ``null``.

    Screen: the optimized-order recommendation payload
    (``POST /api/reorders/purchase-orders/create_optimized_order/``), per line.
    """
    item = _item("Unpriced", current_stock=0, minimum_stock=10)
    _link(item, "Acme", unit_cost=None)

    response = api.post(OPTIMIZED_URL, {}, format="json")
    assert response.status_code == 200
    rec, line = _line_for(response.data["recommendations"], item)

    assert line["estimated_line_total"] is None
    assert line["unit_cost"] is None
    assert line["unit_cost_state"] == PRICE_NOT_RECORDED
    assert "Acme" in line["unit_cost_detail"]


def test_a_recommendation_total_says_how_many_lines_it_could_not_price(api):
    """BEFORE/AFTER. The group total omitted the unpriced line SILENTLY.

    Screen: the same payload's per-supplier ``estimated_total`` and the
    response-level ``total_estimated_cost``. The numbers are unchanged — the
    unpriced line contributed nothing before and contributes nothing now — but
    the claim beside them is not.
    """
    supplier = Supplier.objects.create(name="Acme", supplier_type=Supplier.SupplierType.LOCAL)
    priced = _item("Priced", current_stock=0, minimum_stock=10, reorder_quantity=4)
    unpriced = _item("Unpriced", current_stock=0, minimum_stock=10, reorder_quantity=4)
    for item, cost in ((priced, Decimal("2.00")), (unpriced, None)):
        ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku=f"{item.sku}-s",
            unit_cost=cost,
            quantity_per_package=1,
            average_lead_time=7,
        )

    response = api.post(OPTIMIZED_URL, {}, format="json")
    rec, _line = _line_for(response.data["recommendations"], unpriced)

    assert rec["unpriced_item_count"] == 1
    assert rec["estimated_total_is_partial"] is True
    assert response.data["unpriced_item_count"] == 1
    assert response.data["total_estimated_cost_is_partial"] is True
    # The priced line is still summed exactly as base summed it.
    assert rec["estimated_total"] > 0


def test_a_free_supplier_is_a_priced_recommendation_line(api):
    """CONTROL for the second half of the rule. A $0 vendor is PRICED.

    Screen: the same payload. ``estimated_line_total`` is ``0.00`` — a real
    number for a real price — and the line is NOT counted as unpriced, which is
    the whole difference between it and the test above.
    """
    item = _item("Donated", current_stock=0, minimum_stock=10, reorder_quantity=4)
    _link(item, "Charity", unit_cost="0.00")

    response = api.post(OPTIMIZED_URL, {}, format="json")
    rec, line = _line_for(response.data["recommendations"], item)

    assert line["estimated_line_total"] == Decimal("0.00")
    assert line["unit_cost"] == Decimal("0.00")
    assert line["unit_cost_state"] == PRICE_KNOWN
    assert line["unit_cost_detail"] is None
    assert rec["unpriced_item_count"] == 0
    assert rec["estimated_total_is_partial"] is False


def test_a_priced_recommendation_is_byte_for_byte_what_base_produced(api):
    """CONTROL. The invariant: no money moves where the price was known."""
    item = _item("Priced", current_stock=0, minimum_stock=10, reorder_quantity=4)
    _link(item, "Acme", unit_cost="2.50")

    response = api.post(OPTIMIZED_URL, {}, format="json")
    rec, line = _line_for(response.data["recommendations"], item)

    assert line["estimated_line_total"] == Decimal("2.50") * line["recommended_quantity"]
    assert rec["estimated_total"] == line["estimated_line_total"]
    assert response.data["total_estimated_cost"] == rec["estimated_total"]


# ── Site 2: the order pad (reorder_data) ─────────────────────────────────────


def test_the_order_pad_sends_null_for_a_price_nobody_recorded(api):
    """BEFORE/AFTER. ``unit_cost`` was the string ``"0.00"``; it is now ``null``.

    Screen: the PO create form's supplier pad (``reorder_data``), the price cell
    and the line total beside it. The form prefilled ``0.00`` into the cost box
    and the operator confirmed it, which is how an unpriced item reached a
    purchase order costed at nothing.
    """
    item = _item("Unpriced", current_stock=0, minimum_stock=10)
    _link(item, "Acme", unit_cost=None)

    response = api.get(REORDER_DATA_URL)
    assert response.status_code == 200
    group, line = _pad_line_for(response.data["suppliers"], item)

    assert line["unit_cost"] is None
    assert line["line_total"] is None
    assert line["unit_cost_state"] == PRICE_NOT_RECORDED
    assert "Acme" in line["unit_cost_detail"]
    assert group["unpriced_item_count"] == 1
    assert group["estimated_total_is_partial"] is True


def test_the_order_pad_prices_a_free_supplier_at_zero(api):
    """CONTROL. ``"0.00"`` on the pad now means the vendor charges nothing."""
    item = _item("Donated", current_stock=0, minimum_stock=10)
    _link(item, "Charity", unit_cost="0.00")

    response = api.get(REORDER_DATA_URL)
    group, line = _pad_line_for(response.data["suppliers"], item)

    assert line["unit_cost"] == "0.00"
    assert line["line_total"] == "0.00"
    assert line["unit_cost_state"] == PRICE_KNOWN
    assert group["unpriced_item_count"] == 0
    assert group["estimated_total_is_partial"] is False


def test_the_order_pad_total_is_unchanged_where_every_line_is_priced(api):
    """CONTROL. The invariant, on the pad."""
    item = _item("Priced", current_stock=0, minimum_stock=10)
    _link(item, "Acme", unit_cost="3.00")

    response = api.get(REORDER_DATA_URL)
    group, line = _pad_line_for(response.data["suppliers"], item)

    expected = Decimal("3.00") * line["suggested_quantity"]
    assert Decimal(line["line_total"]) == expected
    assert Decimal(group["estimated_total"]) == expected
    assert group["estimated_total_is_partial"] is False


def test_an_unknown_package_cost_is_null_rather_than_a_zero_case_price(api):
    """A case price nobody recorded must not read as a free case."""
    item = _item("Unpriced", current_stock=0, minimum_stock=10)
    # pack=0 is the one shape where save() derives neither column from the
    # other — see ``test_package_price_reads_its_own_column``.
    _link(item, "Acme", unit_cost="1.00", package_cost=None, pack=0)

    response = api.get(REORDER_DATA_URL)
    _group, line = _pad_line_for(response.data["suppliers"], item)
    assert line["package_cost"] is None
    assert line["unit_cost"] == "1.00"


# ── Site 3: the purchase order itself — the price that gets STORED ───────────


def _po_payload(item_supplier, supplier, **line_extra):
    line = {"item_supplier_id": item_supplier.id, "quantity": 2}
    line.update(line_extra)
    return {"supplier": supplier.id, "items": [line]}


def test_creating_an_order_refuses_a_line_no_price_is_on_file_for(api):
    """BEFORE/AFTER. Base wrote ``unit_cost_ordered = 0.0000`` and moved on.

    Screen: ``POST /api/reorders/purchase-orders/``. ``unit_cost_ordered`` is
    NON-NULLABLE and permanent, so a fabricated zero is laundered into the
    order's stored ``estimated_total``, its payment schedule and every report
    downstream, with nothing left to say it was never a price. The asset and
    freeform branches of the same function ALREADY refuse for exactly this
    reason ("unit_cost is required when purchasing asset X"); the inventory
    branch was the one that substituted instead.
    """
    item = _item("Unpriced")
    link = _link(item, "Acme", unit_cost=None)

    response = api.post(
        "/api/reorders/purchase-orders/",
        _po_payload(link, link.supplier),
        format="json",
    )

    assert response.status_code == 400
    body = str(response.data)
    assert "no price is on file" in body.lower()
    # The remedy, both halves of it, so the refusal is actionable.
    assert "unit_cost" in body and "supplier link" in body
    assert not PurchaseOrder.objects.exists()


def test_creating_an_order_accepts_an_explicit_price_for_an_unpriced_link(api):
    """CONTROL. The refusal is escapable exactly as its message says."""
    item = _item("Unpriced")
    link = _link(item, "Acme", unit_cost=None)

    response = api.post(
        "/api/reorders/purchase-orders/",
        _po_payload(link, link.supplier, unit_cost="7.25"),
        format="json",
    )

    assert response.status_code == 201
    stored = PurchaseOrderItem.objects.get()
    assert stored.unit_cost_ordered == Decimal("7.2500")


def test_creating_an_order_from_a_free_supplier_stores_a_real_zero(api):
    """CONTROL. A vendor that charges nothing is priced, so the order is created."""
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00")

    response = api.post(
        "/api/reorders/purchase-orders/",
        _po_payload(link, link.supplier),
        format="json",
    )

    assert response.status_code == 201
    stored = PurchaseOrderItem.objects.get()
    assert stored.unit_cost_ordered == Decimal("0.0000")
    assert stored.purchase_order.estimated_total == Decimal("0.00")


def test_creating_an_order_from_a_priced_supplier_is_unchanged(api):
    """CONTROL. The invariant, on the stored total."""
    item = _item("Priced")
    link = _link(item, "Acme", unit_cost="4.00")

    response = api.post(
        "/api/reorders/purchase-orders/",
        _po_payload(link, link.supplier),
        format="json",
    )

    assert response.status_code == 201
    assert PurchaseOrderItem.objects.get().unit_cost_ordered == Decimal("4.0000")
    assert PurchaseOrder.objects.get().estimated_total == Decimal("8.00")


# ── Site 4: adding a line by scan (line_entry) ───────────────────────────────


def _draft_for(supplier, user):
    return PurchaseOrder.objects.create(
        supplier=supplier, status=PurchaseOrder.Status.DRAFT, created_by=user
    )


@pytest.fixture
def scanner_user():
    return User.objects.create_user(username="scanner", password="pw")


def test_a_default_unit_cost_is_none_when_nothing_is_on_file():
    """BEFORE/AFTER. ``default_unit_cost`` returned ``Decimal("0.00")``."""
    item = _item("Unpriced")
    link = _link(item, "Acme", unit_cost=None)
    assert line_entry.default_unit_cost(link) is None


def test_a_default_unit_cost_of_zero_means_the_vendor_charges_nothing():
    """CONTROL. And it does NOT fall through to purchase history."""
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00")
    assert line_entry.default_unit_cost(link) == Decimal("0.00")


def test_adding_a_scanned_line_is_refused_when_no_price_is_on_file(scanner_user):
    """BEFORE/AFTER. The scan used to add the line at a fabricated ``0.0000``.

    Screen: the scanner add-line flow (``POST .../items/`` on an open order,
    and ScanTTY's add-line prompt). The refusal names both remedies, which is
    what makes it a fix rather than a wall.
    """
    item = _item("Unpriced")
    link = _link(item, "Acme", unit_cost=None)
    order = _draft_for(link.supplier, scanner_user)

    with pytest.raises(line_entry.LineEntryError) as exc:
        line_entry.add_line_item(order, link)

    assert exc.value.code == "no_unit_cost"
    assert "Acme" in str(exc.value)
    assert not PurchaseOrderItem.objects.exists()


def test_a_scanned_line_with_an_explicit_price_is_added(scanner_user):
    """CONTROL. The documented escape from the refusal."""
    item = _item("Unpriced")
    link = _link(item, "Acme", unit_cost=None)
    order = _draft_for(link.supplier, scanner_user)

    line, created = line_entry.add_line_item(order, link, unit_cost="3.50")
    assert created and line.unit_cost_ordered == Decimal("3.5000")


def test_a_scanned_line_from_a_free_supplier_is_added_at_zero(scanner_user):
    """CONTROL. A free vendor is priced, so the scan works as it always did."""
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00")
    order = _draft_for(link.supplier, scanner_user)

    line, created = line_entry.add_line_item(order, link)
    assert created and line.unit_cost_ordered == Decimal("0.0000")


def test_an_unpriced_link_still_falls_back_to_what_it_last_cost(scanner_user):
    """CONTROL. The purchase-history fallback is untouched by the refusal."""
    item = _item("Unpriced")
    link = _link(item, "Acme", unit_cost=None)
    first = _draft_for(link.supplier, scanner_user)
    PurchaseOrderItem.objects.create(
        purchase_order=first,
        item_supplier=link,
        quantity_ordered=1,
        unit_cost_ordered=Decimal("6.0000"),
        order_in_packages=1,
    )

    assert line_entry.default_unit_cost(link) == Decimal("6.00")


def test_the_candidate_payload_suggests_null_rather_than_a_zero_to_accept(scanner_user):
    """BEFORE/AFTER. ``suggested_unit_cost`` was the string ``"0.00"``.

    Screen: the line-entry candidate list a scanner prompt renders. A
    ``"0.00"`` there is a number an operator accepts by reflex; ScanTTY had
    already written a special hint beside it saying the zero was "the default,
    not a quote" — which is the downstream cost of this collapse, and which
    misfires on a vendor that genuinely charges nothing.
    """
    item = _item("Unpriced")
    link = _link(item, "Acme", unit_cost=None)
    candidate = type("C", (), {"item_supplier": link, "existing_line": None})()
    candidate.match_kind = "supplier_sku"
    candidate.match_label = None
    candidate.matched_value = "x"

    assert line_entry.serialize_candidate(candidate)["suggested_unit_cost"] is None

    free = _item("Donated")
    free_link = _link(free, "Charity", unit_cost="0.00")
    candidate.item_supplier = free_link
    assert line_entry.serialize_candidate(candidate)["suggested_unit_cost"] == "0.00"


# ── Site 5: what the stock on the shelf is worth ─────────────────────────────


def test_stock_value_is_unknown_when_nobody_records_a_price():
    """BEFORE/AFTER. ``total_value`` was ``Decimal("0")`` — "worth nothing"."""
    item = _item("Unpriced", current_stock=40)
    _link(item, "Acme", unit_cost=None)
    assert _fresh(item).total_value is None


def test_a_free_supplier_values_the_shelf_at_zero():
    """CONTROL. Free stock IS worth nothing, and that is a fact not a gap."""
    item = _item("Donated", current_stock=40)
    _link(item, "Charity", unit_cost="0.00")
    assert _fresh(item).total_value == Decimal("0.00")


def test_a_priced_shelf_is_valued_exactly_as_before():
    """CONTROL. The invariant, on stock value."""
    item = _item("Priced", current_stock=40)
    _link(item, "Acme", unit_cost="1.25")
    assert _fresh(item).total_value == Decimal("50.00")


def test_the_item_list_renders_for_an_item_whose_stock_cannot_be_valued(api):
    """The consumer half: a null ``total_value`` must not break the page.

    Round 5 of the previous branch shipped a null against untyped consumers and
    blanked two member-facing pages. This is the same check, one fact along.
    """
    item = _item("Unpriced", current_stock=40)
    _link(item, "Acme", unit_cost=None)

    response = api.get(f"/api/inventory/items/{item.id}/")
    assert response.status_code == 200
    assert response.data["total_value"] is None


def test_the_stock_value_report_counts_the_items_it_could_not_price(api):
    """BEFORE/AFTER on the CLAIM, not on the number.

    Screen: the inventory report's stock-by-category table. ``total_value``
    there is ``Sum(stock * Coalesce(unit_cost, 0))`` — ``unit_cost or 0``
    written in SQL — so an unpriced item contributed nothing and the column
    read as a complete valuation. The total is deliberately UNCHANGED (moving
    it would be inventing money); ``items_without_price`` is what makes it
    honest.
    """
    priced = _item("Priced", current_stock=10)
    _link(priced, "Acme", unit_cost="2.00")
    unpriced = _item("Unpriced", current_stock=10)
    _link(unpriced, "Silent", unit_cost=None)

    response = api.get(STOCK_BY_CATEGORY_URL)
    assert response.status_code == 200
    row = next(r for r in response.data if r["category_name"] == "Uncategorized")
    assert row["total_value"] == 20.0
    assert row["items_without_price"] == 1


# ── Site 6: a reorder request's estimated cost ───────────────────────────────


def test_the_location_value_report_counts_what_it_could_not_price(api):
    """The location twin of the category report — a SEPARATE endpoint payload.

    ``value_by_location`` builds the same ``Coalesce(unit_cost, 0)`` total from
    its own query, so it needs its own count and its own test; ScanTTY reads
    this one too (``/reports/inventory/value_by_location/``).
    """
    priced = _item("Priced", current_stock=10)
    _link(priced, "Acme", unit_cost="2.00")
    unpriced = _item("Unpriced", current_stock=10)
    _link(unpriced, "Silent", unit_cost=None)

    response = api.get(VALUE_BY_LOCATION_URL)
    assert response.status_code == 200
    row = next(r for r in response.data if r["location_name"] == "No Location")
    assert row["total_value"] == 20.0
    assert row["items_without_price"] == 1


def test_a_free_item_costs_zero_rather_than_nothing_known():
    """BEFORE/AFTER on the second half of the rule.

    Screen: the reorder-request list and detail. ``if unit_cost:`` reported
    ``None`` ("we cannot cost this") for an item a vendor gives away.
    """
    from reorder_queue.models import ReorderRequest

    item = _item("Donated")
    _link(item, "Charity", unit_cost="0.00")
    request = ReorderRequest.objects.create(item=_fresh(item), quantity=6)
    assert request.estimated_cost == Decimal("0.00")


def test_an_unpriced_item_still_has_no_estimated_cost():
    """CONTROL. The other half stays ``None``."""
    from reorder_queue.models import ReorderRequest

    item = _item("Unpriced")
    _link(item, "Acme", unit_cost=None)
    request = ReorderRequest.objects.create(item=_fresh(item), quantity=6)
    assert request.estimated_cost is None


# ── Site 7: price history and the price-trend report ─────────────────────────


def _history(link, costs):
    """Replace ``link``'s price history with exactly ``costs``, oldest first.

    ``ItemSupplier.save()`` writes a ``CREATED`` snapshot of its own, which
    would otherwise be the "first price" these tests reason about. Cleared here
    so each test states its whole history.
    """
    PriceHistory.objects.filter(item_supplier=link).delete()
    rows = []
    for cost in costs:
        rows.append(
            PriceHistory.objects.create(
                item_supplier=link,
                unit_cost=None if cost is None else Decimal(cost),
                package_cost=None,
                quantity_per_package=1,
            )
        )
    return rows


def test_a_drop_to_free_is_a_price_change_of_minus_one_hundred_percent():
    """BEFORE/AFTER. ``if previous.unit_cost and self.unit_cost`` swallowed it.

    Screen: the item detail's price-trend summary and the purchasing
    price-trend report. A supplier that started donating an item reported "no
    change" — the single most notable price move there is.
    """
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00")
    _first, latest = _history(link, ["5.00", "0.00"])
    assert latest.price_change_percentage == Decimal("-100.00")


def test_a_change_from_free_has_no_percentage_because_there_is_no_base():
    """CONTROL. Undefined arithmetic, not a data gap — and it stays ``None``."""
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="4.00")
    _first, latest = _history(link, ["0.00", "4.00"])
    assert latest.price_change_percentage is None


def test_an_unrecorded_price_still_yields_no_percentage():
    """CONTROL. Both halves of the guard still refuse an unknown."""
    item = _item("Mystery")
    link = _link(item, "Acme", unit_cost="4.00")
    _first, latest = _history(link, [None, "4.00"])
    assert latest.price_change_percentage is None


def test_an_ordinary_price_rise_is_unchanged():
    """CONTROL. The invariant, on the percentage."""
    item = _item("Priced")
    link = _link(item, "Acme", unit_cost="5.00")
    _first, latest = _history(link, ["4.00", "5.00"])
    assert latest.price_change_percentage == Decimal("25.00")


def test_the_price_trend_report_sends_null_rather_than_a_zero_price(api):
    """BEFORE/AFTER. ``float(x or 0)`` reported "$0.00" for three different facts.

    Screen: the purchasing price-trend report table (and ScanTTY's
    ``report_table`` row). "No price recorded", "this vendor is free" and "no
    price history at all" all rendered as ``0``.
    """
    item = _item("Mystery")
    link = _link(item, "Acme", unit_cost=None)
    _history(link, [None, None])

    response = api.get(PRICE_TRENDS_URL)
    assert response.status_code == 200
    row = next(r for r in response.data if r["item_name"] == "Mystery")
    assert row["min_unit_cost"] is None
    assert row["max_unit_cost"] is None
    assert row["latest_unit_cost"] is None


def test_the_price_trend_report_still_reports_a_real_free_price(api):
    """CONTROL. ``0.0`` on that report now means the vendor charges nothing."""
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00")
    _history(link, ["0.00", "0.00"])

    response = api.get(PRICE_TRENDS_URL)
    row = next(r for r in response.data if r["item_name"] == "Donated")
    assert row["min_unit_cost"] == 0.0
    assert row["latest_unit_cost"] == 0.0


def test_the_price_trend_report_reports_a_drop_to_free(api):
    """BEFORE/AFTER. ``if first_price.unit_cost and latest_price.unit_cost``.

    Screen: the purchasing price-trend report's "% change" column. A supplier
    that stopped charging is the largest price move the report can show, and
    the truthiness guard reported nothing for it. This is the report's own
    guard, distinct from ``PriceHistory.price_change_percentage`` — a mutation
    run proved the two need separate tests, because every other input gives
    both spellings the same answer.
    """
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00")
    _history(link, ["5.00", "0.00"])

    response = api.get(PRICE_TRENDS_URL)
    row = next(r for r in response.data if r["item_name"] == "Donated")
    assert row["price_change_percentage"] == Decimal("-100.00")
    assert row["latest_unit_cost"] == 0.0


def test_the_price_trend_report_is_unchanged_for_ordinary_prices(api):
    """CONTROL. The invariant, on the report."""
    item = _item("Priced")
    link = _link(item, "Acme", unit_cost="5.00")
    _history(link, ["4.00", "5.00"])

    response = api.get(PRICE_TRENDS_URL)
    row = next(r for r in response.data if r["item_name"] == "Priced")
    assert row["min_unit_cost"] == 4.0
    assert row["max_unit_cost"] == 5.0
    assert row["latest_unit_cost"] == 5.0
    assert row["price_change_percentage"] == 25.0


def test_a_kit_row_on_the_pad_never_prices_an_unknown_at_zero(api):
    """BEFORE/AFTER. The kit row is informational, and it is still a price.

    Screen: the PO create form's "kits that would restock a low component"
    strip. It touches no total (kits are never action rows — op-8n0), which is
    exactly why an unknown price there had nothing else to correct it.
    """
    supplier = Supplier.objects.create(name="Acme", supplier_type=Supplier.SupplierType.LOCAL)
    component = _item("Ink", current_stock=0, minimum_stock=10)
    ItemSupplier.objects.create(
        item=component,
        supplier=supplier,
        supplier_sku="ink",
        unit_cost=Decimal("1.00"),
        quantity_per_package=1,
        average_lead_time=7,
    )
    kit = _item("InkKit", is_kit=True)
    kit.kit_components.create(component=component, quantity=2)
    ItemSupplier.objects.create(
        item=kit,
        supplier=supplier,
        supplier_sku="kit",
        unit_cost=None,
        quantity_per_package=1,
        average_lead_time=7,
    )

    response = api.get(REORDER_DATA_URL)
    group = next(g for g in response.data["suppliers"] if g["id"] == supplier.id)
    row = next(k for k in group["kits"] if k["name"] == "InkKit")
    assert row["unit_cost"] is None


def test_the_supplier_price_summary_counts_a_free_snapshot(api):
    """CONTROL, not BEFORE/AFTER — these three figures did NOT move.

    Base already spelled this filter ``if ph.unit_cost is not None``, so
    ``average_unit_cost`` / ``min_unit_cost`` / ``max_unit_cost`` are identical
    to base; only the per-record ``unit_cost`` / ``package_cost`` inside
    ``trends`` moved (``null`` -> ``0.0`` for a recorded zero), and
    ``test_the_supplier_price_trend_records_a_free_snapshot_as_zero`` owns
    that. This pins the summary so the comprehension cannot quietly become a
    truthiness filter, which is the change that WOULD push the average and the
    minimum up. Screen: the supplier detail's price-trend summary.
    """
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00")
    _history(link, ["4.00", "0.00"])

    response = api.get(f"/api/inventory/suppliers/{link.supplier.id}/")
    assert response.status_code == 200
    summary = response.data["price_trends"]["summary"]
    assert summary["min_unit_cost"] == 0.0
    assert summary["average_unit_cost"] == 2.0


def test_the_supplier_price_trend_records_a_free_snapshot_as_zero(api):
    """BEFORE/AFTER — the one figure on this payload that DID move.

    Screen: the supplier detail's price-trend chart. Base spelled the
    per-record value ``float(ph.unit_cost) if ph.unit_cost else None``, so a
    snapshot recording ``0.00`` — a supplier that started donating an item —
    arrived as ``null`` and the chart drew a gap where the drop to free was.
    It is ``0.0`` now, and ``null`` is reserved for a snapshot that records no
    price at all.
    """
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00")
    _history(link, ["4.00", "0.00"])

    response = api.get(f"/api/inventory/suppliers/{link.supplier.id}/")
    assert response.status_code == 200
    trend = next(t for t in response.data["price_trends"]["trends"] if t["item_name"] == "Donated")
    assert [ph["unit_cost"] for ph in trend["price_history"]] == [4.0, 0.0]


def test_the_supplier_analytics_feed_records_a_free_snapshot_as_zero(api):
    """CONTROL. The SECOND consumer of the one "a Price as JSON" rendering.

    ``pricing.price_float`` was written out twice, character-for-character, in
    ``inventory/views.py`` and ``inventory/serializers.py``. Now that both
    import the one owner, this pins the view's own payload — the supplier
    analytics feed — so a future edit to that owner cannot regress one caller
    while the other stays green.
    """
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00")
    _history(link, ["4.00", "0.00"])

    response = api.get(f"/api/inventory/suppliers/{link.supplier.id}/analytics/")
    assert response.status_code == 200
    changes = response.data["price_trends"]["recent_changes"]
    assert [c["unit_cost"] for c in changes] == [0.0, 4.0]


def test_the_supplier_price_summary_ignores_a_snapshot_with_no_price(api):
    """CONTROL. An unrecorded price is still not a data point."""
    item = _item("Mystery")
    link = _link(item, "Acme", unit_cost="4.00")
    _history(link, ["4.00", None])

    response = api.get(f"/api/inventory/suppliers/{link.supplier.id}/")
    summary = response.data["price_trends"]["summary"]
    assert summary["min_unit_cost"] == 4.0
    assert summary["average_unit_cost"] == 4.0


# ── Round 2: the consumers the first sweep missed ────────────────────────────


def test_the_dashboard_summary_survives_an_item_it_cannot_value(api):
    """BEFORE/AFTER on the CLAIM; the NUMBER must not move.

    Screen: the public ``/api/dashboard/inventory-summary/`` tile. Making
    ``InventoryItem.total_value`` nullable left this ``sum()`` folding ``None``
    into an int accumulator, so one unpriced active item turned the whole
    endpoint into a 500 through its blanket ``except``. The total reproduces
    base exactly — base contributed ``Decimal("0")`` for those items — and
    ``items_without_price`` is what stops it reading as a complete valuation.
    """
    priced = _item("Priced", current_stock=10)
    _link(priced, "Acme", unit_cost="2.00")
    unpriced = _item("Unpriced", current_stock=10)
    _link(unpriced, "Silent", unit_cost=None)
    orphan = _item("Orphan", current_stock=10)

    response = api.get(DASHBOARD_SUMMARY_URL)
    assert response.status_code == 200
    inventory = response.data["inventory"]
    assert inventory["total_value"] == 20.0
    assert inventory["items_without_price"] == 2
    assert orphan.pk is not None


def test_the_dashboard_summary_is_unchanged_when_everything_is_priced(api):
    """CONTROL. The invariant, on the dashboard tile."""
    priced = _item("Priced", current_stock=10)
    _link(priced, "Acme", unit_cost="2.00")
    free = _item("Donated", current_stock=10)
    _link(free, "Charity", unit_cost="0.00")

    response = api.get(DASHBOARD_SUMMARY_URL)
    assert response.status_code == 200
    inventory = response.data["inventory"]
    assert inventory["total_value"] == 20.0
    assert inventory["items_without_price"] == 0


def _csv_rows(response):
    import csv
    import io

    body = b"".join(response.streaming_content) if response.streaming else response.content
    return list(csv.DictReader(io.StringIO(body.decode())))


def test_the_price_trend_export_leaves_an_unknown_price_blank(api):
    """BEFORE/AFTER. ``f"{None:.2f}"`` raised — an unhandled 500 on the export.

    Payload: ``GET /api/reorders/reports/purchasing/export/?type=price_trends``.
    A blank cell sums as nothing AND reads as nothing, which is the truth;
    "0.00" would make a spreadsheet count the unknowns as free. The same rule
    ``csvExport.ts``'s ``reportMoney`` follows on the browser-side export.
    """
    item = _item("Mystery")
    link = _link(item, "Acme", unit_cost=None)
    _history(link, [None, None])

    response = api.get(PURCHASING_EXPORT_URL, {"type": "price_trends"})
    assert response.status_code == 200
    row = next(r for r in _csv_rows(response) if r["item_name"] == "Mystery")
    assert row["min_unit_cost"] == ""
    assert row["max_unit_cost"] == ""
    assert row["latest_unit_cost"] == ""


def test_the_price_trend_export_writes_a_real_zero_for_a_free_vendor(api):
    """CONTROL. A recorded 0.00 is a price and must not export as a blank."""
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00")
    _history(link, ["0.00", "0.00"])

    response = api.get(PURCHASING_EXPORT_URL, {"type": "price_trends"})
    row = next(r for r in _csv_rows(response) if r["item_name"] == "Donated")
    assert row["min_unit_cost"] == "0.00"
    assert row["latest_unit_cost"] == "0.00"
    # The percentage IS undefined here — there is no base to divide by — so a
    # blank is the honest cell for it, unlike the prices beside it.
    assert row["price_change_percentage"] == ""


def test_the_price_trend_export_writes_a_real_zero_percent_change(api):
    """CONTROL on the second half of the rule, one column along.

    A price that did not move is a 0.00% change and a fact; the falsy guard
    ``if row["price_change_percentage"]`` exported it as the same blank an
    INCOMPUTABLE percentage gets, collapsing the two (op-9m2v).
    """
    item = _item("Steady")
    link = _link(item, "Acme", unit_cost="4.00")
    _history(link, ["4.00", "4.00"])

    response = api.get(PURCHASING_EXPORT_URL, {"type": "price_trends"})
    row = next(r for r in _csv_rows(response) if r["item_name"] == "Steady")
    assert row["price_change_percentage"] == "0.00%"


def test_the_price_trend_export_is_unchanged_for_ordinary_prices(api):
    """CONTROL. The invariant, on the export."""
    item = _item("Priced")
    link = _link(item, "Acme", unit_cost="5.00")
    _history(link, ["4.00", "5.00"])

    response = api.get(PURCHASING_EXPORT_URL, {"type": "price_trends"})
    row = next(r for r in _csv_rows(response) if r["item_name"] == "Priced")
    assert row["min_unit_cost"] == "4.00"
    assert row["max_unit_cost"] == "5.00"
    assert row["latest_unit_cost"] == "5.00"
    assert row["price_change_percentage"] == "25.00%"


def test_the_stock_value_export_carries_the_count_that_qualifies_the_total(api):
    """BEFORE/AFTER on the CLAIM, not on the number.

    Payload: ``GET /api/inventory/reports/inventory/export/?type=stock_by_category``.
    The JSON payload, the UI table and the browser-side export all carry
    ``items_without_price``; the server-side CSV — the surface most likely to
    be pasted into a spreadsheet and summed — did not.
    """
    priced = _item("Priced", current_stock=10)
    _link(priced, "Acme", unit_cost="2.00")
    unpriced = _item("Unpriced", current_stock=10)
    _link(unpriced, "Silent", unit_cost=None)

    response = api.get(INVENTORY_EXPORT_URL, {"type": "stock_by_category"})
    assert response.status_code == 200
    row = next(r for r in _csv_rows(response) if r["category_name"] == "Uncategorized")
    assert row["total_value"] == "20.00"
    assert row["items_without_price"] == "1"


def test_the_location_value_export_carries_the_count_too(api):
    """BEFORE/AFTER on the CLAIM. The location twin — its own CSV branch."""
    priced = _item("Priced", current_stock=10)
    _link(priced, "Acme", unit_cost="2.00")
    unpriced = _item("Unpriced", current_stock=10)
    _link(unpriced, "Silent", unit_cost=None)

    response = api.get(INVENTORY_EXPORT_URL, {"type": "value_by_location"})
    assert response.status_code == 200
    row = next(r for r in _csv_rows(response) if r["location_name"] == "No Location")
    assert row["total_value"] == "20.00"
    assert row["items_without_price"] == "1"


def test_the_by_supplier_total_says_how_many_requests_it_could_not_price(api):
    """BEFORE/AFTER on the CLAIM, not on the number.

    Screen: the admin dashboard's "Requests by Supplier" modal, which renders
    ``total_estimated_cost.toFixed(2)`` as a bulk-ordering total. An unpriced
    request contributed nothing and the payload said nothing about it — the
    defect ``PriceRollup`` exists for. The number is unchanged.
    """
    from reorder_queue.models import ReorderRequest

    priced = _item("Priced")
    _link(priced, "Acme", unit_cost="2.00", is_primary=True)
    unpriced = _item("Unpriced")
    _link(unpriced, "Acme Two", unit_cost=None, is_primary=True)
    ReorderRequest.objects.create(item=_fresh(priced), quantity=5)
    ReorderRequest.objects.create(item=_fresh(unpriced), quantity=5)

    response = api.get(BY_SUPPLIER_URL)
    assert response.status_code == 200
    priced_group = next(g for g in response.data if g["supplier"] == "Acme")
    unpriced_group = next(g for g in response.data if g["supplier"] == "Acme Two")

    assert priced_group["total_estimated_cost"] == 10.0
    assert priced_group["unpriced_item_count"] == 0
    assert priced_group["estimated_total_is_partial"] is False

    assert unpriced_group["total_estimated_cost"] == 0
    assert unpriced_group["unpriced_item_count"] == 1
    assert unpriced_group["estimated_total_is_partial"] is True


def test_the_by_supplier_total_counts_a_free_request_as_priced(api):
    """CONTROL. A vendor that charges nothing is PRICED, so nothing is missing."""
    from reorder_queue.models import ReorderRequest

    free = _item("Donated")
    _link(free, "Charity", unit_cost="0.00", is_primary=True)
    ReorderRequest.objects.create(item=_fresh(free), quantity=5)

    response = api.get(BY_SUPPLIER_URL)
    group = next(g for g in response.data if g["supplier"] == "Charity")
    assert group["total_estimated_cost"] == 0
    assert group["unpriced_item_count"] == 0
    assert group["estimated_total_is_partial"] is False


def test_a_price_that_did_not_move_is_still_reported_as_stable(api):
    """CONTROL. A REAL 0% change is a fact and must keep its number."""
    item = _item("Priced")
    link = _link(item, "Acme", unit_cost="4.00", is_primary=True)
    _history(link, ["4.00", "4.00"])

    response = api.get(f"/api/inventory/items/{item.id}/")
    summary = response.data["price_trend_summary"]
    assert summary["trend"] == "stable"
    assert summary["change_percentage"] == Decimal("0.00")


def test_an_ordinary_price_rise_is_reported_unchanged(api):
    """CONTROL. The invariant, on the item-detail trend summary."""
    item = _item("Priced")
    link = _link(item, "Acme", unit_cost="5.00", is_primary=True)
    _history(link, ["4.00", "5.00"])

    response = api.get(f"/api/inventory/items/{item.id}/")
    summary = response.data["price_trend_summary"]
    assert summary["trend"] == "increasing"
    assert summary["change_percentage"] == Decimal("25.00")


def _webhook_payload_for(item, quantity=6):
    """The outbound reorder webhook body for a fresh request against ``item``."""
    from unittest.mock import patch

    from reorder_queue.models import ReorderRequest
    from reorder_queue.tasks import trigger_reorder_request_webhook

    req = ReorderRequest.objects.create(item=_fresh(item), quantity=quantity)
    with patch("reorder_queue.tasks.send_webhook_notification") as webhook:
        trigger_reorder_request_webhook(req.id)
    call = webhook.run.call_args or webhook.delay.call_args
    assert call is not None, "the webhook task dispatched neither eagerly nor async"
    return call[0][1]


def test_the_reorder_webhook_announces_a_free_request_as_costing_zero():
    """BEFORE/AFTER on the CLAIM. Payload: the outbound reorder webhook.

    Discord/Slack were told a request for a donated item had no estimated cost
    at all, because the payload re-collapsed the real ``Decimal("0.00")`` with
    ``if request.estimated_cost``.
    """
    item = _item("Donated")
    _link(item, "Charity", unit_cost="0.00", is_primary=True)
    assert _webhook_payload_for(item)["data"]["estimated_cost"] == 0.0


def test_the_reorder_webhook_still_sends_null_for_an_unpriced_request():
    """CONTROL. A price nobody recorded is still an absence."""
    item = _item("Unpriced")
    _link(item, "Acme", unit_cost=None, is_primary=True)
    assert _webhook_payload_for(item)["data"]["estimated_cost"] is None


def test_the_admin_renders_a_free_reorder_request_as_a_real_zero():
    """BEFORE/AFTER on the CLAIM. Screen: the ReorderRequest admin changelist.

    The em dash means "not known". A request for a donated item is known, and
    it costs $0.00.
    """
    from django.contrib.admin.sites import AdminSite

    from reorder_queue.admin import ReorderRequestAdmin
    from reorder_queue.models import ReorderRequest

    item = _item("Donated")
    _link(item, "Charity", unit_cost="0.00", is_primary=True)
    req = ReorderRequest.objects.create(item=_fresh(item), quantity=6)

    admin = ReorderRequestAdmin(ReorderRequest, AdminSite())
    assert admin.estimated_cost_display(req) == "$0.00"


def test_the_admin_still_dashes_a_reorder_request_it_cannot_cost():
    """CONTROL. The dash is reserved for the genuine absence."""
    from django.contrib.admin.sites import AdminSite

    from reorder_queue.admin import ReorderRequestAdmin
    from reorder_queue.models import ReorderRequest

    item = _item("Unpriced")
    _link(item, "Acme", unit_cost=None, is_primary=True)
    req = ReorderRequest.objects.create(item=_fresh(item), quantity=6)

    admin = ReorderRequestAdmin(ReorderRequest, AdminSite())
    assert admin.estimated_cost_display(req) == "-"


def test_the_admin_renders_a_comped_order_line_as_a_real_zero():
    """BEFORE/AFTER on the CLAIM. Screen: the PurchaseOrderItem admin inline.

    ``PurchaseOrderItem.estimated_cost`` is NON-nullable and returns
    ``Decimal("0.00")`` for a free line, so the dash there has always meant the
    wrong thing — the reader kept the derivation from reaching the surface.
    """
    from django.contrib.admin.sites import AdminSite

    from reorder_queue.admin import PurchaseOrderItemAdmin

    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00", is_primary=True)
    user = User.objects.create_user(username="po-admin", password="pw")
    order = _draft_for(link.supplier, user)
    line = PurchaseOrderItem.objects.create(
        purchase_order=order,
        item_supplier=link,
        quantity_ordered=4,
        unit_cost_ordered=Decimal("0.0000"),
        order_in_packages=1,
    )

    admin = PurchaseOrderItemAdmin(PurchaseOrderItem, AdminSite())
    assert admin.estimated_cost_display(line) == "$0.00"


# ── Round 3: the public transparency payload, and a label of its own ─────────


def _free_transparency_request(actual_cost=None):
    """A pending request for a DONATED item that the transparency feed shows.

    ``order_number`` is one of the six OR'd conditions the transparency
    queryset selects on, so setting it is what puts the row on the public feed.
    """
    from reorder_queue.models import ReorderRequest

    item = _item("Donated")
    _link(item, "Charity", unit_cost="0.00", is_primary=True)
    return ReorderRequest.objects.create(
        item=_fresh(item),
        quantity=6,
        order_number="PO-FREE-1",
        actual_cost=actual_cost,
    )


def _transparency_row(api, request_id):
    response = api.get(TRANSPARENCY_URL)
    assert response.status_code == 200
    return next(r for r in response.data["orders"] if r["id"] == request_id)


def test_the_public_transparency_feed_publishes_a_free_order_as_costing_zero(api):
    """BEFORE/AFTER on the CLAIM. Payload: the public AllowAny transparency feed.

    ``float(order.estimated_cost) if order.estimated_cost else None`` re-collapsed
    the real ``Decimal("0.00")`` the derivation now returns, so the community
    feed published ``estimated_cost: null`` — "we do not know what this cost" —
    for a request whose cost is a known $0.00.
    """
    req = _free_transparency_request()

    assert _transparency_row(api, req.id)["estimated_cost"] == 0.0


def test_the_public_transparency_feed_still_publishes_null_for_an_unpriced_order(api):
    """CONTROL. ``null`` keeps meaning "no price is on file"."""
    from reorder_queue.models import ReorderRequest

    item = _item("Unpriced")
    _link(item, "Acme", unit_cost=None, is_primary=True)
    req = ReorderRequest.objects.create(item=_fresh(item), quantity=6, order_number="PO-UNPRICED-1")

    assert _transparency_row(api, req.id)["estimated_cost"] is None


def test_the_public_transparency_feed_computes_a_variance_against_a_free_estimate(api):
    """BEFORE/AFTER on the CLAIM. Same payload, the ``cost_variance`` column.

    ``if (order.actual_cost and order.estimated_cost)`` refused to subtract from
    a known ``0.00`` estimate, so a donated item that ended up being invoiced
    published no variance at all — the one number that says the estimate was
    wrong.
    """
    req = _free_transparency_request(actual_cost=Decimal("12.00"))

    assert _transparency_row(api, req.id)["cost_variance"] == 12.0


def test_the_public_transparency_ledger_carries_the_free_estimate_too(api):
    """BEFORE/AFTER on the CLAIM. The ledger block is a SECOND copy of the read.

    Two spellings of the same collapse in one response body is exactly the
    "all but one site" shape the gate exists to stop.
    """
    req = _free_transparency_request()

    response = api.get(TRANSPARENCY_URL)
    entry = next(e for e in response.data["ledger"] if e["id"] == req.id)
    assert entry["estimated_cost"] == 0.0


def test_the_public_transparency_feed_reports_no_variance_it_cannot_stand_behind(api):
    """CONTROL restored to base. The two halves of the payload must agree.

    ``ReorderRequest.actual_cost`` is an operator-typed nullable column this
    branch deliberately does NOT own, so a recorded ``0.00`` there still
    publishes ``actual_cost: null``. A variance computed against it would be a
    number that can only be true if the actual cost is a known ``0.00`` —
    published beside a field saying it is unknown. ``cost_variance`` is gated on
    the SAME predicate ``actual_cost`` is, so the exclusion boundary does not
    run through one arithmetic expression.
    """
    from reorder_queue.models import ReorderRequest

    item = _item("Priced")
    _link(item, "Acme", unit_cost="2.00", is_primary=True)
    req = ReorderRequest.objects.create(
        item=_fresh(item),
        quantity=5,
        order_number="PO-COMPED-1",
        actual_cost=Decimal("0.00"),
    )

    row = _transparency_row(api, req.id)
    assert row["estimated_cost"] == 10.0
    assert row["actual_cost"] is None
    assert row["cost_variance"] is None


def test_the_public_transparency_feed_prices_a_free_purchase_order_at_zero(api):
    """BEFORE/AFTER on the CLAIM. The THIRD block of the same public payload.

    ``PurchaseOrder.estimated_total`` is NON-nullable with
    ``default=Decimal("0.00")``, so ``null`` was never a true answer for it —
    the falsy guard could only ever mislabel a real zero as "we do not know
    what this cost". A donated line writes ``unit_cost_ordered = 0.00``, which
    sums to an order total of ``0.00``, and the community feed published that
    as ``null`` (op-9m2v).
    """
    from reorder_queue.models import PurchaseOrder, PurchaseOrderItem

    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00", is_primary=True)
    user = User.objects.create_user(username="po-transparency", password="pw")
    order = PurchaseOrder.objects.create(
        supplier=link.supplier, status=PurchaseOrder.Status.SENT, created_by=user
    )
    PurchaseOrderItem.objects.create(
        purchase_order=order,
        item_supplier=link,
        quantity_ordered=4,
        unit_cost_ordered=Decimal("0.0000"),
        order_in_packages=1,
    )
    order.estimated_total = order.calculate_estimated_total()
    order.save(update_fields=["estimated_total"])

    response = api.get(TRANSPARENCY_URL)
    assert response.status_code == 200
    row = next(p for p in response.data["purchase_orders"] if p["id"] == str(order.id))
    assert row["estimated_total"] == 0.0
    assert row["actual_total"] is None


def _po_with_lines_at(unit_cost, user_name, status=None):
    """A purchase order whose one line is priced at ``unit_cost``, total rolled."""
    from reorder_queue.models import PurchaseOrder, PurchaseOrderItem

    item = _item(f"Item-{user_name}")
    link = _link(item, f"Supplier-{user_name}", unit_cost=unit_cost, is_primary=True)
    user = User.objects.create_user(username=user_name, password="pw")
    order = PurchaseOrder.objects.create(
        supplier=link.supplier,
        status=status or PurchaseOrder.Status.SENT,
        created_by=user,
    )
    PurchaseOrderItem.objects.create(
        purchase_order=order,
        item_supplier=link,
        quantity_ordered=4,
        unit_cost_ordered=Decimal(unit_cost).quantize(Decimal("0.0001")),
        order_in_packages=1,
    )
    order.estimated_total = order.calculate_estimated_total()
    order.save(update_fields=["estimated_total"])
    return order


def _po_admin():
    from django.contrib.admin.sites import AdminSite

    from reorder_queue.admin import PurchaseOrderAdmin
    from reorder_queue.models import PurchaseOrder

    return PurchaseOrderAdmin(PurchaseOrder, AdminSite())


def test_the_admin_shows_a_free_purchase_order_as_costing_zero():
    """BEFORE/AFTER on the CLAIM. Screen: the PurchaseOrder changelist's Est. Total.

    The SCREEN twin of the transparency payload's ``estimated_total``, on the
    same non-nullable-with-default column: ``None`` is never one of its
    answers, so the falsy guard could only ever render a known $0.00 as the em
    dash that means "we cannot cost this" everywhere else in that file
    (op-9m2v).
    """
    order = _po_with_lines_at("0.00", "po-admin-free")

    assert _po_admin().estimated_total_display(order) == "$0.00"


def test_the_admin_is_unchanged_for_a_priced_purchase_order():
    """CONTROL. The invariant, on the same column."""
    order = _po_with_lines_at("2.00", "po-admin-priced")

    assert _po_admin().estimated_total_display(order) == "$8.00"


def test_the_public_transparency_feed_is_unchanged_for_a_priced_purchase_order(api):
    """CONTROL. The invariant, on the purchase-order block."""
    from reorder_queue.models import PurchaseOrder, PurchaseOrderItem

    item = _item("Priced")
    link = _link(item, "Acme", unit_cost="2.00", is_primary=True)
    user = User.objects.create_user(username="po-transparency2", password="pw")
    order = PurchaseOrder.objects.create(
        supplier=link.supplier, status=PurchaseOrder.Status.SENT, created_by=user
    )
    PurchaseOrderItem.objects.create(
        purchase_order=order,
        item_supplier=link,
        quantity_ordered=5,
        unit_cost_ordered=Decimal("2.0000"),
        order_in_packages=1,
    )
    order.estimated_total = order.calculate_estimated_total()
    order.save(update_fields=["estimated_total"])

    response = api.get(TRANSPARENCY_URL)
    row = next(p for p in response.data["purchase_orders"] if p["id"] == str(order.id))
    assert row["estimated_total"] == 10.0


def test_the_public_transparency_feed_is_unchanged_for_a_priced_order(api):
    """CONTROL. The invariant, on the public payload."""
    from reorder_queue.models import ReorderRequest

    item = _item("Priced")
    _link(item, "Acme", unit_cost="2.00", is_primary=True)
    req = ReorderRequest.objects.create(
        item=_fresh(item),
        quantity=5,
        order_number="PO-PRICED-1",
        actual_cost=Decimal("12.00"),
    )

    row = _transparency_row(api, req.id)
    assert row["estimated_cost"] == 10.0
    assert row["actual_cost"] == 12.0
    assert row["cost_variance"] == 2.0


def test_a_rise_from_free_is_reported_under_its_own_label(api):
    """BEFORE/AFTER. Screen: the item detail's ``price_trend_summary``.

    Base said ``{"trend": "no_change", "change_percentage": 0}`` — an undefined
    percentage as a confident zero. The first fix said ``no_data``, which is the
    label for "a snapshot records NO PRICE AT ALL" and threw away the two prices
    that ARE known. ``no_baseline`` is neither: the percentage has no answer, the
    direction does, and both costs stay on the payload.
    """
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="4.00", is_primary=True)
    _history(link, ["0.00", "4.00"])

    response = api.get(f"/api/inventory/items/{item.id}/")
    assert response.status_code == 200
    summary = response.data["price_trend_summary"]
    assert summary["trend"] == "no_baseline"
    assert summary["direction"] == "increasing"
    assert summary["change_percentage"] is None
    assert summary["previous_cost"] == Decimal("0.00")
    assert summary["latest_cost"] == Decimal("4.00")
    assert summary["last_updated"] is not None


def test_a_snapshot_with_no_price_at_all_keeps_the_no_data_label(api):
    """CONTROL. The two labels must not collapse back into one.

    ``no_data`` is a genuine absence — there is no price to show and no
    direction to give — and it is a DIFFERENT fact from ``no_baseline``.
    """
    item = _item("Mystery")
    link = _link(item, "Acme", unit_cost="4.00", is_primary=True)
    _history(link, ["4.00", None])

    response = api.get(f"/api/inventory/items/{item.id}/")
    summary = response.data["price_trend_summary"]
    assert summary["trend"] == "no_data"
    assert summary["change_percentage"] is None
    assert "direction" not in summary


def test_a_drop_to_free_is_a_measurable_minus_one_hundred_percent(api):
    """CONTROL. A zero on the LATEST side has a percentage and keeps ``trend``.

    Only a zero BASELINE is undefined, so this must not be swept into
    ``no_baseline`` along with it.
    """
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00", is_primary=True)
    _history(link, ["5.00", "0.00"])

    response = api.get(f"/api/inventory/items/{item.id}/")
    summary = response.data["price_trend_summary"]
    assert summary["trend"] == "decreasing"
    assert summary["change_percentage"] == Decimal("-100.00")


def test_two_free_snapshots_have_no_baseline_and_no_direction_either(api):
    """CONTROL on the direction. 0.00 -> 0.00 has no percentage and no movement."""
    item = _item("Donated")
    link = _link(item, "Charity", unit_cost="0.00", is_primary=True)
    _history(link, ["0.00", "0.00"])

    response = api.get(f"/api/inventory/items/{item.id}/")
    summary = response.data["price_trend_summary"]
    assert summary["trend"] == "no_baseline"
    assert summary["direction"] == "stable"
    assert summary["change_percentage"] is None
