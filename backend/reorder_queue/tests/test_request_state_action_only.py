"""Reorder-request workflow state moves through the actions, never through PATCH
(op-xj1i — the side door left open by op-tm70's approve gate).

op-tm70 restricted ``POST /reorders/requests/<id>/approve/`` to approvers for
the item and made ``approved`` the status that lets a request be purchased. The
gate was real for the product flow (the dashboard's Approve button posts to that
action) but the generic ModelViewSet ``update`` path went around it:

    PATCH /api/reorders/requests/<id>/  {"status": "approved"}

``status`` was writable on ``ReorderRequestSerializer``, and the viewset is only
gated by ``IsAuthenticated`` — so any authenticated member could sign their own
request off, and the row landed ``approved`` with ``reviewed_by``/``reviewed_at``
NULL. An ask nobody reviewed then became spendable.

The fix is a contract change on update/partial_update rather than another
permission check: ``status``, ``reviewed_by``, ``reviewed_at`` and ``ordered_at``
are read-only on the serializer, so the workflow actions — where the permission
checks and the timestamp stamping live — are the only way state moves. It is
action-only, not merely approver-only: **staff cannot PATCH state either**, so
there is no second path to keep the two gates in sync on.

Everything else on the row stays editable: the dashboard's Update-Tracking form
PATCHes ``delivery_tracking_url`` (``reorderAPI.updateTracking``,
``frontend/src/services/api.ts``), and the transparency fields are corrected the
same way. Those are covered here too — over-locking the serializer would break a
live flow just as surely as under-locking it opened this one.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.tests.factories import InventoryItemFactory
from membership.models import SIGAdmin
from reorder_queue import services
from reorder_queue.models import ReorderRequest
from reorder_queue.tests.factories import ReorderRequestFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — same cast as test_approval_gate.py, so the two files describe the
# same approver set.
# ─────────────────────────────────────────────────────────────────────────────
def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _staff():
    return User.objects.create_user(username="storekeeper", password="x", is_staff=True)


def _non_approver():
    """A member who administers SOME OTHER SIG — the one shape that is not an
    approver for a space-owned item while still being an authenticated member."""
    user = User.objects.create_user(username="other-sig-lead", password="x")
    SIGAdmin.objects.create(user=user, group=Group.objects.create(name="Other SIG"))
    return user


def _detail_url(request_obj):
    return reverse("reorderrequest-detail", kwargs={"pk": request_obj.pk})


def _patch(user, request_obj, payload):
    return _client(user).patch(_detail_url(request_obj), payload, format="json")


# ─────────────────────────────────────────────────────────────────────────────
# The side door: PATCH cannot move the workflow state
# ─────────────────────────────────────────────────────────────────────────────
class TestPatchCannotApprove:
    def test_non_approver_patch_to_approved_is_ignored(self):
        request_obj = ReorderRequestFactory(status=ReorderRequest.Status.PENDING)

        response = _patch(_non_approver(), request_obj, {"status": "approved"})

        assert response.status_code == status.HTTP_200_OK
        # The response body echoes the true state, not the requested one.
        assert response.data["status"] == ReorderRequest.Status.PENDING
        request_obj.refresh_from_db()
        assert request_obj.status == ReorderRequest.Status.PENDING
        assert request_obj.reviewed_by is None
        assert request_obj.reviewed_at is None

    def test_approver_patch_to_approved_is_ignored_too(self):
        """Action-only, not approver-only: an approver has the ``approve``
        action, so the update path never needs to move state for anyone."""
        request_obj = ReorderRequestFactory(status=ReorderRequest.Status.PENDING)

        response = _patch(_staff(), request_obj, {"status": "approved"})

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == ReorderRequest.Status.PENDING
        request_obj.refresh_from_db()
        assert request_obj.status == ReorderRequest.Status.PENDING
        assert request_obj.reviewed_by is None

    @pytest.mark.parametrize(
        "state",
        [
            ReorderRequest.Status.APPROVED,
            ReorderRequest.Status.ORDERED,
            ReorderRequest.Status.RECEIVED,
            ReorderRequest.Status.CANCELLED,
        ],
    )
    def test_no_workflow_state_is_reachable_by_patch(self, state):
        """``ordered``/``received``/``cancelled`` have gated actions of their own
        (``mark_ordered`` stamps ``ordered_at``, ``mark_received`` moves stock);
        PATCH must not reach any of them either."""
        request_obj = ReorderRequestFactory(status=ReorderRequest.Status.PENDING)

        response = _patch(_non_approver(), request_obj, {"status": state})

        assert response.status_code == status.HTTP_200_OK
        request_obj.refresh_from_db()
        assert request_obj.status == ReorderRequest.Status.PENDING

    def test_patch_cannot_forge_the_reviewer_stamp(self):
        """The audit half of approval: even if state were reachable some other
        way, ``reviewed_by``/``reviewed_at`` may not be written by a client."""
        reviewer = _staff()
        request_obj = ReorderRequestFactory(status=ReorderRequest.Status.PENDING)

        response = _patch(
            _non_approver(),
            request_obj,
            {"reviewed_by": reviewer.pk, "reviewed_at": timezone.now().isoformat()},
        )

        assert response.status_code == status.HTTP_200_OK
        request_obj.refresh_from_db()
        assert request_obj.reviewed_by is None
        assert request_obj.reviewed_at is None

    def test_patch_cannot_stamp_ordered_at(self):
        """``ordered_at`` is stamped by ``mark_ordered`` — it is the timestamp
        the lead-time analytics read, not a free-text field."""
        request_obj = ReorderRequestFactory(status=ReorderRequest.Status.PENDING)

        response = _patch(_non_approver(), request_obj, {"ordered_at": timezone.now().isoformat()})

        assert response.status_code == status.HTTP_200_OK
        request_obj.refresh_from_db()
        assert request_obj.ordered_at is None

    def test_put_preserves_the_state_it_is_handed(self):
        """A full update is the same door. It must neither 400 on the read-only
        field nor apply it — the row keeps the state the actions gave it."""
        request_obj = ReorderRequestFactory(status=ReorderRequest.Status.PENDING, quantity=3)

        response = _client(_non_approver()).put(
            _detail_url(request_obj),
            {
                "item": str(request_obj.item_id),
                "quantity": 5,
                "status": "approved",
                "priority": ReorderRequest.Priority.NORMAL,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        request_obj.refresh_from_db()
        assert request_obj.quantity == 5  # the writable half still applies
        assert request_obj.status == ReorderRequest.Status.PENDING


# ─────────────────────────────────────────────────────────────────────────────
# Why it matters: approval is the spend gate (op-tm70 BUG 2)
# ─────────────────────────────────────────────────────────────────────────────
class TestPatchCannotMakeARequestSpendable:
    def test_patched_request_never_becomes_po_eligible(self):
        item = InventoryItemFactory()
        request_obj = ReorderRequestFactory(item=item, status=ReorderRequest.Status.PENDING)

        _patch(_non_approver(), request_obj, {"status": "approved"})

        assert services.get_approved_reorder_request(item) is None

    def test_the_approve_action_is_still_the_way_through(self):
        """The door that is open: an approver signs off through the action and
        the request becomes purchasable, reviewer stamp and all."""
        item = InventoryItemFactory()
        approver = _staff()
        request_obj = ReorderRequestFactory(item=item, status=ReorderRequest.Status.PENDING)

        response = _client(approver).post(
            reverse("reorderrequest-approve", kwargs={"pk": request_obj.pk}), {}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        request_obj.refresh_from_db()
        assert request_obj.status == ReorderRequest.Status.APPROVED
        assert request_obj.reviewed_by == approver
        assert services.get_approved_reorder_request(item) == request_obj


# ─────────────────────────────────────────────────────────────────────────────
# The half that stays writable — guard against over-locking the serializer
# ─────────────────────────────────────────────────────────────────────────────
class TestTheEditableFieldsStillEdit:
    def test_update_tracking_flow_still_works(self):
        """``reorderAPI.updateTracking`` — the only PATCH the web client makes."""
        request_obj = ReorderRequestFactory(status=ReorderRequest.Status.ORDERED)

        response = _patch(
            _staff(),
            request_obj,
            {"delivery_tracking_url": "https://carrier.example/track/A1"},
        )

        assert response.status_code == status.HTTP_200_OK
        request_obj.refresh_from_db()
        assert request_obj.delivery_tracking_url == "https://carrier.example/track/A1"
        assert request_obj.status == ReorderRequest.Status.ORDERED

    def test_transparency_and_admin_fields_still_edit(self):
        request_obj = ReorderRequestFactory(status=ReorderRequest.Status.ORDERED)

        response = _patch(
            _staff(),
            request_obj,
            {
                "admin_notes": "backordered until Friday",
                "invoice_number": "INV-42",
                "actual_cost": "19.99",
                "order_number": "PO-7",
                "public_notes": "on its way",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        request_obj.refresh_from_db()
        assert request_obj.admin_notes == "backordered until Friday"
        assert request_obj.invoice_number == "INV-42"
        assert str(request_obj.actual_cost) == "19.99"
        assert request_obj.order_number == "PO-7"
        assert request_obj.public_notes == "on its way"
