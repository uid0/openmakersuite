"""Approval is the gate between "somebody asked" and "we are buying it" (op-tm70).

Three bugs, one theme — approval had no teeth:

1. **An approver's own scan sat in the pending queue** waiting for that same
   person to come back and click approve. A request raised by someone who can
   already approve it for that item is stamped ``approved`` on create.
2. **Unapproved asks prefilled purchase orders.** The PO-candidate pad, its
   per-line request flags, the per-supplier cart links and the "sending this PO
   closes these requests" sweep all counted ``pending`` alongside ``approved``,
   so an unreviewed request could be ordered — and sending the PO then flipped
   it straight to ``ordered``, out of the queue nobody had reviewed it in.
   Everything that spends money now reads approved-only.
3. **Any authenticated member could approve anything**, including their own
   request. ``approve`` is restricted to approvers for the item.

The approver set on both sides of approval is ``can_manage_sig_inventory`` —
staff/superusers, Logistics, the item's SIG admins, and, for a space-owned item
(no ``owning_group``), any member who does not administer some other SIG.
Deliberately the same set for auto-approve and for the ``approve`` action: the
queue must never hold a row waiting on a click from the one person who raised
it, and nobody gets to sign off through the API what they could not sign off in
the auto path.

REGRESSION GUARD: the inventory-side "active reorder" badge must keep counting
pending — see ``inventory/tests/test_inventory_list_metrics.py::TestPendingRequestStillLightsBadge``.
The badge asks "does an ask exist?"; this file's gate asks "may it be bought?".
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.tests.factories import InventoryItemFactory, SupplierFactory
from membership.models import SIGAdmin
from reorder_queue import services
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem, ReorderRequest
from reorder_queue.tests.factories import ReorderRequestFactory

User = get_user_model()

pytestmark = pytest.mark.django_db

REORDER_DATA_URL = "/api/reorders/purchase-orders/reorder_data/"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _staff():
    return User.objects.create_user(username="storekeeper", password="x", is_staff=True)


def _member(username="member"):
    """A plain member — an approver for space-owned items, per the approver set."""
    return User.objects.create_user(username=username, password="x")


def _outsider_sig_admin():
    """A member who administers SOME OTHER SIG.

    The one shape that is *not* an approver for a space-owned item while still
    clearing ``can_create_reorder_request``: SIG admins are scoped to their own
    SIG's inventory, so they are explicitly not handed the space's.
    """
    user = User.objects.create_user(username="other-sig-lead", password="x")
    SIGAdmin.objects.create(user=user, group=Group.objects.create(name="Other SIG"))
    return user


def _scan(client, item, **extra):
    """POST the reorder-request create endpoint — the one server-side path both
    the web scan flow and ScanTTY go through."""
    payload = {"item": str(item.id), "quantity": 4, "requested_by": "Scanner"}
    payload.update(extra)
    return client.post(reverse("reorderrequest-list"), payload, format="json")


def _line_for(response, item):
    """The reorder_data line for ``item``, from whichever supplier carries it."""
    for supplier in response.json()["suppliers"]:
        for line in supplier["items"]:
            if line["item_id"] == str(item.id):
                return line
    return None


def _stocked_item(**kwargs):
    """An item with a supplier link, well above its reorder point by default.

    Above the point on purpose: it then reaches the PO pad only via a request,
    so the pad's contents isolate the approval gate from the low-stock rule.
    ``quantity_per_package=1`` pins the suggested quantity — a supplier case
    size would round it up and blur what the line is actually quoting.
    """
    kwargs.setdefault("image", None)
    kwargs.setdefault("current_stock", 100)
    kwargs.setdefault("minimum_stock", 1)
    kwargs.setdefault("reorder_quantity", 7)
    kwargs.setdefault("unit_cost", Decimal("1.00"))
    kwargs.setdefault("quantity_per_package", 1)
    return InventoryItemFactory(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# BUG 1 — an approver's own scan is approved on the spot
# ─────────────────────────────────────────────────────────────────────────────
class TestScanAutoApproval:
    def test_approver_scan_is_created_approved(self):
        user = _staff()
        item = _stocked_item()

        response = _scan(_client(user), item)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["status"] == ReorderRequest.Status.APPROVED

        created = ReorderRequest.objects.get(id=response.data["id"])
        assert created.status == ReorderRequest.Status.APPROVED
        assert created.reviewed_by == user
        assert created.reviewed_at is not None

    def test_sig_admin_scan_of_own_sig_item_is_created_approved(self):
        """Item-scoped, not role-only: the SIG's own admin approves its item."""
        sig = Group.objects.create(name="Woodshop")
        user = _member("woodshop-lead")
        SIGAdmin.objects.create(user=user, group=sig)
        item = _stocked_item(owning_group=sig)

        response = _scan(_client(user), item)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["status"] == ReorderRequest.Status.APPROVED
        assert ReorderRequest.objects.get(id=response.data["id"]).reviewed_by == user

    def test_non_approver_scan_stays_pending(self):
        user = _outsider_sig_admin()
        item = _stocked_item()

        response = _scan(_client(user), item)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["status"] == ReorderRequest.Status.PENDING

        created = ReorderRequest.objects.get(id=response.data["id"])
        assert created.status == ReorderRequest.Status.PENDING
        assert created.reviewed_by is None
        assert created.reviewed_at is None

    def test_anonymous_scan_stays_pending(self):
        """The printed-QR-label flow: no session, no self-approval."""
        item = _stocked_item()

        response = _scan(_client(), item)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["status"] == ReorderRequest.Status.PENDING

        created = ReorderRequest.objects.get(id=response.data["id"])
        assert created.status == ReorderRequest.Status.PENDING
        assert created.reviewed_by is None
        assert created.reviewed_at is None

    def test_auto_approval_does_not_disturb_the_submitted_fields(self):
        """Only the three approval fields are stamped — the scan is otherwise
        recorded exactly as submitted (``save(update_fields=...)``)."""
        user = _staff()
        item = _stocked_item()

        response = _scan(
            _client(user), item, quantity=13, request_notes="down to the last box", priority="high"
        )

        created = ReorderRequest.objects.get(id=response.data["id"])
        assert created.quantity == 13
        assert created.request_notes == "down to the last box"
        assert created.priority == ReorderRequest.Priority.HIGH
        assert created.requested_by == "Scanner"
        assert created.admin_notes == ""


# ─────────────────────────────────────────────────────────────────────────────
# BUG 3 — only approvers may approve
# ─────────────────────────────────────────────────────────────────────────────
class TestApproveActionIsApproverOnly:
    def test_approver_may_approve(self):
        user = _staff()
        request_obj = ReorderRequestFactory(status=ReorderRequest.Status.PENDING)

        url = reverse("reorderrequest-approve", kwargs={"pk": request_obj.pk})
        response = _client(user).post(url, {"admin_notes": "ok"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        request_obj.refresh_from_db()
        assert request_obj.status == ReorderRequest.Status.APPROVED
        assert request_obj.reviewed_by == user

    def test_sig_admin_may_approve_their_own_sigs_request(self):
        sig = Group.objects.create(name="Metal Shop")
        user = _member("metal-lead")
        SIGAdmin.objects.create(user=user, group=sig)
        request_obj = ReorderRequestFactory(
            item=InventoryItemFactory(owning_group=sig), status=ReorderRequest.Status.PENDING
        )

        url = reverse("reorderrequest-approve", kwargs={"pk": request_obj.pk})
        response = _client(user).post(url, {}, format="json")

        assert response.status_code == status.HTTP_200_OK

    def test_non_approver_gets_403_and_the_request_is_untouched(self):
        user = _outsider_sig_admin()
        request_obj = ReorderRequestFactory(status=ReorderRequest.Status.PENDING, admin_notes="")

        url = reverse("reorderrequest-approve", kwargs={"pk": request_obj.pk})
        response = _client(user).post(url, {"admin_notes": "rubber stamp"}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        request_obj.refresh_from_db()
        assert request_obj.status == ReorderRequest.Status.PENDING
        assert request_obj.reviewed_by is None
        assert request_obj.admin_notes == ""


# ─────────────────────────────────────────────────────────────────────────────
# BUG 2 — only approved requests reach purchasing
# ─────────────────────────────────────────────────────────────────────────────
class TestReorderDataIsApprovedOnly:
    def test_approved_request_drives_the_line(self):
        client = _client(_staff())
        item = _stocked_item()
        approved = ReorderRequestFactory(
            item=item, status=ReorderRequest.Status.APPROVED, quantity=9
        )

        line = _line_for(client.get(REORDER_DATA_URL), item)

        assert line is not None
        assert line["has_active_reorder_request"] is True
        assert line["reorder_request_id"] == approved.id
        assert line["suggested_quantity"] == 9

    def test_pending_request_never_reaches_the_pad(self):
        """A well-stocked item whose only request is pending has no business on
        a purchase order at all — it is neither request-driven nor low."""
        client = _client(_staff())
        item = _stocked_item()
        ReorderRequestFactory(item=item, status=ReorderRequest.Status.PENDING, quantity=9)

        assert _line_for(client.get(REORDER_DATA_URL), item) is None

    def test_low_stock_item_with_a_pending_request_shows_no_request_flags(self):
        """It still appears — on the low-stock half — but carries none of the
        request's authority: no id, no flag, and the suggested quantity comes
        from the item's own reorder rule, not the unapproved ask."""
        client = _client(_staff())
        item = _stocked_item(current_stock=0, minimum_stock=5, reorder_quantity=7)
        ReorderRequestFactory(item=item, status=ReorderRequest.Status.PENDING, quantity=99)

        line = _line_for(client.get(REORDER_DATA_URL), item)

        assert line is not None
        assert line["has_active_reorder_request"] is False
        assert line["reorder_request_id"] is None
        assert line["suggested_quantity"] == 7

    def test_approved_request_wins_over_a_newer_pending_one(self):
        """The per-line lookup is approved-only, not merely 'newest request'."""
        client = _client(_staff())
        item = _stocked_item()
        approved = ReorderRequestFactory(
            item=item, status=ReorderRequest.Status.APPROVED, quantity=6
        )
        ReorderRequestFactory(item=item, status=ReorderRequest.Status.PENDING, quantity=99)

        line = _line_for(client.get(REORDER_DATA_URL), item)

        assert line["reorder_request_id"] == approved.id
        assert line["suggested_quantity"] == 6


class TestCartLinksAreApprovedOnly:
    def test_pending_request_is_left_off_the_order_pad(self):
        supplier = SupplierFactory(name="Acme Tools")
        approved_item = InventoryItemFactory(supplier=supplier, supplier_sku="APPR-1")
        pending_item = InventoryItemFactory(supplier=supplier, supplier_sku="PEND-1")
        ReorderRequestFactory(item=approved_item, status=ReorderRequest.Status.APPROVED, quantity=2)
        ReorderRequestFactory(item=pending_item, status=ReorderRequest.Status.PENDING, quantity=8)

        response = _client(_staff()).get(reverse("reorderrequest-generate-cart-links"))

        assert response.status_code == status.HTTP_200_OK
        pad = response.data["Acme Tools"]
        assert pad["line_count"] == 1
        assert "APPR-1,2" in pad["csv"]
        assert "PEND-1" not in pad["csv"]


class TestSendingAPoClosesApprovedRequestsOnly:
    """``update_reorder_requests_from_po`` — sending a PO used to sweep pending
    requests for the same item into ``ordered``, which retired an ask nobody had
    reviewed. Now it only closes what approval cleared."""

    def _po_with_line(self, item):
        item_supplier = item.item_suppliers.first()
        po = PurchaseOrder.objects.create(
            supplier=item_supplier.supplier,
            status=PurchaseOrder.Status.SENT,
            created_by=_staff(),
            sent_at=timezone.now(),
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            item_supplier=item_supplier,
            quantity_ordered=5,
            unit_cost_ordered=Decimal("1.00"),
        )
        return po

    def test_approved_request_is_marked_ordered(self):
        item = _stocked_item()
        approved = ReorderRequestFactory(item=item, status=ReorderRequest.Status.APPROVED)

        services.update_reorder_requests_from_po(self._po_with_line(item))

        approved.refresh_from_db()
        assert approved.status == ReorderRequest.Status.ORDERED
        assert approved.ordered_at is not None

    def test_pending_request_for_the_same_item_is_left_alone(self):
        item = _stocked_item()
        approved = ReorderRequestFactory(item=item, status=ReorderRequest.Status.APPROVED)
        pending = ReorderRequestFactory(item=item, status=ReorderRequest.Status.PENDING)

        services.update_reorder_requests_from_po(self._po_with_line(item))

        approved.refresh_from_db()
        pending.refresh_from_db()
        assert approved.status == ReorderRequest.Status.ORDERED
        assert pending.status == ReorderRequest.Status.PENDING
        assert pending.ordered_at is None
        assert pending.order_number == ""


# ─────────────────────────────────────────────────────────────────────────────
# The service the PO paths read through
# ─────────────────────────────────────────────────────────────────────────────
class TestGetApprovedReorderRequest:
    @pytest.mark.parametrize(
        "state",
        [
            ReorderRequest.Status.PENDING,
            ReorderRequest.Status.ORDERED,
            ReorderRequest.Status.RECEIVED,
            ReorderRequest.Status.CANCELLED,
        ],
    )
    def test_only_approved_counts(self, state):
        item = InventoryItemFactory()
        ReorderRequestFactory(item=item, status=state)

        assert services.get_approved_reorder_request(item) is None

    def test_returns_the_most_recent_approved_request(self):
        item = InventoryItemFactory()
        ReorderRequestFactory(item=item, status=ReorderRequest.Status.APPROVED)
        newest = ReorderRequestFactory(item=item, status=ReorderRequest.Status.APPROVED)

        assert services.get_approved_reorder_request(item) == newest

    def test_prefetched_and_live_paths_agree(self):
        """The prefetch mirrors the live filter + ``-requested_at`` order, so a
        cached read and an uncached one return the same row (the #890 trap)."""
        item = InventoryItemFactory()
        ReorderRequestFactory(item=item, status=ReorderRequest.Status.PENDING)
        newest = ReorderRequestFactory(item=item, status=ReorderRequest.Status.APPROVED)

        prefetched = (
            type(item)
            .objects.filter(pk=item.pk)
            .prefetch_related(services.approved_requests_prefetch())
            .first()
        )

        assert services.get_approved_reorder_request(prefetched) == newest
        assert services.get_approved_reorder_request(item) == newest

    def test_prefetched_item_with_no_approved_request_reads_none_without_a_query(self):
        item = InventoryItemFactory()
        ReorderRequestFactory(item=item, status=ReorderRequest.Status.PENDING)

        prefetched = (
            type(item)
            .objects.filter(pk=item.pk)
            .prefetch_related(services.approved_requests_prefetch())
            .first()
        )

        assert getattr(prefetched, services.APPROVED_REQUESTS_ATTR) == []
        assert services.get_approved_reorder_request(prefetched) is None
