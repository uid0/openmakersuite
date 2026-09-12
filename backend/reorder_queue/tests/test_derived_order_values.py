"""Every order-level value computed from the lines, held to one rule.

``estimated_total`` went stale after a line delete. That instance was closed
where it was found — and it was never the point. ``estimated_total`` is a
DIFFERENT value from settlement with the IDENTICAL shape: stored on the order,
computed from the lines, moved by many paths, and re-derived by only some of
them. Closing it and stopping would have left the third one to be discovered
the same way, by an operator reading a wrong number off a screen.

So nothing here is a test for ``estimated_total``, and nothing here is a test
for ``status``. Every test in this file is parameterized over
:data:`~reorder_queue.settlement_signals.DERIVED_ORDER_VALUES` and over the
INPUT FIELDS each value's own derivation reaches — both derived from the model
— so a third value declared tomorrow arrives already covered, and a fourth
input field added to an existing derivation arrives already covered. A test per
value, written by hand, is exactly what lets the next one in.

Two properties are proved separately, because passing one while failing the
other is how the class survived this long:

* :class:`TestEveryValueReDerives` — after a write, the stored value equals what
  re-deriving it from scratch would produce. This is the correctness claim.
* :class:`TestEveryValueIsMovedByItsOwnInputsOnly` — and only its OWN inputs
  move it. This is what keeps a receipt out of the money and a note edit out of
  everything, and it is what a single "something changed, re-derive it all"
  flag would quietly destroy while leaving the first property green.

``TestTheGuardCoversEveryValue`` covers the static half — see
``test_settlement_sites.py`` for the guard's own machinery, which this only
exercises per value.
"""

import importlib
import json
from decimal import Decimal

from django.contrib import admin
from django.db import models
from django.forms.models import model_to_dict
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from inventory.models import ItemSupplier
from inventory.tests.factories import InventoryItemFactory, SupplierFactory
from reorder_queue import settlement_sites
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.settlement_signals import DERIVED_ORDER_VALUES, settlement_batch

#: Every ``(value, input field)`` pair the model declares, so one case exists
#: per pair without any of them being written down. ``pytest`` needs the ids to
#: be stable, hence the sort.
VALUE_FIELD_PAIRS = [
    (value, field) for value in DERIVED_ORDER_VALUES for field in sorted(value.inputs)
]


def pair_id(pair):
    value, field = pair
    return f"{value.column}-via-{field}"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def operator(django_user_model):
    return django_user_model.objects.create_superuser(
        username="derived-values-operator",
        email="ops@example.com",
        password="pw",
    )


@pytest.fixture
def api(operator):
    client = APIClient()
    client.force_authenticate(user=operator)
    return client


@pytest.fixture
def admin_client(operator):
    browser = Client()
    browser.force_login(operator)
    return browser


@pytest.fixture
def supplier(db):
    return SupplierFactory(name="Grainger")


def make_item(name, supplier):
    item = InventoryItemFactory(
        name=name, current_stock=0, minimum_stock=0, reorder_quantity=0, image=None
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


def add_line(purchase_order, item, quantity=10, unit_cost=Decimal("10.00")):
    return PurchaseOrderItem.objects.create(
        purchase_order=purchase_order,
        item_supplier=item.item_suppliers.first(),
        quantity_ordered=quantity,
        unit_cost_ordered=unit_cost,
    )


# ---------------------------------------------------------------------------
# generic helpers — everything below works off the model, never off a list
# ---------------------------------------------------------------------------


def moved_value(field, current):
    """A different, still-valid value for ``field``, chosen from its COLUMN TYPE.

    Per type rather than per field on purpose: a sixth input field added to a
    derivation tomorrow is covered by this the moment it lands, as long as it
    is one of the shapes a derivation can be built from. A type nothing here
    knows RAISES rather than skipping — a case that quietly does not run is
    indistinguishable from one that runs and passes, and this whole file exists
    because something was assumed covered and was not.
    """
    column = PurchaseOrderItem._meta.get_field(field)
    if isinstance(column, models.BooleanField):
        return not current
    if isinstance(column, models.DateTimeField):
        return timezone.now()
    if isinstance(column, models.DecimalField):
        return (current or Decimal("0")) + Decimal("1")
    if isinstance(column, models.IntegerField):
        return (current or 0) + 1
    raise AssertionError(
        f"{field} is a {type(column).__name__}; this file does not know how to move "
        f"one, so its value's re-derivation would go untested. Teach moved_value() "
        f"the type rather than excluding the field."
    )


def rederived(purchase_order, value):
    """What ``value`` would be if it were computed from the lines right now.

    Computed on a SEPARATELY FETCHED instance, and read back out of the
    database, so it says what the derivation produces rather than what the
    instance under test happens to be carrying.
    """
    fresh = PurchaseOrder.objects.get(pk=purchase_order.pk)
    value.rederive(fresh)
    return getattr(PurchaseOrder.objects.get(pk=purchase_order.pk), value.column)


def stored(purchase_order, value):
    return getattr(PurchaseOrder.objects.get(pk=purchase_order.pk), value.column)


def form_value(instance, name):
    raw = model_to_dict(instance, fields=[name]).get(name)
    if raw is None:
        return ""
    if isinstance(raw, (dict, list)):
        return json.dumps(raw)
    return raw


def line_form_data(line, operator, **overrides):
    """The line's admin change form, filled from the line and then overridden.

    Built from the ModelAdmin's own form so the payload cannot rot as the
    admin's field set changes — the same construction ``test_settlement_sites``
    uses, and for the same reason.
    """
    model_admin = admin.site._registry[PurchaseOrderItem]
    request = RequestFactory().get("/")
    request.user = operator
    form_class = model_admin.get_form(request, obj=line, change=True)
    data = {name: form_value(line, name) for name in form_class.base_fields}
    data.update(overrides)
    return data


def edit_line_in_admin(admin_client, operator, line, **overrides):
    response = admin_client.post(
        f"/admin/reorder_queue/purchaseorderitem/{line.pk}/change/",
        line_form_data(line, operator, **overrides),
    )
    assert response.status_code == 302, getattr(response, "context_data", None)
    line.refresh_from_db()
    return line


# ---------------------------------------------------------------------------
# the correctness claim
# ---------------------------------------------------------------------------


class TestEveryValueReDerives:
    """Move one of a value's inputs, by any route, and the value follows it.

    "By any route" is the whole point and is why the admin is driven here
    rather than the API: the API's line endpoints already re-rolled the total
    on their own path, and the admin's change form and inline did not. A rule
    honoured on the route somebody happened to be looking at is the defect, not
    the fix.
    """

    @pytest.mark.django_db
    @pytest.mark.parametrize("pair", VALUE_FIELD_PAIRS, ids=pair_id)
    def test_an_admin_edit_to_an_input_leaves_the_value_derived(
        self, pair, admin_client, supplier, operator
    ):
        value, field = pair
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item(f"Gasket {field}", supplier))

        edit_line_in_admin(
            admin_client,
            operator,
            line,
            **{field: moved_value(field, getattr(line, field))},
        )

        assert stored(purchase_order, value) == rederived(purchase_order, value)

    @pytest.mark.django_db
    def test_each_value_has_an_input_whose_edit_actually_moves_it(
        self, admin_client, supplier, operator
    ):
        """The case above is not vacuous: each value really does move.

        Without this, a derivation that never moved — or a mutation that did
        not defeat anything — would keep the parameterized case above green
        while proving nothing, which is the failure mode of asserting that two
        equal things are equal.
        """
        moved_by = {value.column: [] for value in DERIVED_ORDER_VALUES}
        for value, field in VALUE_FIELD_PAIRS:
            purchase_order = make_po(supplier, operator)
            line = add_line(purchase_order, make_item(f"Collar {value.column}{field}", supplier))
            before = stored(purchase_order, value)
            edit_line_in_admin(
                admin_client,
                operator,
                line,
                **{field: moved_value(field, getattr(line, field))},
            )
            if stored(purchase_order, value) != before:
                moved_by[value.column].append(field)

        for column, fields in moved_by.items():
            assert fields, (
                f"no input field's admin edit moved {column}, so every case above "
                f"passed by comparing a value to itself"
            )

    @pytest.mark.django_db
    @pytest.mark.parametrize("value", DERIVED_ORDER_VALUES, ids=lambda v: v.column)
    def test_deleting_a_line_leaves_every_value_derived(self, value, supplier, operator):
        """A delete writes no field and still changes every answer.

        The instance the whole issue was filed from: nobody subtracts a line
        that is gone, so the order went on reporting money for a line it no
        longer had.
        """
        purchase_order = make_po(supplier, operator)
        add_line(purchase_order, make_item(f"Kept {value.column}", supplier))
        going = add_line(purchase_order, make_item(f"Going {value.column}", supplier), 4)

        going.delete()

        assert stored(purchase_order, value) == rederived(purchase_order, value)

    @pytest.mark.django_db
    @pytest.mark.parametrize("value", DERIVED_ORDER_VALUES, ids=lambda v: v.column)
    def test_reparenting_a_line_leaves_every_value_derived_on_both_orders(
        self, value, supplier, operator
    ):
        """A move is a removal from one order and an addition to the other.

        Both sides, because an order left carrying a line's contribution after
        the line went elsewhere is wrong in exactly the way the delete was, and
        the order that gained it understates by the same amount.
        """
        source = make_po(supplier, operator)
        destination = make_po(supplier, operator)
        add_line(source, make_item(f"Stays {value.column}", supplier))
        moving = add_line(source, make_item(f"Moves {value.column}", supplier), 7)

        moving.purchase_order = destination
        moving.save()

        assert stored(source, value) == rederived(source, value)
        assert stored(destination, value) == rederived(destination, value)

    @pytest.mark.django_db
    @pytest.mark.parametrize("value", DERIVED_ORDER_VALUES, ids=lambda v: v.column)
    def test_a_batch_re_derives_each_value_once_per_order(
        self, value, monkeypatch, supplier, operator
    ):
        """Coalescing holds per value, not just overall.

        Counted by patching the re-derivation the DECLARATION names, so the
        count is of the work actually done, and so a third value is counted the
        same way without this test learning its name.
        """
        purchase_order = make_po(supplier, operator)
        lines = [
            add_line(purchase_order, make_item(f"Batched {value.column} {index}", supplier))
            for index in range(3)
        ]

        module = importlib.import_module(value.module, package="reorder_queue")
        original = getattr(module, value.refresh)
        calls = []

        def counted(order):
            calls.append(order.pk)
            return original(order)

        monkeypatch.setattr(module, value.refresh, counted)
        with settlement_batch():
            for line in lines:
                for field in sorted(value.inputs):
                    setattr(line, field, moved_value(field, getattr(line, field)))
                line.save()

        assert calls == [purchase_order.pk], f"re-derived {len(calls)} times, not once"


# ---------------------------------------------------------------------------
# the isolation claim
# ---------------------------------------------------------------------------


class TestEveryValueIsMovedByItsOwnInputsOnly:
    """A value is re-derived when ITS inputs move, and left alone otherwise.

    This is the half a single "a line changed, re-derive everything" flag would
    destroy silently: every value would still be correct, every test above
    would still be green, and receiving an order would rewrite its money and
    bump its ``updated_at`` on every scan. The narrow gate is not an
    optimisation — ``updated_at`` is serialized, and a status an operator chose
    by hand is overwritable.
    """

    @pytest.mark.django_db
    @pytest.mark.parametrize("value", DERIVED_ORDER_VALUES, ids=lambda v: v.column)
    def test_another_values_inputs_do_not_re_derive_this_one(
        self, value, monkeypatch, admin_client, supplier, operator
    ):
        """Move a field that belongs to some OTHER value and not to this one.

        The live instance of this is receiving: ``quantity_received`` is a
        settlement input and not a cost one, so a receipt must move the status
        and leave the money alone. Stated as a rule over the declaration rather
        than as a test about receiving, so the next pair of values inherits it.

        Asserted on whether the re-derivation was CALLED, not on whether the
        stored value changed, and the difference is the whole test. A
        re-derivation that runs when it should not usually writes the value it
        already had — so the figure looks right, and the only traces are a
        query nobody needed, an ``updated_at`` bump on an order nobody edited,
        and a status re-derived over one an operator may have chosen by hand. A
        mutation collapsing the per-value gate into one shared gate was NOT
        caught while this compared values; it is caught now.
        """
        others = set()
        for other in DERIVED_ORDER_VALUES:
            if other.column != value.column:
                others |= other.inputs
        exclusive = sorted(others - value.inputs)
        if not exclusive:
            pytest.skip(
                f"no other value has an input {value.column} does not share; "
                f"there is nothing this value could be moved by mistake"
            )

        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item(f"Isolated {value.column}", supplier))

        module = importlib.import_module(value.module, package="reorder_queue")
        called = []
        monkeypatch.setattr(module, value.refresh, lambda order: called.append(order.pk))

        for field in exclusive:
            edit_line_in_admin(
                admin_client,
                operator,
                line,
                **{field: moved_value(field, getattr(line, field))},
            )

        assert called == [], (
            f"{value.column} was re-derived by a write to {exclusive}, none of which "
            f"is one of its own inputs ({sorted(value.inputs)})"
        )

    @pytest.mark.django_db
    def test_an_edit_that_moves_no_input_touches_the_order_at_all(
        self, admin_client, supplier, operator
    ):
        """A note edit asks nothing of any value, so it does not write the order.

        Asserted on ``updated_at``, which is the observable consequence: it is
        serialized to every client, and an order bumped by a note on one of its
        lines reads as an order somebody changed.
        """
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Annotated", supplier))
        before = PurchaseOrder.objects.get(pk=purchase_order.pk).updated_at

        edit_line_in_admin(admin_client, operator, line, notes="a note, and nothing else")

        assert PurchaseOrder.objects.get(pk=purchase_order.pk).updated_at == before


# ---------------------------------------------------------------------------
# what an operator sees
# ---------------------------------------------------------------------------


class TestTheMoneyAnOperatorReads:
    """The money half, through the endpoints the screens actually call.

    Named as money rather than folded into the generic cases above because a
    total is not just another derived value to whoever reads it: these assert
    the FIGURES, not merely that two derivations agree.
    """

    @pytest.mark.django_db
    def test_repricing_a_line_in_the_admin_moves_the_total_the_api_serves(
        self, api, admin_client, supplier, operator
    ):
        """An admin reprice used to leave the order reporting the old total.

        £50 of stock repriced to £10 went on reading £50 on the detail page, in
        the list, and in ``payment_schedule`` — while the detail page's own
        footer, which sums the rendered lines, read £10 beside it.
        """
        purchase_order = make_po(supplier, operator, PurchaseOrder.Status.DRAFT)
        line = add_line(purchase_order, make_item("Spacer", supplier), 10, Decimal("5.0000"))
        purchase_order.refresh_from_db()
        assert purchase_order.estimated_total == Decimal("50.00")

        edit_line_in_admin(admin_client, operator, line, unit_cost_ordered="1.0000")

        purchase_order.refresh_from_db()
        assert purchase_order.estimated_total == Decimal("10.00")
        served = api.get(reverse("purchaseorder-detail", args=[purchase_order.pk]))
        assert served.status_code == 200, served.data
        assert Decimal(served.data["estimated_total"]) == Decimal("10.00")
        assert Decimal(served.data["payment_schedule"]["amount"]) == Decimal("10.00")

    @pytest.mark.django_db
    def test_lowering_a_quantity_in_the_admin_moves_the_total(
        self, admin_client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator, PurchaseOrder.Status.DRAFT)
        line = add_line(purchase_order, make_item("Collar", supplier), 10, Decimal("5.0000"))
        purchase_order.refresh_from_db()
        assert purchase_order.estimated_total == Decimal("50.00")

        edit_line_in_admin(admin_client, operator, line, quantity_ordered=2)

        purchase_order.refresh_from_db()
        assert purchase_order.estimated_total == Decimal("10.00")

    @pytest.mark.django_db
    def test_receiving_moves_the_status_and_not_a_penny_of_the_total(self, api, supplier, operator):
        """Receiving is not a repricing, and must not read as one.

        The strongest single case for the per-value gate: a receipt writes
        ``quantity_received``, which decides settlement and has nothing to do
        with what the order cost.
        """
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Washer", supplier), 10, Decimal("5.0000"))
        purchase_order.refresh_from_db()
        before = purchase_order.estimated_total

        received = api.post(
            reverse("purchaseorder-receive", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": str(line.pk), "quantity_received": 10}]},
            format="json",
        )
        assert received.status_code == 200, received.data

        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED
        assert purchase_order.estimated_total == before

    @pytest.mark.django_db
    def test_voiding_a_line_leaves_its_money_in_the_stored_sum(self, api, supplier, operator):
        """Struck-off money stays visible, and that is deliberate.

        ``is_voided`` is a settlement input and not a cost one, so the stored
        total keeps the voided line and ``effective_estimated_total`` subtracts
        it at read time. The per-value gate is what preserves that without a
        special case: nothing here says "except voiding".
        """
        purchase_order = make_po(supplier, operator)
        kept = add_line(purchase_order, make_item("Kept", supplier), 4, Decimal("5.0000"))
        struck = add_line(purchase_order, make_item("Struck", supplier), 2, Decimal("5.0000"))
        purchase_order.refresh_from_db()
        assert purchase_order.estimated_total == Decimal("30.00")

        voided = api.post(
            reverse("purchaseorder-void-item", args=[purchase_order.pk, struck.pk]),
            {"reason": "discontinued"},
            format="json",
        )
        assert voided.status_code == 200, voided.data

        purchase_order.refresh_from_db()
        assert purchase_order.estimated_total == Decimal("30.00"), "the stored sum keeps it"
        assert purchase_order.effective_estimated_total == Decimal("20.00")
        assert kept.pk  # the kept line is what the effective total is left holding


# ---------------------------------------------------------------------------
# the guard
# ---------------------------------------------------------------------------


class TestTheGuardCoversEveryValue:
    """The static half: the guard knows every value, and holds each to its own.

    The machinery — the caller graph, the sweep, the unreadable-file rule — is
    covered in ``test_settlement_sites.py``. What is asserted here is that it
    is applied ONCE PER VALUE, because a guard that knows one value is exactly
    what let the second one through.
    """

    @pytest.fixture(scope="class")
    def sweep(self):
        return settlement_sites.scan()

    def test_the_guard_derives_the_same_values_the_routing_declares(self, sweep):
        """Neither can move without the other.

        Read off the routing module's source by the guard, so a value routed
        and not guarded — or guarded and not routed — cannot exist.
        """
        assert {(anchor.column, anchor.seed, anchor.refresh) for anchor in sweep.anchors} == {
            (value.column, value.seed, value.refresh) for value in DERIVED_ORDER_VALUES
        }

    def test_the_guard_derives_each_values_input_fields_from_the_model(self, sweep):
        for anchor in sweep.anchors:
            value = next(v for v in DERIVED_ORDER_VALUES if v.column == anchor.column)
            assert anchor.all_fields == value.inputs, anchor.column
            assert anchor.all_fields, f"{anchor.column} is derived from nothing"

    def test_the_tree_bypasses_no_values_derivation(self, sweep):
        assert sweep.findings == []
        assert sweep.swept_whole_tree

    def test_the_report_names_every_value_and_its_re_derivation(self, capsys):
        assert settlement_sites.main([]) == 0
        printed = capsys.readouterr().out
        for value in DERIVED_ORDER_VALUES:
            assert f"PurchaseOrder.{value.column}" in printed
            assert f"{value.refresh}()" in printed
            for field in value.inputs:
                assert field in printed

    def test_a_price_is_read_as_a_measure_and_not_as_an_event(self, sweep):
        """``unit_cost_ordered`` is a quantity, so reading it alone is fair.

        The split used to be "is the declared column an ``IntegerField``?",
        which filed a ``DecimalField`` price as an event marker — and a marker
        the definition never reads as a bare truth test is reported wherever it
        appears. Every read of a line's price in the repository would have been
        a finding, and the guard would have been turned off rather than fixed.
        """
        cost = next(a for a in sweep.anchors if "unit_cost_ordered" in a.all_fields)
        assert "unit_cost_ordered" in cost.quantities
        assert cost.entangled == frozenset()

    def test_the_settlement_definition_still_reads_its_markers_as_markers(self, sweep):
        """Widening the numeric test did not widen settlement's answer.

        The two stamps only mean anything against each other, and that is what
        makes reading either one alone already wrong. A change made for the
        money must not have quietly bought settlement out of that.
        """
        status = next(a for a in sweep.anchors if a.column == "status")
        assert status.entangled == frozenset({"closed_short_at", "reopened_at"})
        assert status.quantities == frozenset({"quantity_ordered", "quantity_received"})
