"""What "empty" means to the purchase-order list, and which kind of empty hides an order.

The list filter (``PurchaseOrderViewSet.get_queryset``, ``action == "list"``)
has only ever had one written rationale: *a PO whose items are all voided has
nothing to show or pay for, so it should not appear in the list*. It was
implemented as "no line item is active", and those two are not the same
sentence. "No line is active" is also true of an order that has NO LINES AT
ALL, and about that order the rationale says nothing. A draft with nothing on
it is not "nothing to pay for"; it is work in progress. (Creation refuses an
empty ``items`` list, so in practice that order is one whose lines were
DELETED — established by probing the real create endpoint, not assumed.)

That gap became reachable when line DELETION shipped (oms-po-line-delete):
"delete the wrong line, then add the right one" is the workflow deletion exists
for, and it dropped the operator's own order off the only list that leads back
to it. Detail retrieval is deliberately unfiltered, so the order still exists —
but an order you can only reach if you already kept the link is unreachable in
practice.

So the filter now hides an order **emptied by voiding** — it has line items,
every one of them is struck off, **and it has left the shop**. Emptiness with no
lines behind it is not hiding grounds in any status, and neither is emptiness on
an order that is still the shop's own private document: "nothing to pay for"
presupposes an obligation, and an obligation exists only once the order has gone
to a supplier. That boundary is ``PurchaseOrder.PRE_SUPPLIER_STATUSES``, read
off the order's own state machine — the same set line deletion reads, for the
same reason — never a status name.

Everything here drives the REAL list endpoint, because the defect was in what
that endpoint returns and nowhere else. The two axes are crossed deliberately:
every status × {no lines, emptied by voiding, one line still active}, so the
answer for a status is derived from the fixture rather than enumerated in prose.
"""

from __future__ import annotations

from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status as http

from inventory.tests.factories import ItemSupplierFactory, SupplierFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.tests.factories import UserFactory

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

ALL_STATUSES = [s.value for s in PurchaseOrder.Status]

#: Read off the order's own state machine, never retyped. These are the
#: statuses in which the order is still the shop's private document — the
#: boundary oms-po-line-delete drew for line deletion, reused here because it is
#: the same boundary: an obligation exists only once the document has left.
PRE_SUPPLIER = [s.value for s in PurchaseOrder.PRE_SUPPLIER_STATUSES]
POST_SUPPLIER = [s for s in ALL_STATUSES if s not in PRE_SUPPLIER]

#: The statuses an anonymous caller is allowed to see at all, mirroring the
#: unauthenticated branch of ``get_queryset``. Derived from that branch rather
#: than retyped so a change there shows up here as a failure, not a silence.
PUBLIC_STATUSES = [
    PurchaseOrder.Status.SENT.value,
    PurchaseOrder.Status.CONFIRMED.value,
    PurchaseOrder.Status.PARTIALLY_RECEIVED.value,
    PurchaseOrder.Status.RECEIVED.value,
]


def _make_po(user, *, po_status, line_count=0, void_lines=False):
    """Build a PO in an exact status with an exact line population.

    The status is stamped LAST, through a queryset ``update()``, on purpose:
    writing ``is_voided`` fires the settlement signals, which re-derive an
    order's status from its lines. Setting the status after that, by a write
    those signals never hear, means the fixture is the status the test names
    rather than whatever the derivation last decided.
    """
    supplier = SupplierFactory()
    purchase_order = PurchaseOrder.objects.create(
        supplier=supplier,
        status=PurchaseOrder.Status.DRAFT,
        created_by=user,
    )
    for _ in range(line_count):
        line = PurchaseOrderItem.objects.create(
            purchase_order=purchase_order,
            item_supplier=ItemSupplierFactory(supplier=supplier),
            quantity_ordered=5,
            unit_cost_ordered=Decimal("10.00"),
        )
        if void_lines:
            line.is_voided = True
            line.voided_at = timezone.now()
            line.voided_by = user
            line.void_reason = "discontinued"
            line.save()
    PurchaseOrder.objects.filter(pk=purchase_order.pk).update(status=po_status)
    purchase_order.refresh_from_db()
    return purchase_order


def _listed_ids(response):
    assert response.status_code == http.HTTP_200_OK
    return {row["id"] for row in response.data["results"]}


class TestNoLinesIsNotHidingGrounds:
    """An order with no line items is findable, whatever status it is in.

    This is the whole defect: the code hid it, the comment never asked for it,
    and the list is the only route back to an order whose link the operator
    does not already hold.
    """

    @pytest.mark.parametrize("po_status", ALL_STATUSES)
    def test_an_order_with_no_lines_is_listed(self, authenticated_client, po_status):
        client, user = authenticated_client
        purchase_order = _make_po(user, po_status=po_status, line_count=0)

        response = client.get(reverse("purchaseorder-list"))

        assert purchase_order.id in _listed_ids(response)

    def test_an_empty_draft_is_findable_under_the_draft_filter(self, authenticated_client):
        """The web list's "Draft" option sends ``?status=draft``; it must find it."""
        client, user = authenticated_client
        empty_draft = _make_po(user, po_status=PurchaseOrder.Status.DRAFT, line_count=0)

        response = client.get(reverse("purchaseorder-list"), {"status": "draft"})

        assert empty_draft.id in _listed_ids(response)

    @pytest.mark.parametrize("po_status", PUBLIC_STATUSES)
    def test_the_public_list_also_shows_an_order_with_no_lines(self, api_client, po_status):
        """Stated deliberately: this widens what an anonymous caller sees.

        A sent order carrying no lines is reachable today — ``send_to_supplier``
        and the sales-order-number auto-send both accept an order with nothing
        on it — and it was invisible on every list. It is a data problem someone
        has to be able to see, not noise; hiding it hid the problem, not the
        clutter.
        """
        owner = UserFactory()
        purchase_order = _make_po(owner, po_status=po_status, line_count=0)

        response = api_client.get(reverse("purchaseorder-list"))

        assert purchase_order.id in _listed_ids(response)


class TestEmptiedByVoidingHidesOnlyOnceThereIsAnObligation:
    """An obligation has to exist before there can be nothing left to pay.

    An order that HAS lines and has had every one of them struck off has
    nothing left to show or pay for — but that sentence only says anything
    about an order that ever took an obligation on. While the order is still
    the shop's own private document it has not; striking a line off it is the
    operator editing their own draft, which is the very act line deletion
    replaced, and hiding the order for it is the same "vanishes mid-edit" trap
    in a second guise. ``void_item`` carries no status gate, so this is
    reachable, not theoretical.

    The boundary is therefore ``PRE_SUPPLIER_STATUSES`` — the order's own state
    machine, the same set line deletion reads — and never a status name.
    """

    @pytest.mark.parametrize("po_status", POST_SUPPLIER)
    def test_an_order_emptied_by_voiding_is_hidden_once_it_has_left_the_shop(
        self, authenticated_client, po_status
    ):
        client, user = authenticated_client
        emptied = _make_po(user, po_status=po_status, line_count=2, void_lines=True)

        response = client.get(reverse("purchaseorder-list"))

        assert emptied.id not in _listed_ids(response)

    @pytest.mark.parametrize("po_status", PRE_SUPPLIER)
    def test_an_order_emptied_by_voiding_is_listed_while_it_is_still_the_shops_own(
        self, authenticated_client, po_status
    ):
        client, user = authenticated_client
        emptied = _make_po(user, po_status=po_status, line_count=2, void_lines=True)

        response = client.get(reverse("purchaseorder-list"))

        assert emptied.id in _listed_ids(response)

    def test_the_split_is_the_pre_supplier_set_itself(self, authenticated_client):
        """The derivation guard, and the reason no status is named in the filter.

        One emptied order per status, one call to the list, and the set of
        statuses that came back compared to ``PRE_SUPPLIER_STATUSES`` itself. A
        status added to that frozenset changes what this expects without anyone
        editing this test; a filter that stopped reading the frozenset and
        started naming ``"draft"`` would keep passing today and fail the moment
        a second pre-send status existed — which is the point of pinning the
        SET rather than the two lists it currently produces.
        """
        client, user = authenticated_client
        by_id = {
            _make_po(user, po_status=s, line_count=2, void_lines=True).id: s for s in ALL_STATUSES
        }

        listed = _listed_ids(client.get(reverse("purchaseorder-list")))

        visible = {by_id[i] for i in listed if i in by_id}
        assert visible == {s.value for s in PurchaseOrder.PRE_SUPPLIER_STATUSES}

    @pytest.mark.parametrize("po_status", ALL_STATUSES)
    def test_an_order_keeping_one_active_line_is_listed(self, authenticated_client, po_status):
        """The control: a partly-voided order is not an emptied one."""
        client, user = authenticated_client
        partly_voided = _make_po(user, po_status=po_status, line_count=1)
        PurchaseOrderItem.objects.create(
            purchase_order=partly_voided,
            item_supplier=ItemSupplierFactory(supplier=partly_voided.supplier),
            quantity_ordered=1,
            unit_cost_ordered=Decimal("1.00"),
            is_voided=True,
            voided_at=timezone.now(),
            void_reason="discontinued",
        )

        response = client.get(reverse("purchaseorder-list"))

        assert partly_voided.id in _listed_ids(response)

    def test_a_hidden_order_is_still_retrievable_by_id(self, authenticated_client):
        """Hiding from the list never 404s the detail route."""
        client, user = authenticated_client
        emptied = _make_po(user, po_status=PurchaseOrder.Status.SENT, line_count=2, void_lines=True)

        response = client.get(reverse("purchaseorder-detail", kwargs={"pk": emptied.pk}))

        assert response.status_code == http.HTTP_200_OK
        assert response.data["id"] == emptied.id


class TestTheDeleteWorkflowKeepsItsOrder:
    """The reported trap, driven end to end through the real endpoints.

    "Delete the wrong line, then add the right one" is what line deletion was
    built for. The operator must still be able to reach the order in between,
    from the list, without having kept a direct link.
    """

    def _delete_url(self, purchase_order, line):
        return reverse("purchaseorder-update-item", args=[purchase_order.pk, line.pk])

    def test_deleting_the_only_line_of_a_draft_leaves_it_on_the_list(self, authenticated_client):
        client, user = authenticated_client
        draft = _make_po(user, po_status=PurchaseOrder.Status.DRAFT, line_count=1)
        line = draft.items.get()

        deleted = client.delete(self._delete_url(draft, line))
        assert deleted.status_code == http.HTTP_200_OK

        assert draft.items.count() == 0
        assert draft.id in _listed_ids(client.get(reverse("purchaseorder-list")))

    def test_the_emptied_draft_can_take_a_new_line_and_stays_listed(self, authenticated_client):
        """The second half of the workflow: the order is usable, not just visible."""
        client, user = authenticated_client
        draft = _make_po(user, po_status=PurchaseOrder.Status.DRAFT, line_count=1)
        line = draft.items.get()
        client.delete(self._delete_url(draft, line))

        replacement = ItemSupplierFactory(supplier=draft.supplier)
        added = client.post(
            f"/api/reorders/purchase-orders/{draft.id}/items/",
            {"item_supplier": replacement.id, "quantity": 3},
            format="json",
        )
        assert added.status_code in (http.HTTP_200_OK, http.HTTP_201_CREATED)

        assert draft.id in _listed_ids(client.get(reverse("purchaseorder-list")))

    def test_voiding_the_only_line_of_a_draft_leaves_it_on_the_list(self, authenticated_client):
        """The second route into the trap, through the real void endpoint.

        Voiding was the ONLY way to empty an order before deletion shipped, and
        ``void_item`` still refuses nothing on a draft. An operator who voids
        instead of deleting must not lose the order either.
        """
        client, user = authenticated_client
        draft = _make_po(user, po_status=PurchaseOrder.Status.DRAFT, line_count=1)
        line = draft.items.get()

        voided = client.post(
            reverse("purchaseorder-void-item", args=[draft.pk, line.pk]),
            {"reason": "wrong part"},
            format="json",
        )
        assert voided.status_code == http.HTTP_200_OK

        draft.refresh_from_db()
        assert draft.status == PurchaseOrder.Status.DRAFT
        assert draft.id in _listed_ids(client.get(reverse("purchaseorder-list")))

    def test_voiding_the_only_line_of_a_sent_order_still_removes_it(self, authenticated_client):
        """The preserved half, through the real void endpoint rather than the ORM."""
        client, user = authenticated_client
        user.is_staff = True
        user.save()
        sent = _make_po(user, po_status=PurchaseOrder.Status.SENT, line_count=1)
        line = sent.items.get()

        voided = client.post(
            reverse("purchaseorder-void-item", args=[sent.pk, line.pk]),
            {"reason": "discontinued"},
            format="json",
        )
        assert voided.status_code == http.HTTP_200_OK

        assert sent.id not in _listed_ids(client.get(reverse("purchaseorder-list")))
