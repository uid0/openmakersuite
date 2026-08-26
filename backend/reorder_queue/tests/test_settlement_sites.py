"""The guard on the settlement derivation, and the fixes it was built to find.

Six defects of one shape reached the captain before this file existed: some code
changed whether a purchase-order line counts as settled, or read that fact, and
did not go through the same derivation as its siblings. Each was found on its
own. The class kept producing new sites, and the sweep commissioned to find
every consumer still missed the one that lived in another app.

So this holds three different things, and each answers a different question.

* :class:`TestDerivationIsHonoured` runs
  :mod:`reorder_queue.settlement_sites` over the whole tree — both apps, the
  frontend, migrations included — and fails when any site decides settlement for
  itself. It is what makes an omission fail the build.
* :class:`TestOrmAndPythonAgree` builds a line for EVERY combination of the
  settlement fields and asserts the SQL twin and the Python property give the
  same answer for each. Two derivations of one rule can only be trusted if
  something proves they still say the same thing, and "somebody will remember"
  is what the last six defects disproved.
* The remaining classes are the behaviour: what the two known defects did to a
  screen and to an operator, driven through the endpoints and services the web
  UI and ScanTTY actually call rather than by asking the helper directly.
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.forms.models import model_to_dict
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import ItemSupplier
from inventory.services.item_metrics import compute_item_metrics
from inventory.tests.factories import InventoryItemFactory, SupplierFactory
from reorder_queue import services, settlement_sites
from reorder_queue.admin import PurchaseOrderItemAdmin, ReceiptStatusFilter
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem, PurchaseOrderItemQuerySet


@pytest.fixture(scope="module")
def sweep():
    """One whole-tree sweep, shared by every test that only reads its result.

    The scan re-parses every ``.py`` under ``backend/`` and every ``.ts``/
    ``.tsx`` under ``frontend/src``; doing that once per test was seconds of
    identical work on every run. The two tests that assert on ``main()``'s
    printed output keep their own calls, because what they check is the
    printing.
    """
    return settlement_sites.scan()


@pytest.fixture
def operator(django_user_model):
    return django_user_model.objects.create_user(
        username="settlement-clerk", password="pw", is_staff=True, is_superuser=True
    )


@pytest.fixture
def client(operator):
    api = APIClient()
    api.force_authenticate(user=operator)
    return api


@pytest.fixture
def supplier(db):
    return SupplierFactory(name="Grainger")


def make_item(name, supplier, *, stock=0, reorder_quantity=0):
    item = InventoryItemFactory(
        name=name,
        current_stock=stock,
        minimum_stock=0,
        reorder_quantity=reorder_quantity,
        image=None,
    )
    ItemSupplier.objects.create(
        item=item,
        supplier=supplier,
        supplier_sku=f"SUP-{name[:6]}",
        unit_cost=Decimal("10.00"),
        quantity_per_package=1,
        is_primary=True,
    )
    return item


def make_po(supplier, operator, status_value=PurchaseOrder.Status.SENT):
    return PurchaseOrder.objects.create(
        supplier=supplier,
        status=status_value,
        order_date=timezone.now(),
        created_by=operator,
    )


def add_line(purchase_order, item, quantity):
    return PurchaseOrderItem.objects.create(
        purchase_order=purchase_order,
        item_supplier=item.item_suppliers.first(),
        quantity_ordered=quantity,
        unit_cost_ordered=Decimal("10.00"),
    )


# ---------------------------------------------------------------------------


class TestDerivationIsHonoured:
    """No site decides settlement for itself.

    Deliberately NOT a list of the sites known today: a hand-maintained list of
    settlement sites is exactly what produced the six defects, so this derives
    the set on every run from ``PurchaseOrderItem.is_settled`` and reports
    whatever it finds. A new site added tomorrow — in any app, in a migration,
    in the web UI — is in scope without anyone remembering to add it.
    """

    def test_settlement_definition_is_derived_from_the_model(self, sweep):
        """The anchor is read off the model, not written down here.

        Asserts the SHAPE of what was derived rather than the field names: a
        field added to the definition must not need this test edited, which is
        the whole point. What it does insist on is that the walk actually
        reached the data — a closure that found no fields would make the scan
        below pass vacuously, and "found nothing" and "could not tell" are
        different facts.
        """
        anchor = sweep.anchor

        assert anchor.fields, "the walk from is_settled reached no model fields"
        assert anchor.quantities, "no quantity field in the settlement definition"
        assert anchor.entangled, (
            "no field the definition refuses to trust alone — if that is really "
            "true, the entanglement arm of the guard now checks nothing"
        )
        assert settlement_sites.SEED in anchor.members
        assert anchor.mutating_methods, "no model method writes settlement state"

    def test_no_site_bypasses_the_derivation(self, sweep):
        report = sweep
        assert report.sites, "the sweep read no settlement site at all"
        assert not report.findings, "\n\n" + "\n\n".join(str(f) for f in report.findings)

    def test_the_sweep_says_what_it_could_not_read(self, sweep):
        """A partial run must not read as a clean one.

        The docker-compose CI job mounts ``backend/`` alone, so the frontend arm
        genuinely cannot run there — it runs in Frontend Lint instead. What
        matters is that the report distinguishes "looked and found nothing" from
        "could not look", rather than reporting silence as coverage.
        """
        report = sweep
        assert report.scanned, "the report claims to have read nothing"
        assert "frontend" in " ".join(
            report.scanned + report.unscanned
        ), "the frontend tree is neither reported as scanned nor as unreadable"

    def test_the_command_line_form_reports_and_exits_zero(self, capsys):
        """The exact form CI's Frontend Lint step runs."""
        assert settlement_sites.main([]) == 0
        printed = capsys.readouterr().out
        assert "No site bypasses the derivation." in printed
        assert "Scanned:" in printed

    def test_the_report_names_the_write_shapes_it_cannot_see(self, capsys):
        """A clean run must not read as "there is nothing left".

        The write arm has been blind to a write SHAPE three times over, so the
        report carries its own edges: what it can see and what it cannot, every
        run, beside the clean verdict. The module's docstring says the limits
        are stated in the report — this is that claim being honoured rather
        than asserted.
        """
        assert settlement_sites.main([]) == 0
        printed = capsys.readouterr().out

        assert "Write shapes this scan CAN see:" in printed
        assert "Write shapes it CANNOT see" in printed
        for shape in settlement_sites.WRITE_SHAPES_SEEN:
            assert shape in printed
        for shape in settlement_sites.WRITE_SHAPES_UNSEEN:
            assert shape in printed
        assert settlement_sites.WRITE_SHAPES_UNSEEN, (
            "the scan claims to name its own holes but lists none — that is a "
            "claim of completeness this arm has already been wrong about twice"
        )

    def test_the_command_line_form_can_list_the_whole_derived_set(self, capsys):
        """``--sites`` is how the derived set was read off for the PR."""
        assert settlement_sites.main(["--sites"]) == 0
        printed = capsys.readouterr().out
        assert "sites naming a settlement field" in printed
        assert "backend/reorder_queue/models.py" in printed


class TestOrmAndPythonAgree:
    """The SQL twin says what the Python property says, for every combination.

    A queryset cannot call a property, so the derivation has to exist twice.
    That is the shape every one of these defects had — two answers to one
    question — and the only thing that makes it safe is proving they agree
    rather than asserting it. The combinations are built from the settlement
    fields the derivation itself reaches, so a field added to it widens this
    product without the test being touched.
    """

    @pytest.fixture
    def combinations(self, supplier, operator):
        """One line per reachable combination of the settlement fields."""
        purchase_order = make_po(supplier, operator)
        now = timezone.now()
        earlier, later = now - timedelta(days=2), now - timedelta(days=1)
        ordered = 10
        lines = []
        received_values = [0, 4, ordered, ordered + 3]
        # (closed_short_at, reopened_at): never closed, closed, closed then
        # reopened, and reopened then closed again — the last two being the
        # pair whose ORDER decides the answer.
        stamp_pairs = [
            (None, None),
            (earlier, None),
            (earlier, later),
            (later, earlier),
        ]
        for index, (received, voided, (closed_at, reopened_at)) in enumerate(
            itertools.product(received_values, [False, True], stamp_pairs)
        ):
            item = make_item(f"Part {index}", supplier)
            line = add_line(purchase_order, item, ordered)
            line.quantity_received = received
            line.is_voided = voided
            line.closed_short_at = closed_at
            line.reopened_at = reopened_at
            line.save()
            lines.append(line)
        return lines

    def test_receipt_state_annotation_matches_the_property(self, combinations):
        alias = PurchaseOrderItemQuerySet.RECEIPT_STATE_ALIAS
        annotated = {
            row.pk: getattr(row, alias) for row in PurchaseOrderItem.objects.with_receipt_state()
        }
        mismatched = {
            line.pk: (line.receipt_state, annotated[line.pk])
            for line in combinations
            if line.receipt_state != annotated[line.pk]
        }
        assert not mismatched, f"SQL and Python disagree on receipt_state: {mismatched}"

    def test_settled_and_outstanding_match_is_settled(self, combinations):
        settled_in_sql = set(PurchaseOrderItem.objects.settled().values_list("pk", flat=True))
        settled_in_python = {line.pk for line in combinations if line.is_settled}
        assert settled_in_sql == settled_in_python

        outstanding_in_sql = set(
            PurchaseOrderItem.objects.outstanding().values_list("pk", flat=True)
        )
        assert outstanding_in_sql == {line.pk for line in combinations} - settled_in_python

    def test_outstanding_quantity_expression_matches_quantity_pending(self, combinations):
        computed = dict(
            PurchaseOrderItem.objects.annotate(
                pending=PurchaseOrderItem.outstanding_quantity_expression()
            ).values_list("pk", "pending")
        )
        mismatched = {
            line.pk: (line.quantity_pending, computed[line.pk])
            for line in combinations
            if line.quantity_pending != computed[line.pk]
        }
        assert not mismatched, f"SQL and Python disagree on the pending quantity: {mismatched}"


@pytest.mark.django_db
class TestQuantityEditSettlesTheOrder:
    """Editing a line down to what already arrived finishes the order off.

    The first of the two known defects. ``update_item`` let an operator set
    ``quantity_ordered`` to exactly what had been received — which settles the
    line — and re-rolled the order's cost without re-deriving its status. The
    order sat at ``partially_received`` with nothing outstanding, and both
    close-out actions refused it: a state reachable and not leavable.

    Driven through the PATCH endpoint, because that is the one ScanTTY and the
    web UI both call.
    """

    def _order_with_one_short_line(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        item = make_item("Widget", supplier)
        line = add_line(purchase_order, item, 10)
        receive = client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(line.pk), "quantity_received": 6}]},
            format="json",
        )
        assert receive.status_code == status.HTTP_200_OK, receive.data
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED
        return purchase_order, line

    def test_lowering_the_quantity_to_what_arrived_finishes_the_order(
        self, client, supplier, operator
    ):
        purchase_order, line = self._order_with_one_short_line(client, supplier, operator)

        response = client.patch(
            reverse("purchaseorder-update-item", args=[purchase_order.pk, line.pk]),
            {"quantity_ordered": 6},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        purchase_order.refresh_from_db()
        assert purchase_order.outstanding_line_count == 0
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED

    def test_the_order_is_no_longer_stranded_between_both_close_out_actions(
        self, client, supplier, operator
    ):
        """The symptom the operator actually hit: no way forward from either door."""
        purchase_order, line = self._order_with_one_short_line(client, supplier, operator)
        client.patch(
            reverse("purchaseorder-update-item", args=[purchase_order.pk, line.pk]),
            {"quantity_ordered": 6},
            format="json",
        )

        marked = client.post(
            reverse("purchaseorder-mark-received", args=[purchase_order.pk]), {}, format="json"
        )
        delivered = client.post(
            reverse("purchaseorder-mark-delivered", args=[purchase_order.pk]), {}, format="json"
        )
        # Both refuse — but because the order has already finished receiving,
        # which the payload now says, not because it is stuck short of it.
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED
        for response in (marked, delivered):
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert (
                "outstanding" in str(response.data).lower()
                or "received" in str(response.data).lower()
            )

    def test_settling_one_line_does_not_finish_an_order_still_owed_another(
        self, client, supplier, operator
    ):
        """Re-deriving is not the same as advancing: the other line still counts.

        Corroborating, not discriminating: dropping the refresh leaves the order
        at ``partially_received`` too, so this passes either way. What it pins is
        the opposite error — a refresh that advances an order still owed a line.
        """
        purchase_order, line = self._order_with_one_short_line(client, supplier, operator)
        second = add_line(purchase_order, make_item("Bracket", supplier), 3)

        response = client.patch(
            reverse("purchaseorder-update-item", args=[purchase_order.pk, line.pk]),
            {"quantity_ordered": 6},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED
        assert [item.pk for item in purchase_order.outstanding_items] == [second.pk]

    def test_a_finished_order_refuses_a_further_quantity_edit_and_says_why(
        self, client, supplier, operator
    ):
        """The boundary this fix moves, stated rather than left to be discovered.

        An order that has finished receiving is closed to quantity edits — the
        rule ``update_item`` already applied to every other route into
        ``received``, which growing an order that is a matter of record is a new
        purchase order rather than a line edit. Closing the order out sooner
        means an operator who lowers a quantity by mistake meets that rule
        rather than the stranded ``partially_received`` this used to leave. It
        refuses in terms of the order's state, not silently.
        """
        purchase_order, line = self._order_with_one_short_line(client, supplier, operator)
        client.patch(
            reverse("purchaseorder-update-item", args=[purchase_order.pk, line.pk]),
            {"quantity_ordered": 6},
            format="json",
        )
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED

        response = client.patch(
            reverse("purchaseorder-update-item", args=[purchase_order.pk, line.pk]),
            {"quantity_ordered": 9},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "partially received" in response.data["error"].lower()
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED


@pytest.mark.django_db
class TestWrittenOffUnitsAreNotInTransit:
    """Units written off as never arriving stop counting as on their way.

    The second known defect, and the one a whole-codebase sweep still missed:
    ``quantity_in_transit`` lives in ``inventory``, not ``reorder_queue``, and
    filtered on ``quantity_received < quantity_ordered`` with no notion of a
    line closed short. An inflated QIT can suppress a reorder for stock that is
    not coming.

    Asserted through ``compute_item_metrics`` — the payload the item screen and
    the ScanTTY TUI both read — and closed through the real close-short action.

    Every case here puts TWO lines on the order, and that is the point rather
    than incidental setup. QIT only looks at orders in ``partially_received``,
    so closing an order's ONLY line short settles the order, moves it to
    ``received``, and drops the line out of the metric through the status
    filter — with the old predicate and the new one alike. A one-line fixture
    therefore proves nothing about the predicate: the order status does all the
    work. A second, genuinely outstanding line holds the order in
    ``partially_received`` so the closed-short line is judged on its own terms.
    """

    def _order_with_a_short_line_and_an_outstanding_one(self, client, supplier, operator):
        """One order, ``partially_received``, whose two lines settle differently.

        ``short`` has taken 4 of 10 and is about to be written off; ``other``
        is untouched and keeps the order in receiving so the metric keeps
        looking at it.
        """
        purchase_order = make_po(supplier, operator)
        written_off = make_item("Bearing", supplier, stock=0, reorder_quantity=5)
        still_coming = make_item("Collar", supplier, stock=0, reorder_quantity=5)
        short = add_line(purchase_order, written_off, 10)
        other = add_line(purchase_order, still_coming, 5)
        receive = client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(short.pk), "quantity_received": 4}]},
            format="json",
        )
        assert receive.status_code == status.HTTP_200_OK, receive.data
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED
        assert compute_item_metrics(written_off)["quantity_in_transit"] == 6
        assert compute_item_metrics(still_coming)["quantity_in_transit"] == 5
        return purchase_order, short, other, written_off, still_coming

    def test_closing_a_line_short_takes_its_balance_out_of_in_transit(
        self, client, supplier, operator
    ):
        purchase_order, short, _other, written_off, still_coming = (
            self._order_with_a_short_line_and_an_outstanding_one(client, supplier, operator)
        )

        response = client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": short.pk, "reason": "backorder cancelled"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED, (
            "the other line must keep the order in receiving, or the status filter "
            "removes the closed-short line before the predicate ever judges it"
        )
        assert compute_item_metrics(written_off)["quantity_in_transit"] == 0
        assert compute_item_metrics(still_coming)["quantity_in_transit"] == 5

    def test_reopening_the_line_puts_the_balance_back_in_transit(self, client, supplier, operator):
        """A close-short taken back is a correction, and the metric follows it."""
        purchase_order, short, _other, written_off, still_coming = (
            self._order_with_a_short_line_and_an_outstanding_one(client, supplier, operator)
        )
        client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": short.pk, "reason": "short-shipped"}]},
            format="json",
        )
        assert compute_item_metrics(written_off)["quantity_in_transit"] == 0

        response = client.post(
            reverse("purchaseorder-reopen-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": short.pk, "reason": "shipped after all"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED
        assert compute_item_metrics(written_off)["quantity_in_transit"] == 6
        assert compute_item_metrics(still_coming)["quantity_in_transit"] == 5

    def test_a_voided_line_was_already_excluded_and_still_is(self, client, supplier, operator):
        """The behaviour that was already right stays right.

        Corroborating, not discriminating: the old predicate carried
        ``is_voided=False`` of its own, so this passes either way. It is here to
        pin that routing through ``outstanding()`` did not lose the exclusion
        the old spelling already had.
        """
        purchase_order = make_po(supplier, operator)
        item = make_item("Seal", supplier)
        line = add_line(purchase_order, item, 10)
        client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(line.pk), "quantity_received": 2}]},
            format="json",
        )
        assert compute_item_metrics(item)["quantity_in_transit"] == 8

        services.void_line_item(line, operator, "discontinued")
        item.refresh_from_db()
        assert compute_item_metrics(item)["quantity_in_transit"] == 0


@pytest.mark.django_db
class TestAdminFilterAgreesWithTheColumnBesideIt:
    """The changelist filter files a line where its own row says it belongs.

    The filter used to spell three states out in SQL and knew nothing of the
    other three: a struck-off line came back under "Pending Receipt" and a line
    closed short under "Partially Received", each contradicting the "Pending"
    column rendered from ``receipt_state`` on the same row.
    """

    def _filtered(self, value):
        request = RequestFactory().get("/", {"receipt_status": value})
        model_admin = PurchaseOrderItemAdmin(PurchaseOrderItem, AdminSite())
        filter_ = ReceiptStatusFilter(
            request, {"receipt_status": [value]}, PurchaseOrderItem, model_admin
        )
        return set(
            filter_.queryset(request, PurchaseOrderItem.objects.all()).values_list("pk", flat=True)
        )

    def test_every_state_the_line_can_report_can_be_filtered_for(self, client, supplier, operator):
        """The options come off the enum, so no state is unreachable."""
        offered = {
            value
            for value, _ in ReceiptStatusFilter(
                RequestFactory().get("/"),
                {},
                PurchaseOrderItem,
                PurchaseOrderItemAdmin(PurchaseOrderItem, AdminSite()),
            ).lookups(None, None)
        }
        assert offered == {state.value for state in PurchaseOrderItem.ReceiptState}

    def test_a_closed_short_line_is_not_filed_as_partially_received(
        self, client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator)
        item = make_item("Coupling", supplier)
        line = add_line(purchase_order, item, 10)
        client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(line.pk), "quantity_received": 3}]},
            format="json",
        )
        assert self._filtered(PurchaseOrderItem.ReceiptState.PARTIALLY_RECEIVED) == {line.pk}

        client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "cancelled"}]},
            format="json",
        )
        line.refresh_from_db()
        assert line.receipt_state == PurchaseOrderItem.ReceiptState.CLOSED_SHORT
        assert self._filtered(PurchaseOrderItem.ReceiptState.PARTIALLY_RECEIVED) == set()
        assert self._filtered(PurchaseOrderItem.ReceiptState.CLOSED_SHORT) == {line.pk}

    def test_a_struck_off_line_is_not_filed_as_awaiting_receipt(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        item = make_item("Flange", supplier)
        line = add_line(purchase_order, item, 4)
        assert self._filtered(PurchaseOrderItem.ReceiptState.NOT_RECEIVED) == {line.pk}

        services.void_line_item(line, operator, "discontinued")
        line.refresh_from_db()
        assert self._filtered(PurchaseOrderItem.ReceiptState.NOT_RECEIVED) == set()
        assert self._filtered(PurchaseOrderItem.ReceiptState.VOIDED) == {line.pk}


@pytest.mark.django_db
class TestPendingCountsOnlyWhatIsStillComing:
    """ "Pending" means what receiving is still owed, on both dashboards.

    Both figures used to be a subtraction of gross totals — everything ordered
    minus everything received — which reports the shortfall of a line written
    off as never arriving as goods still on their way, for ever.
    """

    def test_order_metrics_stop_counting_a_written_off_balance(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        item = make_item("Spacer", supplier)
        line = add_line(purchase_order, item, 10)
        client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(line.pk), "quantity_received": 4}]},
            format="json",
        )
        before = client.get(reverse("purchaseorder-dashboard-summary"))
        assert before.data["items_pending_receipt"] == 6

        client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "cancelled"}]},
            format="json",
        )
        after = client.get(reverse("purchaseorder-dashboard-summary"))
        assert after.data["items_pending_receipt"] == 0
        # The gross running totals are unchanged: what was ordered was still
        # ordered, and what arrived still arrived.
        assert after.data["total_items_on_order"] == before.data["total_items_on_order"]
        assert after.data["total_items_received"] == before.data["total_items_received"]

    def _pending_row(self, client, purchase_order):
        rows = client.get(reverse("orderdelivery-pending-orders")).data
        return next(row for row in rows if str(row["id"]) == str(purchase_order.pk))

    def test_pending_orders_stops_counting_a_written_off_balance(self, client, supplier, operator):
        """The closed-short case, which is the one the old subtraction got wrong.

        ``total_quantity - total_received_quantity`` counted its two sides over
        different sets of lines: the ordered side already dropped struck-off
        lines while the received side kept them. A line CLOSED SHORT is in both
        sets, so its written-off balance survived the subtraction and went on
        being reported as goods still on their way.
        """
        purchase_order = make_po(supplier, operator)
        kept = add_line(purchase_order, make_item("Kept", supplier), 5)
        written_off = add_line(purchase_order, make_item("WroteOff", supplier), 7)
        receive = client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(kept.pk), "quantity_received": 1}]},
            format="json",
        )
        assert receive.status_code == status.HTTP_200_OK, receive.data
        assert self._pending_row(client, purchase_order)["items_pending"] == 4 + 7

        response = client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": written_off.pk, "reason": "never shipped"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED
        assert self._pending_row(client, purchase_order)["items_pending"] == 4

    def test_pending_orders_stops_counting_a_struck_off_line(self, client, supplier, operator):
        """The voided case.

        Corroborating, not discriminating: the old subtraction already dropped a
        voided line from its ordered side, so both spellings agree here. It
        stays to pin that the new expression did not lose that.
        """
        purchase_order = make_po(supplier, operator)
        kept = add_line(purchase_order, make_item("Kept", supplier), 5)
        struck = add_line(purchase_order, make_item("Struck", supplier), 7)
        client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(kept.pk), "quantity_received": 1}]},
            format="json",
        )
        assert self._pending_row(client, purchase_order)["items_pending"] == 4 + 7

        services.void_line_item(struck, operator, "discontinued")
        assert self._pending_row(client, purchase_order)["items_pending"] == 4


@pytest.mark.django_db
class TestAdminEditsReDeriveTheOrder:
    """The Django admin settles lines too, and owes the same re-derivation.

    ``quantity_ordered``, ``quantity_received`` and ``is_voided`` are all
    editable on the line's change form and on the inline under a purchase
    order, so a staff user can settle a line there exactly as ``update_item``
    does — the first known defect reached through a different door. The write
    happens through a ``ModelForm``, which is why the scanner's ordinary write
    arm could not see it: there is no attribute assignment and no ``create()``
    keyword to find.

    Driven through the real admin change forms, built from the ModelAdmin's own
    form so the payload cannot rot as the admin's field set changes.
    """

    @pytest.fixture
    def admin_client(self, operator):
        browser = Client()
        browser.force_login(operator)
        return browser

    def _order_with_one_short_line(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        item = make_item("Gasket", supplier)
        line = add_line(purchase_order, item, 10)
        receive = client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(line.pk), "quantity_received": 6}]},
            format="json",
        )
        assert receive.status_code == status.HTTP_200_OK, receive.data
        purchase_order.refresh_from_db()
        # The receipt wrote ``quantity_received`` in the database. This instance
        # still says 0, and every form below is built from it — posting that 0
        # back would un-receive the line and quietly test nothing.
        line.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED
        assert line.quantity_received == 6
        return purchase_order, line

    @staticmethod
    def _form_value(instance, name):
        value = model_to_dict(instance, fields=[name]).get(name)
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return value

    def _line_form_data(self, line, operator, **overrides):
        model_admin = admin.site._registry[PurchaseOrderItem]
        request = RequestFactory().get("/")
        request.user = operator
        form_class = model_admin.get_form(request, obj=line, change=True)
        data = {name: self._form_value(line, name) for name in form_class.base_fields}
        data.update(overrides)
        return data

    def _order_settled_but_for_one_line(self, admin_client, client, supplier, operator, spare_name):
        """An order whose only remaining outstanding line is the one to delete.

        The spare line is added BEFORE anything settles, because a raw
        ``objects.create`` re-derives nothing — adding it afterwards would leave
        the order sitting at ``received`` over an outstanding line and prove
        only that this setup was wrong.
        """
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item(f"Gasket {spare_name}", supplier), 10)
        spare = add_line(purchase_order, make_item(spare_name, supplier), 5)
        receive = client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(line.pk), "quantity_received": 6}]},
            format="json",
        )
        assert receive.status_code == status.HTTP_200_OK, receive.data
        line.refresh_from_db()

        response = admin_client.post(
            f"/admin/reorder_queue/purchaseorderitem/{line.pk}/change/",
            self._line_form_data(line, operator, quantity_ordered=6),
        )
        assert response.status_code == 302, getattr(response, "context_data", None)
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED
        assert [item.pk for item in purchase_order.outstanding_items] == [spare.pk]
        return purchase_order, line, spare

    def test_lowering_a_line_on_its_change_form_finishes_the_order(
        self, admin_client, client, supplier, operator
    ):
        purchase_order, line = self._order_with_one_short_line(client, supplier, operator)

        response = admin_client.post(
            f"/admin/reorder_queue/purchaseorderitem/{line.pk}/change/",
            self._line_form_data(line, operator, quantity_ordered=6),
        )

        assert response.status_code == 302, getattr(response, "context_data", None)
        purchase_order.refresh_from_db()
        assert purchase_order.outstanding_line_count == 0
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED

    def test_settling_one_line_in_the_admin_leaves_an_order_still_owed_another(
        self, admin_client, client, supplier, operator
    ):
        """Re-deriving is not advancing: the other line still counts.

        Corroborating, not discriminating, for the same reason as its sibling in
        :class:`TestQuantityEditSettlesTheOrder`: it pins over-advancing, which
        removing the refresh does not cause.
        """
        purchase_order, line = self._order_with_one_short_line(client, supplier, operator)
        second = add_line(purchase_order, make_item("Shim", supplier), 3)

        response = admin_client.post(
            f"/admin/reorder_queue/purchaseorderitem/{line.pk}/change/",
            self._line_form_data(line, operator, quantity_ordered=6),
        )

        assert response.status_code == 302
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED
        assert [item.pk for item in purchase_order.outstanding_items] == [second.pk]

    def _order_form_data(self, purchase_order, line, operator, **overrides):
        """The order's change form, its line inline included, shaped for a POST."""
        model_admin = admin.site._registry[PurchaseOrder]
        request = RequestFactory().get("/")
        request.user = operator

        data = {}
        for name in model_admin.get_form(request, obj=purchase_order, change=True).base_fields:
            value = model_to_dict(purchase_order, fields=[name]).get(name)
            if isinstance(value, datetime):
                # The admin renders datetimes through a split date/time widget.
                data[f"{name}_0"] = value.date().isoformat()
                data[f"{name}_1"] = value.time().isoformat()
            else:
                data[name] = "" if value is None else value

        prefix = "items"
        data.update(
            {
                f"{prefix}-TOTAL_FORMS": "1",
                f"{prefix}-INITIAL_FORMS": "1",
                f"{prefix}-MIN_NUM_FORMS": "0",
                f"{prefix}-MAX_NUM_FORMS": "1000",
            }
        )
        inline = model_admin.get_inline_instances(request, purchase_order)[0]
        for name in inline.get_formset(request, purchase_order).form.base_fields:
            data[f"{prefix}-0-{name}"] = self._form_value(line, name)
        data[f"{prefix}-0-id"] = str(line.pk)
        data[f"{prefix}-0-purchase_order"] = str(purchase_order.pk)
        data.update(overrides)
        return data

    def test_lowering_a_line_on_the_order_inline_finishes_the_order(
        self, admin_client, client, supplier, operator
    ):
        """The inline writes the same columns, so it owes the same refresh.

        The order admin prefetches ``items``, so this also pins that the refresh
        re-reads rather than trusting the cached relation's pre-edit quantities.
        """
        purchase_order, line = self._order_with_one_short_line(client, supplier, operator)

        response = admin_client.post(
            f"/admin/reorder_queue/purchaseorder/{purchase_order.pk}/change/",
            self._order_form_data(
                purchase_order, line, operator, **{"items-0-quantity_ordered": "6"}
            ),
        )

        assert response.status_code == 302, getattr(response, "context_data", None)
        line.refresh_from_db()
        assert line.quantity_ordered == 6
        purchase_order.refresh_from_db()
        assert purchase_order.outstanding_line_count == 0
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED

    def test_an_order_save_that_moved_no_line_keeps_the_status_the_operator_chose(
        self, admin_client, client, supplier, operator
    ):
        """The operator's own status choice survives a save that touched no line.

        ``save_related`` runs ``save_formset`` for every inline on every save of
        this form, so a refresh that did not ask whether a line had actually
        moved would re-derive the status straight back over the one the operator
        had just picked — a received order set back to "Confirmed" for a
        re-shipment would silently reappear as "Received". ``status`` is
        editable here on purpose and staying editable is the point.
        """
        purchase_order = make_po(supplier, operator)
        item = make_item("Pulley", supplier)
        line = add_line(purchase_order, item, 10)
        client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(line.pk), "quantity_received": 10}]},
            format="json",
        )
        purchase_order.refresh_from_db()
        line.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED

        response = admin_client.post(
            f"/admin/reorder_queue/purchaseorder/{purchase_order.pk}/change/",
            self._order_form_data(
                purchase_order,
                line,
                operator,
                status=PurchaseOrder.Status.CONFIRMED,
                notes="Re-shipment agreed with the supplier",
            ),
        )

        assert response.status_code == 302, getattr(response, "context_data", None)
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.CONFIRMED
        assert purchase_order.notes == "Re-shipment agreed with the supplier"

    def test_deleting_the_last_outstanding_line_finishes_the_order(
        self, admin_client, client, supplier, operator
    ):
        """A delete writes no settlement field and still settles the order.

        Driven through the admin's own delete-confirmation POST, the door an
        operator actually uses.
        """
        purchase_order, settled, outstanding = self._order_settled_but_for_one_line(
            admin_client, client, supplier, operator, "Idler"
        )

        response = admin_client.post(
            f"/admin/reorder_queue/purchaseorderitem/{outstanding.pk}/delete/", {"post": "yes"}
        )

        assert response.status_code == 302, getattr(response, "context_data", None)
        purchase_order.refresh_from_db()
        assert purchase_order.outstanding_line_count == 0
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED

    def test_the_bulk_delete_action_re_derives_every_order_it_touched(
        self, admin_client, client, supplier, operator
    ):
        """ "Delete selected" never reaches ``delete_model``, so it needs its own.

        Two orders in one selection, so this also pins that each affected order
        is re-derived rather than only the first.
        """
        orders = []
        doomed = []
        for name in ("Cam", "Lever"):
            purchase_order, _settled, outstanding = self._order_settled_but_for_one_line(
                admin_client, client, supplier, operator, name
            )
            orders.append(purchase_order)
            doomed.append(outstanding)

        response = admin_client.post(
            "/admin/reorder_queue/purchaseorderitem/",
            {
                "action": "delete_selected",
                "_selected_action": [str(line.pk) for line in doomed],
                "post": "yes",
            },
        )

        assert response.status_code == 302, getattr(response, "context_data", None)
        for purchase_order in orders:
            purchase_order.refresh_from_db()
            assert purchase_order.outstanding_line_count == 0
            assert purchase_order.status == PurchaseOrder.Status.RECEIVED


class TestTheGuardSeesAdminWriters:
    """The arm that found the admin, exercised on modules built to trip it.

    A ``ModelAdmin`` is invisible to the ordinary write arm — it names no
    settlement field and calls nothing the arm recognises — so the obligation is
    derived from the CLASS: which model it edits, which of that model's
    settlement columns it leaves writable, and whether it can delete rows at
    all. These feed the scanner admin modules of each shape and assert what it
    says about them, which is the same judgement it passes on the real tree.

    Deletion is its own shape and gets its own cases: it writes no settlement
    field, so no widening of the field-write rule could reach it, and an admin
    can be perfectly closed for saves while still stranding an order by removing
    its last outstanding line.
    """

    def _findings(self, sweep, source):
        scanner = settlement_sites._PyScanner(sweep.anchor, "someapp/admin.py", source)
        scanner.run()
        return settlement_sites._write_arm(
            sweep.anchor, scanner.functions, scanner.admin_obligations
        )

    @staticmethod
    def _no_deletes():
        return """
    def has_delete_permission(self, request, obj=None):
        return False
"""

    def test_an_admin_that_can_settle_a_line_and_never_refreshes_is_flagged(self, sweep):
        findings = self._findings(
            sweep,
            f"""
@admin.register(PurchaseOrderItem)
class LineAdmin(admin.ModelAdmin):
    readonly_fields = ["created_at"]
{self._no_deletes()}
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
""",
        )
        assert [f.arm for f in findings] == ["write"]
        assert "LineAdmin" in findings[0].detail
        assert "editable" in findings[0].detail

    def test_the_same_admin_is_clean_once_its_save_hook_re_derives_the_order(self, sweep):
        findings = self._findings(
            sweep,
            f"""
@admin.register(PurchaseOrderItem)
class LineAdmin(admin.ModelAdmin):
    readonly_fields = ["created_at"]
{self._no_deletes()}
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        refresh_receipt_status(PurchaseOrder.objects.get(pk=obj.purchase_order_id))
""",
        )
        assert findings == []

    def test_a_model_admin_must_answer_in_its_own_save_hook_not_a_formset_one(self, sweep):
        """Each door is answered where Django actually goes through it.

        A ``ModelAdmin`` registered on the settlement model writes its object in
        ``save_model``; ``save_formset`` on the same class runs for its inlines,
        never for the object itself, so discharging there leaves the change form
        writing settlement columns with nothing re-deriving behind it.
        """
        findings = self._findings(
            sweep,
            f"""
@admin.register(PurchaseOrderItem)
class LineAdmin(admin.ModelAdmin):
    readonly_fields = ["created_at"]
{self._no_deletes()}
    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        refresh_receipt_status(PurchaseOrder.objects.get(pk=1))
""",
        )
        assert [f.arm for f in findings] == ["write"]
        assert settlement_sites.ADMIN_SAVE_HOOK in findings[0].detail

    def test_save_related_discharges_the_save_door_it_wraps(self, sweep):
        findings = self._findings(
            sweep,
            f"""
@admin.register(PurchaseOrderItem)
class LineAdmin(admin.ModelAdmin):
    readonly_fields = ["created_at"]
{self._no_deletes()}
    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        refresh_receipt_status(PurchaseOrder.objects.get(pk=1))
""",
        )
        assert findings == []

    def test_an_admin_that_makes_every_settlement_column_readonly_is_not_a_writer(self, sweep):
        """Refusing the edit is a legitimate way to satisfy the save rule."""
        readonly = ", ".join(f'"{name}"' for name in sorted(sweep.anchor.all_fields))
        findings = self._findings(
            sweep,
            f"""
@admin.register(PurchaseOrderItem)
class LineAdmin(admin.ModelAdmin):
    readonly_fields = [{readonly}]
{self._no_deletes()}
""",
        )
        assert findings == []

    def test_an_admin_that_can_delete_lines_and_never_refreshes_is_flagged(self, sweep):
        """Closed for saves, open for deletes — still a way to strand an order."""
        readonly = ", ".join(f'"{name}"' for name in sorted(sweep.anchor.all_fields))
        findings = self._findings(
            sweep,
            f"""
@admin.register(PurchaseOrderItem)
class LineAdmin(admin.ModelAdmin):
    readonly_fields = [{readonly}]
""",
        )
        assert [f.arm for f in findings] == ["write"]
        assert "delete" in findings[0].detail
        for hook in settlement_sites.ADMIN_DELETE_HOOKS:
            assert hook in findings[0].detail

    def test_closing_only_the_row_delete_door_leaves_the_bulk_one_open(self, sweep):
        """Django dispatches to exactly one delete hook and never falls through.

        "Delete selected" reaches ``delete_queryset`` and nothing else, so an
        admin that re-derives in ``delete_model`` alone is still stranding
        orders through the door operators use for several lines at once. Any
        one hook satisfying the obligation would have called this clean.
        """
        readonly = ", ".join(f'"{name}"' for name in sorted(sweep.anchor.all_fields))
        findings = self._findings(
            sweep,
            f"""
@admin.register(PurchaseOrderItem)
class LineAdmin(admin.ModelAdmin):
    readonly_fields = [{readonly}]

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        refresh_receipt_status(PurchaseOrder.objects.get(pk=obj.purchase_order_id))
""",
        )
        assert [f.arm for f in findings] == ["write"]
        assert "delete_queryset" in findings[0].detail
        assert "delete_model does not" not in findings[0].detail

    def test_the_same_admin_is_clean_once_both_delete_doors_re_derive_the_order(self, sweep):
        readonly = ", ".join(f'"{name}"' for name in sorted(sweep.anchor.all_fields))
        findings = self._findings(
            sweep,
            f"""
@admin.register(PurchaseOrderItem)
class LineAdmin(admin.ModelAdmin):
    readonly_fields = [{readonly}]

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        refresh_receipt_status(PurchaseOrder.objects.get(pk=obj.purchase_order_id))

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        for order in PurchaseOrder.objects.filter(pk__in=[1]):
            refresh_receipt_status(order)
""",
        )
        assert findings == []

    def test_an_admin_that_denies_deletion_owes_nothing_for_it(self, sweep):
        """Taking the action away is as good an answer as re-deriving after it."""
        readonly = ", ".join(f'"{name}"' for name in sorted(sweep.anchor.all_fields))
        findings = self._findings(
            sweep,
            f"""
@admin.register(PurchaseOrderItem)
class LineAdmin(admin.ModelAdmin):
    readonly_fields = [{readonly}]

    def has_delete_permission(self, request, obj=None):
        return False
""",
        )
        assert findings == []

    def test_an_inline_puts_the_obligation_on_the_order_admin_that_hosts_it(self, sweep):
        """An inline has no hook of its own — its parent's formset writes it.

        Its DELETIONS land there too: ``formset.save()`` performs them, so the
        parent's save hooks are where an inline's removals must be answered for.
        """
        findings = self._findings(
            sweep,
            """
class LineInline(admin.TabularInline):
    model = PurchaseOrderItem


@admin.register(PurchaseOrder)
class OrderAdmin(admin.ModelAdmin):
    inlines = [LineInline]

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
""",
        )
        assert [f.arm for f in findings] == ["write"]
        assert "OrderAdmin" in findings[0].detail
        assert "LineInline" in findings[0].detail
        assert "delete" in findings[0].detail

    def test_an_admin_of_another_model_entirely_is_left_alone(self, sweep):
        findings = self._findings(
            sweep,
            """
@admin.register(DeliveryItem)
class DeliveryItemAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
""",
        )
        assert findings == []


class TestTheScannerSeesAnUpdateItCannotResolve:
    """An ``update()`` it cannot trace back to a model is treated as a line.

    The resolution is syntactic and cannot follow a variable, so
    ``qs.update(quantity_ordered=..., quantity_received=...)`` on a queryset
    held in a local names no model for the scanner to recognise. The two ways of
    being wrong are not symmetric: a false positive costs one explicit receiver,
    a false negative is the whole defect class. An identifier that names some
    other model still buys the call its way out.
    """

    def _findings(self, sweep, source):
        scanner = settlement_sites._PyScanner(sweep.anchor, "someapp/service.py", source)
        scanner.run()
        return scanner.findings + settlement_sites._write_arm(
            sweep.anchor, scanner.functions, scanner.admin_obligations
        )

    def test_an_update_on_an_opaque_local_queryset_is_a_settlement_write(self, sweep):
        findings = self._findings(
            sweep,
            """
def settle_them(qs):
    qs.update(quantity_ordered=5, quantity_received=5)
""",
        )
        assert [f.arm for f in findings] == ["write"]
        assert "settle_them" in findings[0].detail

    def test_it_is_clean_once_that_writer_re_derives_the_order(self, sweep):
        findings = self._findings(
            sweep,
            """
def settle_them(qs, purchase_order):
    qs.update(quantity_ordered=5, quantity_received=5)
    refresh_receipt_status(purchase_order)
""",
        )
        assert findings == []

    def test_an_update_on_another_model_is_not_a_settlement_write(self, sweep):
        """The same column name on ``DeliveryItem`` is a different question."""
        findings = self._findings(
            sweep,
            """
def record_receipt(delivery):
    DeliveryItem.objects.filter(delivery=delivery).update(quantity_received=5)
""",
        )
        assert findings == []
