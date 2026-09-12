"""What a kit-create request does with supplier terms it cannot use (op-kit-terms).

The set was DERIVED rather than recalled, by posting the real ``/api/inventory/
kits/`` endpoint with each way a ``supplier_terms`` value can be wrong and
recording what came back. ``supplier_terms`` was a pass-through
``serializers.DictField``, so every key went to the ORM exactly as sent, and the
answer split three ways:

======================================  =========================  =============
supplier_terms value                    base behaviour             now
======================================  =========================  =============
``supplier`` not a number               **500** ``ValueError``      400
``supplier`` names no row               **500** at COMMIT [#]_      400
``average_lead_time`` not a number      **500** ``ValueError``      400
``average_lead_time`` negative          **500** ``IntegrityError``  400
``unit_cost`` past ``max_digits``       **500** ``DataError``       400
``supplier_sku`` past ``max_length``    **500** ``DataError``       400
``supplier_url`` not a URL              201, garbage stored         400
``average_lead_time`` of ``2.5``        201, stored as ``2``        400
``package_cost``                        201, **silently dropped**   400
``quantity_per_package``                201, **silently dropped**   400
any other ``ItemSupplier`` column       201, **silently dropped**   400
======================================  =========================  =============

.. [#] Django declares foreign keys ``DEFERRABLE INITIALLY DEFERRED``, so a
   supplier id naming no row is not caught by the ``INSERT``. It surfaces as an
   ``IntegrityError`` when the transaction commits — invisible to an ordinary
   test, which rolls back, which is why
   :meth:`TestARefusalLeavesNoRowsBehind.test_a_supplier_id_naming_no_row_is_refused_before_commit`
   runs with ``transaction=True``. The project sets no ``ATOMIC_REQUESTS``, so on
   base the kit row was already committed by then: a 500 AND a half-written kit.

**One cause, one fix.** Every row above is the same defect — an untyped dict
handed straight to ``update_or_create`` — so the fix is to stop passing it
through: :class:`~inventory.serializers.KitSupplierTermsSerializer` validates the
block against the ``ItemSupplier`` columns it writes. The refusals are therefore
the ones ``/api/inventory/item-suppliers/`` already gives, in the same words,
through the ``config.api_errors`` envelope every endpoint already returns. No
client learns a new refusal SHAPE; what changes is that these inputs now produce
one at all.

**Deliberate exclusions**, measured and left alone:

* ``unit_cost`` of ``"-5.00"`` is still accepted. The column declares no lower
  bound, so ``/item-suppliers/`` and the admin take it too. Refusing it here
  would be a rule this path invented, which is the opposite of what the fix
  does. Whether a negative price should be storable at all is a question about
  the field, not about this endpoint.
* ``supplier_sku`` of ``""`` is still accepted, though the model field is
  non-blank and ``/item-suppliers/`` refuses it. The kit form has always allowed
  an empty part number and this change is about 500s and silent drops, not about
  newly refusing saves that work today.
* Everything the kit write does with PRICE once the terms are valid —
  ``derive_costs``, the unit/case pair, ``PriceHistory`` — is untouched and
  covered by ``test_supplier_cost_derivation.py`` and ``test_price_guards.py``.
* The kit form's habit of writing one vendor's terms onto another
  (``docs/oms-supplier-cost-write-path-record.md``) is a frontend defect about
  WHICH supplier is named, not about whether a value is usable. Nothing here
  can see it: every payload below names one supplier deliberately.
"""

from decimal import Decimal

from django.urls import reverse

import pytest

from inventory.models import InventoryItem, ItemSupplier
from inventory.serializers import UNSUPPORTED_TERM
from inventory.tests.factories import InventoryItemFactory, SupplierFactory

pytestmark = pytest.mark.django_db

KIT_SKU = "KIT-TERMS"


@pytest.fixture
def supplier():
    return SupplierFactory()


@pytest.fixture
def component():
    return InventoryItemFactory(image=None, is_kit=False, is_serialized=False)


def post_kit(client, component, **terms):
    """POST the real kit-create endpoint with ``terms`` as its supplier block."""
    return client.post(
        reverse("kit-list"),
        {
            "name": "Ink Kit",
            "sku": KIT_SKU,
            "description": "Four cartridges",
            "reorder_quantity": 1,
            "components": [{"component": component.pk, "quantity": 1}],
            "supplier_terms": terms,
        },
        format="json",
    )


def refusal_for(response, field):
    """The messages the standard envelope carries for one key of the terms block.

    Reads through ``error.details`` rather than off the top level, because the
    contract this must keep is ``config.api_errors``' — a ``400`` whose body is
    ``{"error": {"code", "message", "details"}}`` — and a test that read the
    detail some other way would pass against a response that had quietly
    stopped using the shared envelope.
    """
    assert response.status_code == 400, response.data
    body = response.data["error"]
    assert body["code"] == "validation_failed"
    assert body["message"] == "One or more fields failed validation."
    return body["details"]["supplier_terms"][field]


class TestAnUnusableValueIsRefusedNotCrashed:
    """The six inputs that returned a 500, each with the sentence it now gets.

    Each message is asserted in full rather than by status alone: "a readable
    refusal" is the point, and a 400 carrying an empty or generic body would
    satisfy a status-only check while telling the operator nothing. They are the
    ``ItemSupplier`` fields' own words, so a message that changes here has
    changed for ``/item-suppliers/`` too.
    """

    def test_a_non_numeric_supplier_id_is_refused(self, authenticated_client, component):
        client, _ = authenticated_client

        response = post_kit(client, component, supplier="not-a-number", supplier_sku="S")

        assert refusal_for(response, "supplier") == [
            "Incorrect type. Expected pk value, received str."
        ]

    def test_a_malformed_lead_time_is_refused(self, authenticated_client, supplier, component):
        client, _ = authenticated_client

        response = post_kit(
            client, component, supplier=supplier.pk, supplier_sku="S", average_lead_time="soon"
        )

        assert refusal_for(response, "average_lead_time") == ["A valid integer is required."]

    def test_a_negative_lead_time_is_refused(self, authenticated_client, supplier, component):
        """Base reached the DB's own CHECK constraint, which is a 500, not a 400."""
        client, _ = authenticated_client

        response = post_kit(
            client, component, supplier=supplier.pk, supplier_sku="S", average_lead_time=-3
        )

        assert refusal_for(response, "average_lead_time") == [
            "Ensure this value is greater than or equal to 0."
        ]

    def test_a_fractional_lead_time_is_refused_rather_than_truncated(
        self, authenticated_client, supplier, component
    ):
        """Not a 500 on base — a SILENT one: ``2.5`` days was stored as ``2``.

        The same class as the dropped keys below. The operator was told their
        lead time was accepted; a different one was recorded.
        """
        client, _ = authenticated_client

        response = post_kit(
            client, component, supplier=supplier.pk, supplier_sku="S", average_lead_time=2.5
        )

        assert refusal_for(response, "average_lead_time") == ["A valid integer is required."]
        assert not ItemSupplier.objects.filter(item__sku=KIT_SKU).exists()

    def test_a_cost_overflowing_max_digits_is_refused(
        self, authenticated_client, supplier, component
    ):
        client, _ = authenticated_client

        response = post_kit(
            client,
            component,
            supplier=supplier.pk,
            supplier_sku="S",
            unit_cost="123456789012.00",
        )

        assert refusal_for(response, "unit_cost") == [
            "Ensure that there are no more than 10 digits in total."
        ]

    def test_a_supplier_sku_past_max_length_is_refused(
        self, authenticated_client, supplier, component
    ):
        client, _ = authenticated_client

        response = post_kit(client, component, supplier=supplier.pk, supplier_sku="X" * 200)

        assert refusal_for(response, "supplier_sku") == [
            "Ensure this field has no more than 100 characters."
        ]

    def test_a_malformed_supplier_url_is_refused(self, authenticated_client, supplier, component):
        """Base stored it verbatim and rendered it as a "View on supplier" link."""
        client, _ = authenticated_client

        response = post_kit(
            client, component, supplier=supplier.pk, supplier_sku="S", supplier_url="not a url"
        )

        assert refusal_for(response, "supplier_url") == ["Enter a valid URL."]

    def test_terms_that_are_not_an_object_are_refused(self, authenticated_client, component):
        """The one input base already refused — but the SENTENCE changed.

        ``DictField`` said ``Expected a dictionary of items but got type
        "list".``; a nested serializer says ``Invalid data. Expected a
        dictionary, but got list.`` and files it under ``non_field_errors``
        rather than directly under ``supplier_terms``. Same status, same
        envelope, same ``error.code`` — DRF's own wording for the field type
        that replaced the old one. Pinned because it is the only refusal on this
        endpoint whose wire text this change moves, and a client that matched on
        the text needs it written down rather than discovered.
        """
        client, _ = authenticated_client

        response = client.post(
            reverse("kit-list"),
            {
                "name": "Ink Kit",
                "sku": KIT_SKU,
                "description": "d",
                "reorder_quantity": 1,
                "components": [{"component": component.pk, "quantity": 1}],
                "supplier_terms": ["not", "a", "dict"],
            },
            format="json",
        )

        assert refusal_for(response, "non_field_errors") == [
            "Invalid data. Expected a dictionary, but got list."
        ]


class TestASuppliedFieldIsNeverSilentlyDROPPED:
    """The other half: keys that reached the endpoint and were discarded at 201.

    ``defaults`` was filtered to a fixed key set, so anything else vanished and
    the caller was told the write succeeded. The filter is gone; the block now
    NAMES what it will not write. Which of the two available fixes this is, and
    why, is argued in the PR — in short, accepting ``quantity_per_package`` here
    would reopen the hole PR #1061 closed on a second path, and accepting
    ``package_cost`` would put a new input into price derivation.
    """

    def test_a_package_cost_is_refused_by_name(self, authenticated_client, supplier, component):
        client, _ = authenticated_client

        response = post_kit(
            client, component, supplier=supplier.pk, supplier_sku="S", package_cost="12.00"
        )

        assert refusal_for(response, "package_cost") == [UNSUPPORTED_TERM]
        assert not ItemSupplier.objects.filter(item__sku=KIT_SKU).exists()

    def test_a_pack_size_is_refused_by_name(self, authenticated_client, supplier, component):
        client, _ = authenticated_client

        response = post_kit(
            client, component, supplier=supplier.pk, supplier_sku="S", quantity_per_package=4
        )

        assert refusal_for(response, "quantity_per_package") == [UNSUPPORTED_TERM]
        assert not ItemSupplier.objects.filter(item__sku=KIT_SKU).exists()

    def test_the_refusal_points_at_the_endpoint_that_does_accept_the_field(self):
        """A refusal is only actionable if it says where the field DOES go.

        ``package_cost``, ``quantity_per_package``, the dimensions and the notes
        are all real columns; ``/api/inventory/item-suppliers/`` writes and
        validates every one of them. Pinned as a string so a reword that drops
        the destination fails rather than quietly leaving an operator stuck.
        """
        assert "/api/inventory/item-suppliers/" in UNSUPPORTED_TERM

    def test_any_other_supplier_column_is_refused_the_same_way(
        self, authenticated_client, supplier, component
    ):
        """Not a hand-listed denylist: anything outside the block's field set.

        ``notes`` and ``is_active`` are neither of the two keys the report
        named, and were dropped by the same filter. One mechanism closes all of
        them, which is why this is worth an assertion of its own.
        """
        client, _ = authenticated_client

        response = post_kit(
            client,
            component,
            supplier=supplier.pk,
            supplier_sku="S",
            notes="ships slowly",
            is_active=False,
        )

        assert response.status_code == 400, response.data
        details = response.data["error"]["details"]["supplier_terms"]
        assert details == {"is_active": [UNSUPPORTED_TERM], "notes": [UNSUPPORTED_TERM]}

    def test_a_stray_key_and_a_bad_value_are_reported_together(
        self, authenticated_client, supplier, component
    ):
        """One payload, one refusal — not "fix this, now fix that"."""
        client, _ = authenticated_client

        response = post_kit(
            client,
            component,
            supplier=supplier.pk,
            supplier_sku="S",
            package_cost="12.00",
            unit_cost="free",
        )

        assert response.status_code == 400, response.data
        details = response.data["error"]["details"]["supplier_terms"]
        assert details == {
            "package_cost": [UNSUPPORTED_TERM],
            "unit_cost": ["A valid number is required."],
        }


class TestARefusalLeavesNoRowsBehind:
    """A refused kit write must not half-happen.

    On base the terms were applied AFTER ``super().create()``, so every 500
    above left a committed kit with no supplier link — the operator saw a server
    error and got a duplicate-SKU refusal on their next attempt. Validating the
    block as a nested serializer moves the refusal into ``is_valid()``, before
    any write. That ordering is the reason these assertions can be made at all,
    so they are pinned separately from the messages.
    """

    def test_a_refused_write_creates_no_kit(self, authenticated_client, supplier, component):
        client, _ = authenticated_client

        response = post_kit(
            client, component, supplier=supplier.pk, supplier_sku="S", unit_cost="123456789012.00"
        )

        assert response.status_code == 400, response.data
        assert not InventoryItem.objects.filter(sku=KIT_SKU).exists()
        assert not ItemSupplier.objects.filter(item__sku=KIT_SKU).exists()

    @pytest.mark.django_db(transaction=True)
    def test_a_supplier_id_naming_no_row_is_refused_before_commit(
        self, authenticated_client, component
    ):
        """``transaction=True`` because the base defect only exists at COMMIT.

        The foreign key is ``DEFERRABLE INITIALLY DEFERRED``, so on base the
        ``INSERT`` succeeded and an ordinary rolled-back test saw a cheerful
        201. The real request committed and raised. Resolving the supplier in
        the serializer refuses it up front, which is the same answer at either
        isolation level.
        """
        client, _ = authenticated_client

        response = post_kit(client, component, supplier=999999, supplier_sku="S")

        assert refusal_for(response, "supplier") == ['Invalid pk "999999" - object does not exist.']
        assert not InventoryItem.objects.filter(sku=KIT_SKU).exists()


class TestTheTermsAKitFormACTUALLYSENDSStillWork:
    """The refusals must not cost the endpoint its job.

    ``KitDetailPage`` sends exactly ``supplier``, ``supplier_sku`` and
    ``unit_cost``, the last of which it sends as ``null`` when the box is blank.
    Every one of those still goes through, and — the part a validating layer is
    most likely to break — an OMITTED key still stays out of the write, because
    ``derive_costs`` reads "omitted" as "leave the stored value alone".
    """

    def test_the_form_payload_still_creates_a_buyable_kit(
        self, authenticated_client, supplier, component
    ):
        client, _ = authenticated_client

        response = post_kit(
            client, component, supplier=supplier.pk, supplier_sku="T3200", unit_cost="89.99"
        )

        assert response.status_code == 201, response.data
        link = ItemSupplier.objects.get(item_id=response.data["id"])
        assert (link.supplier_id, link.supplier_sku, link.unit_cost) == (
            supplier.pk,
            "T3200",
            Decimal("89.99"),
        )
        assert link.is_primary is True

    def test_an_omitted_lead_time_does_not_overwrite_the_stored_one(
        self, authenticated_client, supplier, component
    ):
        """The regression a defaulting serializer would reintroduce.

        A serializer ``default`` on ``average_lead_time`` would put 7 into
        ``defaults`` on every save, resetting a recorded 21 the operator never
        touched — the same shape as the ``setdefault`` that used to reset a
        recorded pack size to 1. Declaring the fields optional with no defaults
        is what keeps the write PARTIAL.
        """
        client, _ = authenticated_client
        created = post_kit(
            client, component, supplier=supplier.pk, supplier_sku="S", average_lead_time=21
        )
        assert created.status_code == 201, created.data
        kit_id = created.data["id"]

        response = client.patch(
            reverse("kit-detail", args=[kit_id]),
            {"supplier_terms": {"supplier": supplier.pk, "supplier_sku": "S-EDITED"}},
            format="json",
        )

        assert response.status_code == 200, response.data
        link = ItemSupplier.objects.get(item_id=kit_id)
        assert (link.supplier_sku, link.average_lead_time) == ("S-EDITED", 21)

    def test_a_blank_supplier_sku_is_still_accepted(
        self, authenticated_client, supplier, component
    ):
        """A deliberate DIVERGENCE from ``/item-suppliers/``, pinned so it is not
        closed by accident: the model field is non-blank, but clearing the kit
        form's SKU box has always been allowed and this change does not take
        working saves away."""
        client, _ = authenticated_client

        response = post_kit(client, component, supplier=supplier.pk, supplier_sku="")

        assert response.status_code == 201, response.data
        assert ItemSupplier.objects.get(item_id=response.data["id"]).supplier_sku == ""

    def test_a_negative_unit_cost_is_still_accepted(
        self, authenticated_client, supplier, component
    ):
        """The other deliberate exclusion: the column declares no lower bound,
        so this path does not invent one. Pinned so the exclusion is visible
        rather than merely absent."""
        client, _ = authenticated_client

        response = post_kit(
            client, component, supplier=supplier.pk, supplier_sku="S", unit_cost="-5.00"
        )

        assert response.status_code == 201, response.data
        assert ItemSupplier.objects.get(item_id=response.data["id"]).unit_cost == Decimal("-5.00")
