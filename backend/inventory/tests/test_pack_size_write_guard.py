"""A pack size below 1 cannot be RECORDED through any supported write path.

``ItemSupplier.quantity_per_package`` carries ``MinValueValidator(1)``, and a
validator only bites under ``full_clean()`` — which ``Model.save()`` never
calls. So the question is not "does the field declare a bound" but **where can
an ``ItemSupplier`` be created or updated without the validators the model
declares**, and the answer had to be derived rather than recalled. Measured
against ``HEAD``, every path that writes the row:

======================================  ==========================  ===========
path                                    validators                  pack size
======================================  ==========================  ===========
``/inventory/item-suppliers/`` POST      DRF carries the model       refused
``/inventory/item-suppliers/`` PATCH     validator onto the field    refused
Django admin form / inline               ``ModelForm.full_clean()``  refused
``/inventory/items/`` POST               **none — hand-rolled        WAS STORED
                                         ``update_or_create``**
``/inventory/kits/`` POST + PATCH        **none — hand-rolled**      cannot be
                                                                     supplied
======================================  ==========================  ===========

Two of those five are hand-rolled writers that run no model validation at all,
which is why the endpoint named in the report was not assumed to be unique.
They differ in what they let a caller reach:

* ``InventoryItemViewSet._sync_primary_supplier`` reads
  ``quantity_per_package`` straight off ``request.data``, so a posted ``0``
  landed on disk. That is the one fixed here, and
  :class:`TestTheItemCreatePathRefusesAPackSizeBelowOne` is its named check.
  **No serializer could have caught it**: ``InventoryItem`` exposes
  ``quantity_per_package`` as a PROPERTY reading the primary link, so
  ``InventoryItemSerializer`` maps that key to a ``ReadOnlyField`` and never
  sees a submitted value. The raw read exists precisely because the validated
  field is not on that serializer, which is why the bound has to be run by the
  writer.
* ``KitSerializer._apply_supplier_terms`` writes through an untyped
  ``DictField`` with no validation either, but its ``defaults`` are filtered to
  a fixed key set that does NOT include the pack size, so no caller can reach
  the column through it. **Deliberate exclusion**, pinned below by
  :meth:`TestTheKitPathCannotReachTheColumnAtAll.test_kit_terms_cannot_set_a_pack_size`
  so the exclusion fails the build if that key set ever widens. Its other
  validation gaps (an unvalidated ``unit_cost``, the ``DictField`` 500s) are a
  separate defect, already filed in
  ``docs/oms-supplier-cost-write-path-record.md`` under "Still open, filed not
  fixed", and are not touched here.

Paths that save an EXISTING row without re-supplying a pack size —
``ItemSupplierViewSet.mark_discontinued``,
``reorder_queue.services.purchase_orders.void_line_item``,
``inventory.tasks.update_lead_times`` — are excluded on purpose: they take no
pack size from a caller, so there is nothing for a validator to catch, and
guarding them would instead make the rows already on disk unsavable.

**Rows already on disk are not touched.** Making the state uncreatable is not
the same as rewriting the rows that predate the guard; that decision is the
captain's and this branch does not take it. The links are left exactly as
recorded, still readable as ``pack_size.PACK_SIZE_RECORDED_ZERO``, still
savable, and the ``derive_costs`` hold that keeps an ordinary save from
destroying their price stays where it is.
"""

from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.urls import reverse

import pytest

from inventory.admin import ItemSupplierAdmin
from inventory.models import InventoryItem, ItemSupplier
from inventory.services.pack_size import PACK_SIZE_RECORDED_ZERO, pack_size_of
from inventory.tests.factories import (
    InventoryItemFactory,
    ItemSupplierFactory,
    SupplierFactory,
)

pytestmark = pytest.mark.django_db

#: The refusal every path owes, as the field itself words it. Named once so a
#: test cannot quietly accept a DIFFERENT sentence for the same refusal.
REFUSAL = "Ensure this value is greater than or equal to 1."


@pytest.fixture
def supplier():
    return SupplierFactory()


def post_item(client, supplier, **extra):
    """POST the real item-create endpoint with a primary supplier attached.

    ``description`` and ``reorder_quantity`` are required by
    ``InventoryItemSerializer``; everything else here is the supplier block
    ``_sync_primary_supplier`` reads off the raw request.
    """
    payload = {
        "name": "Bolt, M3x10",
        "sku": "BOLT-M3X10",
        "description": "Hex head",
        "reorder_quantity": 1,
        "supplier": supplier.pk,
        "unit_cost": "2.50",
    }
    payload.update(extra)
    return client.post(reverse("inventoryitem-list"), payload, format="json")


class TestTheItemCreatePathRefusesAPackSizeBelowOne:
    """The hand-rolled create path now runs the bound the field declares.

    Pack 0 is not merely untidy: it is the shape where cost derivation cannot
    run at all — ``derive_costs`` short-circuits above its divide-by-zero guard
    — so a link created this way carried a unit price with no case price and no
    way to derive one. The hold that stops an ordinary save destroying that
    price is correct and stays; this stops the row being created.
    """

    def test_a_posted_pack_size_of_zero_is_refused(self, authenticated_client, supplier):
        client, _ = authenticated_client

        response = post_item(client, supplier, quantity_per_package=0)

        assert response.status_code == 400, response.data
        assert response.data["error"]["details"]["quantity_per_package"] == [REFUSAL]

    def test_a_refused_pack_size_writes_no_supplier_link(self, authenticated_client, supplier):
        client, _ = authenticated_client

        post_item(client, supplier, quantity_per_package=0)

        assert not ItemSupplier.objects.exists()

    def test_a_refused_pack_size_leaves_no_half_made_item_behind(
        self, authenticated_client, supplier
    ):
        """The whole request rolls back, not just the link.

        ``create()`` saves the item and syncs the supplier inside ONE
        ``transaction.atomic()``. Were that not so, a refused pack size would
        answer 400 and still leave a supplier-less item on the catalog, which is
        a worse outcome than the row being refused.
        """
        client, _ = authenticated_client

        post_item(client, supplier, quantity_per_package=0)

        assert not InventoryItem.objects.filter(sku="BOLT-M3X10").exists()

    def test_a_negative_pack_size_is_refused_too(self, authenticated_client, supplier):
        """Not a zero check. The bound is the field's, so everything under it goes."""
        client, _ = authenticated_client

        response = post_item(client, supplier, quantity_per_package=-3)

        assert response.status_code == 400, response.data
        assert response.data["error"]["details"]["quantity_per_package"] == [REFUSAL]

    def test_a_string_zero_is_refused_as_the_number_it_parses_to(
        self, authenticated_client, supplier
    ):
        """Form-encoded and JSON callers send the same pack size differently."""
        client, _ = authenticated_client

        response = post_item(client, supplier, quantity_per_package="0")

        assert response.status_code == 400, response.data
        assert response.data["error"]["details"]["quantity_per_package"] == [REFUSAL]


class TestTheRefusalIsTheONEThatAlreadyExisted:
    """Same envelope, same field key, same sentence as the validated endpoint.

    A second spelling of one refusal is its own defect: ``ScanTTY`` decodes the
    standard envelope through ``parseError``, and a new shape or a new sentence
    for the same rejection would be a wire change it has to follow. There is
    none — the guard runs the FIELD's validators and raises Django's own
    ``ValidationError`` keyed on the column, which
    ``config.api_errors.standardized_exception_handler`` already translates.
    """

    def test_the_item_path_and_the_item_supplier_path_refuse_identically(
        self, authenticated_client, supplier
    ):
        client, _ = authenticated_client
        item = InventoryItemFactory(image=None)

        through_item_create = post_item(client, supplier, quantity_per_package=0)
        through_the_serializer = client.post(
            reverse("itemsupplier-list"),
            {
                "item": str(item.pk),
                "supplier": supplier.pk,
                "supplier_sku": "SKU-1",
                "unit_cost": "2.50",
                "quantity_per_package": 0,
            },
            format="json",
        )

        assert through_the_serializer.status_code == through_item_create.status_code == 400
        assert through_item_create.data == through_the_serializer.data

    def test_the_admin_form_refuses_it_as_it_always_has(self, supplier):
        """The third member of the derived set, unchanged — a ``ModelForm`` cleans."""
        form_class = ItemSupplierAdmin(ItemSupplier, AdminSite()).get_form(None)
        form = form_class(
            data={
                "item": str(InventoryItemFactory(image=None).pk),
                "supplier": str(supplier.pk),
                "supplier_sku": "SKU-1",
                "quantity_per_package": "0",
                "average_lead_time": "7",
            }
        )

        assert not form.is_valid()
        assert form.errors["quantity_per_package"] == [REFUSAL]


class TestWhatTheGuardDoesNotChange:
    """The create path's other readings of the key are untouched.

    The guard validates the pack size a request RECORDS. It is not a new rule
    about which keys a caller must send, and the two existing omissions — absent
    and malformed — keep their documented behaviour, because turning either into
    a refusal is a different question from the one the bound answers.
    """

    def test_a_usable_pack_size_is_still_recorded(self, authenticated_client, supplier):
        client, _ = authenticated_client

        response = post_item(client, supplier, quantity_per_package=12)

        assert response.status_code == 201, response.data
        link = ItemSupplier.objects.get()
        assert link.quantity_per_package == 12
        assert link.unit_cost == Decimal("2.50")
        assert link.package_cost == Decimal("30.00")

    def test_a_request_that_names_no_pack_size_still_takes_the_column_default(
        self, authenticated_client, supplier
    ):
        client, _ = authenticated_client

        response = post_item(client, supplier)

        assert response.status_code == 201, response.data
        assert ItemSupplier.objects.get().quantity_per_package == 1

    def test_an_unparseable_pack_size_is_still_omitted_rather_than_refused(
        self, authenticated_client, supplier
    ):
        """Deliberately unchanged, and deliberately NOT a refusal.

        ``_process_pack_size`` omits a pack size it cannot read, on the same
        terms the two costs keep through ``_safe_decimal_conversion``. Whether
        malformed input should be refused rather than dropped is a separate
        question about this path's whole contract, not about the bound the field
        declares, and answering it here would have widened the change silently.
        """
        client, _ = authenticated_client

        response = post_item(client, supplier, quantity_per_package="not a number")

        assert response.status_code == 201, response.data
        assert ItemSupplier.objects.get().quantity_per_package == 1


class TestTheKitPathCannotReachTheColumnAtAll:
    """The OTHER hand-rolled writer, excluded because the column is out of reach.

    ``_apply_supplier_terms`` builds its ``defaults`` from a fixed key set —
    ``supplier_sku``, ``supplier_url``, ``unit_cost``, ``average_lead_time`` —
    so a ``quantity_per_package`` in the terms dict is dropped before the write.
    This pins that: if the key set ever widens to include the pack size, this
    fails and the guard has to be extended to that writer too.
    """

    def test_kit_terms_cannot_set_a_pack_size(self, authenticated_client, supplier):
        client, _ = authenticated_client
        component = InventoryItemFactory(image=None, is_kit=False, is_serialized=False)

        response = client.post(
            reverse("kit-list"),
            {
                "name": "Ink Kit",
                "sku": "KIT-INK",
                "description": "Four cartridges",
                "reorder_quantity": 1,
                "components": [{"component": component.pk, "quantity": 1}],
                "supplier_terms": {
                    "supplier": supplier.pk,
                    "supplier_sku": "KIT-SKU",
                    "unit_cost": "3.00",
                    "quantity_per_package": 0,
                },
            },
            format="json",
        )

        assert response.status_code == 201, response.data
        link = ItemSupplier.objects.get(item_id=response.data["id"])
        assert link.quantity_per_package == 1


class TestRowsAlreadyOnDiskAreLeftAlone:
    """Uncreatable is not the same as rewritten, and this branch only does the first.

    What happens to the ``quantity_per_package`` 0 rows already recorded is the
    captain's decision, so no data migration and no ``CheckConstraint`` is added
    here — a constraint would have to rewrite or reject them to be installed at
    all. They stay readable, stay savable, and keep the ``derive_costs`` hold
    that stops an ordinary save destroying their price.
    """

    def test_a_recorded_zero_still_reads_as_recorded_zero(self):
        link = ItemSupplierFactory(quantity_per_package=0)

        assert pack_size_of(link).state == PACK_SIZE_RECORDED_ZERO
        assert pack_size_of(link).units is None

    def test_a_recorded_zero_row_can_still_be_marked_discontinued(self, authenticated_client):
        """A guard on the write path, not on the row.

        ``mark_discontinued`` re-supplies no pack size, so retiring a legacy
        link is not blocked by the bound its stored value fails. Blocking it
        would strand exactly the rows an operator is most likely to be cleaning
        up.
        """
        client, _ = authenticated_client
        link = ItemSupplierFactory(quantity_per_package=0, unit_cost=Decimal("1.00"))

        response = client.post(reverse("itemsupplier-mark-discontinued", args=[link.pk]))

        link.refresh_from_db()
        assert response.status_code == 200, response.data
        assert link.is_discontinued is True
        assert link.quantity_per_package == 0

    def test_correcting_a_recorded_zero_through_the_validated_endpoint_works(
        self, authenticated_client
    ):
        """The remedy ``pack_size`` prescribes, and ``ScanPage`` prints, is reachable."""
        client, _ = authenticated_client
        link = ItemSupplierFactory(quantity_per_package=0, unit_cost=Decimal("1.00"))

        response = client.patch(
            reverse("itemsupplier-detail", args=[link.pk]),
            {"quantity_per_package": 6},
            format="json",
        )

        link.refresh_from_db()
        assert response.status_code == 200, response.data
        assert link.quantity_per_package == 6
