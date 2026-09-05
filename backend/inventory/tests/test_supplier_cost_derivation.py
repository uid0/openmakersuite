"""The supplier cost write path, stated as invariants rather than as symptoms.

``ItemSupplier.save()`` derives ``unit_cost`` and ``package_cost`` from each
other. Both columns are ``DecimalField(decimal_places=2)``, so the
``package -> unit`` direction is LOSSY and the pair does not round-trip:

    package_cost 10.00, quantity_per_package 3
      unit_cost    = 10.00 / 3 = 3.3333...  stored as 3.33   (a cent is lost)
      package_cost = 3.33  * 3 =                     9.99    (and never comes back)

Every defect on this path is that one cent escaping. A write site that hands the
model a PARTIAL picture makes the model re-derive from whatever survived, and the
re-derivation runs `package -> unit -> package` on values nobody edited.

These tests fix the invariants that must hold at EVERY write site, so the path is
not patched one symptom at a time. The fixtures deliberately use a pack size that
does NOT divide the case price evenly — every pre-existing test of this behaviour
uses ``quantity_per_package=1``, where the derivation is exact and no defect on
this path is reachable.

Test naming: BEFORE/AFTER marks behaviour this branch moves, CONTROL marks
behaviour that must NOT move.
"""

from decimal import Decimal

from django.urls import reverse

import pytest

from rest_framework.test import APIClient

from inventory.models.core import ItemSupplier, PriceHistory
from inventory.tests.factories import InventoryItemFactory, SupplierFactory

pytestmark = pytest.mark.django_db


# The pair that cannot round-trip. 10.00 / 3 -> 3.33 -> 9.99.
LOSSY_PACKAGE_COST = Decimal("10.00")
LOSSY_PACK_SIZE = 3
LOSSY_UNIT_COST = Decimal("3.33")


@pytest.fixture
def item():
    return InventoryItemFactory(image=None)


@pytest.fixture
def supplier():
    return SupplierFactory()


@pytest.fixture
def staff_client(django_user_model):
    """Kits are staff-writable; mirrors the fixture in ``test_kits.py``."""
    user = django_user_model.objects.create_user(
        username="cost-staff", password="pw", is_staff=True
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_link(item, supplier, **overrides):
    """A saved link whose stored pair cannot be re-derived from either half."""
    fields = {
        "item": item,
        "supplier": supplier,
        "supplier_sku": "SKU-ORIGINAL",
        "supplier_url": "",
        "unit_cost": None,
        "package_cost": LOSSY_PACKAGE_COST,
        "quantity_per_package": LOSSY_PACK_SIZE,
        "average_lead_time": 7,
        "is_primary": True,
    }
    fields.update(overrides)
    link = ItemSupplier.objects.create(**fields)
    link.refresh_from_db()
    return link


def stored_pair(link):
    link.refresh_from_db()
    return (link.unit_cost, link.package_cost, link.quantity_per_package)


def history_rows(link):
    return list(
        PriceHistory.objects.filter(item_supplier=link)
        .order_by("recorded_at", "pk")
        .values_list("change_type", "unit_cost", "package_cost", "quantity_per_package")
    )


class TestInvariantTheDerivationIsLossy:
    """The arithmetic the whole path turns on, pinned so it is never assumed away."""

    def test_the_stored_pair_cannot_be_rebuilt_from_the_unit_cost(self, item, supplier):
        """CONTROL: this is WHY a partial write corrupts. 10.00 -> 3.33 -> 9.99."""
        link = make_link(item, supplier)

        assert link.package_cost == Decimal("10.00")
        assert link.unit_cost == Decimal("3.33")
        # Re-deriving the case price from the stored unit price loses a cent.
        assert link.unit_cost * link.quantity_per_package == Decimal("9.99")
        assert link.unit_cost * link.quantity_per_package != link.package_cost

    def test_a_derived_unit_cost_is_stored_at_the_column_scale(self, item, supplier):
        """AFTER: what save() leaves in memory equals what the database holds.

        save() assigned ``package_cost / quantity_per_package`` unrounded, so the
        in-memory row carried 3.333333333333333333333333333 while the row on disk
        held 3.33. Every reader between save() and a refresh saw a number the
        database does not have — including record_price_history().
        """
        link = ItemSupplier(
            item=item,
            supplier=supplier,
            supplier_sku="SKU-SCALE",
            package_cost=LOSSY_PACKAGE_COST,
            quantity_per_package=LOSSY_PACK_SIZE,
        )
        link.save()

        in_memory = link.unit_cost
        link.refresh_from_db()
        assert in_memory == link.unit_cost
        assert in_memory.as_tuple().exponent == -2


class TestInvariantASaveThatChangesNoPriceMovesNoPrice:
    """Invariant 1. Editing a SKU, or a flag, is not a price edit."""

    def test_flag_only_save_leaves_both_stored_prices_byte_identical(self, item, supplier):
        """CONTROL: ``mark_discontinued`` / ``void_line_item`` re-enter the derivation.

        Verified passing on base too: the re-derived unit price rounds back to the
        same 3.33, so the STORED pair does not move here. What base got wrong on
        this exact save is the price-history row, pinned in the test below. This
        stays as the control that says the columns themselves were never the
        casualty of a flag edit — so a future change cannot make them one.
        """
        link = make_link(item, supplier)
        before = stored_pair(link)

        fresh = ItemSupplier.objects.get(pk=link.pk)
        fresh.is_discontinued = True
        fresh.is_active = False
        fresh.save()

        assert stored_pair(link) == before

    def test_flag_only_save_writes_no_price_history_row(self, item, supplier):
        """BEFORE/AFTER: the captain reads this history. A flag edit is not a price.

        ``pricing_changed()`` compared the STORED 3.33 against the freshly
        re-derived, unrounded 3.3333..., so every save of a link whose case price
        is not evenly divisible filed an ``updated`` price-history row recording a
        price change that did not happen.
        """
        link = make_link(item, supplier)
        PriceHistory.objects.filter(item_supplier=link).delete()

        fresh = ItemSupplier.objects.get(pk=link.pk)
        fresh.is_discontinued = True
        fresh.save()

        assert history_rows(link) == []

    def test_sku_only_edit_on_the_item_form_leaves_both_prices_byte_identical(
        self, item, supplier, authenticated_client
    ):
        """CONTROL: a PATCH naming no cost at all never moved a price, on base either.

        Kept as the floor of the invariant: the defect needs the form to ECHO a
        cost box, which the test below does. Without this control, a fix could
        satisfy the echo case by special-casing it and quietly break the plain one.
        """
        client, _ = authenticated_client
        link = make_link(item, supplier)
        before = stored_pair(link)

        response = client.patch(
            reverse("itemsupplier-detail", args=[link.pk]),
            {"supplier_sku": "SKU-EDITED"},
            format="json",
        )

        assert response.status_code == 200
        assert stored_pair(link) == before

    def test_sku_only_edit_that_echoes_the_stored_unit_cost_moves_no_price(
        self, item, supplier, authenticated_client
    ):
        """BEFORE/AFTER: symptom 5 as the forms actually send it.

        Every supplier form seeds both cost boxes from the stored row and sends
        them back. An echoed unit cost beside an edited SKU is not a price edit,
        and must not re-derive the case price it was itself rounded from.
        """
        client, _ = authenticated_client
        link = make_link(item, supplier)
        before = stored_pair(link)

        response = client.patch(
            reverse("itemsupplier-detail", args=[link.pk]),
            {"supplier_sku": "SKU-EDITED", "unit_cost": str(LOSSY_UNIT_COST)},
            format="json",
        )

        assert response.status_code == 200
        assert stored_pair(link) == before
        assert history_rows(link) == [("created", LOSSY_UNIT_COST, LOSSY_PACKAGE_COST, 3)]


class TestInvariantAnAbsentValueIsNeverFabricated:
    """Invariant 3. Absent is not zero, and absent is not the field default."""

    def test_create_with_no_cost_at_all_leaves_both_prices_null(self, item, supplier):
        """CONTROL: no price on file must stay reachable — it is not a free item."""
        link = make_link(item, supplier, unit_cost=None, package_cost=None)

        assert link.unit_cost is None
        assert link.package_cost is None

    def test_kit_terms_that_omit_the_pack_size_do_not_reset_a_recorded_one(
        self, supplier, staff_client
    ):
        """BEFORE/AFTER: symptom 2. ``defaults.setdefault("quantity_per_package", 1)``.

        The kit form does not offer a pack-size box, so the kit write site
        supplied the model default for it on EVERY save. A recorded pack size of 3
        was reset to 1, and the unit price then re-derived from the untouched case
        price at the wrong pack size. Driven through the real kit endpoint, not a
        copy of its ``defaults`` dict.
        """
        component = InventoryItemFactory(image=None, is_kit=False, is_serialized=False)
        kit = InventoryItemFactory(
            image=None, is_kit=True, current_stock=0, minimum_stock=0
        )
        link = make_link(kit, supplier)
        before = stored_pair(link)

        response = staff_client.patch(
            reverse("kit-detail", args=[kit.pk]),
            {
                "name": kit.name,
                "components": [{"component": component.pk, "quantity": 1}],
                "supplier_terms": {
                    "supplier": supplier.pk,
                    "supplier_sku": "KIT-SKU-EDITED",
                },
            },
            format="json",
        )

        assert response.status_code == 200, response.data
        link.refresh_from_db()
        assert link.supplier_sku == "KIT-SKU-EDITED"
        assert stored_pair(link) == before


class TestInvariantOperatorInputIsNotDiscarded:
    """A price the operator names governs. Neither half is inert."""

    def test_a_typed_unit_cost_is_honoured_on_a_link_that_has_a_case_price(
        self, item, supplier, authenticated_client
    ):
        """BEFORE/AFTER: symptom 3. save() re-derived unit_cost from the stored case
        price and overwrote what the operator typed, so the box was inert."""
        client, _ = authenticated_client
        link = make_link(item, supplier)

        response = client.patch(
            reverse("itemsupplier-detail", args=[link.pk]),
            {"unit_cost": "4.00"},
            format="json",
        )

        assert response.status_code == 200
        link.refresh_from_db()
        assert link.unit_cost == Decimal("4.00")
        # The case price follows the value the operator named: 4.00 x 3.
        assert link.package_cost == Decimal("12.00")

    def test_a_derived_case_price_is_persisted_on_a_restricted_update(self, item, supplier):
        """BEFORE/AFTER: symptom 4. ``update_or_create`` restricts ``update_fields``
        to its own ``defaults`` keys, so a case price save() derived was computed
        and then dropped on the floor."""
        link = make_link(item, supplier, unit_cost=None, package_cost=None,
                         quantity_per_package=LOSSY_PACK_SIZE)

        ItemSupplier.objects.update_or_create(
            item=link.item,
            supplier=link.supplier,
            defaults={"unit_cost": Decimal("5.00"), "supplier_sku": "SKU-COSTED"},
        )

        link.refresh_from_db()
        assert link.unit_cost == Decimal("5.00")
        assert link.package_cost == Decimal("15.00")


class TestInvariantEveryWriteSiteObeysTheSameRule:
    """Invariant 5. One rule, so a fix at one site is a fix at all of them."""

    def test_the_item_form_write_site_omits_a_cost_the_request_did_not_carry(
        self, item, supplier
    ):
        """BEFORE/AFTER: views.py ``_create_supplier_relationship``.

        It used to put ``"package_cost": None`` in ``defaults`` whenever the
        request carried only a unit cost. ``update_or_create`` applies every key
        in ``defaults`` to a row it finds, and ``save()`` reads a cost that moved
        to ``None`` as "the operator cleared this price" — so the shape that means
        "absent" would have read as "clear". Pinned against the real method so the
        two cannot drift apart.
        """
        from inventory.views import InventoryItemViewSet

        link = make_link(item, supplier)
        before = stored_pair(link)
        view = InventoryItemViewSet()

        view._create_supplier_relationship(
            item,
            supplier,
            {"supplier_sku": "SKU-EDITED"},
            (None, LOSSY_UNIT_COST),
            7,
            LOSSY_PACK_SIZE,
        )

        link.refresh_from_db()
        assert link.supplier_sku == "SKU-EDITED"
        assert stored_pair(link) == before

    def test_a_sku_edit_moves_no_price_through_the_kit_write_site(self, item, supplier):
        """CONTROL: the kit site's partial ``defaults``, with its pack size left alone.

        Passes on base as well: with ``quantity_per_package`` absent from
        ``defaults`` the stored pack size survives and the re-derived unit price
        rounds back unchanged. Base's defect was that the site ALWAYS supplied a
        pack size (``setdefault(..., 1)``), which is pinned separately. This holds
        the shape steady so the partial write itself stays harmless.
        """
        link = make_link(item, supplier)
        before = stored_pair(link)

        ItemSupplier.objects.update_or_create(
            item=item,
            supplier_id=supplier.pk,
            defaults={"supplier_sku": "KIT-EDITED", "is_primary": True},
        )

        assert stored_pair(link) == before


class TestInvariantTheDisagreementRuleIsStated:
    """Invariant 4. The forms permit an inconsistent pair, so which one governs is stated.

    Decided by the operator: ``package_cost`` governs. It is what the shop
    actually pays for a case, ``unit_cost`` is documented on the model as
    "auto-calculated from package cost", the Django admin already renders
    ``unit_cost`` read-only, ``views._process_cost_data`` already prefers the case
    price, and ScanTTY's API client documents the same precedence. It is also the
    only safe direction: ``package -> unit`` is the lossy half, so letting a
    rounded unit price reconstruct a case price is symptom 5 by another name.

    Measured against base: this ruling CONFIRMS the behaviour the model already
    had on all three branches below rather than changing any of them. Nothing
    asserted it, which is the whole reason it was reachable for a later round to
    undo. These are controls, and their job is to make that undoable-ness fail.
    """

    def test_a_create_with_a_contradictory_pair_stores_the_case_price(self, item, supplier):
        """CONTROL: 9.00/unit and 12.00/case at pack 3 cannot both be true. The case wins.

        Verified passing on base: the operator's ruling on which cost governs
        CONFIRMS what the model already did here rather than changing it. It is
        pinned because nothing asserted it — and an unasserted rule is exactly what
        the withdrawn attempt removed when a later round dropped a comparison an
        earlier round depended on.
        """
        link = ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku="SKU-BOTH",
            unit_cost=Decimal("9.00"),
            package_cost=Decimal("12.00"),
            quantity_per_package=LOSSY_PACK_SIZE,
        )

        link.refresh_from_db()
        assert link.package_cost == Decimal("12.00")
        assert link.unit_cost == Decimal("4.00")

    def test_an_update_moving_both_costs_apart_stores_the_case_price(
        self, item, supplier, authenticated_client
    ):
        """CONTROL: both boxes edited in one request, and they disagree.

        Passes on base too, for the same reason as the create case above: base's
        first branch already preferred ``package_cost`` whenever it was present.
        Pinned so the delta rule cannot quietly reverse it.
        """
        client, _ = authenticated_client
        link = make_link(item, supplier)

        response = client.patch(
            reverse("itemsupplier-detail", args=[link.pk]),
            {"unit_cost": "9.00", "package_cost": "12.00"},
            format="json",
        )

        assert response.status_code == 200
        link.refresh_from_db()
        assert link.package_cost == Decimal("12.00")
        assert link.unit_cost == Decimal("4.00")

    def test_a_pack_size_correction_holds_the_case_price(
        self, item, supplier, authenticated_client
    ):
        """CONTROL: "the case holds 5, not 3" is about packing, not about price.

        Holding the UNIT price instead would re-quote the case at 3.33 x 5 =
        16.65 — a price the supplier never gave — which is the same silent
        corruption in a new place.

        Passes on base as well. This is the branch the operator's ruling had to
        survive, and it does: the delta rule reaches the same answer by a
        different route, so a pack-size correction still holds the case price.
        """
        client, _ = authenticated_client
        link = make_link(item, supplier)

        response = client.patch(
            reverse("itemsupplier-detail", args=[link.pk]),
            {"quantity_per_package": 5},
            format="json",
        )

        assert response.status_code == 200
        link.refresh_from_db()
        assert link.package_cost == LOSSY_PACKAGE_COST
        assert link.unit_cost == Decimal("2.00")


class TestInvariantClearingAPriceIsObservable:
    """Invariant 4's other half, and the operator's condition on it.

    Clearing the authoritative cost clears both — that is how "I no longer know
    what this costs" is said, and it has to stay sayable. Clearing only the
    DERIVED cost cannot succeed, because the figure is derived; the condition is
    that the value coming back is visible, so the write response carries it.
    """

    def test_clearing_the_case_price_clears_both(self, item, supplier, authenticated_client):
        """AFTER: "no price on file" must stay reachable — it is not a free item."""
        client, _ = authenticated_client
        link = make_link(item, supplier)

        response = client.patch(
            reverse("itemsupplier-detail", args=[link.pk]),
            {"package_cost": None},
            format="json",
        )

        assert response.status_code == 200
        link.refresh_from_db()
        assert link.package_cost is None
        assert link.unit_cost is None

    def test_clearing_only_the_unit_price_re_derives_it_and_says_so_in_the_response(
        self, item, supplier, authenticated_client
    ):
        """CONTROL for the value, AFTER for the guarantee.

        Base re-derived the unit price here too, so the stored figures do not move.
        What was missing is any assertion that the RESPONSE carries the value that
        came back — the operator's condition on this ruling, and what the form
        re-seeds its box from. Pinned here so the guarantee cannot lapse silently.
        """
        client, _ = authenticated_client
        link = make_link(item, supplier)

        response = client.patch(
            reverse("itemsupplier-detail", args=[link.pk]),
            {"unit_cost": None},
            format="json",
        )

        assert response.status_code == 200
        link.refresh_from_db()
        assert link.package_cost == LOSSY_PACKAGE_COST
        assert link.unit_cost == LOSSY_UNIT_COST
        # The response body, not just the row: this is what the form re-seeds from.
        assert Decimal(response.data["unit_cost"]) == LOSSY_UNIT_COST
        assert Decimal(response.data["package_cost"]) == LOSSY_PACKAGE_COST

    def test_the_write_response_carries_the_re_derived_case_price(
        self, item, supplier, authenticated_client
    ):
        """AFTER: editing the unit box re-derives the case price, and the response
        shows the new one rather than the value the operator's form still held."""
        client, _ = authenticated_client
        link = make_link(item, supplier)

        response = client.patch(
            reverse("itemsupplier-detail", args=[link.pk]),
            {"unit_cost": "4.00"},
            format="json",
        )

        assert response.status_code == 200
        assert Decimal(response.data["unit_cost"]) == Decimal("4.00")
        assert Decimal(response.data["package_cost"]) == Decimal("12.00")
