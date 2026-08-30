"""Every surface that answers "which supplier?" answers it the same way (op-2rsp).

``inventory/tests/test_supplier_selection.py`` pins the derivation itself. These
tests go through the REAL endpoints and services the derivation feeds, because
the defect this closes was never in the rule — it was that six surfaces read a
rule with no orderability filter while the reorder recommendations engine
applied one, so the same item could be quoted from a supplier that had been
marked discontinued.

The fixture shape is deliberately the awkward one: **the cheapest supplier is
discontinued**. Under the old rule it won every one of these surfaces, because
selection was ``ORDER BY -is_primary, unit_cost`` with nothing filtered.

One surface is deliberately absent: ``PurchaseOrderViewSet._find_best_supplier``
ranks the orderable candidates by a weighted cost/lead-time score instead of by
cost alone. It already filters orderability, so it never had this defect;
whether its RANKING should replace "cheapest" as the fallback when nothing is
flagged primary is an open product decision that changes what gets bought, and
is not settled here.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from inventory.models import InventoryItem, ItemSupplier, Supplier
from reorder_queue.models import ReorderRequest

pytestmark = pytest.mark.django_db


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


def _link(item, name, *, unit_cost, lead=7, **flags):
    return ItemSupplier.objects.create(
        item=item,
        supplier=Supplier.objects.create(name=name, supplier_type=Supplier.SupplierType.LOCAL),
        supplier_sku=f"{name}-sku",
        unit_cost=Decimal(unit_cost),
        quantity_per_package=1,
        average_lead_time=lead,
        is_primary=flags.get("is_primary", False),
        is_active=flags.get("is_active", True),
        is_discontinued=flags.get("is_discontinued", False),
    )


def _cheap_dead_dear_live(item):
    """The awkward pair: cheapest is discontinued, only the dear one is buyable."""
    dead = _link(item, "DeadCheap", unit_cost="1.00", lead=1, is_discontinued=True)
    live = _link(item, "LiveDear", unit_cost="9.00", lead=20)
    return dead, live


@pytest.fixture
def api():
    client = APIClient()
    client.force_authenticate(
        user=get_user_model().objects.create_user(
            username="purchasing", password="pw", is_staff=True, is_superuser=True
        )
    )
    return client


# ── Item detail / list serializer: the flat compat fields ScanTTY reads ──────


def test_item_detail_never_names_a_discontinued_supplier(api):
    item = _item("Bolt")
    _cheap_dead_dear_live(item)

    response = api.get(f"/api/inventory/items/{item.id}/")

    assert response.status_code == 200, response.content
    assert response.data["supplier_name"] == "LiveDear"
    assert response.data["supplier_sku"] == "LiveDear-sku"
    assert Decimal(str(response.data["unit_cost"])) == Decimal("9.00")
    # The full array still shows every link, discontinued ones included — the
    # page dims them. Hiding them would lose the operator's own history.
    assert {row["supplier_name"] for row in response.data["all_suppliers"]} == {
        "DeadCheap",
        "LiveDear",
    }


def test_item_detail_reports_no_supplier_when_none_is_orderable(api):
    item = _item("Orphan")
    _link(item, "DeadOnly", unit_cost="1.00", is_discontinued=True)

    response = api.get(f"/api/inventory/items/{item.id}/")

    assert response.status_code == 200, response.content
    # Not the dead one dressed up as the choice. The link is still listed, so
    # the operator can see WHY there is no supplier name.
    assert response.data["supplier_name"] is None
    assert response.data["unit_cost"] is None
    assert [row["supplier_name"] for row in response.data["all_suppliers"]] == ["DeadOnly"]


# ── /metrics/ — the pinned ScanTTY TUI contract ──────────────────────────────


def test_metrics_costs_and_lead_time_come_from_the_orderable_supplier(api):
    item = _item("Nut")
    _cheap_dead_dear_live(item)

    response = api.get(f"/api/inventory/items/{item.id}/metrics/")

    assert response.status_code == 200, response.content
    assert Decimal(str(response.data["unit_cost"])) == Decimal("9.00")
    assert response.data["lead_time_days"] == 20


def test_metrics_says_nothing_rather_than_quoting_a_dead_supplier(api):
    item = _item("Ghost")
    _link(item, "DeadOnly", unit_cost="1.00", lead=1, is_discontinued=True)

    response = api.get(f"/api/inventory/items/{item.id}/metrics/")

    assert response.status_code == 200, response.content
    assert response.data["unit_cost"] is None
    assert response.data["lead_time_days"] is None


def test_metrics_batch_and_detail_agree_on_the_same_item(api):
    """``?with_metrics=1`` (batch) must not diverge from ``/metrics/`` (single)."""
    item = _item("Washer")
    _cheap_dead_dear_live(item)

    single = api.get(f"/api/inventory/items/{item.id}/metrics/")
    listing = api.get("/api/inventory/items/?with_metrics=1")

    assert single.status_code == 200 and listing.status_code == 200
    rows = [row for row in listing.data["results"] if str(row["id"]) == str(item.id)]
    assert rows, listing.data
    assert rows[0]["metrics"]["unit_cost"] == single.data["unit_cost"]
    assert rows[0]["metrics"]["lead_time_days"] == single.data["lead_time_days"]


# ── The order pad: a part number an operator pastes into a vendor's site ─────


def test_order_pad_groups_under_a_supplier_that_still_sells_the_item(api):
    item = _item("Screw")
    _cheap_dead_dear_live(item)
    ReorderRequest.objects.create(
        item=item,
        quantity=3,
        status=ReorderRequest.Status.APPROVED,
        requested_by="tester",
    )

    response = api.get("/api/reorders/requests/generate_cart_links/")

    assert response.status_code == 200, response.content
    assert "DeadCheap" not in response.data
    assert "LiveDear" in response.data
    assert "LiveDear-sku" in response.data["LiveDear"]["csv"]


def test_order_pad_omits_an_item_no_supplier_can_fill(api):
    item = _item("Unbuyable")
    _link(item, "DeadOnly", unit_cost="1.00", is_discontinued=True)
    ReorderRequest.objects.create(
        item=item,
        quantity=3,
        status=ReorderRequest.Status.APPROVED,
        requested_by="tester",
    )

    response = api.get("/api/reorders/requests/generate_cart_links/")

    assert response.status_code == 200, response.content
    assert response.data == {}


# ── Pending requests grouped by supplier ─────────────────────────────────────


def test_pending_requests_group_under_the_orderable_supplier(api):
    item = _item("Anchor")
    _cheap_dead_dear_live(item)
    ReorderRequest.objects.create(
        item=item,
        quantity=2,
        status=ReorderRequest.Status.PENDING,
        requested_by="tester",
    )

    response = api.get("/api/reorders/requests/by_supplier/")

    assert response.status_code == 200, response.content
    assert {group["supplier"] for group in response.data} == {"LiveDear"}


def test_pending_request_with_no_orderable_supplier_is_not_filed_under_a_dead_one(api):
    item = _item("Stranded")
    _link(item, "DeadOnly", unit_cost="1.00", is_discontinued=True)
    ReorderRequest.objects.create(
        item=item,
        quantity=2,
        status=ReorderRequest.Status.PENDING,
        requested_by="tester",
    )

    response = api.get("/api/reorders/requests/by_supplier/")

    assert response.status_code == 200, response.content
    # "No Supplier" is the existing bucket for an item with nothing to buy from;
    # what matters is that the discontinued vendor does not get a heading.
    assert "DeadOnly" not in {group["supplier"] for group in response.data}


# ── The PO-building screen names what it cannot offer ────────────────────────


def test_reorder_data_names_a_low_item_no_supplier_can_fill(api):
    stranded = _item("Stranded")
    _link(stranded, "DeadOnly", unit_cost="1.00", is_discontinued=True)
    _item("Bare")
    buyable = _item("Buyable")
    _link(buyable, "Live", unit_cost="2.00")

    response = api.get("/api/reorders/purchase-orders/reorder_data/")

    assert response.status_code == 200, response.content
    reported = {row["item_name"]: row for row in response.data["items_without_orderable_supplier"]}
    assert set(reported) == {"Stranded", "Bare"}

    # The two reasons are distinct, and each names an action the operator can take.
    assert reported["Stranded"]["reason"] == "none_orderable"
    assert "discontinued" in reported["Stranded"]["detail"]
    assert "add a supplier" in reported["Stranded"]["detail"]

    assert reported["Bare"]["reason"] == "no_suppliers"
    assert "No supplier is linked" in reported["Bare"]["detail"]

    # The buyable item is still offered as normal, under its live supplier only.
    assert {group["name"] for group in response.data["suppliers"]} == {"Live"}


def test_reorder_data_offers_no_supplier_group_for_a_discontinued_only_item(api):
    item = _item("Screw")
    _cheap_dead_dear_live(item)

    response = api.get("/api/reorders/purchase-orders/reorder_data/")

    assert response.status_code == 200, response.content
    assert {group["name"] for group in response.data["suppliers"]} == {"LiveDear"}
    assert response.data["items_without_orderable_supplier"] == []


# ── Lead-time forecasting reads the same supplier as everything else ─────────


def _serialized_item(name):
    return _item(name, is_serialized=True)


def _forecast_row(item):
    from inventory.services.component_forecast import build_component_forecast

    rows = [row for row in build_component_forecast() if row["item_id"] == str(item.id)]
    assert rows, "item missing from forecast"
    return rows[0]


def test_forecast_lead_time_uses_the_orderable_supplier():
    item = _serialized_item("Fuse")
    _cheap_dead_dear_live(item)

    assert _forecast_row(item)["lead_time_days"] == 20.0


def test_forecast_lead_time_is_unknown_when_nothing_is_orderable():
    """``None`` is "we cannot tell you", which is the truth — not a dead vendor's 1 day."""
    item = _serialized_item("Relay")
    _link(item, "DeadOnly", unit_cost="1.00", lead=1, is_discontinued=True)

    assert _forecast_row(item)["lead_time_days"] is None


def test_forecast_lead_time_matches_the_item_property_it_claims_to_mirror():
    """It previously ordered by ``-is_primary`` alone and could pick another row."""
    item = _serialized_item("Diode")
    _link(item, "Dear", unit_cost="9.00", lead=30)
    _link(item, "Cheap", unit_cost="1.00", lead=3)

    fresh = InventoryItem.objects.get(pk=item.pk)
    assert _forecast_row(item)["lead_time_days"] == float(fresh.average_lead_time)


# ── Printed kanban card ──────────────────────────────────────────────────────


def test_kanban_card_lead_time_comes_from_a_supplier_you_can_still_buy_from():
    """The card is printed and stuck on a shelf; a dead vendor's promise outlives it."""
    item = _item("Grommet")
    _cheap_dead_dear_live(item)

    fresh = InventoryItem.objects.prefetch_related("item_suppliers__supplier").get(pk=item.pk)
    assert fresh.average_lead_time == 20
