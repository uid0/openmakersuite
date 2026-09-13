"""Two supplier links of one item can exchange suppliers in ONE request.

THE DEFECT. Row A links supplier X and row B links supplier Y. Moving A to Y and
B to X one PATCH at a time collides with the ``(item, supplier)`` uniqueness
whichever goes first, and every retry repeats the collision. ``POST
/api/inventory/item-suppliers/batch/`` applies both in one transaction with the
pair check deferred to its end (``inventory.services.link_batch``).

Pinned here: the exchange itself (and the create-onto-a-vacated-pair variant),
the version contract across every row the request touches, the refusals that
are real conflicts, and that every refusal or failure — including one part-way
through the writes — leaves every row exactly as it was.
"""

from unittest import mock

from django.db import IntegrityError, transaction
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from inventory.models import ItemSupplier, PriceHistory
from inventory.services import link_batch
from inventory.tests.factories import (
    InventoryItemFactory,
    ItemSupplierFactory,
    SupplierFactory,
)

pytestmark = pytest.mark.django_db

BATCH_URL = "/api/inventory/item-suppliers/batch/"


@pytest.fixture
def client(django_user_model):
    user = django_user_model.objects.create_user(username="batch", password="pw")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def item():
    return InventoryItemFactory(image=None)


@pytest.fixture
def pair(item):
    """Row A on supplier X (the primary) and row B on supplier Y."""
    row_a = ItemSupplierFactory(item=item, supplier_sku="A-SKU", is_primary=True)
    row_b = ItemSupplierFactory(item=item, supplier_sku="B-SKU", is_primary=False)
    return row_a, row_b


def snapshot(*rows):
    """Everything about the rows a write could disturb, as stored."""
    return {
        row.pk: ItemSupplier.objects.values(
            "supplier_id", "supplier_sku", "is_primary", "version", "unit_cost", "package_cost"
        ).get(pk=row.pk)
        for row in rows
    }


def swap_payload(item, row_a, row_b, **versions):
    return {
        "item": str(item.pk),
        "links": [
            {"id": row_a.pk, "supplier": row_b.supplier_id, **versions.get("a", {})},
            {"id": row_b.pk, "supplier": row_a.supplier_id, **versions.get("b", {})},
        ],
    }


def test_the_route_is_the_documented_one():
    assert reverse("itemsupplier-batch") == BATCH_URL


class TestTheExchange:
    def test_two_links_exchange_suppliers_in_one_request(self, client, item, pair):
        row_a, row_b = pair
        x, y = row_a.supplier_id, row_b.supplier_id

        response = client.post(
            BATCH_URL,
            swap_payload(item, row_a, row_b, a={"version": 1}, b={"version": 1}),
            format="json",
        )

        assert response.status_code == 200, response.data
        # The same rows — ids, and so their price history and PO lines — now
        # carry each other's supplier, and each write moved its version on.
        assert [link["id"] for link in response.data["links"]] == [row_a.pk, row_b.pk]
        assert [link["supplier"] for link in response.data["links"]] == [y, x]
        assert [link["version"] for link in response.data["links"]] == [2, 2]
        stored = snapshot(row_a, row_b)
        assert (stored[row_a.pk]["supplier_id"], stored[row_b.pk]["supplier_id"]) == (y, x)
        assert (stored[row_a.pk]["version"], stored[row_b.pk]["version"]) == (2, 2)

    def test_the_same_exchange_one_patch_at_a_time_is_still_refused(self, client, item, pair):
        """The single-link endpoint is unchanged for the clients that use it (ScanTTY)."""
        row_a, row_b = pair

        response = client.patch(
            reverse("itemsupplier-detail", args=[row_a.pk]),
            {"supplier": row_b.supplier_id},
            format="json",
        )

        assert response.status_code == 400
        assert "must make a unique set" in str(response.data)
        assert snapshot(row_a)[row_a.pk]["supplier_id"] == row_a.supplier_id

    def test_a_new_link_can_claim_the_supplier_an_existing_link_vacates(self, client, item):
        row = ItemSupplierFactory(item=item, is_primary=True)
        vacated, destination = row.supplier_id, SupplierFactory().pk
        links_before = item.item_suppliers.count()

        response = client.post(
            BATCH_URL,
            {
                "item": str(item.pk),
                "links": [
                    {"supplier": vacated, "supplier_sku": "NEW", "is_primary": True, "version": 9},
                    {"id": row.pk, "supplier": destination, "is_primary": False, "version": 1},
                ],
            },
            format="json",
        )

        assert response.status_code == 200, response.data
        created, updated = response.data["links"]
        assert (created["supplier"], created["is_primary"], created["version"]) == (
            vacated,
            True,
            1,
        )
        assert (updated["id"], updated["supplier"], updated["is_primary"]) == (
            row.pk,
            destination,
            False,
        )
        assert item.item_suppliers.count() == links_before + 1

    def test_a_promotion_in_the_batch_does_not_refuse_its_own_demoted_sibling(
        self, client, item, pair
    ):
        """Both versions are checked up front, so the demotion is not "someone else"."""
        row_a, row_b = pair

        response = client.post(
            BATCH_URL,
            {
                "item": str(item.pk),
                "links": [
                    {
                        "id": row_b.pk,
                        "supplier": row_a.supplier_id,
                        "is_primary": True,
                        "version": 1,
                    },
                    {
                        "id": row_a.pk,
                        "supplier": row_b.supplier_id,
                        "is_primary": False,
                        "version": 1,
                    },
                ],
            },
            format="json",
        )

        assert response.status_code == 200, response.data
        assert list(item.item_suppliers.filter(is_primary=True).values_list("pk", flat=True)) == [
            row_b.pk
        ]
        assert [link["is_primary"] for link in response.data["links"]] == [True, False]

    def test_an_entry_that_does_not_mention_the_flag_is_still_demoted_by_the_promotion(
        self, client, item, pair
    ):
        row_a, row_b = pair

        response = client.post(
            BATCH_URL,
            {
                "item": str(item.pk),
                "links": [
                    {"id": row_b.pk, "supplier": row_a.supplier_id, "is_primary": True},
                    {"id": row_a.pk, "supplier": row_b.supplier_id},
                ],
            },
            format="json",
        )

        assert response.status_code == 200, response.data
        assert [link["is_primary"] for link in response.data["links"]] == [True, False]
        assert item.item_suppliers.filter(is_primary=True).count() == 1

    def test_a_batch_without_versions_is_unchecked(self, client, item, pair):
        row_a, row_b = pair
        client.patch(
            reverse("itemsupplier-detail", args=[row_a.pk]),
            {"supplier_sku": "MOVED"},
            format="json",
        )

        response = client.post(BATCH_URL, swap_payload(item, row_a, row_b), format="json")

        assert response.status_code == 200, response.data
        assert snapshot(row_a)[row_a.pk]["version"] == 3


class TestStaleVersions:
    @pytest.mark.parametrize("stale_row", ["a", "b"])
    def test_a_stale_version_on_either_row_refuses_the_whole_exchange(
        self, client, item, pair, stale_row
    ):
        row_a, row_b = pair
        stale = row_a if stale_row == "a" else row_b
        client.patch(
            reverse("itemsupplier-detail", args=[stale.pk]),
            {"supplier_sku": "SOMEONE-ELSE"},
            format="json",
        )
        before = snapshot(row_a, row_b)
        history = PriceHistory.objects.count()

        response = client.post(
            BATCH_URL,
            swap_payload(item, row_a, row_b, a={"version": 1}, b={"version": 1}),
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "stale_version"
        assert response.data["error"]["details"] == {
            "id": stale.pk,
            "sent_version": 1,
            "current_version": 2,
        }
        assert snapshot(row_a, row_b) == before
        assert PriceHistory.objects.count() == history

    def test_a_version_for_a_link_deleted_since_is_stale(self, client, item, pair):
        row_a, row_b = pair
        gone = ItemSupplierFactory(item=item, is_primary=False)
        gone_pk = gone.pk
        gone.delete()
        before = snapshot(row_a, row_b)

        response = client.post(
            BATCH_URL,
            {
                "item": str(item.pk),
                "links": [
                    {"id": row_a.pk, "supplier_sku": "EDIT", "version": 1},
                    {"id": gone_pk, "supplier_sku": "EDIT", "version": 1},
                ],
            },
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["details"] == {
            "id": gone_pk,
            "sent_version": 1,
            "current_version": None,
        }
        assert snapshot(row_a, row_b) == before


class TestAllOrNothing:
    def test_a_failure_part_way_through_the_writes_leaves_both_rows_untouched(
        self, client, item, pair
    ):
        row_a, row_b = pair
        before = snapshot(row_a, row_b)
        history = PriceHistory.objects.count()
        real_save = ItemSupplier.save
        calls = []

        def save_then_fail(instance, *args, **kwargs):
            calls.append(instance.pk)
            if len(calls) == 2:
                raise RuntimeError("the second write fails after the first has landed")
            return real_save(instance, *args, **kwargs)

        with mock.patch.object(ItemSupplier, "save", save_then_fail):
            with pytest.raises(RuntimeError):
                client.post(
                    BATCH_URL,
                    swap_payload(item, row_a, row_b, a={"version": 1}, b={"version": 1}),
                    format="json",
                )

        # The first save ran and was rolled back with the second.
        assert len(calls) == 2
        assert snapshot(row_a, row_b) == before
        assert PriceHistory.objects.count() == history

    def test_the_deferred_check_at_the_end_rolls_everything_back(self, client, item, pair):
        """The backstop: a collision the pre-check missed still writes nothing."""
        row_a, row_b = pair
        holder = ItemSupplierFactory(item=item, is_primary=False)
        before = snapshot(row_a, row_b, holder)

        with mock.patch.object(link_batch, "_refuse_unwritable", lambda changes, rows: None):
            response = client.post(
                BATCH_URL,
                {
                    "item": str(item.pk),
                    "links": [
                        {"id": row_a.pk, "supplier": row_b.supplier_id},
                        {"id": row_b.pk, "supplier": holder.supplier_id},
                    ],
                },
                format="json",
            )

        assert response.status_code == 400
        assert "must make a unique set" in str(response.data)
        assert snapshot(row_a, row_b, holder) == before

    def test_an_invalid_entry_refuses_the_whole_request(self, client, item, pair):
        row_a, row_b = pair
        before = snapshot(row_a, row_b)

        response = client.post(
            BATCH_URL,
            {
                "item": str(item.pk),
                "links": [
                    {"id": row_a.pk, "supplier": row_b.supplier_id},
                    {"id": row_b.pk, "supplier": row_a.supplier_id, "supplier_sku": ""},
                ],
            },
            format="json",
        )

        assert response.status_code == 400
        details = response.data["error"]["details"]["links"]
        assert details[0] == {}
        assert "supplier_sku" in details[1]
        assert snapshot(row_a, row_b) == before


class TestRealConflicts:
    def test_a_supplier_held_by_a_link_the_request_leaves_alone_is_refused(
        self, client, item, pair
    ):
        row_a, row_b = pair
        holder = ItemSupplierFactory(item=item, is_primary=False)
        before = snapshot(row_a, row_b, holder)

        response = client.post(
            BATCH_URL,
            {
                "item": str(item.pk),
                "links": [
                    {"id": row_a.pk, "supplier_sku": "INNOCENT"},
                    {"id": row_b.pk, "supplier": holder.supplier_id},
                ],
            },
            format="json",
        )

        assert response.status_code == 400
        # Only the entry that collides is named; the other one would have landed.
        assert response.data["error"]["details"]["links"] == [
            {},
            {"non_field_errors": ["The fields item, supplier must make a unique set."]},
        ]
        assert snapshot(row_a, row_b, holder) == before

    def test_two_entries_ending_on_one_supplier_are_both_named(self, client, item, pair):
        row_a, row_b = pair
        third = SupplierFactory().pk

        response = client.post(
            BATCH_URL,
            {
                "item": str(item.pk),
                "links": [
                    {"id": row_a.pk, "supplier": third},
                    {"id": row_b.pk, "supplier": third},
                ],
            },
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error"]["details"]["links"] == [
            {"non_field_errors": ["The fields item, supplier must make a unique set."]},
            {"non_field_errors": ["The fields item, supplier must make a unique set."]},
        ]

    def test_two_promotions_in_one_request_are_refused(self, client, item, pair):
        row_a, row_b = pair

        response = client.post(
            BATCH_URL,
            {
                "item": str(item.pk),
                "links": [
                    {"id": row_a.pk, "is_primary": True},
                    {"id": row_b.pk, "is_primary": True},
                ],
            },
            format="json",
        )

        assert response.status_code == 400
        assert [set(entry) for entry in response.data["error"]["details"]["links"]] == [
            {"is_primary"},
            {"is_primary"},
        ]

    def test_a_link_of_another_item_is_refused(self, client, item, pair):
        row_a, _ = pair
        elsewhere = ItemSupplierFactory(is_primary=True)
        before = snapshot(row_a, elsewhere)

        response = client.post(
            BATCH_URL,
            {
                "item": str(item.pk),
                "links": [
                    {"id": row_a.pk, "supplier_sku": "EDIT"},
                    {"id": elsewhere.pk, "supplier_sku": "EDIT", "version": 1},
                ],
            },
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error"]["details"]["links"][1] == {
            "id": ["This supplier link belongs to a different item."]
        }
        assert snapshot(row_a, elsewhere) == before

    def test_an_entry_naming_another_item_is_refused(self, client, item, pair):
        row_a, _ = pair
        other_item = str(InventoryItemFactory(image=None).pk)
        links_before = ItemSupplier.objects.count()

        response = client.post(
            BATCH_URL,
            {
                "item": str(item.pk),
                "links": [
                    {"id": row_a.pk, "item": other_item},
                    {"item": other_item, "supplier": SupplierFactory().pk, "supplier_sku": "NEW"},
                ],
            },
            format="json",
        )

        assert response.status_code == 400
        assert [set(entry) for entry in response.data["error"]["details"]["links"]] == [
            {"item"},
            {"item"},
        ]
        assert ItemSupplier.objects.count() == links_before
        assert snapshot(row_a)[row_a.pk]["version"] == 1

    def test_the_same_link_twice_is_refused(self, client, item, pair):
        row_a, _ = pair

        response = client.post(
            BATCH_URL,
            {
                "item": str(item.pk),
                "links": [{"id": row_a.pk}, {"id": row_a.pk, "supplier_sku": "TWICE"}],
            },
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error"]["details"]["links"][1] == {
            "id": ["This supplier link is named more than once in the request."]
        }

    def test_an_anonymous_request_is_refused(self, item, pair):
        response = APIClient().post(BATCH_URL, swap_payload(item, *pair), format="json")

        assert response.status_code == 401


def test_every_other_write_is_still_checked_at_its_own_statement(item, pair):
    """DEFERRABLE INITIALLY IMMEDIATE: only the batch defers the pair check."""
    row_a, row_b = pair

    with pytest.raises(IntegrityError), transaction.atomic():
        ItemSupplier.objects.filter(pk=row_a.pk).update(supplier_id=row_b.supplier_id)
