"""Kit definition, validation and visibility (op-8n0).

One test per acceptance criterion in ``.criteria/kits.md``; each is named for
the AC it proves. Receiving and purchase-order behaviour live in
``reorder_queue/tests/test_kit_receiving.py`` (AC-22..AC-32).
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import InventoryItem, KitComponent

from .factories import InventoryItemFactory, ItemSupplierFactory, SupplierFactory


@pytest.fixture
def staff_client(django_user_model):
    """An authenticated client — kits are staff-writable, publicly readable."""
    user = django_user_model.objects.create_user(username="kit-staff", password="pw", is_staff=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def anon_client():
    return APIClient()


@pytest.fixture
def ink_components(db):
    """The five items an Eufy Ink Kit contains."""
    return [
        InventoryItemFactory(name=name, is_kit=False, is_serialized=False, image=None)
        for name in ("Cyan", "Magenta", "Yellow", "Black", "Cleaning Kit")
    ]


@pytest.fixture
def eufy_kit(db, ink_components):
    """A saved Eufy Ink Kit containing one of each cartridge."""
    kit = InventoryItemFactory(
        name="Eufy Ink Kit", is_kit=True, current_stock=0, minimum_stock=0, image=None
    )
    for component in ink_components:
        KitComponent.objects.create(kit=kit, component=component, quantity=1)
    return kit


def field_errors(response):
    """Field-addressed errors, unwrapped from the project's error envelope."""
    data = response.data
    if isinstance(data, dict) and "error" in data:
        return data["error"].get("details", {})
    return data


def kit_payload(components, **overrides):
    payload = {
        "name": "Eufy Ink Kit",
        "description": "CMYK + cleaning cartridges",
        "current_stock": 0,
        "minimum_stock": 0,
        "reorder_quantity": 1,
        "components": [{"component": component.pk, "quantity": 1} for component in components],
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestKitDefinition:
    """AC-1, AC-5..AC-13 — creating and editing a kit through the API."""

    def test_ac1_staff_can_create_a_purchasable_kit_sku(self, staff_client, ink_components):
        supplier = SupplierFactory()
        response = staff_client.post(
            reverse("kit-list"),
            kit_payload(
                ink_components,
                supplier_terms={
                    "supplier": supplier.pk,
                    "supplier_sku": "T3200",
                    "unit_cost": "89.99",
                },
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        body = response.data
        assert body["is_kit"] is True
        # A kit is bought on behalf of a component, never requested directly.
        assert body["is_requestable"] is False
        # Receiving credits the components, so the kit itself is never stocked.
        assert body["current_stock"] == 0
        assert body["supplier_sku"] == "T3200"
        assert Decimal(str(body["unit_cost"])) == Decimal("89.99")
        assert len(body["components"]) == 5
        assert {row["component_name"] for row in body["components"]} == {
            component.name for component in ink_components
        }

        kit = InventoryItem.objects.get(pk=body["id"])
        assert kit.is_kit is True
        assert kit.kit_components.count() == 5

    def test_ac5_kit_cannot_be_serialized(self, staff_client, ink_components):
        response = staff_client.post(
            reverse("kit-list"),
            kit_payload(ink_components, is_serialized=True),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "is_serialized" in field_errors(response)
        assert not InventoryItem.objects.filter(is_kit=True).exists()

    def test_ac5_kit_cannot_carry_stock(self, staff_client, ink_components):
        response = staff_client.post(
            reverse("kit-list"),
            kit_payload(ink_components, current_stock=4),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "current_stock" in field_errors(response)
        assert not InventoryItem.objects.filter(is_kit=True).exists()

    def test_ac6_kit_requires_at_least_one_component_on_create(self, staff_client):
        response = staff_client.post(reverse("kit-list"), kit_payload([]), format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "components" in field_errors(response)
        assert not InventoryItem.objects.filter(is_kit=True).exists()

    def test_ac6_kit_cannot_be_emptied_by_update(self, staff_client, eufy_kit):
        response = staff_client.patch(
            reverse("kit-detail", args=[eufy_kit.pk]),
            {"components": []},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "components" in field_errors(response)
        assert eufy_kit.kit_components.count() == 5

    @pytest.mark.parametrize("quantity", [0, -3])
    def test_ac7_component_quantity_must_be_positive(self, staff_client, eufy_kit, quantity):
        first = eufy_kit.kit_components.first()
        response = staff_client.patch(
            reverse("kit-detail", args=[eufy_kit.pk]),
            {"components": [{"component": first.component_id, "quantity": quantity}]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        first.refresh_from_db()
        assert first.quantity == 1

    def test_ac8_kit_cannot_contain_itself_via_api(self, staff_client, eufy_kit):
        response = staff_client.patch(
            reverse("kit-detail", args=[eufy_kit.pk]),
            {"components": [{"component": eufy_kit.pk, "quantity": 1}]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not eufy_kit.kit_components.filter(component=eufy_kit).exists()

    def test_ac8_self_reference_is_refused_by_the_database(self, eufy_kit):
        """The DB constraint, not just ``clean()`` — bulk writes skip validation.

        A self-referencing row would make a receipt credit the kit's own stock,
        so this has to hold even for a path that never calls ``full_clean``.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                KitComponent.objects.bulk_create(
                    [KitComponent(kit=eufy_kit, component=eufy_kit, quantity=1)]
                )

    def test_ac7_nonpositive_quantity_is_refused_by_the_database(self, eufy_kit, ink_components):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                KitComponent.objects.bulk_create(
                    [KitComponent(kit=eufy_kit, component=ink_components[0], quantity=0)]
                )

    def test_ac9_kit_cannot_contain_another_kit(self, staff_client, eufy_kit, ink_components):
        other = InventoryItemFactory(name="Bundle", is_kit=True, current_stock=0, image=None)
        KitComponent.objects.create(kit=other, component=ink_components[0], quantity=1)

        response = staff_client.patch(
            reverse("kit-detail", args=[eufy_kit.pk]),
            {"components": [{"component": other.pk, "quantity": 1}]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not KitComponent.objects.filter(kit=eufy_kit, component=other).exists()

    def test_ac10_serialized_components_are_now_accepted(self, staff_client, eufy_kit):
        """AC-10 REVERSED (oms-po-receiving).

        A serialized component used to be refused here and at the model,
        because receiving the kit would have credited its stock and recorded no
        serial numbers. Receiving can now capture a serial against the
        component — and refuses one aimed at the kit — so the refusal guarded
        against nothing while blocking a real configuration: a kit whose parts
        happen to be serial-tracked.

        The gap that rule was really about is reported instead, as a line's
        ``serials_outstanding``. See
        ``reorder_queue/tests/test_po_receiving_workflow.py``.
        """
        serialized = InventoryItemFactory(name="Serialized Board", is_serialized=True, image=None)
        response = staff_client.patch(
            reverse("kit-detail", args=[eufy_kit.pk]),
            {"components": [{"component": serialized.pk, "quantity": 1}]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert KitComponent.objects.filter(kit=eufy_kit, component=serialized).exists()

    def test_ac10_serialized_component_accepted_at_the_model_too(self, eufy_kit):
        serialized = InventoryItemFactory(name="Serialized Board", is_serialized=True, image=None)

        KitComponent(kit=eufy_kit, component=serialized, quantity=1).save()

        assert eufy_kit.kit_components.filter(component=serialized).exists()

    def test_ac10_the_kit_itself_is_still_never_serialized(self, staff_client, eufy_kit):
        """The rule that did NOT move.

        "A kit may contain serialized parts" and "a kit is itself serialized"
        are different claims. The second remains false: a kit never enters
        stock, so a serial against it names a unit nothing can draw down.
        """
        response = staff_client.patch(
            reverse("kit-detail", args=[eufy_kit.pk]),
            {"is_serialized": True},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "is_serialized" in field_errors(response)
        eufy_kit.refresh_from_db()
        assert eufy_kit.is_serialized is False

    def test_ac11_duplicate_components_are_rejected(self, staff_client, eufy_kit, ink_components):
        cyan = ink_components[0]
        response = staff_client.patch(
            reverse("kit-detail", args=[eufy_kit.pk]),
            {
                "components": [
                    {"component": cyan.pk, "quantity": 1},
                    {"component": cyan.pk, "quantity": 2},
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert eufy_kit.kit_components.filter(component=cyan).count() == 1

    def test_ac12_components_are_protected_from_deletion(self, eufy_kit, ink_components):
        """PROTECT, not CASCADE — a silent delete would change what a receipt credits."""
        cyan = ink_components[0]
        with pytest.raises(ProtectedError):
            with transaction.atomic():
                cyan.delete()

        assert InventoryItem.objects.filter(pk=cyan.pk).exists()
        assert eufy_kit.kit_components.count() == 5

    def test_ac12_component_deletion_is_refused_through_the_api(
        self, staff_client, eufy_kit, ink_components
    ):
        cyan = ink_components[0]
        with pytest.raises(ProtectedError):
            with transaction.atomic():
                staff_client.delete(reverse("inventoryitem-detail", args=[cyan.pk]))
        assert InventoryItem.objects.filter(pk=cyan.pk).exists()

    def test_ac13_surviving_rows_keep_their_identity(self, staff_client, eufy_kit, ink_components):
        """Upsert on the natural key, not delete-and-recreate."""
        cyan, magenta = ink_components[0], ink_components[1]
        cyan_row_id = eufy_kit.kit_components.get(component=cyan).pk
        added = InventoryItemFactory(name="Photo Black", image=None)

        keep = [
            {"component": component.pk, "quantity": 1}
            for component in ink_components
            if component.pk != magenta.pk
        ]
        for row in keep:
            if row["component"] == cyan.pk:
                row["quantity"] = 4
        keep.append({"component": added.pk, "quantity": 1})

        response = staff_client.patch(
            reverse("kit-detail", args=[eufy_kit.pk]), {"components": keep}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        rows = {row["component_name"]: row for row in response.data["components"]}
        # A survives with its ORIGINAL row id and the new quantity...
        assert rows["Cyan"]["id"] == cyan_row_id
        assert rows["Cyan"]["quantity"] == 4
        # ...B is gone, C is new.
        assert "Magenta" not in rows
        assert "Photo Black" in rows


@pytest.mark.django_db
class TestKitApiSurface:
    """AC-2, AC-3, AC-4, AC-15 — permissions, filters and route shape."""

    def test_ac2_anonymous_can_read_but_not_write(self, anon_client, eufy_kit, ink_components):
        assert anon_client.get(reverse("kit-list")).status_code == status.HTTP_200_OK
        assert (
            anon_client.get(reverse("kit-detail", args=[eufy_kit.pk])).status_code
            == status.HTTP_200_OK
        )

        create = anon_client.post(reverse("kit-list"), kit_payload(ink_components), format="json")
        assert create.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

        original_name = eufy_kit.name
        update = anon_client.patch(
            reverse("kit-detail", args=[eufy_kit.pk]), {"name": "Hacked"}, format="json"
        )
        assert update.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        eufy_kit.refresh_from_db()
        assert eufy_kit.name == original_name

    def test_ac3_list_filters_and_default_ordering(self, anon_client, eufy_kit, ink_components):
        supplier = SupplierFactory(name="Eufy Direct")
        ItemSupplierFactory(item=eufy_kit, supplier=supplier, supplier_sku="T3200")

        other_supplier = SupplierFactory(name="Other Co")
        alpha = InventoryItemFactory(name="Alpha Kit", is_kit=True, current_stock=0, image=None)
        KitComponent.objects.create(kit=alpha, component=ink_components[0], quantity=1)
        ItemSupplierFactory(item=alpha, supplier=other_supplier)
        inactive = InventoryItemFactory(
            name="Retired Kit", is_kit=True, is_active=False, current_stock=0, image=None
        )
        KitComponent.objects.create(kit=inactive, component=ink_components[1], quantity=1)

        def names(response):
            data = response.data
            rows = data["results"] if isinstance(data, dict) and "results" in data else data
            return [row["name"] for row in rows]

        # Default ordering is by name.
        listed = names(anon_client.get(reverse("kit-list")))
        assert listed == sorted(listed)
        assert "Alpha Kit" in listed and "Eufy Ink Kit" in listed

        assert names(anon_client.get(reverse("kit-list"), {"search": "Eufy"})) == ["Eufy Ink Kit"]
        assert "Retired Kit" not in names(
            anon_client.get(reverse("kit-list"), {"is_active": "true"})
        )
        assert names(anon_client.get(reverse("kit-list"), {"supplier": supplier.pk})) == [
            "Eufy Ink Kit"
        ]
        by_component = names(
            anon_client.get(reverse("kit-list"), {"component": ink_components[1].pk})
        )
        assert "Eufy Ink Kit" in by_component
        assert "Alpha Kit" not in by_component

    def test_ac4_no_standalone_kit_component_endpoint(self, staff_client):
        """The bill of materials is nested-writable only, like packaging levels."""
        from django.urls import get_resolver

        patterns = {str(pattern.pattern) for pattern in get_resolver().url_patterns}
        assert not any("kit-component" in pattern for pattern in patterns)

        response = staff_client.get("/api/inventory/kit-components/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_ac15_component_detail_lists_supplying_kits_anonymously(
        self, anon_client, eufy_kit, ink_components
    ):
        """Still anonymous — "which kits supply this cartridge?" is reorder
        triage beside stock — but the kit's VENDOR keys are withheld
        (op-anonymous-read-posture). Which kit and how many it holds is the
        answer the card exists to give, and that is unchanged.
        """
        supplier = SupplierFactory(name="Eufy Direct")
        ItemSupplierFactory(
            item=eufy_kit, supplier=supplier, supplier_sku="T3200", unit_cost=Decimal("89.99")
        )
        cyan = ink_components[0]

        response = anon_client.get(reverse("inventoryitem-kits", args=[cyan.pk]))

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        row = response.data[0]
        assert row["name"] == "Eufy Ink Kit"
        assert row["quantity_in_kit"] == 1
        assert row["component_count"] == 5
        assert "supplier_sku" not in row
        assert "supplier_name" not in row
        assert "unit_cost" not in row
        assert row["vendor_data_withheld"] is True
        assert b"Eufy Direct" not in response.content
        assert b"T3200" not in response.content

    def test_ac15_a_signed_in_caller_still_gets_the_kit_supplier_keys(
        self, api_client, django_user_model, eufy_kit, ink_components
    ):
        """CONTROL for the assertions removed above: withheld, not deleted."""
        from django.utils.crypto import get_random_string

        supplier = SupplierFactory(name="Eufy Direct")
        ItemSupplierFactory(
            item=eufy_kit, supplier=supplier, supplier_sku="T3200", unit_cost=Decimal("89.99")
        )
        api_client.force_authenticate(
            user=django_user_model.objects.create_user(
                username="kit-reader", password=get_random_string(24)
            )
        )

        response = api_client.get(reverse("inventoryitem-kits", args=[ink_components[0].pk]))

        assert response.status_code == status.HTTP_200_OK
        row = response.data[0]
        assert row["supplier_sku"] == "T3200"
        assert row["supplier_name"] == "Eufy Direct"
        assert "vendor_data_withheld" not in row

    def test_ac15_item_in_no_kits_returns_empty(self, anon_client):
        lonely = InventoryItemFactory(name="Unbundled", image=None)
        response = anon_client.get(reverse("inventoryitem-kits", args=[lonely.pk]))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == []


@pytest.mark.django_db
class TestKitVisibility:
    """AC-14, AC-16 — kits stay out of the ordinary item surfaces."""

    def test_ac14_item_list_hides_kits_by_default(self, anon_client, eufy_kit, ink_components):
        def ids(response):
            data = response.data
            rows = data["results"] if isinstance(data, dict) and "results" in data else data
            return {row["id"] for row in rows}

        url = reverse("inventoryitem-list")
        kit_id, cyan_id = str(eufy_kit.pk), str(ink_components[0].pk)

        default = ids(anon_client.get(url, {"page_size": 200}))
        assert kit_id not in default
        assert cyan_id in default

        included = ids(anon_client.get(url, {"include_kits": "true", "page_size": 200}))
        assert kit_id in included
        assert cyan_id in included

        only_kits = ids(anon_client.get(url, {"is_kit": "true", "page_size": 200}))
        assert only_kits == {kit_id}

    def test_ac16_kits_never_need_reorder(self, eufy_kit):
        """Stock/minimum values that would make an ordinary item look low."""
        eufy_kit.minimum_stock = 10
        assert eufy_kit.current_stock == 0
        assert eufy_kit.needs_reorder is False

        ordinary = InventoryItemFactory(current_stock=0, minimum_stock=10, image=None)
        assert ordinary.needs_reorder is True

    def test_ac16_kit_is_never_requestable(self, staff_client, ink_components):
        response = staff_client.post(
            reverse("kit-list"),
            kit_payload(ink_components, is_requestable=True),
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["is_requestable"] is False
        assert InventoryItem.objects.get(pk=response.data["id"]).is_requestable is False


@pytest.mark.django_db
class TestKitTasks:
    """AC-20, AC-21 — the periodic tasks skip kits."""

    def test_ac20_stock_snapshots_exclude_kits(self, eufy_kit, ink_components):
        from inventory.models import StockLevelSnapshot
        from inventory.tasks import snapshot_stock_levels

        snapshot_stock_levels()

        snapshotted = set(StockLevelSnapshot.objects.values_list("item_id", flat=True))
        assert eufy_kit.pk not in snapshotted
        assert ink_components[0].pk in snapshotted

    def test_ac21_demand_forecast_excludes_kits(self, eufy_kit, ink_components, monkeypatch):
        """The forecast task must not even consider a kit."""
        from inventory.services import component_forecast

        seen = {}

        def fake_lead_times(items):
            seen["items"] = list(items)
            return {}

        monkeypatch.setattr(component_forecast, "lead_times_for", fake_lead_times)

        from inventory.tasks import generate_demand_forecasts

        generate_demand_forecasts()

        considered = {item.pk for item in seen.get("items", [])}
        assert eufy_kit.pk not in considered
        assert ink_components[0].pk in considered


@pytest.mark.django_db
class TestKitCreditsService:
    """The pure generator behind both the PO preview and the receipt."""

    def test_credits_scale_with_kit_quantity(self, eufy_kit):
        from inventory.services.kits import kit_component_credits

        credits = list(kit_component_credits(eufy_kit, 2))
        assert len(credits) == 5
        assert all(credit.quantity_per_kit == 1 for credit in credits)
        assert all(credit.quantity == 2 for credit in credits)

    def test_non_kit_and_nonpositive_quantities_yield_nothing(self, eufy_kit):
        from inventory.services.kits import kit_component_credits

        ordinary = InventoryItemFactory(image=None)
        assert list(kit_component_credits(ordinary, 2)) == []
        assert list(kit_component_credits(eufy_kit, 0)) == []
        assert list(kit_component_credits(eufy_kit, -1)) == []


@pytest.mark.django_db
def test_ac48_backend_runs_django_6():
    """AC-48 — the docs claim Django 6.0.7; assert the runtime agrees."""
    import django

    assert django.VERSION[0] >= 6


@pytest.mark.django_db
def test_kit_model_str_and_defaults(eufy_kit, ink_components):
    row = eufy_kit.kit_components.get(component=ink_components[0])
    assert "Eufy Ink Kit" in str(row)
    assert "Cyan" in str(row)


@pytest.mark.django_db
def test_kit_list_query_count_is_bounded(
    anon_client, ink_components, django_assert_max_num_queries
):
    """The kits list must not scale its query count with the number of kits.

    ``KitSerializer`` inherits the full ``InventoryItemSerializer`` field set,
    so ``KitViewSet.get_queryset`` has to mirror the item viewset's joins.
    Measured flat at 9 queries for 1, 5 and 10 kits; without the mirrored
    prefetches it was 11/31/56 — about 5 per kit. This is the bound quoted in
    ``docs/API_LIST_CONTRACT.md``; if it moves, update that row too.
    """
    for index in range(5):
        kit = InventoryItemFactory(name=f"Kit {index}", is_kit=True, current_stock=0, image=None)
        for component in ink_components:
            KitComponent.objects.create(kit=kit, component=component, quantity=1)
        ItemSupplierFactory(item=kit, supplier=SupplierFactory())

    with django_assert_max_num_queries(12):
        response = anon_client.get(reverse("kit-list"), {"page_size": 100})
    assert response.status_code == status.HTTP_200_OK
