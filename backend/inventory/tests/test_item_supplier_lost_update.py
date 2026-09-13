"""A supplier-link save made from a stale copy is refused, and the person is told.

THE DEFECT. Two people open the same supplier link. One saves a new lead time
of 12; the other then saves their form, still holding the 7 it loaded, and the
12 is gone with no word to either of them. :mod:`inventory.services.link_version`
closes it with a version every write moves on, which a save may state.

What is pinned here, by the person or client that meets it:

* the API contract a client switches on — the version travels with every row, a
  stale ``version`` is a ``409`` ``stale_version`` with a documented body, and a
  write that carries no version behaves exactly as it did (ScanTTY);
* every write that must make a loaded copy stale does so, including the two
  that do not move ``updated_at``;
* the kit form's terms and the Django admin refuse the same way, each through
  the surface its operator is looking at;
* the check and the write are one step: two saves made from one load, run
  interleaved on two database connections, cannot both land.
"""

import re
import threading
import time
from datetime import timedelta

from django.contrib import admin as django_admin
from django.contrib.admin.sites import AdminSite
from django.db import connection, transaction
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from inventory.admin import ItemSupplierAdmin, ItemSupplierInline
from inventory.models import InventoryItem, ItemSupplier, PriceHistory
from inventory.services.link_version import StaleSupplierLink
from inventory.tasks import update_average_lead_times
from inventory.tests.factories import (
    InventoryItemFactory,
    ItemSupplierFactory,
    SupplierFactory,
)
from reorder_queue.models import ReorderRequest
from reorder_queue.tests.factories import ReorderRequestFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def client(django_user_model):
    user = django_user_model.objects.create_user(
        username="lost-update", password="pw", is_staff=True, is_superuser=True
    )
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def link():
    """A link someone has open: a quoted week, and the item's primary."""
    return ItemSupplierFactory(
        item=InventoryItemFactory(image=None),
        supplier_sku="LOADED",
        average_lead_time=7,
        is_primary=True,
    )


def detail_url(pk):
    return reverse("itemsupplier-detail", args=[pk])


def on_disk(pk):
    return ItemSupplier.objects.values("average_lead_time", "supplier_sku", "version").get(pk=pk)


def load(client, pk):
    """What a client holds after opening the link: the row as the API served it."""
    response = client.get(detail_url(pk))
    assert response.status_code == 200
    return response.data


# ---------------------------------------------------------------------------
# The API contract.
# ---------------------------------------------------------------------------


class TestTheTokenOnTheApi:
    def test_every_representation_carries_the_version_and_every_write_moves_it_on(
        self, client, link
    ):
        loaded = load(client, link.pk)
        assert loaded["version"] == 1

        response = client.patch(detail_url(link.pk), {"supplier_sku": "NEXT"}, format="json")

        assert response.status_code == 200
        assert response.data["version"] == 2
        assert on_disk(link.pk)["version"] == 2

    def test_a_write_from_the_current_version_lands(self, client, link):
        loaded = load(client, link.pk)

        response = client.patch(
            detail_url(link.pk),
            {"average_lead_time": 12, "version": loaded["version"]},
            format="json",
        )

        assert response.status_code == 200, response.data
        assert on_disk(link.pk) == {"average_lead_time": 12, "supplier_sku": "LOADED", "version": 2}

    def test_a_stale_delete_preserves_the_newer_row(self, client, link):
        loaded = load(client, link.pk)
        client.patch(detail_url(link.pk), {"average_lead_time": 12}, format="json")

        response = client.delete(f"{detail_url(link.pk)}?version={loaded['version']}")

        assert response.status_code == 409
        assert response.data["error"]["code"] == "stale_version"
        assert response.data["error"]["details"] == {
            "id": link.pk,
            "sent_version": 1,
            "current_version": 2,
        }
        assert on_disk(link.pk)["average_lead_time"] == 12

    def test_a_current_delete_succeeds(self, client, link):
        response = client.delete(f"{detail_url(link.pk)}?version={link.version}")

        assert response.status_code == 204
        assert not ItemSupplier.objects.filter(pk=link.pk).exists()

    def test_a_tokenless_delete_is_unchanged(self, client, link):
        response = client.delete(detail_url(link.pk))

        assert response.status_code == 204
        assert not ItemSupplier.objects.filter(pk=link.pk).exists()

    def test_a_versioned_delete_of_an_already_deleted_row_is_stale(self, client, link):
        link_id = link.pk
        version = link.version
        link.delete()

        response = client.delete(f"{detail_url(link_id)}?version={version}")

        assert response.status_code == 409
        assert response.data["error"]["details"] == {
            "id": link_id,
            "sent_version": version,
            "current_version": None,
        }

    def test_a_versioned_patch_of_an_already_deleted_row_is_stale(self, client, link):
        link_id = link.pk
        version = link.version
        link.delete()

        response = client.patch(
            detail_url(link_id), {"supplier_sku": "TOO-LATE", "version": version}, format="json"
        )

        assert response.status_code == 409
        assert response.data["error"]["details"] == {
            "id": link_id,
            "sent_version": version,
            "current_version": None,
        }

    @pytest.mark.parametrize("method", ["delete", "patch"])
    def test_a_tokenless_write_of_an_already_deleted_row_stays_not_found(
        self, client, link, method
    ):
        link_id = link.pk
        link.delete()

        if method == "delete":
            response = client.delete(detail_url(link_id))
        else:
            response = client.patch(detail_url(link_id), {"supplier_sku": "TOO-LATE"}, format="json")

        assert response.status_code == 404

    @pytest.mark.parametrize("version", ["not-an-integer", "0", "-1"])
    def test_an_invalid_delete_version_is_rejected(self, client, link, version):
        response = client.delete(f"{detail_url(link.pk)}?version={version}")

        assert response.status_code == 400
        assert ItemSupplier.objects.filter(pk=link.pk).exists()

    def test_the_second_of_two_saves_from_one_load_is_refused_and_told_why(self, client, link):
        """The captain's scenario, through the routed endpoint."""
        first_person = load(client, link.pk)
        second_person = load(client, link.pk)

        saved = client.patch(
            detail_url(link.pk),
            {"average_lead_time": 12, "version": second_person["version"]},
            format="json",
        )
        assert saved.status_code == 200

        stale = client.patch(
            detail_url(link.pk),
            {
                "average_lead_time": first_person["average_lead_time"],
                "supplier_sku": "FIRST-PERSON",
                "version": first_person["version"],
            },
            format="json",
        )

        assert stale.status_code == 409
        assert stale.data == {
            "error": {
                "code": "stale_version",
                "message": (
                    "Someone else changed this supplier link after you loaded it, so your "
                    "changes were not saved. Your copy is out of date: reload to see the "
                    "current values, then make your change again."
                ),
                "details": {"id": link.pk, "sent_version": 1, "current_version": 2},
            }
        }
        # Nothing of the refused write landed — not even the field nobody else touched.
        assert on_disk(link.pk) == {"average_lead_time": 12, "supplier_sku": "LOADED", "version": 2}

    def test_put_is_refused_the_same_way(self, client, link):
        loaded = load(client, link.pk)
        client.patch(detail_url(link.pk), {"supplier_sku": "MOVED"}, format="json")

        response = client.put(
            detail_url(link.pk),
            {
                "item": str(link.item_id),
                "supplier": link.supplier_id,
                "supplier_sku": "PUT",
                "quantity_per_package": 1,
                "version": loaded["version"],
            },
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "stale_version"
        assert on_disk(link.pk)["supplier_sku"] == "MOVED"

    def test_a_write_that_carries_no_version_behaves_exactly_as_before(self, client, link):
        """The compatibility contract ScanTTY depends on: no token, no check."""
        client.patch(detail_url(link.pk), {"average_lead_time": 12}, format="json")

        response = client.patch(detail_url(link.pk), {"average_lead_time": 7}, format="json")

        assert response.status_code == 200
        assert on_disk(link.pk)["average_lead_time"] == 7
        assert response.data["version"] == 3

    def test_a_refused_promotion_demotes_no_sibling_and_files_no_price_history(self, client, link):
        other = ItemSupplierFactory(item=link.item, is_primary=False, package_cost="10.00")
        loaded = load(client, other.pk)
        client.patch(detail_url(other.pk), {"supplier_sku": "MOVED"}, format="json")
        history = PriceHistory.objects.filter(item_supplier=other).count()

        response = client.patch(
            detail_url(other.pk),
            {"is_primary": True, "package_cost": "99.00", "version": loaded["version"]},
            format="json",
        )

        assert response.status_code == 409
        assert ItemSupplier.objects.get(pk=link.pk).is_primary is True
        assert ItemSupplier.objects.get(pk=other.pk).is_primary is False
        assert PriceHistory.objects.filter(item_supplier=other).count() == history

    def test_a_create_takes_no_version_from_the_caller(self, client):
        item = InventoryItemFactory(image=None)

        response = client.post(
            reverse("itemsupplier-list"),
            {
                "item": str(item.pk),
                "supplier": SupplierFactory().pk,
                "supplier_sku": "NEW",
                "quantity_per_package": 1,
                "version": 40,
            },
            format="json",
        )

        assert response.status_code == 201, response.data
        assert response.data["version"] == 1

    @pytest.mark.parametrize("version", [0, -1, "soon"])
    def test_a_version_that_is_not_one_is_a_validation_error(self, client, link, version):
        response = client.patch(
            detail_url(link.pk), {"supplier_sku": "X", "version": version}, format="json"
        )

        assert response.status_code == 400
        assert "version" in response.data["error"]["details"]
        assert on_disk(link.pk)["supplier_sku"] == "LOADED"


# ---------------------------------------------------------------------------
# Every write that must make a loaded copy stale.
# ---------------------------------------------------------------------------


def write_via_tokenless_patch(client, link):
    client.patch(detail_url(link.pk), {"supplier_sku": "TOKENLESS"}, format="json")


def write_via_measuring_task(client, link):
    """Saves with ``update_fields`` — which never moves ``updated_at``."""
    ordered = timezone.now() - timedelta(days=20)
    ReorderRequestFactory(
        item=link.item,
        status=ReorderRequest.Status.RECEIVED,
        ordered_at=ordered,
        actual_delivery=(ordered + timedelta(days=12)).date(),
    )
    update_average_lead_times()
    assert on_disk(link.pk)["average_lead_time"] == 12


def write_via_sibling_promotion(client, link):
    """Demotes this row with a ``QuerySet.update()`` — which never moves ``updated_at``."""
    ItemSupplierFactory(item=link.item, is_primary=True)
    assert ItemSupplier.objects.get(pk=link.pk).is_primary is False


def write_via_mark_discontinued(client, link):
    client.post(reverse("itemsupplier-mark-discontinued", args=[link.pk]))


def write_via_kit_supplier_terms(client, link):
    InventoryItem.objects.filter(pk=link.item_id).update(is_kit=True, current_stock=0)
    response = client.patch(
        reverse("kit-detail", args=[link.item_id]),
        {"supplier_terms": {"supplier": link.supplier_id, "supplier_sku": "KIT"}},
        format="json",
    )
    assert response.status_code == 200, response.data


def write_via_admin_change_form(client, link):
    fresh = ItemSupplier.objects.get(pk=link.pk)
    form_class = ItemSupplierAdmin(ItemSupplier, AdminSite()).get_form(None, obj=fresh, change=True)
    form = form_class(data=_admin_post(fresh, supplier_sku="ADMIN"), instance=fresh)
    assert form.is_valid(), form.errors
    form.save()


def write_via_orm_save(client, link):
    fresh = ItemSupplier.objects.get(pk=link.pk)
    fresh.notes = "a note"
    fresh.save()


INTERVENING_WRITES = [
    write_via_tokenless_patch,
    write_via_measuring_task,
    write_via_sibling_promotion,
    write_via_mark_discontinued,
    write_via_kit_supplier_terms,
    write_via_admin_change_form,
    write_via_orm_save,
]


@pytest.mark.parametrize("intervening", INTERVENING_WRITES, ids=lambda f: f.__name__)
def test_every_write_makes_an_earlier_copy_stale(client, link, intervening):
    loaded = load(client, link.pk)

    intervening(client, link)

    response = client.patch(
        detail_url(link.pk),
        {"supplier_sku": "FROM-THE-OLD-PAGE", "version": loaded["version"]},
        format="json",
    )
    assert response.status_code == 409, response.data
    assert on_disk(link.pk)["supplier_sku"] != "FROM-THE-OLD-PAGE"


# ---------------------------------------------------------------------------
# The kit form's terms.
# ---------------------------------------------------------------------------


@pytest.fixture
def kit_link(link):
    InventoryItem.objects.filter(pk=link.item_id).update(is_kit=True, current_stock=0)
    return ItemSupplier.objects.get(pk=link.pk)


def kit_patch(client, kit_link, **terms):
    return client.patch(
        reverse("kit-detail", args=[kit_link.item_id]),
        {
            "name": "Renamed Kit",
            "supplier_terms": {"supplier": kit_link.supplier_id, **terms},
        },
        format="json",
    )


class TestKitSupplierTerms:
    def test_the_kit_payload_carries_each_links_version(self, client, kit_link):
        response = client.get(reverse("kit-detail", args=[kit_link.item_id]))

        versions = {row["id"]: row["version"] for row in response.data["suppliers"]}
        assert versions == dict(
            ItemSupplier.objects.filter(item_id=kit_link.item_id).values_list("id", "version")
        )
        assert versions[kit_link.pk] == 1

    def test_terms_from_the_current_version_land(self, client, kit_link):
        response = kit_patch(client, kit_link, supplier_sku="KIT-EDIT", version=1)

        assert response.status_code == 200, response.data
        assert on_disk(kit_link.pk)["supplier_sku"] == "KIT-EDIT"

    def test_stale_terms_refuse_the_whole_kit_save(self, client, kit_link):
        client.patch(detail_url(kit_link.pk), {"supplier_sku": "NEWER"}, format="json")

        response = kit_patch(client, kit_link, supplier_sku="STALE", version=1)

        assert response.status_code == 409
        assert response.data["error"]["code"] == "stale_version"
        assert on_disk(kit_link.pk)["supplier_sku"] == "NEWER"
        # The kit's own fields were written first; the refusal rolled them back too.
        assert InventoryItem.objects.get(pk=kit_link.item_id).name != "Renamed Kit"

    def test_terms_without_a_version_behave_exactly_as_before(self, client, kit_link):
        client.patch(detail_url(kit_link.pk), {"supplier_sku": "NEWER"}, format="json")

        response = kit_patch(client, kit_link, supplier_sku="NO-TOKEN")

        assert response.status_code == 200
        assert on_disk(kit_link.pk)["supplier_sku"] == "NO-TOKEN"

    def test_a_version_for_a_deleted_link_refuses_recreation(self, client, kit_link):
        kit_id = kit_link.item_id
        supplier = kit_link.supplier
        kit_link.delete()

        response = client.patch(
            reverse("kit-detail", args=[kit_id]),
            {
                "name": "Renamed Kit",
                "supplier_terms": {"supplier": supplier.pk, "supplier_sku": "STALE", "version": 1},
            },
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["details"] == {
            "id": None,
            "sent_version": 1,
            "current_version": None,
        }
        assert not ItemSupplier.objects.filter(item_id=kit_id, supplier=supplier).exists()
        assert InventoryItem.objects.get(pk=kit_id).name != "Renamed Kit"

    def test_expected_absence_refuses_a_link_created_since(self, client, kit_link):
        other = SupplierFactory()
        newer = ItemSupplierFactory(
            item=kit_link.item, supplier=other, supplier_sku="NEWER", is_primary=False
        )

        response = client.patch(
            reverse("kit-detail", args=[kit_link.item_id]),
            {
                "name": "Renamed Kit",
                "supplier_terms": {"supplier": other.pk, "supplier_sku": "STALE", "version": 0},
            },
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["details"] == {
            "id": newer.pk,
            "sent_version": 0,
            "current_version": newer.version,
        }
        newer.refresh_from_db()
        assert newer.supplier_sku == "NEWER"
        assert InventoryItem.objects.get(pk=kit_link.item_id).name != "Renamed Kit"

    def test_expected_absence_creates_when_the_link_is_still_absent(self, client, kit_link):
        other = SupplierFactory()

        response = client.patch(
            reverse("kit-detail", args=[kit_link.item_id]),
            {"supplier_terms": {"supplier": other.pk, "supplier_sku": "NEW", "version": 0}},
            format="json",
        )

        assert response.status_code == 200, response.data
        assert ItemSupplier.objects.get(item_id=kit_link.item_id, supplier=other).supplier_sku == "NEW"

    def test_tokenless_terms_still_create_an_absent_link(self, client, kit_link):
        other = SupplierFactory()

        response = client.patch(
            reverse("kit-detail", args=[kit_link.item_id]),
            {"supplier_terms": {"supplier": other.pk, "supplier_sku": "COMPAT"}},
            format="json",
        )

        assert response.status_code == 200, response.data
        assert ItemSupplier.objects.get(item_id=kit_link.item_id, supplier=other).supplier_sku == "COMPAT"


# ---------------------------------------------------------------------------
# The Django admin.
# ---------------------------------------------------------------------------


def _admin_post(link, **overrides):
    """What a browser posts from a change form rendered for ``link``."""
    data = {
        "loaded_version": str(link.version),
        "item": str(link.item_id),
        "supplier": str(link.supplier_id),
        "supplier_sku": link.supplier_sku,
        "supplier_url": link.supplier_url,
        "quantity_per_package": str(link.quantity_per_package),
        "average_lead_time": str(link.average_lead_time),
        "is_active": "on" if link.is_active else "",
        "is_primary": "on" if link.is_primary else "",
        "notes": link.notes,
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value != ""}


def admin_change_form(link):
    return ItemSupplierAdmin(ItemSupplier, AdminSite()).get_form(None, obj=link, change=True)


class TestAdminChangeForm:
    def test_the_rendered_page_carries_its_version_in_a_hidden_field(self, client, link):
        django_client = _logged_in_admin()
        ItemSupplier.objects.get(pk=link.pk).save()  # version 2, so 1 cannot pass by accident

        page = django_client.get(reverse("admin:inventory_itemsupplier_change", args=[link.pk]))

        assert page.status_code == 200
        assert re.search(
            rb'<input type="hidden" name="loaded_version" value="2"[^>]*>', page.content
        ), "the change form does not render the version it was loaded at"

    def test_a_stale_page_is_refused_with_a_form_error_and_writes_nothing(self, link):
        page = ItemSupplier.objects.get(pk=link.pk)  # the first person opens the page
        newer = ItemSupplier.objects.get(pk=link.pk)
        newer.average_lead_time = 12
        newer.save()  # the second person saves a new quote

        post = _admin_post(page, supplier_sku="FIRST-PERSON")
        form = admin_change_form(ItemSupplier.objects.get(pk=link.pk))(
            data=post, instance=ItemSupplier.objects.get(pk=link.pk)
        )

        assert not form.is_valid()
        assert form.non_field_errors() == [StaleSupplierLink(link.pk, 1, 2).message]
        assert on_disk(link.pk) == {"average_lead_time": 12, "supplier_sku": "LOADED", "version": 2}

    def test_a_current_page_saves(self, link):
        current = ItemSupplier.objects.get(pk=link.pk)
        form = admin_change_form(current)(
            data=_admin_post(current, average_lead_time="12"), instance=current
        )

        assert form.is_valid(), form.errors
        form.save()

        assert on_disk(link.pk)["average_lead_time"] == 12
        assert on_disk(link.pk)["version"] == 2

    def test_a_post_that_does_not_say_what_it_loaded_is_refused(self, link):
        current = ItemSupplier.objects.get(pk=link.pk)
        post = _admin_post(current, supplier_sku="NO-VERSION")
        del post["loaded_version"]

        form = admin_change_form(current)(data=post, instance=current)

        assert not form.is_valid()
        assert "Reload the page" in form.non_field_errors()[0]
        assert on_disk(link.pk)["supplier_sku"] == "LOADED"

    def test_an_add_form_needs_no_version(self):
        item, supplier = InventoryItemFactory(image=None), SupplierFactory()
        form_class = ItemSupplierAdmin(ItemSupplier, AdminSite()).get_form(None)

        form = form_class(
            data={
                "item": str(item.pk),
                "supplier": str(supplier.pk),
                "supplier_sku": "ADD",
                "quantity_per_package": "1",
            }
        )

        assert form.is_valid(), form.errors
        assert form.save().version == 1

    def test_the_real_admin_view_refuses_a_stale_post_on_the_page(self, link):
        django_client = _logged_in_admin()
        url = reverse("admin:inventory_itemsupplier_change", args=[link.pk])
        page = ItemSupplier.objects.get(pk=link.pk)
        newer = ItemSupplier.objects.get(pk=link.pk)
        newer.average_lead_time = 12
        newer.save()

        response = django_client.post(url, _admin_post(page, supplier_sku="FIRST-PERSON"))

        assert response.status_code == 200  # re-rendered with the error, not redirected
        assert b"Your copy is out of date" in response.content
        assert on_disk(link.pk) == {"average_lead_time": 12, "supplier_sku": "LOADED", "version": 2}

    def test_a_refusal_raised_by_the_save_itself_becomes_a_message_not_a_server_error(
        self, link, monkeypatch
    ):
        """The backstop, for a stale save that form validation could not see."""
        django_client = _logged_in_admin()
        url = reverse("admin:inventory_itemsupplier_change", args=[link.pk])
        current = ItemSupplier.objects.get(pk=link.pk)

        def refuse(self, request, obj, form, change):
            raise StaleSupplierLink(obj.pk, obj.version, obj.version + 1)

        monkeypatch.setattr(ItemSupplierAdmin, "save_model", refuse)

        response = django_client.post(url, _admin_post(current, supplier_sku="LOST"), follow=True)

        assert response.redirect_chain == [(url, 302)]
        assert b"Your copy is out of date" in response.content
        assert on_disk(link.pk)["supplier_sku"] == "LOADED"


def _logged_in_admin():
    from django.contrib.auth import get_user_model
    from django.test import Client

    user = get_user_model().objects.create_superuser(
        username="admin-lost-update", email="a@example.com", password="pw"
    )
    django_client = Client()
    django_client.force_login(user)
    return django_client


class TestAdminItemInline:
    def _formset(self, item, rows):
        from django.contrib.auth import get_user_model

        request = RequestFactory().post("/admin/")
        request.user = get_user_model().objects.create_superuser(
            username="inline-lost-update", email="i@example.com", password="pw"
        )
        formset_class = ItemSupplierInline(InventoryItem, AdminSite()).get_formset(
            request, obj=item
        )
        prefix = formset_class.get_default_prefix()
        data = {
            f"{prefix}-TOTAL_FORMS": str(len(rows)),
            f"{prefix}-INITIAL_FORMS": str(len(rows)),
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
        for index, row in enumerate(rows):
            for key, value in row.items():
                data[f"{prefix}-{index}-{key}"] = value
        return formset_class(data=data, instance=item, prefix=prefix)

    def _row(self, link, **overrides):
        row = _admin_post(link, **overrides)
        row.pop("item")
        row["id"] = str(link.pk)
        return row

    def test_the_item_page_renders_each_rows_version(self, link):
        django_client = _logged_in_admin()
        ItemSupplier.objects.get(pk=link.pk).save()

        page = django_client.get(
            reverse("admin:inventory_inventoryitem_change", args=[link.item_id])
        )

        assert page.status_code == 200
        assert re.search(
            rb'<input type="hidden" name="item_suppliers-0-loaded_version" value="2"',
            page.content,
        )

    def test_a_stale_row_refuses_the_item_save(self, link):
        page = ItemSupplier.objects.get(pk=link.pk)
        newer = ItemSupplier.objects.get(pk=link.pk)
        newer.average_lead_time = 12
        newer.save()

        formset = self._formset(link.item, [self._row(page, supplier_sku="INLINE-STALE")])

        assert not formset.is_valid()
        assert formset.errors[0]["__all__"] == [StaleSupplierLink(link.pk, 1, 2).message]
        assert on_disk(link.pk)["average_lead_time"] == 12

    def test_a_current_row_saves(self, link):
        current = ItemSupplier.objects.get(pk=link.pk)

        formset = self._formset(link.item, [self._row(current, average_lead_time="12")])

        assert formset.is_valid(), formset.errors
        formset.save()
        assert on_disk(link.pk)["average_lead_time"] == 12

    def test_a_primary_switch_does_not_stale_its_own_demoted_row(self):
        item = InventoryItemFactory(image=None)
        promoted = ItemSupplierFactory(item=item, is_primary=False, supplier_sku="PROMOTE")
        demoted = ItemSupplierFactory(item=item, is_primary=True, supplier_sku="DEMOTE")
        promoted.refresh_from_db()
        formset = self._formset(
            item,
            [
                self._row(promoted, is_primary="on"),
                self._row(demoted, is_primary=""),
            ],
        )

        assert formset.is_valid(), formset.errors
        with transaction.atomic():
            formset.save()

        promoted.refresh_from_db()
        demoted.refresh_from_db()
        assert promoted.is_primary
        assert not demoted.is_primary


def test_admin_registrations_carry_the_backstop():
    """Both admin pages that save a link turn a save-time refusal into a message."""
    from inventory.admin import RefuseStaleSupplierLinkMixin

    assert isinstance(django_admin.site._registry[ItemSupplier], RefuseStaleSupplierLinkMixin)
    assert isinstance(django_admin.site._registry[InventoryItem], RefuseStaleSupplierLinkMixin)


# ---------------------------------------------------------------------------
# The check and the write are one step.
# ---------------------------------------------------------------------------


def test_a_refused_save_is_still_refused_when_the_same_copy_is_saved_again(link):
    """A token is used up by a write that LANDED, not by one that was refused.

    Otherwise a caller that catches the refusal and simply calls ``save()`` again
    would find the check gone and overwrite the newer values after all.
    """
    page = ItemSupplier.objects.get(pk=link.pk)
    page.expected_version = page.version
    newer = ItemSupplier.objects.get(pk=link.pk)
    newer.average_lead_time = 12
    newer.save()

    page.average_lead_time = 7
    for _attempt in range(2):
        with pytest.raises(StaleSupplierLink):
            page.save()

    assert on_disk(link.pk)["average_lead_time"] == 12


def _wait_until_a_connection_waits_on_a_lock(timeout=15):
    """Block until another backend on this database is waiting on a row lock.

    Polled rather than slept, so the interleaving below is a fact about the
    database, not a guess about timing.
    """
    deadline = time.monotonic() + timeout
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                "AND pid <> pg_backend_pid()"
            )
            if cursor.fetchone()[0]:
                return
            time.sleep(0.02)
    raise AssertionError("the second save never waited on the first save's lock")


@pytest.mark.django_db(transaction=True)
def test_two_interleaved_saves_from_one_load_cannot_both_land():
    """Two saves, both made from version 1, on two connections, at once.

    The first save's transaction is held open after its write. The second save
    starts while it is open and must wait on the row lock; only once the first
    commits does it see version 2 and refuse. Take the lock out of
    ``claim_version`` and the second save reads version 1 (the first is not
    committed yet), passes its check, then overwrites the 12 with a 7 as soon
    as the first commits — the lost update, reproduced.
    """
    if not connection.features.has_select_for_update:
        pytest.skip("requires row-level select_for_update locking")

    link = ItemSupplierFactory(
        item=InventoryItemFactory(image=None), average_lead_time=7, is_primary=True
    )
    first_copy = ItemSupplier.objects.get(pk=link.pk)
    second_copy = ItemSupplier.objects.get(pk=link.pk)

    first_written = threading.Event()
    release_first = threading.Event()
    outcomes = {}

    def first_person():
        try:
            with transaction.atomic():
                first_copy.average_lead_time = 12
                first_copy.expected_version = 1
                first_copy.save()
                first_written.set()
                assert release_first.wait(timeout=30)
            outcomes["first"] = "saved"
        except Exception as exc:  # pragma: no cover - reported by the assertion below
            outcomes["first"] = exc
            first_written.set()
        finally:
            connection.close()

    def second_person():
        try:
            second_copy.average_lead_time = 7
            second_copy.supplier_sku = "SECOND-PERSON"
            second_copy.expected_version = 1
            second_copy.save()
            outcomes["second"] = "saved"
        except StaleSupplierLink as exc:
            outcomes["second"] = exc
        except Exception as exc:  # pragma: no cover
            outcomes["second"] = exc
        finally:
            connection.close()

    first = threading.Thread(target=first_person, daemon=True)
    second = threading.Thread(target=second_person, daemon=True)
    first.start()
    assert first_written.wait(timeout=30)
    second.start()
    try:
        _wait_until_a_connection_waits_on_a_lock()
    finally:
        release_first.set()
        first.join(timeout=30)
        second.join(timeout=30)

    assert [first.is_alive(), second.is_alive()] == [False, False]
    assert outcomes["first"] == "saved"
    assert isinstance(outcomes["second"], StaleSupplierLink), outcomes["second"]
    assert (outcomes["second"].sent, outcomes["second"].current) == (1, 2)
    row = ItemSupplier.objects.get(pk=link.pk)
    assert (row.average_lead_time, row.supplier_sku, row.version) == (12, link.supplier_sku, 2)


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_primary_promotions_serialize_to_one_winner():
    """Concurrent promotions cannot deterministically fail on every PostgreSQL plan."""
    if not connection.features.has_select_for_update:
        pytest.skip("requires row-level select_for_update locking")

    item = InventoryItemFactory(image=None)
    first_link = ItemSupplierFactory(item=item, is_primary=False)
    second_link = ItemSupplierFactory(item=item, is_primary=False)
    first_copy = ItemSupplier.objects.get(pk=first_link.pk)
    second_copy = ItemSupplier.objects.get(pk=second_link.pk)
    first_written = threading.Event()
    release_first = threading.Event()
    outcomes = {}

    def promote_first():
        try:
            with transaction.atomic():
                first_copy.is_primary = True
                first_copy.save()
                first_written.set()
                assert release_first.wait(timeout=30)
            outcomes["first"] = "saved"
        except Exception as exc:  # pragma: no cover
            outcomes["first"] = exc
            first_written.set()
        finally:
            connection.close()

    def promote_second():
        try:
            second_copy.is_primary = True
            second_copy.save()
            outcomes["second"] = "saved"
        except Exception as exc:  # pragma: no cover
            outcomes["second"] = exc
        finally:
            connection.close()

    first = threading.Thread(target=promote_first, daemon=True)
    second = threading.Thread(target=promote_second, daemon=True)
    first.start()
    assert first_written.wait(timeout=30)
    second.start()
    try:
        _wait_until_a_connection_waits_on_a_lock()
    finally:
        release_first.set()
        first.join(timeout=30)
        second.join(timeout=30)

    assert [first.is_alive(), second.is_alive()] == [False, False]
    assert outcomes == {"first": "saved", "second": "saved"}
    assert ItemSupplier.objects.filter(item=item, is_primary=True).count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_first_primary_creates_serialize_to_one_winner():
    if connection.vendor != "postgresql":
        pytest.skip("requires PostgreSQL transaction advisory locks")

    item = InventoryItemFactory(image=None)
    first_link = ItemSupplier(item=item, supplier=SupplierFactory(), is_primary=True)
    second_link = ItemSupplier(item=item, supplier=SupplierFactory(), is_primary=True)
    first_written = threading.Event()
    release_first = threading.Event()
    outcomes = {}

    def create_first():
        try:
            with transaction.atomic():
                first_link.save()
                first_written.set()
                assert release_first.wait(timeout=30)
            outcomes["first"] = "saved"
        except Exception as exc:  # pragma: no cover
            outcomes["first"] = exc
            first_written.set()
        finally:
            connection.close()

    def create_second():
        try:
            second_link.save()
            outcomes["second"] = "saved"
        except Exception as exc:  # pragma: no cover
            outcomes["second"] = exc
        finally:
            connection.close()

    first = threading.Thread(target=create_first, daemon=True)
    second = threading.Thread(target=create_second, daemon=True)
    first.start()
    assert first_written.wait(timeout=30)
    second.start()
    try:
        _wait_until_a_connection_waits_on_a_lock()
    finally:
        release_first.set()
        first.join(timeout=30)
        second.join(timeout=30)

    assert [first.is_alive(), second.is_alive()] == [False, False]
    assert outcomes == {"first": "saved", "second": "saved"}
    assert ItemSupplier.objects.filter(item=item, is_primary=True).count() == 1


@pytest.mark.django_db(transaction=True)
def test_tokened_primary_patch_racing_a_sibling_promotion_does_not_deadlock(
    django_user_model,
):
    if connection.vendor != "postgresql":
        pytest.skip("requires PostgreSQL transaction advisory locks")

    user = django_user_model.objects.create_user(
        username="lock-order", password="pw", is_staff=True, is_superuser=True
    )
    item = InventoryItemFactory(image=None)
    primary = ItemSupplierFactory(item=item, is_primary=True, supplier_sku="PRIMARY")
    sibling = ItemSupplierFactory(item=item, is_primary=False, supplier_sku="SIBLING")
    first_written = threading.Event()
    release_first = threading.Event()
    outcomes = {}

    def patch_primary():
        api = APIClient()
        api.force_authenticate(user=user)
        try:
            with transaction.atomic():
                response = api.patch(
                    detail_url(primary.pk),
                    {"supplier_sku": "PRIMARY-EDIT", "version": primary.version},
                    format="json",
                )
                outcomes["primary"] = response.status_code
                first_written.set()
                assert release_first.wait(timeout=30)
        except Exception as exc:  # pragma: no cover
            outcomes["primary"] = exc
            first_written.set()
        finally:
            connection.close()

    def promote_sibling():
        api = APIClient()
        api.force_authenticate(user=user)
        try:
            response = api.patch(
                detail_url(sibling.pk),
                {"is_primary": True, "version": sibling.version},
                format="json",
            )
            outcomes["sibling"] = response.status_code
        except Exception as exc:  # pragma: no cover
            outcomes["sibling"] = exc
        finally:
            connection.close()

    first = threading.Thread(target=patch_primary, daemon=True)
    second = threading.Thread(target=promote_sibling, daemon=True)
    first.start()
    assert first_written.wait(timeout=30)
    second.start()
    try:
        _wait_until_a_connection_waits_on_a_lock()
    finally:
        release_first.set()
        first.join(timeout=30)
        second.join(timeout=30)

    assert [first.is_alive(), second.is_alive()] == [False, False]
    assert outcomes["primary"] in (200, 409)
    assert outcomes["sibling"] in (200, 409)
    assert ItemSupplier.objects.filter(item=item, is_primary=True).count() == 1
