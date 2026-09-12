"""The ``line_voided`` refusal names an action that EXISTS.

The refusal used to read "Restore or remove that line before ordering it
again.". Nothing in OpenMakerSuite restores a voided purchase-order line, and
the set was derived rather than assumed:

* ``is_voided`` is only ever written ``True`` — by
  :func:`reorder_queue.services.purchase_orders.void_line_item` and by the
  PO-level void beside it. No service, view, serializer or management command
  writes it back to ``False``.
* ``PurchaseOrderViewSet.update_item`` (``PATCH .../items/<id>/``) reads a
  closed list of keys off the request and ``is_voided`` is not among them, so
  the one writable door onto a line cannot un-void one either. Its sibling
  ``void_item`` only goes one way, and refuses a line that is already voided.

So "Restore" sent the operator looking for a button that does not exist. What
DOES exist here is deletion, and it is available every time this refusal fires:
``assert_addable`` has already proved the order is in
``PurchaseOrder.PRE_SUPPLIER_STATUSES``, and ``assert_deletable`` gates
``DELETE .../items/<id>/`` on that same frozenset — so the remedy the message
names cannot be refused by the endpoint it points at. That invariant is
asserted below rather than argued, on both line shapes that carry the refusal.

Everything here drives the REAL HTTP endpoints; the message is what a client
renders, so a unit call on the service would not prove the operator sees it.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from inventory.models import InventoryItem, ItemSupplier, Supplier
from inventory.tests.factories import AssetFactory, CategoryFactory, LocationFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.services.line_entry import VOIDED_LINE_REMEDY

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
def bolt(supplier):
    item = InventoryItem.objects.create(
        name="M3 hex bolt",
        description="",
        sku="OMS-M3-BOLT",
        category=CategoryFactory(),
        location=LocationFactory(),
        current_stock=0,
        minimum_stock=10,
        reorder_quantity=4,
    )
    return ItemSupplier.objects.create(
        item=item,
        supplier=supplier,
        supplier_sku="ACME-M3-100",
        unit_cost=Decimal("0.25"),
    )


@pytest.fixture
def acme_asset(supplier):
    """An asset Acme made — so this order's supplier can legally sell it."""
    return AssetFactory(name="Bandsaw blade guide", manufacturer=supplier)


def add_line(client, po, payload):
    return client.post(f"/api/reorders/purchase-orders/{po.id}/items/", payload, format="json")


def delete_line(client, po, line):
    return client.delete(f"/api/reorders/purchase-orders/{po.id}/items/{line.pk}/")


def assert_names_a_real_remedy(message):
    """The message must not promise a restore, and must name deletion instead."""
    assert "restore" not in message.lower(), message
    assert "delete" in message.lower(), message


# --------------------------------------------------------------------------
# The inventory shape
# --------------------------------------------------------------------------


def test_a_voided_inventory_line_is_refused_without_promising_a_restore(
    staff_client, staff_user, draft_po, bolt
):
    PurchaseOrderItem.objects.create(
        purchase_order=draft_po,
        item_supplier=bolt,
        quantity_ordered=4,
        unit_cost_ordered=Decimal("2.50"),
        order_in_packages=1,
        is_voided=True,
        voided_by=staff_user,
    )

    response = add_line(staff_client, draft_po, {"item_supplier": bolt.pk})

    assert response.status_code == 400, response.json()
    envelope = response.json()["error"]
    assert envelope["code"] == "line_voided"
    message = envelope["message"]
    assert "M3 hex bolt" in message
    # The fact, stated rather than implied by the absence of a remedy.
    assert "cannot be un-voided" in message
    assert_names_a_real_remedy(message)


def test_voiding_through_the_endpoint_pre_empts_line_voided_with_discontinued(
    staff_client, draft_po, bolt
):
    """Why "then order it again" needs no caveat about the catalogue entry.

    ``void_line_item`` also marks the line's ``ItemSupplier`` discontinued and
    inactive, so "delete it, then order it again" looks like a remedy whose
    second half the next request would reject — the same failure one step
    further along. It is not, and this is the proof rather than the argument:
    an operator whose link has been struck off never SEES ``line_voided``.
    Naming the ``item_supplier`` explicitly is refused by
    ``resolve_item_supplier`` first, with ``discontinued`` and its own sentence.

    So a ``line_voided`` refusal is itself evidence the link is still live, and
    the plain remedy holds unconditionally. If this test ever fails, the
    ``line_voided`` message has become reachable with a struck-off link and owes
    the operator a clause about reinstating it.
    """
    line = PurchaseOrderItem.objects.create(
        purchase_order=draft_po,
        item_supplier=bolt,
        quantity_ordered=4,
        unit_cost_ordered=Decimal("2.50"),
        order_in_packages=1,
    )
    voided = staff_client.post(
        f"/api/reorders/purchase-orders/{draft_po.id}/items/{line.pk}/void/",
        {"reason": "discontinued by supplier"},
        format="json",
    )
    assert voided.status_code == 200, voided.json()
    bolt.refresh_from_db()
    assert bolt.is_discontinued is True

    refused = add_line(staff_client, draft_po, {"item_supplier": bolt.pk})

    assert refused.status_code == 400, refused.json()
    assert refused.json()["error"]["code"] == "discontinued"


def test_the_scanned_identifier_path_also_pre_empts_it(staff_client, draft_po, bolt):
    """The sibling door, because one proved path proves nothing about the other.

    ``lookup_candidates`` reports a discontinued link as *unavailable* rather
    than offering it, so the scan path cannot reach ``line_voided`` with a
    struck-off link either.
    """
    line = PurchaseOrderItem.objects.create(
        purchase_order=draft_po,
        item_supplier=bolt,
        quantity_ordered=4,
        unit_cost_ordered=Decimal("2.50"),
        order_in_packages=1,
    )
    staff_client.post(
        f"/api/reorders/purchase-orders/{draft_po.id}/items/{line.pk}/void/",
        {"reason": "discontinued by supplier"},
        format="json",
    )

    refused = add_line(staff_client, draft_po, {"identifier": "ACME-M3-100"})

    assert refused.status_code == 400, refused.json()
    assert refused.json()["error"]["code"] != "line_voided", refused.json()


def test_the_whole_refusal_sentence_is_pinned(staff_client, staff_user, draft_po, bolt):
    """The exact wording, once — the remedy is a pinned contract, not a vibe.

    ScanTTY renders this sentence verbatim, and the substring assertions above
    would all still pass if the remedy silently grew a clause naming something
    else the operator cannot do. ``VOIDED_LINE_REMEDY`` is asserted through the
    HTTP boundary rather than read back from the module, so the constant and what
    goes on the wire cannot drift apart.
    """
    PurchaseOrderItem.objects.create(
        purchase_order=draft_po,
        item_supplier=bolt,
        quantity_ordered=4,
        unit_cost_ordered=Decimal("2.50"),
        order_in_packages=1,
        is_voided=True,
        voided_by=staff_user,
    )

    body = add_line(staff_client, draft_po, {"item_supplier": bolt.pk}).json()
    message = body["error"]["message"]

    assert message == (
        f"M3 hex bolt is already on {draft_po.po_number} as a voided line, and a "
        f"voided line cannot be un-voided. Delete that line first, then order it again."
    )
    assert VOIDED_LINE_REMEDY in message


# --------------------------------------------------------------------------
# The asset shape
# --------------------------------------------------------------------------


def test_a_voided_asset_line_is_refused_without_promising_a_restore(
    staff_client, draft_po, acme_asset
):
    add_line(staff_client, draft_po, {"asset": str(acme_asset.id), "unit_cost": "10.00"})
    line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
    line.is_voided = True
    line.save(update_fields=["is_voided"])

    response = add_line(staff_client, draft_po, {"asset": str(acme_asset.id), "unit_cost": "10.00"})

    assert response.status_code == 400, response.json()
    envelope = response.json()["error"]
    assert envelope["code"] == "line_voided"
    message = envelope["message"]
    assert "Bandsaw blade guide" in message
    assert "cannot be un-voided" in message
    assert_names_a_real_remedy(message)


# --------------------------------------------------------------------------
# The remedy the message names actually works
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["inventory", "asset"])
def test_the_delete_the_refusal_names_is_accepted_on_the_order_that_refused(
    staff_client, staff_user, draft_po, bolt, acme_asset, shape
):
    """A refusal is only doing its job when the operator can act on it.

    Both shapes, because a remedy proved on one line shape proves nothing about
    the branch beside it.
    """
    if shape == "inventory":
        line = PurchaseOrderItem.objects.create(
            purchase_order=draft_po,
            item_supplier=bolt,
            quantity_ordered=4,
            unit_cost_ordered=Decimal("2.50"),
            order_in_packages=1,
            is_voided=True,
            voided_by=staff_user,
        )
        payload = {"item_supplier": bolt.pk}
    else:
        add_line(staff_client, draft_po, {"asset": str(acme_asset.id), "unit_cost": "10.00"})
        line = PurchaseOrderItem.objects.get(purchase_order=draft_po)
        line.is_voided = True
        line.save(update_fields=["is_voided"])
        payload = {"asset": str(acme_asset.id), "unit_cost": "10.00"}

    refused = add_line(staff_client, draft_po, payload)
    assert refused.status_code == 400
    assert_names_a_real_remedy(refused.json()["error"]["message"])

    # The door the message points at opens.
    deleted = delete_line(staff_client, draft_po, line)
    assert deleted.status_code == 200, deleted.json()
    assert not PurchaseOrderItem.objects.filter(pk=line.pk).exists()
