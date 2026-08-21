"""Adding the OTHER two line shapes to a draft PO — asset and freeform.

Salvaged from PR #1019, fitted onto the identifier-based add-lines endpoint
that landed in #1020.

``PurchaseOrderItem`` has always had three targets and ``create_purchase_order``
has always accepted all three, but the add-a-line endpoint only spoke the
inventory one. That gap is the same one the whole feature exists to close: an
order that turns out to be missing a tracked asset, or a one-off freight
charge, could still only be fixed by deleting the PO and retyping it. The three
shapes now go through one endpoint and one set of guards.

The identifier ladder is deliberately NOT involved here — an asset is named by
id and a freeform line is only prose — so these tests pin the shape dispatch,
the price rule, the supplier boundary, and the uniqueness behaviour, and leave
the ladder to ``test_po_add_line_item``.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from inventory.models import Supplier
from inventory.tests.factories import AssetFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderAuditEvent, PurchaseOrderItem

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


@pytest.fixture
def supplier():
    return Supplier.objects.create(name="Acme Fasteners")


@pytest.fixture
def draft_po(staff_user, supplier):
    return PurchaseOrder.objects.create(
        supplier=supplier,
        created_by=staff_user,
        status=PurchaseOrder.Status.DRAFT,
        estimated_total=Decimal("0.00"),
    )


@pytest.fixture
def acme_asset(supplier):
    """An asset Acme made — so this order's supplier can legally sell it."""
    return AssetFactory(name="Bandsaw blade guide", manufacturer=supplier)


def add_line(client, po, payload):
    return client.post(f"/api/reorders/purchase-orders/{po.id}/items/", payload, format="json")


# --------------------------------------------------------------------------
# Asset lines
# --------------------------------------------------------------------------


def test_an_asset_can_be_added_to_a_draft(staff_client, draft_po, acme_asset):
    response = add_line(
        staff_client, draft_po, {"asset": str(acme_asset.id), "unit_cost": "149.00"}
    )

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["created"] is True
    assert body["line_item"]["item_type"] == "asset"
    assert body["match"] is None

    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.asset_id == acme_asset.id
    assert line.item_supplier_id is None
    assert line.quantity_ordered == 1
    assert line.unit_cost_ordered == Decimal("149.0000")
    # Assets carry no packaging, exactly as the create path writes them.
    assert line.order_in_packages == 0

    draft_po.refresh_from_db()
    assert draft_po.estimated_total == Decimal("149.00")
    assert body["purchase_order"]["estimated_total"] == "149.00"


def test_an_asset_line_honours_an_explicit_quantity(staff_client, draft_po, acme_asset):
    response = add_line(
        staff_client,
        draft_po,
        {"asset": str(acme_asset.id), "unit_cost": "10.00", "quantity": 3},
    )

    assert response.status_code == 201, response.json()
    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.quantity_ordered == 3
    draft_po.refresh_from_db()
    assert draft_po.estimated_total == Decimal("30.00")


def test_an_asset_needs_a_price(staff_client, draft_po, acme_asset):
    """No supplier relationship, no purchase history — nothing to price it from.

    The create path refuses an asset line with no ``unit_cost`` for exactly this
    reason; adding one late cannot be laxer, or a zero-cost line would land on
    the order and understate it.
    """
    response = add_line(staff_client, draft_po, {"asset": str(acme_asset.id)})

    assert response.status_code == 400
    details = response.json()["error"]["details"]
    assert "unit_cost" in details
    assert "an asset line" in details["unit_cost"][0]


def test_an_asset_from_another_manufacturer_is_refused(staff_client, draft_po):
    """The supplier boundary the inventory path gets from ``ItemSupplier``.

    An asset has no catalogue row to scope a lookup through, so the check is
    explicit — otherwise anything would be orderable from anybody, provided you
    waited until after the PO existed.
    """
    other = Supplier.objects.create(name="Rival Tooling")
    asset = AssetFactory(name="Rival spindle", manufacturer=other)

    response = add_line(staff_client, draft_po, {"asset": str(asset.id), "unit_cost": "5.00"})

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "supplier_mismatch"
    assert "Rival Tooling" in body["error"]
    assert not PurchaseOrderItem.objects.filter(purchase_order=draft_po).exists()


def test_an_asset_with_no_recorded_manufacturer_is_allowed(staff_client, draft_po):
    """Unknown provenance is not evidence of a mismatch — the create path's reading."""
    asset = AssetFactory(name="Salvaged vise", manufacturer=None)

    response = add_line(staff_client, draft_po, {"asset": str(asset.id), "unit_cost": "20.00"})

    assert response.status_code == 201, response.json()


def test_an_unknown_asset_id_is_a_clean_refusal(staff_client, draft_po):
    response = add_line(
        staff_client,
        draft_po,
        {"asset": "11111111-1111-4111-8111-111111111111", "unit_cost": "1.00"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "not_found"


@pytest.mark.parametrize(
    "re_add_cost",
    [
        pytest.param("149.00", id="identical-string"),
        pytest.param("149", id="no-decimal-places"),
        pytest.param("149.0000", id="stored-precision"),
    ],
)
def test_re_adding_an_asset_at_the_same_price_grows_the_existing_line(
    staff_client, draft_po, acme_asset, re_add_cost
):
    """``(purchase_order, asset)`` is unique_together, so a second line is impossible.

    Same resolution the inventory path documents: grow the line rather than
    surface the database's IntegrityError. By one, not by a package — an asset
    is a discrete thing with no case size.

    The parametrised prices all name the same money as the stored 149.0000, so
    they must all be accepted: the comparison is numeric, not textual.
    """
    first = add_line(staff_client, draft_po, {"asset": str(acme_asset.id), "unit_cost": "149.00"})
    assert first.status_code == 201

    second = add_line(
        staff_client, draft_po, {"asset": str(acme_asset.id), "unit_cost": re_add_cost}
    )

    assert second.status_code == 200
    assert second.json()["created"] is False
    assert PurchaseOrderItem.objects.filter(purchase_order=draft_po).count() == 1
    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.quantity_ordered == 2
    assert line.unit_cost_ordered == Decimal("149.0000")
    draft_po.refresh_from_db()
    assert draft_po.estimated_total == Decimal("298.00")


def test_re_adding_an_asset_at_a_different_price_is_refused(staff_client, draft_po, acme_asset):
    """A re-add may grow a line; it may not silently reprice the units on it.

    This function already refuses to overwrite an existing work order or
    committee. A price is the more consequential field — quietly rewriting an
    asset line from 149.00 to 1.00 changes what the shop believes it is
    committing to spend — so a conflicting price is refused the same way, and
    the refused request leaves the line exactly as it was.
    """
    assert (
        add_line(
            staff_client, draft_po, {"asset": str(acme_asset.id), "unit_cost": "149.00"}
        ).status_code
        == 201
    )

    response = add_line(staff_client, draft_po, {"asset": str(acme_asset.id), "unit_cost": "1.00"})

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "price_conflict"
    assert "149" in body["error"]
    assert "1.00" in body["error"]

    assert PurchaseOrderItem.objects.filter(purchase_order=draft_po).count() == 1
    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.quantity_ordered == 1
    assert line.unit_cost_ordered == Decimal("149.0000")
    draft_po.refresh_from_db()
    assert draft_po.estimated_total == Decimal("149.00")


def test_a_voided_asset_line_is_refused_rather_than_resurrected(staff_client, draft_po, acme_asset):
    add_line(staff_client, draft_po, {"asset": str(acme_asset.id), "unit_cost": "10.00"})
    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    line.is_voided = True
    line.save(update_fields=["is_voided"])

    response = add_line(staff_client, draft_po, {"asset": str(acme_asset.id), "unit_cost": "10.00"})

    assert response.status_code == 400
    assert response.json()["code"] == "line_voided"


# --------------------------------------------------------------------------
# Freeform lines
# --------------------------------------------------------------------------


def test_a_freeform_line_can_be_added_to_a_draft(staff_client, draft_po):
    response = add_line(
        staff_client,
        draft_po,
        {"description": "Pallet freight surcharge", "unit_cost": "75.00"},
    )

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["created"] is True
    assert body["line_item"]["item_type"] == "freeform"

    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    assert line.description == "Pallet freight surcharge"
    assert line.item_supplier_id is None
    assert line.asset_id is None
    assert line.quantity_ordered == 1
    assert line.order_in_packages == 0
    draft_po.refresh_from_db()
    assert draft_po.estimated_total == Decimal("75.00")


def test_a_freeform_line_needs_a_price(staff_client, draft_po):
    response = add_line(staff_client, draft_po, {"description": "Mystery charge"})

    assert response.status_code == 400
    details = response.json()["error"]["details"]
    assert "unit_cost" in details
    assert "a freeform line" in details["unit_cost"][0]


def test_two_freeform_lines_with_the_same_words_are_two_lines(staff_client, draft_po):
    """No uniqueness and nothing to match on — merging them would fuse unrelated charges."""
    add_line(staff_client, draft_po, {"description": "Freight", "unit_cost": "10.00"})
    second = add_line(staff_client, draft_po, {"description": "Freight", "unit_cost": "12.00"})

    assert second.status_code == 201
    assert second.json()["created"] is True
    assert PurchaseOrderItem.objects.filter(purchase_order=draft_po).count() == 2
    draft_po.refresh_from_db()
    assert draft_po.estimated_total == Decimal("22.00")


# --------------------------------------------------------------------------
# The shared guards apply to every shape, not just the inventory one
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"unit_cost": "1.00"}, id="asset"),
        pytest.param({"description": "Freight", "unit_cost": "1.00"}, id="freeform"),
    ],
)
def test_no_shape_may_be_added_to_a_sent_order(staff_client, draft_po, acme_asset, payload):
    draft_po.status = PurchaseOrder.Status.SENT
    draft_po.save(update_fields=["status"])
    payload = {**payload}
    if "description" not in payload:
        payload["asset"] = str(acme_asset.id)

    response = add_line(staff_client, draft_po, payload)

    assert response.status_code == 400
    assert response.json()["code"] == "not_draft"
    assert not PurchaseOrderItem.objects.filter(purchase_order=draft_po).exists()


@pytest.mark.parametrize(
    "payload,reason",
    [
        pytest.param({}, "nothing named", id="nothing"),
        pytest.param(
            {"description": "Freight", "asset": "11111111-1111-4111-8111-111111111111"},
            "two shapes",
            id="two-shapes",
        ),
        pytest.param(
            {"identifier": "bolt", "description": "Freight"},
            "identifier and freeform",
            id="identifier-plus-freeform",
        ),
    ],
)
def test_exactly_one_shape_must_be_named(staff_client, draft_po, payload, reason):
    response = add_line(staff_client, draft_po, {**payload, "unit_cost": "1.00"})

    assert response.status_code == 400, reason
    assert not PurchaseOrderItem.objects.filter(purchase_order=draft_po).exists()


def test_the_audit_event_names_the_shape_it_wrote(staff_client, draft_po, acme_asset):
    """An audit reader should not have to infer the shape from which key is present."""
    add_line(staff_client, draft_po, {"asset": str(acme_asset.id), "unit_cost": "10.00"})

    event = PurchaseOrderAuditEvent.objects.filter(
        action=PurchaseOrderAuditEvent.Action.PO_LINE_ADD
    ).latest("created_at")
    assert event.metadata["line_shape"] == "asset"
    assert event.metadata["asset_id"] == str(acme_asset.id)
    assert event.metadata["item_supplier"] is None


def test_the_audit_event_still_names_the_item_on_a_freeform_line(staff_client, draft_po):
    add_line(staff_client, draft_po, {"description": "Crating", "unit_cost": "9.00"})

    event = PurchaseOrderAuditEvent.objects.filter(
        action=PurchaseOrderAuditEvent.Action.PO_LINE_ADD
    ).latest("created_at")
    assert event.metadata["line_shape"] == "freeform"
    assert event.metadata["description"] == "Crating"
    assert event.metadata["asset_id"] is None


def test_there_is_exactly_one_add_lines_endpoint(staff_client, draft_po):
    """One implementation, one route (the salvage's whole point).

    PR #1019 added a second, batch-shaped ``POST .../items/``; #1020's
    identifier-based one landed first. If a second ever reappears, the router
    will carry a second action for the same path and this fails.
    """
    from reorder_queue.views import PurchaseOrderViewSet

    add_actions = [
        name
        for name in dir(PurchaseOrderViewSet)
        if getattr(getattr(PurchaseOrderViewSet, name, None), "url_path", None) == "items"
        and "post" in getattr(getattr(PurchaseOrderViewSet, name, None), "mapping", {})
    ]
    assert add_actions == ["add_item"]
