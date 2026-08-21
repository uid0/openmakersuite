"""Adding a line to a draft purchase order by typed/scanned identifier (oms-po-add-item).

The operator has the thing in front of them and names it however it is
labelled — item name, item SKU, package barcode, unit barcode, or the vendor's
part number. The order's supplier decides whether that is a legal line at all,
and the server is where that is decided: ScanTTY drives the same endpoints the
web UI does.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

import pytest
from rest_framework.test import APIClient

from inventory.models import InventoryItem, ItemSupplier, Supplier, WorkOrder
from inventory.tests.factories import AssetFactory, CategoryFactory, LocationFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderAuditEvent, PurchaseOrderItem
from reorder_queue.services import line_entry

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_user():
    return User.objects.create_user(username="quartermaster", password="x", is_staff=True)


@pytest.fixture
def staff_client(staff_user):
    api = APIClient()
    api.force_authenticate(user=staff_user)
    return api


def make_item(name, sku, *, minimum_stock=10, current_stock=0, reorder_quantity=4):
    return InventoryItem.objects.create(
        name=name,
        description="",
        sku=sku,
        category=CategoryFactory(),
        location=LocationFactory(),
        current_stock=current_stock,
        minimum_stock=minimum_stock,
        reorder_quantity=reorder_quantity,
    )


@pytest.fixture
def supplier():
    return Supplier.objects.create(name="Acme Fasteners")


@pytest.fixture
def bolt(supplier):
    """An M3 bolt Acme sells: vendor SKU, both barcodes, $2.50/unit, case of 5."""
    item = make_item("M3 hex bolt", "OMS-M3-HEX")
    return ItemSupplier.objects.create(
        item=item,
        supplier=supplier,
        supplier_sku="ACME-M3-100",
        package_upc="012345678905",
        unit_upc="998877665544",
        unit_cost=Decimal("2.50"),
        quantity_per_package=5,
    )


@pytest.fixture
def draft_po(staff_user, supplier):
    return PurchaseOrder.objects.create(
        supplier=supplier,
        created_by=staff_user,
        status=PurchaseOrder.Status.DRAFT,
        estimated_total=Decimal("0.00"),
    )


def add_line(client, po, payload):
    return client.post(f"/api/reorders/purchase-orders/{po.id}/items/", payload, format="json")


def lookup(client, po, query):
    return client.get(f"/api/reorders/purchase-orders/{po.id}/item-lookup/", {"q": query})


# --------------------------------------------------------------------------
# AC-1 / AC-2: lookup resolves all four identifier kinds, in supplier context
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected_kind",
    [
        ("M3 hex bolt", "item_name"),
        ("OMS-M3-HEX", "item_sku"),
        ("012345678905", "package_barcode"),
        ("998877665544", "unit_barcode"),
        ("ACME-M3-100", "vendor_sku"),
    ],
)
def test_lookup_resolves_every_identifier_kind(staff_client, draft_po, bolt, query, expected_kind):
    response = lookup(staff_client, draft_po, query)

    assert response.status_code == 200
    body = response.json()
    assert body["resolves"] is True
    assert body["best_match_kind"] == expected_kind
    assert [c["item_supplier"] for c in body["candidates"]] == [bolt.pk]
    candidate = body["candidates"][0]
    assert candidate["item"]["name"] == "M3 hex bolt"
    assert candidate["match_kind"] == expected_kind
    assert candidate["is_exact"] is True
    assert candidate["already_on_order"] is None


def test_lookup_is_case_insensitive_for_typed_identifiers(staff_client, draft_po, bolt):
    body = lookup(staff_client, draft_po, "acme-m3-100").json()

    assert body["best_match_kind"] == "vendor_sku"
    assert [c["item_supplier"] for c in body["candidates"]] == [bolt.pk]


def test_lookup_returns_the_candidate_set_when_a_partial_name_is_ambiguous(
    staff_client, draft_po, supplier, bolt
):
    sibling = ItemSupplier.objects.create(
        item=make_item("M3 hex nut", "OMS-M3-NUT"),
        supplier=supplier,
        supplier_sku="ACME-M3-200",
        unit_cost=Decimal("0.40"),
    )

    body = lookup(staff_client, draft_po, "M3 hex").json()

    assert body["best_match_kind"] == "partial_item_name"
    # Ambiguous, so the operator picks — the server does not choose for them.
    assert body["resolves"] is False
    assert {c["item_supplier"] for c in body["candidates"]} == {bolt.pk, sibling.pk}


def test_an_exact_vendor_sku_outranks_a_partial_name_match(staff_client, draft_po, supplier, bolt):
    """A string that is one item's exact SKU and another's partial name resolves.

    The whole point of the tier ladder: the scan-and-Enter path stays a single
    round trip instead of stopping to ask about an unrelated near-miss.
    """
    ItemSupplier.objects.create(
        item=make_item("Spare ACME-M3-100 replacement head", "OMS-SPARE"),
        supplier=supplier,
        supplier_sku="ACME-SPARE",
        unit_cost=Decimal("1.00"),
    )

    body = lookup(staff_client, draft_po, "ACME-M3-100").json()

    assert body["best_match_kind"] == "vendor_sku"
    assert body["resolves"] is True
    assert body["candidates"][0]["item_supplier"] == bolt.pk


def test_lookup_names_the_item_and_supplier_when_another_vendor_carries_it(
    staff_client, draft_po, supplier
):
    other = Supplier.objects.create(name="Bolt Depot")
    ItemSupplier.objects.create(
        item=make_item("M5 carriage bolt", "OMS-M5-CAR"),
        supplier=other,
        supplier_sku="BD-M5",
        unit_cost=Decimal("1.10"),
    )

    body = lookup(staff_client, draft_po, "BD-M5").json()

    assert body["candidates"] == []
    assert body["unavailable"][0]["reason"] == "not_supplied"
    assert "Acme Fasteners does not supply M5 carriage bolt" in body["unavailable"][0]["message"]


def test_lookup_reports_a_discontinued_relationship_rather_than_a_bare_miss(
    staff_client, draft_po, bolt
):
    bolt.is_discontinued = True
    bolt.is_active = False
    bolt.save()

    body = lookup(staff_client, draft_po, "ACME-M3-100").json()

    assert body["candidates"] == []
    assert body["unavailable"][0]["reason"] == "discontinued"
    assert "no longer supplies M3 hex bolt" in body["unavailable"][0]["message"]


def test_lookup_flags_an_item_already_on_the_order(staff_client, draft_po, bolt):
    line = PurchaseOrderItem.objects.create(
        purchase_order=draft_po,
        item_supplier=bolt,
        quantity_ordered=5,
        unit_cost_ordered=Decimal("2.50"),
        order_in_packages=1,
    )

    body = lookup(staff_client, draft_po, "ACME-M3-100").json()

    assert body["candidates"][0]["already_on_order"] == {
        "line_item": str(line.pk),
        "quantity_ordered": 5,
        "is_voided": False,
    }


def test_lookup_reports_whether_the_order_still_accepts_lines(staff_client, draft_po, bolt):
    assert (
        lookup(staff_client, draft_po, "ACME-M3-100").json()["purchase_order"]["can_add_items"]
        is True
    )

    draft_po.status = PurchaseOrder.Status.SENT
    draft_po.save()

    assert (
        lookup(staff_client, draft_po, "ACME-M3-100").json()["purchase_order"]["can_add_items"]
        is False
    )


def test_a_blank_query_matches_nothing_rather_than_everything(staff_client, draft_po, bolt):
    body = lookup(staff_client, draft_po, "   ").json()

    assert body["candidates"] == []
    assert body["unavailable"] == []
    assert body["resolves"] is False


def test_lookup_requires_authentication(draft_po, bolt):
    response = lookup(APIClient(), draft_po, "ACME-M3-100")

    assert response.status_code in (401, 403)


# --------------------------------------------------------------------------
# AC-1 / AC-6: the happy path — a scan produces a fully-formed line
# --------------------------------------------------------------------------


def test_scanning_a_package_barcode_adds_a_line_with_derived_defaults(staff_client, draft_po, bolt):
    response = add_line(staff_client, draft_po, {"identifier": "012345678905"})

    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    assert body["match"]["match_kind"] == "package_barcode"
    assert body["match"]["item"]["name"] == "M3 hex bolt"

    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    # minimum 10 - stock 0 = 10, rounded up to Acme's case of 5.
    assert line.quantity_ordered == 10
    assert line.order_in_packages == 2
    assert line.unit_cost_ordered == Decimal("2.5000")
    assert line.item_supplier_id == bolt.pk

    draft_po.refresh_from_db()
    assert draft_po.estimated_total == Decimal("25.00")
    # The full refreshed order rides along so the caller can patch in place.
    assert body["purchase_order"]["estimated_total"] == "25.00"
    assert len(body["purchase_order"]["items"]) == 1


def test_an_explicit_quantity_and_cost_override_the_defaults(staff_client, draft_po, bolt):
    response = add_line(
        staff_client,
        draft_po,
        {"identifier": "ACME-M3-100", "quantity": 3, "unit_cost": "1.75", "notes": "rush"},
    )

    assert response.status_code == 201
    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.quantity_ordered == 3
    assert line.unit_cost_ordered == Decimal("1.7500")
    assert line.notes == "rush"


def test_a_relationship_without_a_price_falls_back_to_the_last_purchase(
    staff_client, staff_user, draft_po, supplier
):
    """AC-6: a priced-nowhere relationship still must not land the line at zero."""
    link = ItemSupplier.objects.create(
        item=make_item("Shop rag", "OMS-RAG", minimum_stock=6, reorder_quantity=6),
        supplier=supplier,
        supplier_sku="ACME-RAG",
    )
    assert link.unit_cost is None
    old_po = PurchaseOrder.objects.create(
        supplier=supplier, created_by=staff_user, status=PurchaseOrder.Status.RECEIVED
    )
    PurchaseOrderItem.objects.create(
        purchase_order=old_po,
        item_supplier=link,
        quantity_ordered=6,
        unit_cost_ordered=Decimal("3.20"),
        order_in_packages=6,
    )

    add_line(staff_client, draft_po, {"identifier": "ACME-RAG"})

    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.unit_cost_ordered == Decimal("3.2000")
    assert line.quantity_ordered == 6


def test_an_operator_may_add_by_explicit_item_supplier_after_choosing(staff_client, draft_po, bolt):
    response = add_line(staff_client, draft_po, {"item_supplier": bolt.pk, "quantity": 2})

    assert response.status_code == 201
    assert response.json()["match"] is None
    assert PurchaseOrderItem.objects.get(purchase_order=draft_po).quantity_ordered == 2


def test_adding_records_an_audit_event(staff_client, draft_po, bolt):
    add_line(staff_client, draft_po, {"identifier": "ACME-M3-100"})

    event = PurchaseOrderAuditEvent.objects.get(action=PurchaseOrderAuditEvent.Action.PO_LINE_ADD)
    assert event.purchase_order_id == draft_po.pk
    assert event.metadata["item_name"] == "M3 hex bolt"
    assert event.metadata["match_kind"] == "vendor_sku"
    assert event.metadata["created"] is True
    assert event.metadata["quantity_ordered"] == 10


def test_adding_requires_authentication(draft_po, bolt):
    response = add_line(APIClient(), draft_po, {"identifier": "ACME-M3-100"})

    assert response.status_code in (401, 403)
    assert not PurchaseOrderItem.objects.filter(purchase_order=draft_po).exists()


# --------------------------------------------------------------------------
# AC-3: the supplier-supplies-it check is server-side
# --------------------------------------------------------------------------


def test_adding_an_item_this_supplier_does_not_carry_is_rejected_by_identifier(
    staff_client, draft_po
):
    other = Supplier.objects.create(name="Bolt Depot")
    ItemSupplier.objects.create(
        item=make_item("M5 carriage bolt", "OMS-M5-CAR"),
        supplier=other,
        supplier_sku="BD-M5",
        unit_cost=Decimal("1.10"),
    )

    response = add_line(staff_client, draft_po, {"identifier": "BD-M5"})

    assert response.status_code == 400
    assert response.json()["code"] == "not_supplied"
    assert "Acme Fasteners does not supply M5 carriage bolt" in response.json()["error"]
    assert not PurchaseOrderItem.objects.filter(purchase_order=draft_po).exists()


def test_a_client_cannot_bypass_the_supplier_check_with_a_raw_item_supplier_id(
    staff_client, draft_po
):
    """The UI is not the boundary: a posted id from another vendor still fails."""
    other = Supplier.objects.create(name="Bolt Depot")
    foreign = ItemSupplier.objects.create(
        item=make_item("M5 carriage bolt", "OMS-M5-CAR"),
        supplier=other,
        supplier_sku="BD-M5",
        unit_cost=Decimal("1.10"),
    )

    response = add_line(staff_client, draft_po, {"item_supplier": foreign.pk})

    assert response.status_code == 400
    assert response.json()["code"] == "supplier_mismatch"
    error = response.json()["error"]
    assert "Acme Fasteners does not supply M5 carriage bolt" in error
    assert "Bolt Depot" in error
    assert not PurchaseOrderItem.objects.filter(purchase_order=draft_po).exists()


def test_adding_a_discontinued_relationship_is_rejected(staff_client, draft_po, bolt):
    bolt.is_discontinued = True
    bolt.is_active = False
    bolt.save()

    response = add_line(staff_client, draft_po, {"item_supplier": bolt.pk})

    assert response.status_code == 400
    assert response.json()["code"] == "discontinued"
    assert "no longer supplies M3 hex bolt" in response.json()["error"]


def test_an_identifier_matching_nothing_is_rejected(staff_client, draft_po, bolt):
    response = add_line(staff_client, draft_po, {"identifier": "not-a-thing"})

    assert response.status_code == 400
    assert response.json()["code"] == "no_match"
    assert "Acme Fasteners" in response.json()["error"]


def test_an_ambiguous_identifier_returns_the_choice_set_instead_of_guessing(
    staff_client, draft_po, supplier, bolt
):
    sibling = ItemSupplier.objects.create(
        item=make_item("M3 hex nut", "OMS-M3-NUT"),
        supplier=supplier,
        supplier_sku="ACME-M3-200",
        unit_cost=Decimal("0.40"),
    )

    response = add_line(staff_client, draft_po, {"identifier": "M3 hex"})

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "ambiguous"
    assert {c["item_supplier"] for c in body["candidates"]} == {bolt.pk, sibling.pk}
    assert not PurchaseOrderItem.objects.filter(purchase_order=draft_po).exists()


def test_naming_the_item_two_ways_at_once_is_rejected(staff_client, draft_po, bolt):
    response = add_line(
        staff_client, draft_po, {"identifier": "ACME-M3-100", "item_supplier": bolt.pk}
    )

    assert response.status_code == 400
    assert not PurchaseOrderItem.objects.filter(purchase_order=draft_po).exists()


def test_naming_the_item_no_way_at_all_is_rejected(staff_client, draft_po, bolt):
    response = add_line(staff_client, draft_po, {"quantity": 3})

    assert response.status_code == 400
    assert not PurchaseOrderItem.objects.filter(purchase_order=draft_po).exists()


# --------------------------------------------------------------------------
# AC-4: draft only
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "po_status",
    [
        PurchaseOrder.Status.SENT,
        PurchaseOrder.Status.CONFIRMED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
        PurchaseOrder.Status.RECEIVED,
        PurchaseOrder.Status.CANCELLED,
        PurchaseOrder.Status.VOIDED,
    ],
)
def test_adding_to_a_non_draft_order_is_rejected(staff_client, draft_po, bolt, po_status):
    draft_po.status = po_status
    draft_po.save()

    response = add_line(staff_client, draft_po, {"identifier": "ACME-M3-100"})

    assert response.status_code == 400
    assert response.json()["code"] == "not_draft"
    assert PurchaseOrder.Status(po_status).label in response.json()["error"]
    assert not PurchaseOrderItem.objects.filter(purchase_order=draft_po).exists()


def test_the_draft_guard_runs_before_the_lookup(staff_client, draft_po):
    """A sent order rejects on status, not on 'no such item' — the clearer answer."""
    draft_po.status = PurchaseOrder.Status.SENT
    draft_po.save()

    response = add_line(staff_client, draft_po, {"identifier": "whatever"})

    assert response.json()["code"] == "not_draft"


# --------------------------------------------------------------------------
# AC-5: invariants — XOR target, (po, item_supplier) uniqueness, quantity
# --------------------------------------------------------------------------


def test_re_adding_an_item_already_on_the_order_grows_the_existing_line(
    staff_client, draft_po, bolt
):
    """The documented duplicate rule: increment, never a second line.

    ``(purchase_order, item_supplier)`` is unique, so a second line is not
    merely undesirable — it is impossible. Scanning the same box twice means
    two of them.
    """
    first = add_line(staff_client, draft_po, {"identifier": "012345678905", "quantity": 4})
    assert first.status_code == 201

    second = add_line(staff_client, draft_po, {"identifier": "012345678905", "quantity": 6})

    assert second.status_code == 200
    assert second.json()["created"] is False
    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.quantity_ordered == 10
    # Package count is re-derived from the grown quantity, not left stale.
    assert line.order_in_packages == 2
    draft_po.refresh_from_db()
    assert draft_po.estimated_total == Decimal("25.00")


def test_growing_a_line_keeps_its_cost_unless_a_new_one_is_given(staff_client, draft_po, bolt):
    add_line(staff_client, draft_po, {"identifier": "ACME-M3-100", "quantity": 2, "unit_cost": "9"})

    add_line(staff_client, draft_po, {"identifier": "ACME-M3-100", "quantity": 2})
    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.unit_cost_ordered == Decimal("9.0000")

    add_line(
        staff_client, draft_po, {"identifier": "ACME-M3-100", "quantity": 1, "unit_cost": "4.25"}
    )
    line.refresh_from_db()
    assert line.unit_cost_ordered == Decimal("4.2500")
    assert line.quantity_ordered == 5


def test_re_adding_a_voided_line_is_refused_with_a_clear_message(
    staff_client, staff_user, draft_po, bolt
):
    line = PurchaseOrderItem.objects.create(
        purchase_order=draft_po,
        item_supplier=bolt,
        quantity_ordered=4,
        unit_cost_ordered=Decimal("2.50"),
        order_in_packages=1,
        is_voided=True,
        voided_by=staff_user,
    )

    response = add_line(staff_client, draft_po, {"item_supplier": bolt.pk})

    assert response.status_code == 400
    assert response.json()["code"] == "line_voided"
    assert "M3 hex bolt" in response.json()["error"]
    line.refresh_from_db()
    assert line.quantity_ordered == 4
    assert PurchaseOrderItem.objects.filter(purchase_order=draft_po).count() == 1


def test_a_non_positive_quantity_is_rejected(staff_client, draft_po, bolt):
    response = add_line(staff_client, draft_po, {"identifier": "ACME-M3-100", "quantity": 0})

    assert response.status_code == 400
    assert not PurchaseOrderItem.objects.filter(purchase_order=draft_po).exists()


def test_the_added_line_satisfies_the_typed_target_xor(staff_client, draft_po, bolt):
    add_line(staff_client, draft_po, {"identifier": "ACME-M3-100"})

    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.target_type == "inventory_item"
    assert line.asset_id is None
    assert line.description == ""
    # full_clean runs the CheckConstraint on Django 6 — proves the row is legal.
    line.full_clean()


def test_a_kit_line_added_this_way_freezes_its_bill_of_materials(staff_client, draft_po, supplier):
    """Kits decompose on receipt from the snapshot, so the add path must take one."""
    component = make_item("Toner cartridge", "OMS-TONER")
    kit = make_item("Printer starter kit", "OMS-KIT", minimum_stock=1, reorder_quantity=1)
    kit.is_kit = True
    kit.save()
    kit.kit_components.create(component=component, quantity=2)
    link = ItemSupplier.objects.create(
        item=kit, supplier=supplier, supplier_sku="ACME-KIT", unit_cost=Decimal("40.00")
    )

    response = add_line(staff_client, draft_po, {"item_supplier": link.pk})

    assert response.status_code == 201
    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.is_kit_line is True
    assert line.kit_snapshot["components"][0]["component"] == str(component.pk)
    assert line.kit_snapshot["components"][0]["quantity_per_kit"] == 2


# --------------------------------------------------------------------------
# AC-5: the repeat-scan increment is one supplier package, not a second
# full reorder suggestion
# --------------------------------------------------------------------------


def test_a_repeat_scan_grows_the_line_by_one_supplier_package(staff_client, draft_po, bolt):
    """A second scan means one more box, not a second reorder suggestion.

    The first add still lands on the full suggestion (minimum 10 - stock 0,
    rounded up to Acme's case of 5). Re-scanning the same box adds that one
    case, so an accidental double-scan costs a package rather than doubling the
    order.
    """
    first = add_line(staff_client, draft_po, {"identifier": "012345678905"})
    assert first.status_code == 201
    assert PurchaseOrderItem.objects.get(purchase_order=draft_po).quantity_ordered == 10

    second = add_line(staff_client, draft_po, {"identifier": "012345678905"})

    assert second.status_code == 200
    assert second.json()["created"] is False
    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.quantity_ordered == 15
    assert line.order_in_packages == 3


def test_a_repeat_scan_of_a_singles_item_grows_the_line_by_one(staff_client, draft_po, supplier):
    """``quantity_per_package`` of 1 degrades the package increment to +1."""
    link = ItemSupplier.objects.create(
        item=make_item("Shop rag", "OMS-RAG", minimum_stock=6, reorder_quantity=6),
        supplier=supplier,
        supplier_sku="ACME-RAG",
        unit_cost=Decimal("1.00"),
        quantity_per_package=1,
    )

    add_line(staff_client, draft_po, {"identifier": "ACME-RAG"})
    assert PurchaseOrderItem.objects.get(purchase_order=draft_po).quantity_ordered == 6

    add_line(staff_client, draft_po, {"identifier": "ACME-RAG"})

    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.quantity_ordered == 7
    assert line.item_supplier_id == link.pk


def test_an_explicit_quantity_still_wins_on_the_repeat_path(staff_client, draft_po, bolt):
    """The package increment is only the *default* — an explicit ask is verbatim."""
    add_line(staff_client, draft_po, {"identifier": "ACME-M3-100", "quantity": 2})

    add_line(staff_client, draft_po, {"identifier": "ACME-M3-100", "quantity": 3})

    assert PurchaseOrderItem.objects.get(purchase_order=draft_po).quantity_ordered == 5


# --------------------------------------------------------------------------
# AC-5 / AC-9: a work order or committee on a line being grown
# --------------------------------------------------------------------------


def test_growing_an_untagged_line_applies_the_supplied_work_order(staff_client, draft_po, bolt):
    """A tag on a grow request is applied, not silently dropped."""
    work_order = WorkOrder.objects.create(maintenance_item=None, asset=AssetFactory())
    add_line(staff_client, draft_po, {"identifier": "ACME-M3-100", "quantity": 2})

    response = add_line(
        staff_client,
        draft_po,
        {"identifier": "ACME-M3-100", "quantity": 2, "work_order": str(work_order.id)},
    )

    assert response.status_code == 200
    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.work_order_id == work_order.id
    assert line.quantity_ordered == 4


def test_growing_an_untagged_line_applies_the_supplied_committee(staff_client, draft_po, bolt):
    committee = Group.objects.create(name="Woodshop")
    add_line(staff_client, draft_po, {"identifier": "ACME-M3-100", "quantity": 2})

    response = add_line(
        staff_client,
        draft_po,
        {"identifier": "ACME-M3-100", "quantity": 2, "owning_group": committee.pk},
    )

    assert response.status_code == 200
    assert PurchaseOrderItem.objects.get(purchase_order=draft_po).owning_group_id == committee.pk


def test_a_work_order_clashing_with_the_lines_own_is_refused_and_changes_nothing(
    staff_client, draft_po, bolt
):
    """Neither silent outcome is acceptable, so the request is refused instead.

    Dropping the tag would report success for a half-applied request;
    overwriting would move an existing line's attribution to another job behind
    the operator's back. The message names both work orders.
    """
    original = WorkOrder.objects.create(maintenance_item=None, asset=AssetFactory())
    other = WorkOrder.objects.create(maintenance_item=None, asset=AssetFactory())
    add_line(
        staff_client,
        draft_po,
        {"identifier": "ACME-M3-100", "quantity": 2, "work_order": str(original.id)},
    )

    response = add_line(
        staff_client,
        draft_po,
        {"identifier": "ACME-M3-100", "quantity": 2, "work_order": str(other.id)},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "work_order_conflict"
    assert str(original) in body["error"]
    assert str(other) in body["error"]

    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.work_order_id == original.id
    assert line.quantity_ordered == 2


def test_a_committee_clashing_with_the_lines_own_is_refused_and_changes_nothing(
    staff_client, draft_po, bolt
):
    original = Group.objects.create(name="Woodshop")
    other = Group.objects.create(name="Metal shop")
    add_line(
        staff_client,
        draft_po,
        {"identifier": "ACME-M3-100", "quantity": 2, "owning_group": original.pk},
    )

    response = add_line(
        staff_client,
        draft_po,
        {"identifier": "ACME-M3-100", "quantity": 2, "owning_group": other.pk},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "owning_group_conflict"
    assert "Woodshop" in body["error"]
    assert "Metal shop" in body["error"]

    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.owning_group_id == original.pk
    assert line.quantity_ordered == 2


def test_re_supplying_the_tag_a_line_already_carries_is_not_a_conflict(
    staff_client, draft_po, bolt
):
    work_order = WorkOrder.objects.create(maintenance_item=None, asset=AssetFactory())
    payload = {"identifier": "ACME-M3-100", "quantity": 2, "work_order": str(work_order.id)}
    add_line(staff_client, draft_po, payload)

    response = add_line(staff_client, draft_po, payload)

    assert response.status_code == 200
    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.work_order_id == work_order.id
    assert line.quantity_ordered == 4


# --------------------------------------------------------------------------
# AC-2: a capped candidate list is reported as capped
# --------------------------------------------------------------------------


def _many_siblings(supplier, count):
    """``count`` items whose names all contain "widget", so one query matches all."""
    return [
        ItemSupplier.objects.create(
            item=make_item(f"widget {index:03d}", f"OMS-WID-{index:03d}"),
            supplier=supplier,
            supplier_sku=f"ACME-WID-{index:03d}",
            unit_cost=Decimal("1.00"),
        )
        for index in range(count)
    ]


def test_lookup_reports_the_true_match_count_when_the_list_is_capped(
    staff_client, draft_po, supplier
):
    """The cap must never be presented as the total — see DEFAULT_CANDIDATE_LIMIT."""
    _many_siblings(supplier, 25)

    body = lookup(staff_client, draft_po, "widget").json()

    assert len(body["candidates"]) == 20
    assert body["total_candidates"] == 25
    assert body["best_match_total"] == 25
    assert body["truncated"] is True
    assert body["resolves"] is False


def test_an_uncapped_lookup_says_so(staff_client, draft_po, supplier):
    _many_siblings(supplier, 3)

    body = lookup(staff_client, draft_po, "widget").json()

    assert len(body["candidates"]) == 3
    assert body["total_candidates"] == 3
    assert body["best_match_total"] == 3
    assert body["truncated"] is False


def test_the_ambiguity_message_counts_matches_not_the_capped_list(staff_client, draft_po, supplier):
    _many_siblings(supplier, 25)

    response = add_line(staff_client, draft_po, {"identifier": "widget"})

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "ambiguous"
    assert "matches 25 items" in body["error"]
    # The operator is told the offered choice set is only part of the matches.
    assert "The first 20" in body["error"]
    assert len(body["candidates"]) == 20


# --------------------------------------------------------------------------
# AC-5: a concurrent duplicate add cannot lose a quantity or 500
# --------------------------------------------------------------------------


def test_a_duplicate_insert_racing_the_add_falls_back_to_growing_the_line(
    monkeypatch, staff_user, draft_po, bolt
):
    """The unique constraint firing means someone else got there first, not a 500.

    Simulates the lost race directly: another client's line is committed
    between this add's existence check and its INSERT. The constraint then
    fires, and the documented grow-the-line behaviour has to take over instead
    of the IntegrityError escaping as a server error.
    """
    real_locked = line_entry._locked_existing_line
    calls = {"n": 0}

    def racing_lookup(purchase_order, item_supplier):
        calls["n"] += 1
        if calls["n"] == 1:
            # The pre-insert check answers "nothing there"...
            assert real_locked(purchase_order, item_supplier) is None
            # ...and the rival's line lands before we insert.
            PurchaseOrderItem.objects.create(
                purchase_order=purchase_order,
                item_supplier=item_supplier,
                quantity_ordered=7,
                unit_cost_ordered=Decimal("2.50"),
                order_in_packages=2,
            )
            return None
        return real_locked(purchase_order, item_supplier)

    monkeypatch.setattr(line_entry, "_locked_existing_line", racing_lookup)

    line, created = line_entry.add_line_item(draft_po, bolt, quantity=4)

    assert created is False
    assert PurchaseOrderItem.objects.filter(purchase_order=draft_po).count() == 1
    line.refresh_from_db()
    assert line.quantity_ordered == 11
