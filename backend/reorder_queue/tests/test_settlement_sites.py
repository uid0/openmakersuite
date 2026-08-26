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
        """Re-deriving is not the same as advancing: the other line still counts."""
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
    """

    def test_closing_a_line_short_takes_its_balance_out_of_in_transit(
        self, client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator)
        item = make_item("Bearing", supplier, stock=0, reorder_quantity=5)
        line = add_line(purchase_order, item, 10)
        client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(line.pk), "quantity_received": 4}]},
            format="json",
        )
        assert compute_item_metrics(item)["quantity_in_transit"] == 6

        response = client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "backorder cancelled"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        item.refresh_from_db()
        assert compute_item_metrics(item)["quantity_in_transit"] == 0

    def test_reopening_the_line_puts_the_balance_back_in_transit(self, client, supplier, operator):
        """A close-short taken back is a correction, and the metric follows it."""
        purchase_order = make_po(supplier, operator)
        item = make_item("Bushing", supplier, stock=0, reorder_quantity=5)
        line = add_line(purchase_order, item, 10)
        client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(line.pk), "quantity_received": 4}]},
            format="json",
        )
        client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "short-shipped"}]},
            format="json",
        )
        assert compute_item_metrics(item)["quantity_in_transit"] == 0

        response = client.post(
            reverse("purchaseorder-reopen-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "shipped after all"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        item.refresh_from_db()
        assert compute_item_metrics(item)["quantity_in_transit"] == 6

    def test_a_voided_line_was_already_excluded_and_still_is(self, client, supplier, operator):
        """The behaviour that was already right stays right."""
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

    def test_pending_orders_stops_counting_a_struck_off_line(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        kept = add_line(purchase_order, make_item("Kept", supplier), 5)
        struck = add_line(purchase_order, make_item("Struck", supplier), 7)
        client.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(kept.pk), "quantity_received": 1}]},
            format="json",
        )

        rows = client.get(reverse("orderdelivery-pending-orders")).data
        row = next(r for r in rows if str(r["id"]) == str(purchase_order.pk))
        assert row["items_pending"] == 4 + 7

        services.void_line_item(struck, operator, "discontinued")
        rows = client.get(reverse("orderdelivery-pending-orders")).data
        row = next(r for r in rows if str(r["id"]) == str(purchase_order.pk))
        assert row["items_pending"] == 4


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
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED
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
        """Re-deriving is not advancing: the other line still counts."""
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

    def test_lowering_a_line_on_the_order_inline_finishes_the_order(
        self, admin_client, client, supplier, operator
    ):
        """The inline writes the same columns, so it owes the same refresh.

        The order admin prefetches ``items``, so this also pins that the refresh
        re-reads rather than trusting the cached relation's pre-edit quantities.
        """
        purchase_order, line = self._order_with_one_short_line(client, supplier, operator)
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
        data[f"{prefix}-0-quantity_ordered"] = "6"

        response = admin_client.post(
            f"/admin/reorder_queue/purchaseorder/{purchase_order.pk}/change/", data
        )

        assert response.status_code == 302, getattr(response, "context_data", None)
        line.refresh_from_db()
        assert line.quantity_ordered == 6
        purchase_order.refresh_from_db()
        assert purchase_order.outstanding_line_count == 0
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED


class TestTheGuardSeesAdminWriters:
    """The arm that found the admin, exercised on modules built to trip it.

    A ``ModelAdmin`` is invisible to the ordinary write arm — it names no
    settlement field and calls nothing the arm recognises — so the obligation is
    derived from the CLASS: which model it edits, and which of that model's
    settlement columns it leaves writable. These feed the scanner admin modules
    of each shape and assert what it says about them, which is the same
    judgement it passes on the real tree.
    """

    def _findings(self, sweep, source):
        scanner = settlement_sites._PyScanner(sweep.anchor, "someapp/admin.py", source)
        scanner.run()
        return settlement_sites._write_arm(
            sweep.anchor, scanner.functions, scanner.admin_obligations
        )

    def test_an_admin_that_can_settle_a_line_and_never_refreshes_is_flagged(self, sweep):
        findings = self._findings(
            sweep,
            """
@admin.register(PurchaseOrderItem)
class LineAdmin(admin.ModelAdmin):
    readonly_fields = ["created_at"]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
""",
        )
        assert [f.arm for f in findings] == ["write"]
        assert "LineAdmin" in findings[0].detail

    def test_the_same_admin_is_clean_once_its_save_hook_re_derives_the_order(self, sweep):
        findings = self._findings(
            sweep,
            """
@admin.register(PurchaseOrderItem)
class LineAdmin(admin.ModelAdmin):
    readonly_fields = ["created_at"]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        refresh_receipt_status(PurchaseOrder.objects.get(pk=obj.purchase_order_id))
""",
        )
        assert findings == []

    def test_an_admin_that_makes_every_settlement_column_readonly_is_not_a_writer(self, sweep):
        """Refusing the edit is a legitimate way to satisfy the rule."""
        readonly = ", ".join(f'"{name}"' for name in sorted(sweep.anchor.all_fields))
        findings = self._findings(
            sweep,
            f"""
@admin.register(PurchaseOrderItem)
class LineAdmin(admin.ModelAdmin):
    readonly_fields = [{readonly}]
""",
        )
        assert findings == []

    def test_an_inline_puts_the_obligation_on_the_order_admin_that_hosts_it(self, sweep):
        """An inline has no save hook of its own — its parent's formset writes it."""
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

    def test_an_admin_of_another_model_entirely_is_left_alone(self, sweep):
        findings = self._findings(
            sweep,
            """
@admin.register(DeliveryItem)
class DeliveryItemAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
""",
        )
        assert findings == []
