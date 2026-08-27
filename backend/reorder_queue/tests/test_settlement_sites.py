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
from django.db import connection
from django.db.models.signals import post_save
from django.forms.models import model_to_dict
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import ItemSupplier
from inventory.services.item_metrics import compute_item_metrics
from inventory.tests.factories import InventoryItemFactory, SupplierFactory
from reorder_queue import services, settlement_signals, settlement_sites
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

    def test_the_command_line_form_can_list_the_whole_derived_set(self, capsys, sweep):
        """``--sites`` is how the derived set was read off for the PR."""
        # Named the way the sweep names it, not the way this checkout happens to
        # be laid out: paths are relative to the root the scan anchors on, and
        # the docker-compose job mounts backend/ alone at /app, so a literal
        # "backend/" prefix is a property of a developer checkout rather than of
        # the report.
        model_sites = [path for path, *_ in sweep.sites if path.endswith("reorder_queue/models.py")]
        assert model_sites, "the sweep listed no settlement site in the model that defines it"

        assert settlement_sites.main(["--sites"]) == 0
        printed = capsys.readouterr().out
        assert "sites naming a settlement field" in printed
        assert model_sites[0] in printed


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


class TestTheWriteArmDischargesOnlyAlongRealCallEdges:
    """A same-named function elsewhere cannot answer for a writer's obligation.

    The arm credits a writer's callers with its refresh, and it used to find
    them by BARE name: any function calling ``close_short`` counted as a caller
    of every ``close_short`` in the tree. An uncalled settlement writer was
    therefore reported clean because an unrelated module called the MODEL's
    ``close_short()`` and refreshed — the guard certifying the very thing it
    exists to catch.

    Resolution now goes same-module first, then across modules only through a
    name the calling module actually imports, and an unresolvable call buys no
    discharge at all.
    """

    WRITER = "def close_short(line):\n    line.quantity_received = 0\n"

    def _findings(self, sweep, modules):
        functions = {}
        for rel, source in modules:
            scanner = settlement_sites._PyScanner(sweep.anchor, rel, source)
            scanner.run()
            functions.update(scanner.functions)
        return settlement_sites._write_arm(sweep.anchor, functions)

    def test_a_writer_nothing_calls_is_flagged(self, sweep):
        findings = self._findings(sweep, [("appa/service.py", self.WRITER)])
        assert [f.arm for f in findings] == ["write"]
        assert "close_short" in findings[0].detail

    def test_a_same_named_model_method_elsewhere_does_not_discharge_it(self, sweep):
        """The reported hole, kept as a case so it cannot come back.

        ``appb`` never imports ``appa``; it calls the model's own method on a
        local. Bare-name matching credited that as a call edge.
        """
        findings = self._findings(
            sweep,
            [
                ("appa/service.py", self.WRITER),
                (
                    "appb/service.py",
                    "from reorder_queue.services.receiving import refresh_receipt_status\n"
                    "\n\n"
                    "def close_lines_short(purchase_order, closures):\n"
                    "    for line, reason in closures:\n"
                    "        line.close_short(reason=reason)\n"
                    "    refresh_receipt_status(purchase_order)\n",
                ),
            ],
        )
        assert [f.arm for f in findings] == ["write"]
        assert "close_short" in findings[0].detail

    def test_a_caller_that_really_imports_it_does_discharge_it(self, sweep):
        findings = self._findings(
            sweep,
            [
                ("appa/service.py", self.WRITER),
                (
                    "appc/service.py",
                    "from appa.service import close_short\n"
                    "from reorder_queue.services.receiving import refresh_receipt_status\n"
                    "\n\n"
                    "def settle(line, purchase_order):\n"
                    "    close_short(line)\n"
                    "    refresh_receipt_status(purchase_order)\n",
                ),
            ],
        )
        assert findings == []

    def test_a_caller_in_the_same_module_discharges_it(self, sweep):
        findings = self._findings(
            sweep,
            [
                (
                    "appa/service.py",
                    self.WRITER + "\n\ndef settle(line, purchase_order):\n"
                    "    close_short(line)\n"
                    "    refresh_receipt_status(purchase_order)\n",
                )
            ],
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
        return scanner.findings + settlement_sites._write_arm(sweep.anchor, scanner.functions)

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


@pytest.mark.django_db
class TestLineWritesReDeriveTheirOrder:
    """The routing that replaced the admin's hand-maintained hook list.

    Closing the admin door by door produced a new door every round — change
    form, inline formset, row delete, bulk delete, and finally REPARENTING a
    line onto another order, which no amount of refreshing the order the line
    ended up on could ever have caught. The obligation belongs to the line, so
    it lives on the line's own save/delete signals and no admin hook mentions
    settlement at all.

    Driven through the real admin forms and the real receive endpoint, never by
    calling ``refresh_receipt_status``.
    """

    @pytest.fixture
    def admin_client(self, operator):
        browser = Client()
        browser.force_login(operator)
        return browser

    def _line_form_data(self, line, operator, **overrides):
        model_admin = admin.site._registry[PurchaseOrderItem]
        request = RequestFactory().get("/")
        request.user = operator
        form_class = model_admin.get_form(request, obj=line, change=True)
        data = {}
        for name in form_class.base_fields:
            value = model_to_dict(line, fields=[name]).get(name)
            if value is None:
                data[name] = ""
            elif isinstance(value, (dict, list)):
                data[name] = json.dumps(value)
            else:
                data[name] = value
        data.update(overrides)
        return data

    def test_moving_a_line_to_another_order_re_derives_the_one_it_left(
        self, admin_client, client, supplier, operator
    ):
        """The door that made door-by-door untenable.

        Order A is left with nothing outstanding by a line MOVING AWAY, not by
        anything written on a line of A's. Refreshing ``obj.purchase_order_id``
        after the save re-derives B and never asks A anything.
        """
        source = make_po(supplier, operator)
        settled = add_line(source, make_item("Rivet", supplier), 10)
        moving = add_line(source, make_item("Washer", supplier), 5)
        receive = client.post(
            reverse("purchaseorder-receive", args=[source.pk]),
            {"items": [{"purchase_order_item": str(settled.pk), "quantity_received": 10}]},
            format="json",
        )
        assert receive.status_code == status.HTTP_200_OK, receive.data
        source.refresh_from_db()
        assert source.status == PurchaseOrder.Status.PARTIALLY_RECEIVED
        destination = make_po(supplier, operator)
        moving.refresh_from_db()

        response = admin_client.post(
            f"/admin/reorder_queue/purchaseorderitem/{moving.pk}/change/",
            self._line_form_data(moving, operator, purchase_order=str(destination.pk)),
        )

        assert response.status_code == 302, getattr(response, "context_data", None)
        moving.refresh_from_db()
        assert moving.purchase_order_id == destination.pk
        source.refresh_from_db()
        assert source.outstanding_line_count == 0
        assert source.status == PurchaseOrder.Status.RECEIVED, (
            "the order the line LEFT was never re-derived — it has nothing "
            "outstanding and both close-out actions will refuse it"
        )

    def _status_writes_receiving(self, client, supplier, operator, line_count, tag):
        """Receive a whole order in one request; return its writes to ``status``.

        Counted as writes to the order rather than as calls to the helper: what
        must not fan out is the work, and a receipt that issues one status write
        per line is the thing this measures.
        """
        purchase_order = make_po(supplier, operator)
        lines = [
            add_line(purchase_order, make_item(f"{tag} {n}", supplier), 4)
            for n in range(line_count)
        ]
        with CaptureQueriesContext(connection) as captured:
            response = client.post(
                reverse("purchaseorder-receive", args=[purchase_order.pk]),
                {
                    "items": [
                        {"purchase_order_item": str(line.pk), "quantity_received": 4}
                        for line in lines
                    ]
                },
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK, response.data
        return [
            query["sql"]
            for query in captured.captured_queries
            if 'UPDATE "reorder_queue_purchaseorder"' in query["sql"] and '"status"' in query["sql"]
        ]

    def test_receiving_many_lines_does_not_re_derive_the_order_per_line(
        self, client, supplier, operator
    ):
        """Coalesced per unit of work, so the cost does not follow the line count.

        Asserted by comparing two receipts of very different sizes rather than
        against a magic number: what matters is that a twelve-line order costs
        what a three-line order costs. A bound that grows with the input is not
        a bound.
        """
        few = self._status_writes_receiving(client, supplier, operator, 3, "Bolt")
        many = self._status_writes_receiving(client, supplier, operator, 12, "Screw")

        assert len(many) == len(few), (
            f"{len(few)} status re-derivations for 3 lines but {len(many)} for 12 — "
            "the routing is fanning out per line instead of per unit of work"
        )
        assert len(many) == 1, (
            f"{len(many)} status writes for one receipt. One is the transition the "
            "receipt actually caused; a second would mean a re-derivation that "
            "changed nothing still wrote, which is what the no-op guard exists to stop"
        )

    def test_deleting_many_lines_at_once_re_derives_the_order_once(
        self, client, supplier, operator
    ):
        """The door the admin's "Delete selected" goes through.

        ``queryset.delete()`` fans ``post_delete`` out per row, so without
        coalescing an order would be asked its status once per deleted line.
        Counted as writes to the order, and measured on a delete that really
        does move the status so the count is of re-derivations that mattered.
        """
        purchase_order = make_po(supplier, operator)
        settled = add_line(purchase_order, make_item("Hub", supplier), 5)
        doomed = [add_line(purchase_order, make_item(f"Spoke {n}", supplier), 2) for n in range(4)]
        receive = client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(settled.pk), "quantity_received": 5}]},
            format="json",
        )
        assert receive.status_code == status.HTTP_200_OK, receive.data
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED

        with CaptureQueriesContext(connection) as captured:
            PurchaseOrderItem.objects.filter(pk__in=[line.pk for line in doomed]).delete()

        # Each re-derivation re-reads its order; that read is the unit of work
        # being counted. Counting status WRITES would prove nothing here — only
        # the last of four deletes moves the status, so the no-op guard would
        # collapse an uncoalesced run to one write as well.
        rederivations = [
            query["sql"]
            for query in captured.captured_queries
            if 'FROM "reorder_queue_purchaseorder"' in query["sql"] and '"id" IN (' in query["sql"]
        ]
        assert len(rederivations) == 1, (
            f"{len(rederivations)} re-derivations for {len(doomed)} deleted lines — "
            "the delete is fanning out per row instead of per unit of work"
        )
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED

    def test_the_receive_response_carries_the_re_derived_status(self, client, supplier, operator):
        """ScanTTY reads the status off the receipt's own response.

        Coalescing must happen inside the unit of work. Deferring the flush to
        ``transaction.on_commit`` would still get the database right and would
        answer this request with the status the order had before it.
        """
        purchase_order = make_po(supplier, operator)
        lines = [add_line(purchase_order, make_item(f"Nut {n}", supplier), 3) for n in range(3)]

        response = client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {
                "items": [
                    {"purchase_order_item": str(line.pk), "quantity_received": 3} for line in lines
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["status"] == PurchaseOrder.Status.RECEIVED
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED

    def test_a_refresh_that_touches_a_line_cannot_re_enter_its_own_signal(
        self, client, supplier, operator
    ):
        """The guard, exercised rather than argued from today's call graph.

        ``refresh_receipt_status`` writes only ``PurchaseOrder`` today, so it
        could not recurse anyway — which is a fact about this week's code and
        not a guarantee. This makes the order's own save MOVE a settlement field
        on its lines, the loop a future change could introduce, and drives it
        down the path with NO batch open: outside a batch a line write
        re-derives immediately, so without the flag the second re-derivation
        starts before the first has returned and the stack runs out.

        The stand-in has to make the derived answer ALTERNATE, not merely move.
        A save that changes nothing is stopped by the dirty check, and a status
        re-derived to the value it already holds is stopped by the no-op guard;
        either would leave this passing whether or not the flag existed.
        """
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Ferrule", supplier), 4)
        receive = client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(line.pk), "quantity_received": 4}]},
            format="json",
        )
        assert receive.status_code == status.HTTP_200_OK, receive.data
        purchase_order.refresh_from_db()
        line.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED

        def flip_the_lines(sender, instance, **kwargs):
            for item in instance.items.all():
                item.quantity_ordered = (
                    item.quantity_received
                    if item.quantity_ordered != item.quantity_received
                    else item.quantity_received + 1
                )
                item.save()

        post_save.connect(flip_the_lines, sender=PurchaseOrder)
        try:
            line.quantity_ordered = 7
            line.save()
        finally:
            post_save.disconnect(flip_the_lines, sender=PurchaseOrder)

        purchase_order.refresh_from_db()
        assert purchase_order.status in {
            PurchaseOrder.Status.RECEIVED,
            PurchaseOrder.Status.PARTIALLY_RECEIVED,
        }, "the routing settled somewhere; without the flag it would not have returned at all"


@pytest.mark.django_db
class TestOnlyASettlementChangeReDerivesTheOrder:
    """A line save that settles nothing asks the order nothing.

    Routing onto the model's save signal put the refresh behind EVERY line
    write, including the many that move no settlement field — a note, a landed
    cost, a shipment date. Re-deriving over those rewrites a status an operator
    may have chosen by hand and bumps the order's ``updated_at``, which is
    serialized. This is the same containment the admin formset hook needed one
    layer up, applied where the writes actually are.
    """

    def _order_with_an_operator_chosen_status(self, client, supplier, operator):
        """A received order an operator has deliberately set back to Confirmed."""
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Grommet", supplier), 6)
        receive = client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(line.pk), "quantity_received": 6}]},
            format="json",
        )
        assert receive.status_code == status.HTTP_200_OK, receive.data
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED

        PurchaseOrder.objects.filter(pk=purchase_order.pk).update(
            status=PurchaseOrder.Status.CONFIRMED
        )
        purchase_order.refresh_from_db()
        line.refresh_from_db()
        return purchase_order, line

    @pytest.mark.parametrize(
        "field, value",
        [
            ("notes", "Chased the vendor"),
            ("unit_cost_actual", Decimal("12.34")),
            ("expected_shipment_date", None),
        ],
    )
    def test_editing_a_non_settlement_column_leaves_the_status_alone(
        self, client, supplier, operator, field, value
    ):
        purchase_order, line = self._order_with_an_operator_chosen_status(
            client, supplier, operator
        )
        before = PurchaseOrder.objects.values_list("updated_at", flat=True).get(
            pk=purchase_order.pk
        )
        if field == "expected_shipment_date":
            value = timezone.now().date()

        setattr(line, field, value)
        line.save()

        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.CONFIRMED, (
            "an edit that settles nothing re-derived the order and overwrote the "
            "status the operator chose"
        )
        after = PurchaseOrder.objects.values_list("updated_at", flat=True).get(pk=purchase_order.pk)
        assert after == before, "the order was written for a line edit that settles nothing"

    def test_moving_a_settlement_column_does_re_derive(self, client, supplier, operator):
        """The containment must not become a way to miss a real transition."""
        purchase_order, line = self._order_with_an_operator_chosen_status(
            client, supplier, operator
        )

        line.quantity_ordered = 9
        line.save()

        purchase_order.refresh_from_db()
        assert purchase_order.outstanding_line_count == 1
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED

    def test_moving_the_line_to_another_order_re_derives_both(self, client, supplier, operator):
        """Reparenting moves no settlement column and still changes two answers."""
        source = make_po(supplier, operator)
        settled = add_line(source, make_item("Clip", supplier), 4)
        moving = add_line(source, make_item("Pin", supplier), 3)
        receive = client.post(
            reverse("purchaseorder-receive", args=[source.pk]),
            {"items": [{"purchase_order_item": str(settled.pk), "quantity_received": 4}]},
            format="json",
        )
        assert receive.status_code == status.HTTP_200_OK, receive.data
        source.refresh_from_db()
        assert source.status == PurchaseOrder.Status.PARTIALLY_RECEIVED

        destination = make_po(supplier, operator)
        received_there = add_line(destination, make_item("Stud", supplier), 2)
        client.post(
            reverse("purchaseorder-receive", args=[destination.pk]),
            {"items": [{"purchase_order_item": str(received_there.pk), "quantity_received": 2}]},
            format="json",
        )
        destination.refresh_from_db()
        assert destination.status == PurchaseOrder.Status.RECEIVED

        moving.refresh_from_db()
        moving.purchase_order = destination
        moving.save()

        source.refresh_from_db()
        destination.refresh_from_db()
        assert source.status == PurchaseOrder.Status.RECEIVED, "the order it left"
        assert destination.status == PurchaseOrder.Status.PARTIALLY_RECEIVED, "the order it joined"

    def test_the_dirty_check_reads_the_same_fields_the_guard_derives(self, sweep):
        """One definition, not two.

        The signal decides "did settlement move?" from the closure
        ``settlement_sites`` walks off ``PurchaseOrderItem.is_settled`` — the
        same one the guard enforces the rest of the tree against. A field list
        typed into the signal module would be the hand-maintained list this
        whole change exists to delete, one layer down.
        """
        assert settlement_signals.settlement_fields() == sweep.anchor.all_fields
        assert settlement_signals.settlement_fields(), "the dirty check checks nothing"
