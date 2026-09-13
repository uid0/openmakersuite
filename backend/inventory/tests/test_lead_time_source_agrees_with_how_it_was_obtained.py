"""No write path can store a lead time whose provenance disagrees with how it was obtained.

ONE invariant, stated over the CLASS of write paths rather than one symptom at a
time. The first attempt at a provenance marker wrote a test per defect and ran
five fix rounds, each finding the damage the previous fix had done at the next
write boundary (admin form, then malformed input, then an echoed PATCH). This
file does not list those defects. It drives every write path that can put a
number in ``ItemSupplier.average_lead_time`` — enumerated in the owner module,
:mod:`inventory.services.lead_time_source` — with every input shape that path
accepts, against every provenance a row can already hold, and asserts one
thing about each resulting row.

HOW THE EXPECTATION IS BUILT, and why it cannot drift toward the implementation.
Each case says, in this file's own words, how the caller OBTAINED the value it
handed over: it supplied a number, it re-sent the number already stored, it
supplied nothing (an absent key, a blank box, or input the path documents as an
omission), the value was measured, or the path refused the write outright.
:func:`expected` turns that — and nothing read back from the code under test —
into the provenance and the number the row must hold. A new write path is a new
driver in :data:`CREATE_CASES` or :data:`UPDATE_CASES`; it inherits every shape
and every prior state without a test of its own.

The provenance vocabulary is spelled here as plain strings on purpose, so the
expectation does not borrow its meaning from the enum it is checking.
"""

from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import F
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from inventory.admin import ItemSupplierAdmin, ItemSupplierInline
from inventory.models import InventoryItem, ItemSupplier
from inventory.tasks import update_average_lead_times
from inventory.tests.factories import InventoryItemFactory, SupplierFactory
from reorder_queue.models import ReorderRequest
from reorder_queue.tests.factories import ReorderRequestFactory

pytestmark = pytest.mark.django_db

User = get_user_model()

# ---------------------------------------------------------------------------
# How a value was obtained — the test's own vocabulary.
# ---------------------------------------------------------------------------

SUPPLIED = "the caller supplied this number"
ECHOED = "the caller re-sent the number already stored"
NOT_SUPPLIED = "the caller supplied no number"
MEASURED = "the number was measured from deliveries"
REFUSED = "the write path refused the write"

#: The model's planning default, as prose about the server. Only used to say
#: what number a row that took the default must hold.
PLANNING_DEFAULT_DAYS = 7

#: Absent from the payload / the POST. Distinct from a blank string.
ABSENT = object()

#: A form box the operator never touched: the browser posts back exactly what
#: the form rendered in it. Only meaningful for the admin's HTML forms.
UNTOUCHED = object()

#: What a row can already say about itself before an update. ``unknown`` is what
#: the migration backfills every pre-existing row with. Each is paired with the
#: number it holds; the 7s are the ambiguous case the marker exists for.
PRIOR_STATES = [
    ("unknown", 7),
    ("default", 7),
    ("recorded", 7),
    ("recorded", 12),
    ("measured", 9),
]


def expected(obtained, prior, supplied):
    """The (source, days) a row must hold, from how its value was obtained.

    ``prior`` is ``None`` for a create, else the ``(source, days)`` it held.
    ``supplied`` is the number the caller handed over, where it handed one.
    """
    if obtained == MEASURED:
        return "measured", supplied
    if obtained == SUPPLIED:
        return "recorded", supplied
    if prior is None:
        # Nothing supplied on a create: the system's planning default, and it
        # must say so. (A refused create stores no row; handled by the caller.)
        return "default", PLANNING_DEFAULT_DAYS
    # Nothing supplied, an echo, or a refusal on an update: the row is what it
    # was. An echo must never promote an unknown or a default to a quote.
    return prior


# ---------------------------------------------------------------------------
# Fixtures and seeding.
# ---------------------------------------------------------------------------


@pytest.fixture
def client(django_user_model):
    user = django_user_model.objects.create_user(
        username="lead-time-source", password="pw", is_staff=True, is_superuser=True
    )
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def stored(link_pk):
    """The (source, days) actually on disk, bypassing any in-memory instance."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT average_lead_time_source, average_lead_time "
            "FROM inventory_itemsupplier WHERE id = %s",
            [link_pk],
        )
        row = cursor.fetchone()
    return None if row is None else (row[0], row[1])


def seed_link(item, supplier, prior):
    """A persisted link holding ``prior``, as whatever came before left it.

    Written with raw SQL because the model will not let a provenance be set
    apart from the value — which is the property under test — and a seeded
    ``unknown`` is exactly what the backfill leaves behind.
    """
    source, days = prior
    link = ItemSupplier.objects.create(
        item=item,
        supplier=supplier,
        supplier_sku="SEEDED",
        quantity_per_package=1,
        is_primary=True,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE inventory_itemsupplier "
            "SET average_lead_time = %s, average_lead_time_source = %s WHERE id = %s",
            [days, source, link.pk],
        )
    assert stored(link.pk) == prior
    return link


def terms_with(shape, **rest):
    """A payload dict carrying ``average_lead_time`` as ``shape``, or not at all."""
    payload = dict(rest)
    if shape is not ABSENT:
        payload["average_lead_time"] = shape
    return payload


# ---------------------------------------------------------------------------
# CREATE paths. Each takes an input shape and returns the new link's pk, or
# ``None`` where the path refused and stored nothing.
# ---------------------------------------------------------------------------


def create_via_item_suppliers_api(client, shape):
    item = InventoryItemFactory(image=None)
    response = client.post(
        reverse("itemsupplier-list"),
        terms_with(
            shape,
            item=str(item.pk),
            supplier=SupplierFactory().pk,
            supplier_sku="API-CREATE",
            quantity_per_package=1,
        ),
        format="json",
    )
    return response.data["id"] if response.status_code == 201 else None


def create_via_item_create_sync_primary_supplier(client, shape):
    response = client.post(
        reverse("inventoryitem-list"),
        terms_with(
            shape,
            name="Bolt, M3x10",
            sku="BOLT-M3X10",
            description="Hex head",
            reorder_quantity=1,
            supplier=SupplierFactory().pk,
        ),
        format="json",
    )
    if response.status_code != 201:
        return None
    link = ItemSupplier.objects.filter(item_id=response.data["id"]).first()
    return link.pk if link else None


def create_via_kit_supplier_terms(client, shape):
    component = InventoryItemFactory(image=None, is_kit=False, is_serialized=False)
    response = client.post(
        reverse("kit-list"),
        {
            "name": "Ink Kit",
            "sku": "KIT-INK",
            "description": "Four cartridges",
            "reorder_quantity": 1,
            "components": [{"component": component.pk, "quantity": 1}],
            "supplier_terms": terms_with(
                shape, supplier=SupplierFactory().pk, supplier_sku="KIT-SKU"
            ),
        },
        format="json",
    )
    if response.status_code != 201:
        return None
    return ItemSupplier.objects.get(item_id=response.data["id"]).pk


def _admin_form_data(item, supplier, shape, **rest):
    data = {
        "item": str(item.pk),
        "supplier": str(supplier.pk),
        "supplier_sku": "ADMIN",
        "quantity_per_package": "1",
        **rest,
    }
    if shape is not ABSENT:
        data["average_lead_time"] = str(shape)
    return data


def _rendered(bound_field):
    """What a browser posts back for a box nobody touched."""
    value = bound_field.value()
    return "" if value is None else str(value)


def create_via_admin_add_form(client, shape):
    form_class = ItemSupplierAdmin(ItemSupplier, AdminSite()).get_form(None)
    if shape is UNTOUCHED:
        shape = _rendered(form_class()["average_lead_time"])
    form = form_class(
        data=_admin_form_data(InventoryItemFactory(image=None), SupplierFactory(), shape)
    )
    if not form.is_valid():
        return None
    return form.save().pk


def _inline_formset_class(item):
    request = RequestFactory().post("/admin/")
    request.user = User.objects.filter(is_superuser=True).first()
    return ItemSupplierInline(InventoryItem, AdminSite()).get_formset(request, obj=item)


def _inline_formset(item, rows, initial=0):
    """Bind the item admin's supplier inline with ``rows`` (dicts of POST fields)."""
    formset_class = _inline_formset_class(item)
    prefix = formset_class.get_default_prefix()
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        for key, value in row.items():
            data[f"{prefix}-{index}-{key}"] = value
    return formset_class(data=data, instance=item, prefix=prefix)


def create_via_admin_item_inline(client, shape):
    item = InventoryItemFactory(image=None)
    supplier = SupplierFactory()
    row = {"supplier": str(supplier.pk), "supplier_sku": "INLINE", "quantity_per_package": "1"}
    if shape is UNTOUCHED:
        shape = _rendered(
            _inline_formset_class(item)(instance=item).empty_form["average_lead_time"]
        )
    if shape is not ABSENT:
        row["average_lead_time"] = str(shape)
    formset = _inline_formset(item, [row])
    if not formset.is_valid():
        return None
    formset.save()
    link = ItemSupplier.objects.filter(item=item, supplier=supplier).first()
    return link.pk if link else None


def create_via_orm(client, shape):
    return ItemSupplier.objects.create(
        **terms_with(
            shape,
            item=InventoryItemFactory(image=None),
            supplier=SupplierFactory(),
            supplier_sku="ORM",
            quantity_per_package=1,
        )
    ).pk


def create_via_bulk_create(client, shape):
    """Bypasses ``save()``; must be refused rather than store an undecided row."""
    link = ItemSupplier(
        **terms_with(
            shape,
            item=InventoryItemFactory(image=None),
            supplier=SupplierFactory(),
            supplier_sku="BULK",
            quantity_per_package=1,
        )
    )
    try:
        ItemSupplier.objects.bulk_create([link])
    except TypeError:
        return None
    return ItemSupplier.objects.get(supplier_sku="BULK").pk


#: (driver, shape, how that path obtains a value from that shape).
CREATE_CASES = []
for _driver in (create_via_item_suppliers_api, create_via_kit_supplier_terms):
    # DRF IntegerField: a blank or unparseable value is a 400.
    CREATE_CASES += [
        (_driver, ABSENT, NOT_SUPPLIED),
        (_driver, "", REFUSED),
        (_driver, "soon", REFUSED),
        (_driver, 7, SUPPLIED),
        (_driver, 0, SUPPLIED),
        (_driver, 12, SUPPLIED),
    ]
CREATE_CASES += [
    # ``_sync_primary_supplier``: absent, blank, "null" and unparseable are
    # documented OMISSIONS on this path (same terms as its pack size and costs).
    (create_via_item_create_sync_primary_supplier, ABSENT, NOT_SUPPLIED),
    (create_via_item_create_sync_primary_supplier, "", NOT_SUPPLIED),
    (create_via_item_create_sync_primary_supplier, "null", NOT_SUPPLIED),
    (create_via_item_create_sync_primary_supplier, "soon", NOT_SUPPLIED),
    (create_via_item_create_sync_primary_supplier, 7, SUPPLIED),
    (create_via_item_create_sync_primary_supplier, "7", SUPPLIED),
    (create_via_item_create_sync_primary_supplier, 0, SUPPLIED),
    (create_via_item_create_sync_primary_supplier, 12, SUPPLIED),
]
for _driver in (create_via_admin_add_form, create_via_admin_item_inline):
    CREATE_CASES += [
        (_driver, UNTOUCHED, NOT_SUPPLIED),
        (_driver, ABSENT, NOT_SUPPLIED),
        (_driver, "", NOT_SUPPLIED),
        (_driver, "soon", REFUSED),
        (_driver, 7, SUPPLIED),
        (_driver, 0, SUPPLIED),
        (_driver, 12, SUPPLIED),
    ]
CREATE_CASES += [
    (create_via_orm, ABSENT, NOT_SUPPLIED),
    (create_via_orm, 7, SUPPLIED),
    (create_via_orm, 0, SUPPLIED),
    (create_via_bulk_create, ABSENT, REFUSED),
    (create_via_bulk_create, 7, REFUSED),
]


# ---------------------------------------------------------------------------
# UPDATE paths. Each takes an existing link and an input shape, and writes.
# The shape ECHO means "send the number already stored".
# ---------------------------------------------------------------------------

ECHO = object()


def _resolve(shape, link):
    return stored(link.pk)[1] if shape is ECHO else shape


def update_via_item_suppliers_patch(client, link, shape):
    client.patch(
        reverse("itemsupplier-detail", args=[link.pk]),
        # An unrelated edit rides along, so the save happens whatever the shape.
        terms_with(_resolve(shape, link), supplier_sku="PATCHED"),
        format="json",
    )


def update_via_item_suppliers_put(client, link, shape):
    client.put(
        reverse("itemsupplier-detail", args=[link.pk]),
        terms_with(
            _resolve(shape, link),
            item=str(link.item_id),
            supplier=link.supplier_id,
            supplier_sku="PUT",
            quantity_per_package=1,
            is_primary=True,
        ),
        format="json",
    )


def update_via_kit_supplier_terms(client, link, shape):
    # Any item with a link can be edited as a kit; a kit carries no stock.
    InventoryItem.objects.filter(pk=link.item_id).update(is_kit=True, current_stock=0)
    client.patch(
        reverse("kit-detail", args=[link.item_id]),
        {
            "supplier_terms": terms_with(
                _resolve(shape, link), supplier=link.supplier_id, supplier_sku="KIT-EDIT"
            )
        },
        format="json",
    )


def update_via_admin_change_form(client, link, shape):
    link = ItemSupplier.objects.get(pk=link.pk)
    form_class = ItemSupplierAdmin(ItemSupplier, AdminSite()).get_form(None, obj=link, change=True)
    if shape is UNTOUCHED:
        shape = _rendered(form_class(instance=link)["average_lead_time"])
    form = form_class(
        data=_admin_form_data(
            link.item,
            link.supplier,
            _resolve(shape, link),
            supplier_sku="ADMIN-EDIT",
            # The hidden version the page was rendered with, as a browser posts it.
            loaded_version=_rendered(form_class(instance=link)["loaded_version"]),
        ),
        instance=link,
    )
    if form.is_valid():
        form.save()


def update_via_admin_item_inline(client, link, shape):
    link = ItemSupplier.objects.get(pk=link.pk)
    unbound = _inline_formset_class(link.item)(instance=link.item)
    row = {
        # The hidden version the row was rendered with, as a browser posts it.
        "loaded_version": _rendered(unbound.forms[0]["loaded_version"]),
        "id": str(link.pk),
        "item": str(link.item_id),
        "supplier": str(link.supplier_id),
        "supplier_sku": "INLINE-EDIT",
        "quantity_per_package": "1",
        "is_primary": "on",
        "is_active": "on",
    }
    value = _resolve(shape, link)
    if value is UNTOUCHED:
        value = _rendered(unbound.forms[0]["average_lead_time"])
    if value is not ABSENT:
        row["average_lead_time"] = str(value)
    formset = _inline_formset(link.item, [row], initial=1)
    if formset.is_valid():
        formset.save()


def update_via_mark_discontinued(client, link, shape):
    """A save that does not mean to touch lead time at all."""
    client.post(reverse("itemsupplier-mark-discontinued", args=[link.pk]))


def update_via_orm_save(client, link, shape):
    link = ItemSupplier.objects.get(pk=link.pk)
    value = _resolve(shape, link)
    if value is not ABSENT:
        link.average_lead_time = value
    link.supplier_sku = "ORM-EDIT"
    link.save()


def update_via_orm_save_update_fields(client, link, shape):
    link = ItemSupplier.objects.get(pk=link.pk)
    link.average_lead_time = _resolve(shape, link)
    link.save(update_fields=["average_lead_time"])


def update_via_setting_the_source_directly(client, link, shape):
    """The provenance column is not an input. Asserting it must change nothing."""
    link = ItemSupplier.objects.get(pk=link.pk)
    value = _resolve(shape, link)
    if value is not ABSENT:
        link.average_lead_time = value
    link.average_lead_time_source = "recorded"
    link.save()
    client.patch(
        reverse("itemsupplier-detail", args=[link.pk]),
        {"average_lead_time_source": "recorded"},
        format="json",
    )


def update_via_restricted_save_naming_only_the_source(client, link, shape):
    """A save restricted to the source column writes a source nobody decided."""
    link = ItemSupplier.objects.get(pk=link.pk)
    link.average_lead_time_source = "recorded"
    link.supplier_sku = "RESTRICTED"
    link.save(update_fields=["average_lead_time_source", "supplier_sku"])


def update_via_queryset_update(client, link, shape):
    try:
        ItemSupplier.objects.filter(pk=link.pk).update(average_lead_time=_resolve(shape, link))
    except TypeError:
        pass


def update_via_related_manager_update(client, link, shape):
    try:
        link.item.item_suppliers.update(average_lead_time=_resolve(shape, link))
    except TypeError:
        pass


def update_via_bulk_update(client, link, shape):
    link = ItemSupplier.objects.get(pk=link.pk)
    link.average_lead_time = _resolve(shape, link)
    try:
        ItemSupplier.objects.bulk_update([link], ["average_lead_time"])
    except TypeError:
        pass


def update_via_measuring_task(client, link, shape):
    """``update_average_lead_times``: the value is MEASURED, never quoted."""
    ordered = timezone.now() - timedelta(days=20)
    ReorderRequestFactory(
        item=link.item,
        status=ReorderRequest.Status.RECEIVED,
        ordered_at=ordered,
        actual_delivery=(ordered + timedelta(days=shape)).date(),
    )
    update_average_lead_times()


#: (driver, shape, how that path obtains a value from that shape).
UPDATE_CASES = []
for _driver in (
    update_via_item_suppliers_patch,
    update_via_item_suppliers_put,
    update_via_kit_supplier_terms,
):
    UPDATE_CASES += [
        (_driver, ABSENT, NOT_SUPPLIED),
        (_driver, ECHO, ECHOED),
        (_driver, 30, SUPPLIED),
        (_driver, 0, SUPPLIED),
        (_driver, "soon", REFUSED),
    ]
for _driver in (update_via_admin_change_form, update_via_admin_item_inline):
    UPDATE_CASES += [
        (_driver, UNTOUCHED, ECHOED),
        (_driver, ABSENT, NOT_SUPPLIED),
        (_driver, "", NOT_SUPPLIED),
        (_driver, ECHO, ECHOED),
        (_driver, 30, SUPPLIED),
        (_driver, "soon", REFUSED),
    ]
UPDATE_CASES += [
    (update_via_mark_discontinued, ABSENT, NOT_SUPPLIED),
    (update_via_orm_save, ABSENT, NOT_SUPPLIED),
    (update_via_orm_save, ECHO, ECHOED),
    (update_via_orm_save, 30, SUPPLIED),
    (update_via_orm_save_update_fields, ECHO, ECHOED),
    (update_via_orm_save_update_fields, 30, SUPPLIED),
    (update_via_setting_the_source_directly, ABSENT, NOT_SUPPLIED),
    (update_via_setting_the_source_directly, ECHO, ECHOED),
    (update_via_restricted_save_naming_only_the_source, ABSENT, NOT_SUPPLIED),
    (update_via_queryset_update, 30, REFUSED),
    (update_via_related_manager_update, 30, REFUSED),
    (update_via_bulk_update, 30, REFUSED),
    (update_via_measuring_task, 9, MEASURED),
    (update_via_measuring_task, 30, MEASURED),
]


def _case_id(case):
    driver, shape, _ = case
    label = {ABSENT: "absent", ECHO: "echo", UNTOUCHED: "untouched"}.get(shape, repr(shape))
    return f"{driver.__name__}[{label}]"


# ---------------------------------------------------------------------------
# The invariant.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CREATE_CASES, ids=_case_id)
def test_no_create_path_stores_a_source_that_disagrees_with_how_the_value_was_obtained(
    client, case
):
    driver, shape, obtained = case

    pk = driver(client, shape)

    if obtained == REFUSED:
        assert pk is None, "a refused write stored a row"
        return
    assert pk is not None, "the write path refused a write it should accept"
    supplied = int(shape) if obtained == SUPPLIED else None
    assert stored(pk) == expected(obtained, None, supplied)


@pytest.mark.parametrize("prior", PRIOR_STATES, ids=lambda p: f"was-{p[0]}-{p[1]}")
@pytest.mark.parametrize("case", UPDATE_CASES, ids=_case_id)
def test_no_update_path_stores_a_source_that_disagrees_with_how_the_value_was_obtained(
    client, case, prior
):
    driver, shape, obtained = case
    link = seed_link(InventoryItemFactory(image=None), SupplierFactory(), prior)

    driver(client, link, shape)

    supplied = shape if obtained in (SUPPLIED, MEASURED) else None
    assert stored(link.pk) == expected(obtained, prior, supplied)


def test_every_driver_is_exercised():
    """A driver added above but left out of the case tables tests nothing."""
    drivers = {
        value
        for name, value in globals().items()
        if callable(value) and (name.startswith("create_via_") or name.startswith("update_via_"))
    }
    exercised = {case[0] for case in CREATE_CASES + UPDATE_CASES}
    assert drivers == exercised


def test_an_orm_expression_is_refused_before_it_can_separate_value_and_source():
    link = seed_link(InventoryItemFactory(image=None), SupplierFactory(), ("recorded", 12))
    link.average_lead_time = F("average_lead_time") + 1

    with pytest.raises(TypeError, match="must be an integer value"):
        link.save(update_fields=["average_lead_time"])

    assert stored(link.pk) == ("recorded", 12)


def test_the_source_reaches_the_api_beside_every_stored_value_it_describes(client):
    """Read-side contract: the source travels with the value, and is vendor-gated like it."""
    item = InventoryItemFactory(image=None, is_primary=False)
    link = seed_link(item, SupplierFactory(), ("default", 7))

    row = client.get(reverse("itemsupplier-detail", args=[link.pk])).data
    assert (row["average_lead_time_source"], row["average_lead_time"]) == ("default", 7)

    payload = client.get(reverse("inventoryitem-detail", args=[item.pk])).data
    assert (payload["average_lead_time_source"], payload["average_lead_time"]) == ("default", 7)
    nested = {entry["id"]: entry["average_lead_time_source"] for entry in payload["suppliers"]}
    assert nested[link.pk] == "default"

    response = APIClient().get(reverse("inventoryitem-detail", args=[item.pk]))
    assert response.status_code == 200
    anonymous = response.data
    assert anonymous["vendor_data_withheld"] is True
    assert "average_lead_time" not in anonymous
    assert "average_lead_time_source" not in anonymous
