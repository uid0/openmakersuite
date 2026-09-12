"""What a kit EDIT stores, and what it must leave alone (op-kit-terms-write).

``supplier_terms`` is the only supplier surface a kit save has, and it is
PARTIAL by construction: :class:`~inventory.serializers.KitSupplierTermsSerializer`
declares every field optional with no defaults, so a form that edits a part
number sends a part number. The question this file answers is what the write
path does with the columns the caller did NOT name.

THE DEFECT, measured on the endpoint rather than recalled. ``is_primary=True``
was a create-time convenience sitting in the ``update_or_create`` ``defaults``
dict, and Django applies ``defaults`` to the row it FINDS as well as the one it
makes. So::

    PATCH /api/inventory/kits/<id>/
    {"supplier_terms": {"supplier": 7, "supplier_sku": "T3200-B"}}

promoted supplier 7's link to primary and demoted whichever sibling the
operator had flagged — on a payload that named a part number and nothing else,
with a 200 and no mention of it. Which supplier an item is bought from by
default is an operator decision made through ``/api/inventory/item-suppliers/``;
nothing in a terms block asks to change it.

WHY THE FIXTURE IS SHAPED LIKE THIS, and it is the load-bearing part of the
file. The control this replaces
(``test_kit_supplier_terms_input.py::test_an_omitted_lead_time_does_not_overwrite_the_stored_one``)
edits a kit whose link was built by the kit-create endpoint itself, so it holds
``quantity_per_package=1``, ``package_cost=None``, ``is_primary=True`` and no
sibling. Against that row every one of the assertions below is VACUOUS: a pack
size cannot be reset from 1 to 1, a null price cannot be recomputed wrong, a
sole primary cannot be demoted and a row with no price files no ``PriceHistory``.
The fixture here therefore gives the link a pack size of 25, a real case price,
a ``False`` primary flag and a RIVAL link that holds the primary flag — every
value the defect moves is a value that is demonstrably there to move.

WHAT IS NOT TESTED HERE, deliberately. ``quantity_per_package`` and
``package_cost`` are not writable through this block at all — they are refused
by name (``inventory/tests/test_pack_size_write_guard.py`` and
``test_kit_supplier_terms_input.py`` pin that), so a pack-size EDIT is not a
case this endpoint has. What a pack size already on the row does to a price
edit is tested, because that is reachable: the case price re-derives at 25 and
not at 1. The derivation rule itself belongs to
``test_supplier_cost_derivation.py``.
"""

from decimal import Decimal

from django.urls import reverse

import pytest

from inventory.models import InventoryItem, ItemSupplier, PriceHistory
from inventory.tests.factories import InventoryItemFactory, SupplierFactory

pytestmark = pytest.mark.django_db

KIT_SKU = "KIT-TERMS-EDIT"

#: The stored pack size. Not 1, so a reset to the column default is visible.
PACK_SIZE = 25
#: The stored pair, coherent at ``PACK_SIZE``: 89.99 * 25 == 2249.75.
STORED_UNIT = Decimal("89.99")
STORED_PACKAGE = Decimal("2249.75")


@pytest.fixture
def component():
    return InventoryItemFactory(image=None, is_kit=False, is_serialized=False)


@pytest.fixture
def kit(authenticated_client, component):
    """A kit whose terms are worth overwriting, created through the endpoint."""
    client, _ = authenticated_client
    response = client.post(
        reverse("kit-list"),
        {
            "name": "Ink Kit",
            "sku": KIT_SKU,
            "description": "Four cartridges",
            "reorder_quantity": 1,
            "components": [{"component": component.pk, "quantity": 1}],
            "supplier_terms": {
                "supplier": SupplierFactory(name="Northline Plastics").pk,
                "supplier_sku": "T3200",
                "unit_cost": str(STORED_UNIT),
                "average_lead_time": 21,
            },
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    return InventoryItem.objects.get(pk=response.data["id"])


@pytest.fixture
def terms_link(kit):
    """The kit's link, given the case price and pack size a real one carries.

    Written with a queryset ``update`` rather than through the endpoint because
    neither column is reachable from the kit terms — that is the point of
    :class:`~inventory.serializers.KitSupplierTermsSerializer` — and because a
    ``save()`` here would file the ``PriceHistory`` row the tests below count.
    The flag is cleared so the sibling can hold it.
    """
    link = ItemSupplier.objects.get(item=kit)
    ItemSupplier.objects.filter(pk=link.pk).update(
        quantity_per_package=PACK_SIZE,
        unit_cost=STORED_UNIT,
        package_cost=STORED_PACKAGE,
        is_primary=False,
    )
    link.refresh_from_db()
    return link


@pytest.fixture
def sibling(kit, terms_link):
    """The operator's flagged primary — a DIFFERENT supplier on the same kit."""
    return ItemSupplier.objects.create(
        item=kit,
        supplier=SupplierFactory(name="Gamma Wholesale"),
        supplier_sku="G-1",
        unit_cost=Decimal("95.00"),
        quantity_per_package=PACK_SIZE,
        is_primary=True,
    )


@pytest.fixture
def no_history(terms_link, sibling):
    """Clear the rows the fixture's own writes filed, so counts mean the edit."""
    PriceHistory.objects.all().delete()


def patch_terms(client, kit, **terms):
    """PATCH the real kit endpoint with ``terms`` as its supplier block."""
    return client.patch(
        reverse("kit-detail", args=[kit.pk]),
        {"supplier_terms": terms},
        format="json",
    )


class TestTheFixtureIsWorthTesting:
    """The evidence check, run first: every value the edits must not move is
    actually present and actually different from what the defect writes.

    Without this class the four tests below can all pass against a row that
    never had a pack size other than 1, never had a case price, and had no
    sibling to demote — which is exactly how the previous control certified a
    write path that was rewriting three columns.
    """

    def test_the_stored_row_carries_what_the_defect_would_overwrite(self, terms_link, sibling):
        assert terms_link.quantity_per_package == PACK_SIZE != 1
        assert terms_link.package_cost == STORED_PACKAGE
        assert terms_link.unit_cost == STORED_UNIT
        assert terms_link.is_primary is False
        assert sibling.is_primary is True
        assert sibling.supplier_id != terms_link.supplier_id


class TestAnEditTouchesOnlyWhatTheCallerSent:
    """The first half of the remedy: an unsent key leaves its column alone."""

    def test_a_sku_only_edit_stores_the_sku(
        self, authenticated_client, kit, terms_link, no_history
    ):
        client, _ = authenticated_client

        response = patch_terms(client, kit, supplier=terms_link.supplier_id, supplier_sku="T3200-B")

        assert response.status_code == 200, response.data
        terms_link.refresh_from_db()
        assert terms_link.supplier_sku == "T3200-B"

    def test_a_sku_only_edit_leaves_the_pack_size_and_both_costs_alone(
        self, authenticated_client, kit, terms_link, no_history
    ):
        """The whole terms row, byte-for-byte, apart from the part number.

        Asserted as one tuple rather than field by field so a future key that
        starts being defaulted on update fails this rather than slipping past
        three assertions that each happen to name a different column.
        """
        client, _ = authenticated_client

        response = patch_terms(client, kit, supplier=terms_link.supplier_id, supplier_sku="T3200-B")

        assert response.status_code == 200, response.data
        terms_link.refresh_from_db()
        assert (
            terms_link.quantity_per_package,
            terms_link.unit_cost,
            terms_link.package_cost,
            terms_link.average_lead_time,
        ) == (PACK_SIZE, STORED_UNIT, STORED_PACKAGE, 21)

    def test_a_sku_only_edit_does_not_promote_the_link_or_demote_the_sibling(
        self, authenticated_client, kit, terms_link, sibling, no_history
    ):
        """THE DEFECT. Which supplier a kit is bought from is not a part number.

        Both halves are asserted because the promotion and the demotion are one
        act with two victims: ``enforce_single_primary`` clears the flag on
        every OTHER link of the item, so a terms block that quietly promotes
        also quietly un-flags the supplier the operator chose.
        """
        client, _ = authenticated_client

        response = patch_terms(client, kit, supplier=terms_link.supplier_id, supplier_sku="T3200-B")

        assert response.status_code == 200, response.data
        terms_link.refresh_from_db()
        sibling.refresh_from_db()
        assert terms_link.is_primary is False
        assert sibling.is_primary is True

    def test_a_sku_only_edit_files_no_price_history(
        self, authenticated_client, kit, terms_link, no_history
    ):
        """No price moved, so the captain's price record must show nothing.

        A row here would be a fabricated price change — the reset pack size
        used to re-derive the case price and file one — and it is read as
        history, so it outlives the edit that invented it.
        """
        client, _ = authenticated_client

        response = patch_terms(client, kit, supplier=terms_link.supplier_id, supplier_sku="T3200-B")

        assert response.status_code == 200, response.data
        assert not PriceHistory.objects.filter(item_supplier=terms_link).exists()

    def test_a_lead_time_only_edit_leaves_the_primary_flag_and_the_prices_alone(
        self, authenticated_client, kit, terms_link, sibling, no_history
    ):
        """The same question on the other optional field, because the defect was
        in the write path and not in any one key."""
        client, _ = authenticated_client

        response = patch_terms(client, kit, supplier=terms_link.supplier_id, average_lead_time=30)

        assert response.status_code == 200, response.data
        terms_link.refresh_from_db()
        sibling.refresh_from_db()
        assert terms_link.average_lead_time == 30
        assert (terms_link.unit_cost, terms_link.package_cost) == (STORED_UNIT, STORED_PACKAGE)
        assert (terms_link.is_primary, sibling.is_primary) == (False, True)
        assert not PriceHistory.objects.filter(item_supplier=terms_link).exists()


class TestAnEditActuallyStoresWhatTheCallerSent:
    """The second half, and the trap: leaving unsent keys alone must not become
    dropping the sent one.

    ``update_or_create`` saves the row it found with ``update_fields``
    restricted to its ``defaults`` keys, and ``package_cost`` can never be one
    of them because the kit terms do not offer that column. A price edit whose
    derived twin is dropped returns 200 and stores an INCOHERENT pair — a new
    unit cost beside the old case price — which is worse than the promotion it
    would have been fixing. The write path therefore saves the whole row.
    """

    def test_a_price_edit_persists(self, authenticated_client, kit, terms_link, no_history):
        client, _ = authenticated_client

        response = patch_terms(client, kit, supplier=terms_link.supplier_id, unit_cost="99.99")

        assert response.status_code == 200, response.data
        terms_link.refresh_from_db()
        assert terms_link.unit_cost == Decimal("99.99")

    def test_a_price_edit_re_derives_the_case_price_at_the_stored_pack_size(
        self, authenticated_client, kit, terms_link, no_history
    ):
        """THE TRAP, stated as an arithmetic identity the wrong answers fail.

        2499.75 is 99.99 * 25. A dropped derivation leaves 2249.75 (the stored
        case price, now contradicting the unit price beside it); a pack size
        reset to 1 leaves 99.99. Only a coherent full save gives this number,
        so neither defect can pass this assertion.
        """
        client, _ = authenticated_client

        response = patch_terms(client, kit, supplier=terms_link.supplier_id, unit_cost="99.99")

        assert response.status_code == 200, response.data
        terms_link.refresh_from_db()
        assert terms_link.package_cost == Decimal("99.99") * PACK_SIZE == Decimal("2499.75")
        assert terms_link.quantity_per_package == PACK_SIZE

    def test_a_price_edit_files_the_price_history_row_the_captain_reads(
        self, authenticated_client, kit, terms_link, no_history
    ):
        """A real price change SHOULD be recorded — with the pair as stored.

        Pinned beside the no-spurious-row tests so "files nothing" is not read
        as the rule: what makes a row right is that a price moved, and that the
        figures in it are the ones on the link.
        """
        client, _ = authenticated_client

        response = patch_terms(client, kit, supplier=terms_link.supplier_id, unit_cost="99.99")

        assert response.status_code == 200, response.data
        entry = PriceHistory.objects.get(item_supplier=terms_link)
        assert (entry.unit_cost, entry.package_cost, entry.quantity_per_package) == (
            Decimal("99.99"),
            Decimal("2499.75"),
            PACK_SIZE,
        )

    def test_a_price_edit_still_does_not_move_the_primary_flag(
        self, authenticated_client, kit, terms_link, sibling, no_history
    ):
        """Both halves of the remedy on ONE request: the sent value lands and
        the unsent flag does not move."""
        client, _ = authenticated_client

        response = patch_terms(client, kit, supplier=terms_link.supplier_id, unit_cost="99.99")

        assert response.status_code == 200, response.data
        terms_link.refresh_from_db()
        sibling.refresh_from_db()
        assert terms_link.unit_cost == Decimal("99.99")
        assert (terms_link.is_primary, sibling.is_primary) == (False, True)


class TestACreateStillGetsItsConveniences:
    """``is_primary=True`` moved to the create branch, not out of the code.

    A kit defined in one request has to come out buyable, and the purchasing
    surfaces read a primary link; withholding the flag on create would trade
    this defect for a kit nothing can order.
    """

    def test_a_terms_block_naming_a_new_supplier_creates_it_as_primary(
        self, authenticated_client, kit, terms_link, sibling, no_history
    ):
        """Reached through an EDIT, which is where a create branch is easiest to
        break: the kit exists, but this supplier's link does not."""
        client, _ = authenticated_client
        newcomer = SupplierFactory(name="Beta Parts Co")

        response = patch_terms(client, kit, supplier=newcomer.pk, supplier_sku="B-9")

        assert response.status_code == 200, response.data
        created = ItemSupplier.objects.get(item=kit, supplier=newcomer)
        sibling.refresh_from_db()
        assert (created.supplier_sku, created.is_primary) == ("B-9", True)
        # The create branch's promotion demotes siblings, as every other
        # ``is_primary=True`` writer in this codebase does.
        assert sibling.is_primary is False

    def test_a_kit_created_with_terms_still_gets_a_primary_link(
        self, authenticated_client, component
    ):
        client, _ = authenticated_client
        supplier = SupplierFactory(name="Acme Fasteners")

        response = client.post(
            reverse("kit-list"),
            {
                "name": "Bolt Kit",
                "sku": "KIT-TERMS-CREATE",
                "description": "Bolts",
                "reorder_quantity": 1,
                "components": [{"component": component.pk, "quantity": 1}],
                "supplier_terms": {"supplier": supplier.pk, "supplier_sku": "A-1"},
            },
            format="json",
        )

        assert response.status_code == 201, response.data
        assert ItemSupplier.objects.get(item_id=response.data["id"]).is_primary is True
