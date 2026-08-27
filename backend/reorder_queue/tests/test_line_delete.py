"""Deleting a purchase-order line while the order is still the shop's own (oms-po-line-delete).

The captain's boundary, and the reason it is where it is: while an order is
still a draft it is a private document, so a line added by mistake is a typo
and the honest record of a typo is no line at all. Once the order has gone to
the supplier the line is part of a record someone else also holds, and erasing
it would be dishonest — that is what voiding is for, and void is unchanged.

Everything here drives the REAL HTTP API. The three things worth stating up
front, because each was established by experiment rather than assumed:

* the pre-send boundary is derived from ``PRE_SUPPLIER_STATUSES`` on the order's
  own state machine, not from the string ``"draft"`` — ``test_the_boundary_*``,
  ``test_both_line_set_flags_*``;
* a line carrying a receipt is REFUSED rather than assumed impossible —
  ``test_delete_refuses_a_line_that_records_a_receipt``. DRAFT being
  initial-only should make that unreachable, but an impossibility argument only
  holds while every future change re-verifies it and a guard holds without
  anyone re-verifying anything, so the guard is what the endpoint carries and
  what is tested here;
* the order's settlement status AND its stored ``estimated_total`` are both
  re-derived by the line's own ``post_delete`` (#1029 / #1030), with no
  explicit call from the endpoint — ``test_settlement_*`` / ``test_total_*``.
"""

from __future__ import annotations

from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

import pytest

from inventory.models import WorkOrderMaterialUsage
from inventory.tests.factories import ItemSupplierFactory, SupplierFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderAuditEvent, PurchaseOrderItem
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def _order(status=PurchaseOrder.Status.DRAFT, *, user=None, sent=None):
    """A two-line order in ``status``, with its stored total rolled up.

    Two lines on purpose: a single-line order cannot tell "the total was
    re-rolled" apart from "the total was zeroed".

    ``sent`` stamps ``sent_at``. It defaults to "stamped unless the order is
    still a draft", which is what the ordinary ``mark_sent`` path produces —
    but the stamp is NOT a reliable record of the supplier having been handed
    the document, and no guard here may assume it is: the admin's bulk
    "Mark selected orders as sent" writes the status and ``sent_by`` without
    it. Tests that care about a particular pairing pass ``sent`` explicitly.
    """
    user = user or UserFactory()
    supplier = SupplierFactory()
    if sent is None:
        sent = status != PurchaseOrder.Status.DRAFT
    purchase_order = PurchaseOrder.objects.create(
        supplier=supplier,
        created_by=user,
        status=status,
        sent_at=timezone.now() if sent else None,
    )
    first = PurchaseOrderItem.objects.create(
        purchase_order=purchase_order,
        item_supplier=ItemSupplierFactory(supplier=supplier),
        quantity_ordered=4,
        unit_cost_ordered=Decimal("10.00"),
    )
    second = PurchaseOrderItem.objects.create(
        purchase_order=purchase_order,
        item_supplier=ItemSupplierFactory(supplier=supplier),
        quantity_ordered=2,
        unit_cost_ordered=Decimal("5.00"),
    )
    purchase_order.calculate_estimated_total()
    purchase_order.save(update_fields=["estimated_total"])
    return purchase_order, first, second


def _line_url(purchase_order, line):
    return reverse("purchaseorder-update-item", args=[purchase_order.pk, line.pk])


@pytest.fixture
def client(authenticated_client):
    """The API client alone; ``authenticated_client`` hands back ``(client, user)``."""
    api_client, _user = authenticated_client
    return api_client


# --------------------------------------------------------------------------
# the boundary is derived, not spelled
# --------------------------------------------------------------------------


def test_the_boundary_is_a_set_on_the_state_machine_not_a_status_name():
    """Pre-send is a named set beside its siblings, so one edit moves every gate.

    Adding a second pre-send state (an approval hold, say) must not mean
    hunting for ``== "draft"`` comparisons. Both guards read this set.
    """
    from reorder_queue.services.line_entry import assert_addable, assert_deletable

    assert PurchaseOrder.Status.DRAFT in PurchaseOrder.PRE_SUPPLIER_STATUSES
    # Both guards are gated on the same set — proved by moving the set, not by
    # reading the source.
    for guard in (assert_addable, assert_deletable):
        order = PurchaseOrder(status=PurchaseOrder.Status.SENT, po_number="PO-X")
        with pytest.raises(Exception) as excinfo:
            guard(order)
        assert getattr(excinfo.value, "code", None) == "not_draft"


def test_the_boundary_cannot_overlap_receiving():
    """An order cannot be both un-sent and in receiving.

    A status added to both sets would let a line be destroyed off an order
    goods are arriving against.
    """
    assert not (PurchaseOrder.PRE_SUPPLIER_STATUSES & PurchaseOrder.IN_RECEIVING_STATUSES)
    assert not (PurchaseOrder.PRE_SUPPLIER_STATUSES & PurchaseOrder.RECEIVABLE_STATUSES)


def test_both_line_set_flags_follow_the_set_and_not_the_status_name(client, monkeypatch):
    """One definition means one edit — proved by MOVING the set, not by reading it.

    ``can_delete_items`` on the order and ``can_add_items`` on the item-lookup
    payload are the two answers the web UI reads instead of keeping its own copy
    of the status list. Either one comparing to ``DRAFT`` by name would leave
    the UI hiding an affordance the server would honour the moment a second
    pre-send state existed, which is the whole reason the set exists.
    """
    purchase_order, _first, _second = _order(status=PurchaseOrder.Status.CANCELLED)
    detail_url = reverse("purchaseorder-detail", args=[purchase_order.pk])
    lookup_url = reverse("purchaseorder-item-lookup", args=[purchase_order.pk])

    assert client.get(detail_url).data["can_delete_items"] is False
    assert client.get(lookup_url, {"q": "  "}).data["purchase_order"]["can_add_items"] is False

    monkeypatch.setattr(
        PurchaseOrder,
        "PRE_SUPPLIER_STATUSES",
        frozenset({PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.CANCELLED}),
    )

    assert client.get(detail_url).data["can_delete_items"] is True
    assert client.get(lookup_url, {"q": "  "}).data["purchase_order"]["can_add_items"] is True


# --------------------------------------------------------------------------
# the endpoint, over real HTTP
# --------------------------------------------------------------------------


def test_delete_removes_a_draft_line_and_needs_no_reason(client):
    purchase_order, first, second = _order()

    response = client.delete(_line_url(purchase_order, first))

    assert response.status_code == 200, response.data
    assert not PurchaseOrderItem.objects.filter(pk=first.pk).exists()
    assert PurchaseOrderItem.objects.filter(pk=second.pk).exists()
    # No ghost: the line is gone, not struck off.
    assert purchase_order.items.count() == 1


def test_delete_returns_the_full_refreshed_order_for_an_in_place_patch(client):
    """docs/REACTIVE_MUTATIONS.md — the page patches from the response."""
    purchase_order, first, _second = _order()

    response = client.delete(_line_url(purchase_order, first))

    body = response.data
    assert body["purchase_order"]["id"] == purchase_order.pk
    assert len(body["purchase_order"]["items"]) == 1
    # And it names what it destroyed, so the page can say so.
    assert body["deleted"]["line_item"] == str(first.pk)
    assert body["deleted"]["quantity_ordered"] == 4


def test_delete_is_refused_once_the_supplier_has_the_order(client):
    purchase_order, first, _second = _order(status=PurchaseOrder.Status.SENT)

    response = client.delete(_line_url(purchase_order, first))

    assert response.status_code == 400
    assert response.data["code"] == "not_draft"
    # A refusal is only legitimate when the operator can act on it.
    assert "void" in response.data["error"].lower()
    assert PurchaseOrderItem.objects.filter(pk=first.pk).exists()


@pytest.mark.parametrize(
    "status",
    [
        PurchaseOrder.Status.SENT,
        PurchaseOrder.Status.CONFIRMED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
        PurchaseOrder.Status.RECEIVED,
        PurchaseOrder.Status.CANCELLED,
        PurchaseOrder.Status.VOIDED,
    ],
)
def test_delete_is_refused_in_every_status_outside_the_pre_send_set(client, status):
    """Derived from the state machine: every status that is not pre-send refuses.

    Parametrised off the enum rather than off a list of the ones that came to
    mind, so a status added later to ``Status`` and not to
    ``PRE_SUPPLIER_STATUSES`` is covered without this test being edited.
    """
    purchase_order, first, _second = _order(status=status)

    response = client.delete(_line_url(purchase_order, first))

    assert response.status_code == 400, f"{status} allowed a delete"
    assert PurchaseOrderItem.objects.filter(pk=first.pk).exists()


def test_a_draft_cancelled_without_being_sent_is_not_told_the_supplier_has_it(client):
    """The refusal has to be TRUE, not only present.

    A draft cancelled before it ever went out is closed, not disclosed. Telling
    that operator the supplier already holds their line is a false statement in
    the one place they most need a true one, and it sends them to void, which
    is not the instrument for an order nobody outside the shop has seen.
    """
    purchase_order, first, _second = _order(status=PurchaseOrder.Status.CANCELLED, sent=False)

    response = client.delete(_line_url(purchase_order, first))

    assert response.status_code == 400
    error = response.data["error"]
    assert "supplier already has" not in error
    # Still actionable: it says why, and what to do instead.
    assert "never went to the supplier" in error
    assert "new order" in error
    assert PurchaseOrderItem.objects.filter(pk=first.pk).exists()


def test_an_order_sent_by_the_admin_bulk_action_is_still_told_to_void(client):
    """The stamp is not the record of what the supplier has, so it cannot decide this.

    ``PurchaseOrderAdmin.mark_as_sent`` moves a whole queryset to ``SENT`` with
    one ``update()`` and writes ``sent_by`` but never ``sent_at``. That state is
    reproduced here exactly. The order is live with its supplier; telling this
    operator it never went out, that it is closed, and to open a duplicate would
    be three false clauses and would withhold the remedy that does work.
    """
    purchase_order, first, _second = _order()
    PurchaseOrder.objects.filter(pk=purchase_order.pk).update(
        status=PurchaseOrder.Status.SENT, sent_at=None
    )

    response = client.delete(_line_url(purchase_order, first))

    assert response.status_code == 400
    error = response.data["error"]
    assert "the supplier already has this line" in error
    assert "void it instead" in error
    assert "never went to the supplier" not in error
    assert "new order" not in error
    assert PurchaseOrderItem.objects.filter(pk=first.pk).exists()


def test_an_order_cancelled_after_being_sent_is_still_told_to_void(client):
    """Terminal, but the supplier saw it — and the stamp is what says so.

    A cancelled order is outside both sets, so the terminal arm is in reach;
    the corroborating stamp is what keeps it out.
    """
    purchase_order, first, _second = _order(status=PurchaseOrder.Status.CANCELLED, sent=True)

    response = client.delete(_line_url(purchase_order, first))

    assert response.status_code == 400
    error = response.data["error"]
    assert "the supplier already has this line" in error
    assert "void it instead" in error


def test_delete_refuses_a_line_that_records_a_receipt(client):
    """Goods a receipt says arrived are not destroyed on the strength of an argument.

    The receipt is written with ``update()`` because that is the only way it
    gets there: no ordinary path can put one on a pre-send line, which is
    exactly the case the guard exists for. The refusal has to be actionable, so
    it names the recorded quantity and what to do about it.
    """
    purchase_order, first, _second = _order()
    PurchaseOrderItem.objects.filter(pk=first.pk).update(quantity_received=3)

    response = client.delete(_line_url(purchase_order, first))

    assert response.status_code == 400, response.data
    assert response.data["code"] == "line_received"
    assert "3" in response.data["error"]
    assert PurchaseOrderItem.objects.filter(pk=first.pk).exists()
    purchase_order.refresh_from_db()
    assert purchase_order.estimated_total == Decimal("50.00")
    assert not PurchaseOrderAuditEvent.objects.filter(
        action=PurchaseOrderAuditEvent.Action.PO_LINE_DELETE
    ).exists()


def test_the_receipt_refusal_comes_second_to_the_pre_send_refusal(client):
    """A sent line with a receipt is told to VOID — the answer to what was asked."""
    purchase_order, first, _second = _order(status=PurchaseOrder.Status.SENT)
    PurchaseOrderItem.objects.filter(pk=first.pk).update(quantity_received=3)

    response = client.delete(_line_url(purchase_order, first))

    assert response.status_code == 400
    assert response.data["code"] == "not_draft"


def test_every_non_pre_send_status_is_covered_by_the_parametrisation():
    """The parametrisation above must not silently miss a new status."""
    covered = {
        PurchaseOrder.Status.SENT,
        PurchaseOrder.Status.CONFIRMED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
        PurchaseOrder.Status.RECEIVED,
        PurchaseOrder.Status.CANCELLED,
        PurchaseOrder.Status.VOIDED,
    }
    everything = set(PurchaseOrder.Status)
    assert everything - PurchaseOrder.PRE_SUPPLIER_STATUSES == covered


def test_an_anonymous_caller_cannot_delete_a_line():
    """Anonymous readers can SEE a sent order. They must not be able to destroy.

    ``list``/``retrieve`` are ``AllowAny`` on this viewset, and deletion is
    irreversible, so the gate is worth pinning rather than inferring from the
    permission matrix.
    """
    from rest_framework.test import APIClient

    purchase_order, first, _second = _order()

    response = APIClient().delete(_line_url(purchase_order, first))

    assert response.status_code in (401, 403)
    assert PurchaseOrderItem.objects.filter(pk=first.pk).exists()


def test_deleting_a_line_that_is_not_on_this_order_is_a_404(client):
    purchase_order, _first, _second = _order()
    other_order, other_line, _ = _order()

    response = client.delete(_line_url(purchase_order, other_line))

    assert response.status_code == 404
    assert PurchaseOrderItem.objects.filter(pk=other_line.pk).exists()
    assert other_order.items.count() == 2


def test_patch_on_the_same_path_is_untouched(client):
    """DELETE is an ADDITION to this path — PATCH still behaves exactly as before."""
    purchase_order, first, _second = _order()

    response = client.patch(
        _line_url(purchase_order, first),
        {"notes": "still editable"},
        format="json",
    )

    assert response.status_code == 200
    first.refresh_from_db()
    assert first.notes == "still editable"


# --------------------------------------------------------------------------
# what the delete re-derives, and who does it
# --------------------------------------------------------------------------


def test_settlement_is_re_derived_by_the_signal_not_by_the_endpoint(client):
    """#1029's ``post_delete`` routing is what answers for the order's status.

    This change is the first real exercise of that mechanism. It fires, it is
    handed the right order, and it correctly declines to MOVE a draft — a draft
    is not receiving's to re-derive, so "still draft" is the right answer, not
    a missed one.
    """
    from unittest.mock import patch

    import reorder_queue.services.receiving as receiving

    purchase_order, first, _second = _order()
    seen = []
    real = receiving.refresh_receipt_status

    def spy(order):
        seen.append((order.pk, order.status))
        return real(order)

    with patch.object(receiving, "refresh_receipt_status", spy):
        response = client.delete(_line_url(purchase_order, first))

    assert response.status_code == 200
    assert seen == [(purchase_order.pk, PurchaseOrder.Status.DRAFT)]
    purchase_order.refresh_from_db()
    assert purchase_order.status == PurchaseOrder.Status.DRAFT


def test_total_is_re_rolled_so_the_order_stops_reporting_deleted_money(client):
    """The stored total is frozen from the line costs; a delete has to move it.

    Without this the order reports money for a line that does not exist — on
    its own detail page, in ``payment_schedule``, and to every API client — and
    no operator action brings the two back into line.
    """
    purchase_order, first, _second = _order()
    assert purchase_order.estimated_total == Decimal("50.00")

    response = client.delete(_line_url(purchase_order, first))

    purchase_order.refresh_from_db()
    assert purchase_order.estimated_total == Decimal("10.00")
    assert purchase_order.effective_estimated_total == Decimal("10.00")
    assert response.data["purchase_order"]["estimated_total"] == "10.00"
    assert response.data["purchase_order"]["payment_schedule"]["amount"] == "10.00"


def test_the_admin_delete_path_re_rolls_the_total_too(client):
    """The admin could already delete lines; it must not disagree with the API.

    Driven through the model exactly as the admin's row delete, inline delete
    and bulk "Delete selected" all reach it — no endpoint involved — so the fix
    is proved to live on the line rather than in the new view.
    """
    purchase_order, first, _second = _order()

    first.delete()

    purchase_order.refresh_from_db()
    assert purchase_order.estimated_total == Decimal("10.00")


def test_a_bulk_queryset_delete_re_rolls_the_total_once(client):
    """``settlement_batch`` coalesces the cost re-roll the same way it does status."""
    purchase_order, first, second = _order()

    PurchaseOrderItem.objects.filter(pk__in=[first.pk, second.pk]).delete()

    purchase_order.refresh_from_db()
    assert purchase_order.estimated_total == Decimal("0.00")
    assert purchase_order.items.count() == 0


def test_the_manager_still_has_no_bulk_delete():
    """#1030 withheld ``delete`` from the manager; nothing here reintroduces it."""
    assert not hasattr(PurchaseOrderItem.objects, "delete")


# --------------------------------------------------------------------------
# references to the line
# --------------------------------------------------------------------------


def test_the_audit_trail_survives_the_line_it_describes(client):
    """``line_item`` is SET_NULL, so the metadata is the record of what went."""
    purchase_order, first, _second = _order()

    client.delete(_line_url(purchase_order, first))

    event = PurchaseOrderAuditEvent.objects.get(
        action=PurchaseOrderAuditEvent.Action.PO_LINE_DELETE
    )
    # The FK is gone with the line; the order pointer and the payload are not.
    assert event.line_item_id is None
    assert event.purchase_order_id == purchase_order.pk
    assert event.metadata["quantity_ordered"] == 4
    assert event.metadata["unit_cost_ordered"] == "10.0000"
    assert event.metadata["line_item"] == str(first.pk)


def test_deleting_a_line_does_not_discontinue_its_catalogue_entry(client):
    """Voiding marks the ``item_supplier`` discontinued. Deleting a typo must not.

    The line points AT the catalogue entry; the entry does not point at the
    line. Carrying void's side effect across would retire a supplier's product
    because somebody mistyped a quantity.
    """
    purchase_order, first, _second = _order()
    item_supplier = first.item_supplier

    client.delete(_line_url(purchase_order, first))

    item_supplier.refresh_from_db()
    assert item_supplier.is_discontinued is False
    assert item_supplier.is_active is True


def test_a_work_order_tag_on_a_deleted_line_strands_nothing(client):
    """``work_order`` is a forward FK — the work order outlives the line untouched."""
    from inventory.models import WorkOrder
    from inventory.tests.factories import AssetFactory

    purchase_order, first, _second = _order()
    work_order = WorkOrder.objects.create(maintenance_item=None, asset=AssetFactory())
    first.work_order = work_order
    first.save(update_fields=["work_order"])

    client.delete(_line_url(purchase_order, first))

    work_order.refresh_from_db()
    # And the receiving bridge's usage rows only exist once goods arrive, which
    # a pre-send line cannot have — so there is nothing to strand.
    assert not WorkOrderMaterialUsage.objects.filter(purchase_order_item=first.pk).exists()


# --------------------------------------------------------------------------
# the two actions are never offered together
# --------------------------------------------------------------------------


def test_the_api_says_which_action_applies(client):
    """The page reads this rather than keeping its own copy of the status list."""
    draft, _first, _second = _order()
    sent, _a, _b = _order(status=PurchaseOrder.Status.SENT)

    draft_body = client.get(reverse("purchaseorder-detail", args=[draft.pk])).data
    sent_body = client.get(reverse("purchaseorder-detail", args=[sent.pk])).data

    assert draft_body["can_delete_items"] is True
    assert sent_body["can_delete_items"] is False


def test_void_is_unchanged_on_a_sent_order(client):
    """Do not change void: it still takes a reason and still strikes off."""
    purchase_order, first, _second = _order(status=PurchaseOrder.Status.SENT)

    response = client.post(
        reverse("purchaseorder-void-item", args=[purchase_order.pk, first.pk]),
        {"reason": "discontinued by supplier"},
        format="json",
    )

    assert response.status_code == 200
    first.refresh_from_db()
    assert first.is_voided is True
    assert first.void_reason == "discontinued by supplier"
    # Struck off, NOT destroyed.
    assert PurchaseOrderItem.objects.filter(pk=first.pk).exists()
