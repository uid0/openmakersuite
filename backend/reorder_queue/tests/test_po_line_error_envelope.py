"""Every coded line refusal answers in the STANDARDIZED error envelope.

The envelope is ``config.api_errors``' — ``{"error": {"code", "message"}}``,
with machine-readable hints under ``error.details`` — and it is what every
client in front of this API already knows how to read: ScanTTY's ``parseError``
takes ``error.code``/``error.message`` off it, and the web app's
``extractErrorMessage`` reads ``error.message`` first of all.

The line endpoints used to hand-build ``{"error": "<prose>", "code": "<code>"}``
instead. The remedy sentence still reached the operator, so nothing was lost —
but it reached them as raw JSON on the terminal, which is the difference between
a refusal they can act on and a refusal they have to decode. ``send_to_supplier``
was converted first; these are its siblings, and a sibling that answers the SAME
refusal (``not_draft``) in a different wire shape is the drift this file exists
to pin (#1054).

Everything here drives the REAL HTTP endpoints, and asserts the shape for MORE
THAN ONE code, because a single converted branch proves nothing about the
branch beside it.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from inventory.models import InventoryItem, ItemSupplier, Supplier
from inventory.tests.factories import CategoryFactory, LocationFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem

User = get_user_model()
pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


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
def other_supplier():
    return Supplier.objects.create(name="Beta Hardware")


@pytest.fixture
def draft_po(staff_user, supplier):
    return PurchaseOrder.objects.create(
        supplier=supplier,
        created_by=staff_user,
        status=PurchaseOrder.Status.DRAFT,
        estimated_total=Decimal("0.00"),
    )


def make_item(name, sku):
    return InventoryItem.objects.create(
        name=name,
        description="",
        sku=sku,
        category=CategoryFactory(),
        location=LocationFactory(),
        current_stock=0,
        minimum_stock=10,
        reorder_quantity=4,
    )


@pytest.fixture
def bolt(supplier):
    return ItemSupplier.objects.create(
        item=make_item("M3 hex bolt", "OMS-M3-BOLT"),
        supplier=supplier,
        supplier_sku="ACME-M3-100",
        unit_cost=Decimal("0.25"),
    )


def add_line(client, po, payload):
    return client.post(f"/api/reorders/purchase-orders/{po.id}/items/", payload, format="json")


def delete_line(client, po, line):
    return client.delete(f"/api/reorders/purchase-orders/{po.id}/items/{line.pk}/")


def assert_envelope(response, *, code, status_code=400):
    """The shape assertion itself, in one place.

    It is deliberately strict about what must NOT be there: the old hand-built
    body put the sentence at top-level ``error`` and the code at top-level
    ``code``, and a conversion that merely ADDED the envelope beside them would
    pass a looser check while leaving two shapes on the wire.
    """
    assert response.status_code == status_code, response.json()
    body = response.json()
    assert set(body) == {"error"}, body
    envelope = body["error"]
    assert isinstance(envelope, dict), body
    assert envelope["code"] == code, body
    assert isinstance(envelope["message"], str) and envelope["message"].strip(), body
    return envelope


# --------------------------------------------------------------------------
# POST .../items/ — the add endpoint
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "po_status",
    [PurchaseOrder.Status.SENT, PurchaseOrder.Status.CONFIRMED, PurchaseOrder.Status.CANCELLED],
)
def test_add_refuses_a_non_draft_in_the_envelope(staff_client, draft_po, bolt, po_status):
    PurchaseOrder.objects.filter(pk=draft_po.pk).update(status=po_status)

    response = add_line(staff_client, draft_po, {"item_supplier": bolt.pk})

    envelope = assert_envelope(response, code="not_draft")
    # The remedy text is preserved verbatim — this change made the reason
    # legible, it did not reword the reason.
    assert PurchaseOrder.Status(po_status).label in envelope["message"]


def test_add_refuses_a_voided_line_in_the_envelope(staff_client, staff_user, draft_po, bolt):
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

    envelope = assert_envelope(response, code="line_voided")
    assert "M3 hex bolt" in envelope["message"]


def test_add_refuses_an_item_the_supplier_does_not_carry_in_the_envelope(
    staff_client, draft_po, other_supplier
):
    elsewhere = ItemSupplier.objects.create(
        item=make_item("M5 carriage bolt", "OMS-M5-CARR"),
        supplier=other_supplier,
        supplier_sku="BETA-M5-500",
        unit_cost=Decimal("0.60"),
    )

    response = add_line(staff_client, draft_po, {"item_supplier": elsewhere.pk})

    envelope = assert_envelope(response, code="supplier_mismatch")
    assert "Acme Fasteners" in envelope["message"]


def test_add_refuses_an_unmatched_identifier_in_the_envelope(staff_client, draft_po, bolt):
    response = add_line(staff_client, draft_po, {"identifier": "not-a-thing"})

    envelope = assert_envelope(response, code="no_match")
    assert "Acme Fasteners" in envelope["message"]


def test_add_refuses_a_missing_price_on_a_freeform_line_in_the_envelope(staff_client, draft_po):
    response = add_line(staff_client, draft_po, {"description": "Pallet freight surcharge"})

    assert_envelope(response, code="validation_failed")


def test_the_ambiguous_choice_set_rides_in_error_details(staff_client, draft_po, supplier, bolt):
    """409 ``ambiguous`` still carries its candidates — under ``error.details``.

    ``error.details`` is where ``config.api_errors`` puts machine-readable
    hints, so the choice set goes there rather than staying beside the envelope
    at top level. Losing it would turn "pick one of these two" into a dead end.
    """
    sibling = ItemSupplier.objects.create(
        item=make_item("M3 hex nut", "OMS-M3-NUT"),
        supplier=supplier,
        supplier_sku="ACME-M3-200",
        unit_cost=Decimal("0.40"),
    )

    response = add_line(staff_client, draft_po, {"identifier": "M3 hex"})

    envelope = assert_envelope(response, code="ambiguous", status_code=409)
    candidates = envelope["details"]["candidates"]
    assert {c["item_supplier"] for c in candidates} == {bolt.pk, sibling.pk}
    assert not PurchaseOrderItem.objects.filter(purchase_order=draft_po).exists()


def test_add_refuses_an_unknown_work_order_in_the_envelope(staff_client, draft_po, bolt):
    """A refusal raised in the VIEW, not the service — same envelope either way."""
    response = add_line(
        staff_client,
        draft_po,
        {"item_supplier": bolt.pk, "work_order": "99999999-9999-4999-8999-999999999999"},
    )

    envelope = assert_envelope(response, code="work_order_not_found")
    assert "99999999-9999-4999-8999-999999999999" in envelope["message"]


# --------------------------------------------------------------------------
# DELETE .../items/<id>/ — the add endpoint's mirror
# --------------------------------------------------------------------------


def test_delete_refuses_a_sent_order_in_the_envelope(staff_client, staff_user, draft_po, bolt):
    line = PurchaseOrderItem.objects.create(
        purchase_order=draft_po,
        item_supplier=bolt,
        quantity_ordered=4,
        unit_cost_ordered=Decimal("2.50"),
        order_in_packages=1,
    )
    PurchaseOrder.objects.filter(pk=draft_po.pk).update(status=PurchaseOrder.Status.SENT)

    response = delete_line(staff_client, draft_po, line)

    envelope = assert_envelope(response, code="not_draft")
    # A refusal is only doing its job if the operator can act on it.
    assert "void" in envelope["message"].lower()
    assert PurchaseOrderItem.objects.filter(pk=line.pk).exists()


def test_delete_refuses_a_received_line_in_the_envelope(staff_client, draft_po, bolt):
    line = PurchaseOrderItem.objects.create(
        purchase_order=draft_po,
        item_supplier=bolt,
        quantity_ordered=4,
        unit_cost_ordered=Decimal("2.50"),
        order_in_packages=1,
    )
    PurchaseOrderItem.objects.filter(pk=line.pk).update(quantity_received=3)

    response = delete_line(staff_client, draft_po, line)

    envelope = assert_envelope(response, code="line_received")
    assert "3" in envelope["message"]
    assert PurchaseOrderItem.objects.filter(pk=line.pk).exists()


# --------------------------------------------------------------------------
# The drift this file exists to close (#1054)
# --------------------------------------------------------------------------


def test_not_draft_answers_in_ONE_shape_across_the_sibling_endpoints(
    staff_client, staff_user, draft_po, bolt
):
    """Add, delete and send all refuse ``not_draft`` — in the same wire shape.

    Three endpoints on one resource, one refusal, one shape. The add endpoint
    answering this in a shape of its own is what sent the operator a raw body
    at the step where losing the reason costs them the order.
    """
    line = PurchaseOrderItem.objects.create(
        purchase_order=draft_po,
        item_supplier=bolt,
        quantity_ordered=4,
        unit_cost_ordered=Decimal("2.50"),
        order_in_packages=1,
    )
    PurchaseOrder.objects.filter(pk=draft_po.pk).update(status=PurchaseOrder.Status.CONFIRMED)

    added = add_line(staff_client, draft_po, {"item_supplier": bolt.pk})
    deleted = delete_line(staff_client, draft_po, line)
    sent = staff_client.post(f"/api/reorders/purchase-orders/{draft_po.id}/send_to_supplier/")

    for response in (added, deleted, sent):
        assert_envelope(response, code="not_draft")
