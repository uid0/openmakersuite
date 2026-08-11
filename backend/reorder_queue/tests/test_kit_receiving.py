"""Buying and receiving kit lines (op-8n0), AC-19 and AC-22..AC-32.

The half of the kit slice that touches purchase orders: one line per kit, a
component preview that matches what the receipt applies, decomposition on
receipt, and the guards around both.

There is no ``PurchaseOrderFactory`` in this app's factories (only
``UserFactory`` and ``ReorderRequestFactory``), so purchase orders are built
directly here.
"""

from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import InventoryItem, ItemSupplier, KitComponent
from inventory.tests.factories import InventoryItemFactory, SupplierFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem, ReorderRequest


@pytest.fixture
def receiver(django_user_model):
    return django_user_model.objects.create_user(
        username="kit-receiver", password="pw", is_staff=True, is_superuser=True
    )


@pytest.fixture
def client(receiver):
    api = APIClient()
    api.force_authenticate(user=receiver)
    return api


@pytest.fixture
def supplier(db):
    return SupplierFactory(name="Eufy Direct")


@pytest.fixture
def components(db):
    return [
        InventoryItemFactory(name=name, current_stock=0, minimum_stock=5, image=None)
        for name in ("Cyan", "Magenta", "Yellow", "Black", "Cleaning Kit")
    ]


@pytest.fixture
def kit(db, components, supplier):
    """The Eufy Ink Kit: SKU T3200, $89.99, one of each cartridge."""
    kit = InventoryItemFactory(
        name="Eufy Ink Kit", is_kit=True, current_stock=0, minimum_stock=0, image=None
    )
    for component in components:
        KitComponent.objects.create(kit=kit, component=component, quantity=1)
    ItemSupplier.objects.create(
        item=kit,
        supplier=supplier,
        supplier_sku="T3200",
        unit_cost=Decimal("89.99"),
        quantity_per_package=1,
        is_primary=True,
    )
    return kit


def make_po(supplier, created_by, status_value=PurchaseOrder.Status.SENT):
    return PurchaseOrder.objects.create(
        supplier=supplier,
        status=status_value,
        order_date=timezone.now(),
        created_by=created_by,
    )


def add_kit_line(purchase_order, kit, quantity=2):
    return PurchaseOrderItem.objects.create(
        purchase_order=purchase_order,
        item_supplier=kit.item_suppliers.first(),
        quantity_ordered=quantity,
        unit_cost_ordered=Decimal("89.99"),
    )


def receive(client, purchase_order, po_item, quantity):
    return client.post(
        reverse("purchaseorder-receive", args=[purchase_order.pk]),
        {
            "items": [{"purchase_order_item": str(po_item.pk), "quantity_received": quantity}],
            "delivery_date": timezone.now().date().isoformat(),
        },
        format="json",
    )


@pytest.mark.django_db
class TestKitPurchaseOrderLines:
    """AC-23, AC-24, AC-25 — one line, and a preview that tells the truth."""

    def test_ac23_ordering_kits_creates_one_line(self, kit, supplier, components, receiver):
        purchase_order = make_po(supplier, receiver)
        line = add_kit_line(purchase_order, kit, quantity=2)

        assert purchase_order.items.count() == 1
        assert line.quantity_ordered == 2
        assert line.estimated_cost == Decimal("179.98")
        # Not five component lines.
        assert not PurchaseOrderItem.objects.filter(item_supplier__item__in=components).exists()

    def test_ac24_kit_line_exposes_a_preview_and_ordinary_lines_do_not(
        self, client, kit, supplier, components, receiver
    ):
        purchase_order = make_po(supplier, receiver)
        add_kit_line(purchase_order, kit, quantity=2)

        ordinary = InventoryItemFactory(name="Copy Paper", image=None)
        ordinary_supplier = ItemSupplier.objects.create(
            item=ordinary, supplier=supplier, supplier_sku="PAPER", quantity_per_package=1
        )
        PurchaseOrderItem.objects.create(
            purchase_order=purchase_order,
            item_supplier=ordinary_supplier,
            quantity_ordered=3,
            unit_cost_ordered=Decimal("10.00"),
        )

        response = client.get(reverse("purchaseorder-detail", args=[purchase_order.pk]))
        assert response.status_code == status.HTTP_200_OK

        lines = {row["item_details"]["name"]: row for row in response.data["items"]}
        kit_line = lines["Eufy Ink Kit"]
        assert kit_line["is_kit_line"] is True
        assert len(kit_line["kit_components"]) == 5
        assert all(row["quantity"] == 2 for row in kit_line["kit_components"])

        paper_line = lines["Copy Paper"]
        assert paper_line["is_kit_line"] is False
        # None, not [] — the payload for a kit-free PO stays byte-identical.
        assert paper_line["kit_components"] is None

    def test_ac25_preview_matches_the_receipt_effect(
        self, client, kit, supplier, components, receiver
    ):
        purchase_order = make_po(supplier, receiver)
        line = add_kit_line(purchase_order, kit, quantity=2)

        detail = client.get(reverse("purchaseorder-detail", args=[purchase_order.pk]))
        preview = {
            row["component_name"]: row["quantity"]
            for row in detail.data["items"][0]["kit_components"]
        }

        before = {component.name: component.current_stock for component in components}
        assert receive(client, purchase_order, line, 2).status_code == status.HTTP_200_OK

        for component in components:
            component.refresh_from_db()
            delta = component.current_stock - before[component.name]
            assert delta == preview[component.name]


@pytest.mark.django_db
class TestKitReceiving:
    """AC-26..AC-31 — decomposition, partials, guards and the ledger."""

    def test_ac26_receiving_credits_components_not_the_kit(
        self, client, kit, supplier, components, receiver
    ):
        purchase_order = make_po(supplier, receiver)
        line = add_kit_line(purchase_order, kit, quantity=2)

        assert receive(client, purchase_order, line, 2).status_code == status.HTTP_200_OK

        for component in components:
            component.refresh_from_db()
            assert component.current_stock == 2

        kit.refresh_from_db()
        assert kit.current_stock == 0

    def test_ac27_partial_receipts_are_additive(self, client, kit, supplier, components, receiver):
        purchase_order = make_po(supplier, receiver)
        line = add_kit_line(purchase_order, kit, quantity=3)

        assert receive(client, purchase_order, line, 1).status_code == status.HTTP_200_OK
        for component in components:
            component.refresh_from_db()
            assert component.current_stock == 1

        assert receive(client, purchase_order, line, 2).status_code == status.HTTP_200_OK
        for component in components:
            component.refresh_from_db()
            # 1 then 2 — the second receipt must not re-count the first.
            assert component.current_stock == 3

        kit.refresh_from_db()
        assert kit.current_stock == 0

    def test_ac28_over_receipt_is_rejected_before_stock_changes(
        self, client, kit, supplier, components, receiver
    ):
        purchase_order = make_po(supplier, receiver)
        line = add_kit_line(purchase_order, kit, quantity=2)
        assert receive(client, purchase_order, line, 1).status_code == status.HTTP_200_OK

        response = receive(client, purchase_order, line, 5)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        for component in components:
            component.refresh_from_db()
            assert component.current_stock == 1
        kit.refresh_from_db()
        assert kit.current_stock == 0

    def test_ac29_empty_breakdown_does_not_break_receiving(
        self, client, kit, supplier, components, caplog, receiver
    ):
        """The goods physically arrived — refusing to record that is worse."""
        purchase_order = make_po(supplier, receiver)
        line = add_kit_line(purchase_order, kit, quantity=2)
        kit.kit_components.all().delete()

        with caplog.at_level("WARNING"):
            response = receive(client, purchase_order, line, 2)

        assert response.status_code == status.HTTP_200_OK
        for component in components:
            component.refresh_from_db()
            assert component.current_stock == 0
        kit.refresh_from_db()
        assert kit.current_stock == 0
        assert any("empty bill of materials" in record.message for record in caplog.records)

    def test_ac30_full_receipt_closes_component_reorder_requests(
        self, client, kit, supplier, components, receiver
    ):
        purchase_order = make_po(supplier, receiver)
        line = add_kit_line(purchase_order, kit, quantity=2)

        requests = [
            ReorderRequest.objects.create(
                item=component,
                quantity=1,
                requested_by=receiver,
                status=ReorderRequest.Status.ORDERED,
            )
            for component in components
        ]

        # A partial receipt leaves them open...
        assert receive(client, purchase_order, line, 1).status_code == status.HTTP_200_OK
        for request in requests:
            request.refresh_from_db()
            assert request.status == ReorderRequest.Status.ORDERED

        # ...and completing the line closes every component's.
        assert receive(client, purchase_order, line, 1).status_code == status.HTTP_200_OK
        for request in requests:
            request.refresh_from_db()
            assert request.status == ReorderRequest.Status.RECEIVED

    def test_ac31_ledger_records_one_entry_for_the_kit_sku(
        self, client, kit, supplier, components, django_user_model, receiver
    ):
        """One posting for the SKU that was bought, none per component."""
        from django.contrib.auth.models import Group

        committee = Group.objects.create(name="Print Committee")
        kit.owning_group = committee
        kit.save()

        purchase_order = make_po(supplier, receiver)
        line = add_kit_line(purchase_order, kit, quantity=2)

        postings = []

        import accounting.adapters as adapters

        original = adapters.post_po_receipt

        def spy(**kwargs):
            postings.append(kwargs)
            return original(**kwargs)

        adapters.post_po_receipt = spy
        try:
            assert receive(client, purchase_order, line, 2).status_code == status.HTTP_200_OK
        finally:
            adapters.post_po_receipt = original

        assert len(postings) == 1
        posting = postings[0]
        assert posting["item"].pk == kit.pk
        assert posting["committee"] == committee
        assert posting["amount"] == Decimal("179.98")
        # No component was posted a cost.
        assert not any(
            call["item"].pk in {component.pk for component in components} for call in postings
        )

    def test_ac32_barcode_receiving_fails_loud_for_kit_lines(
        self, client, kit, supplier, components, receiver
    ):
        purchase_order = make_po(supplier, receiver)
        line = add_kit_line(purchase_order, kit, quantity=2)
        item_supplier = kit.item_suppliers.first()
        item_supplier.package_upc = "0123456789012"
        item_supplier.save()

        response = client.post(
            reverse("orderdelivery-scan-barcode"),
            {
                "purchase_order_id": purchase_order.pk,
                "scanned_upc": "0123456789012",
                "quantity_received": 1,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        for component in components:
            component.refresh_from_db()
            assert component.current_stock == 0
        kit.refresh_from_db()
        assert kit.current_stock == 0
        line.refresh_from_db()
        assert line.quantity_received == 0


@pytest.mark.django_db
class TestReorderDataKitContext:
    """AC-17, AC-18, AC-19, AC-22 — kits in triage: visible, never actionable."""

    def test_ac17_optimized_order_excludes_kits(self, client, kit, supplier, components):
        # NOTE: every ItemSupplier here deliberately leaves ``unit_cost`` unset.
        # ``_find_best_supplier`` (reorder_queue/views.py, unchanged by this
        # work) computes ``cost_factor`` in Decimal and then multiplies it by
        # the float 0.4, so scoring ANY supplier that has a unit cost raises
        # TypeError. That bug predates kits and is out of scope here (the
        # criteria exclude changes to ``create_optimized_order`` beyond kit
        # visibility), so this test routes around it rather than asserting on a
        # 500 it did not cause.
        low_kit = InventoryItemFactory(
            name="Low Kit", is_kit=True, current_stock=0, minimum_stock=10, image=None
        )
        KitComponent.objects.create(kit=low_kit, component=components[0], quantity=1)
        ItemSupplier.objects.create(
            item=low_kit, supplier=supplier, supplier_sku="LOWKIT", quantity_per_package=1
        )
        # ``update()`` rather than ``save()``: the model re-derives unit_cost
        # from package_cost on save, so both have to go at the query level.
        ItemSupplier.objects.all().update(unit_cost=None, package_cost=None)

        response = client.post(reverse("purchaseorder-create-optimized-order"), {}, format="json")

        assert response.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED)
        # The endpoint returns recommendations for review rather than creating
        # purchase orders; those recommendation rows ARE the action rows AC-17
        # is about.
        action_rows = {
            str(row["item_id"])
            for recommendation in response.data["recommendations"]
            for row in recommendation["items"]
        }
        assert str(low_kit.pk) not in action_rows
        assert str(kit.pk) not in action_rows
        # Ordinary low-stock items still appear.
        assert {str(component.pk) for component in components} <= action_rows

    def test_ac18_reorder_data_excludes_kits_from_both_paths(
        self, client, kit, supplier, components, receiver
    ):
        # Low by stock...
        low_kit = InventoryItemFactory(
            name="Low Kit", is_kit=True, current_stock=0, minimum_stock=10, image=None
        )
        KitComponent.objects.create(kit=low_kit, component=components[0], quantity=1)
        ItemSupplier.objects.create(
            item=low_kit, supplier=supplier, supplier_sku="LOWKIT", quantity_per_package=1
        )

        response = client.get(reverse("purchaseorder-reorder-data"))
        assert response.status_code == status.HTTP_200_OK

        surfaced = {
            str(row["item_id"]) for entry in response.data["suppliers"] for row in entry["items"]
        }
        assert str(low_kit.pk) not in surfaced
        assert str(kit.pk) not in surfaced

    def test_ac19_and_ac22_low_component_surfaces_its_kit_and_seeds_the_supplier(
        self, client, kit, supplier, components
    ):
        """The supplier's ONLY reason to appear is a low kit component."""
        response = client.get(reverse("purchaseorder-reorder-data"))
        assert response.status_code == status.HTTP_200_OK

        entries = {entry["name"]: entry for entry in response.data["suppliers"]}
        assert "Eufy Direct" in entries, "supplier must be seeded by the kit alone"

        listed_kits = entries["Eufy Direct"]["kits"]
        assert len(listed_kits) == 1
        listed = listed_kits[0]
        assert listed["name"] == "Eufy Ink Kit"
        assert listed["supplier_sku"] == "T3200"
        assert listed["low_component_count"] == 5
        assert len(listed["components"]) == 5

        # Informational only: no kit action row, and the kit adds no cost.
        assert all(str(row["item_id"]) != str(kit.pk) for row in entries["Eufy Direct"]["items"])

    def test_ac22_every_supplier_entry_carries_a_kits_key(self, client, supplier, components):
        """A supplier with no kits still renders — the PO form reads this key."""
        other = SupplierFactory(name="Paper Co")
        paper = InventoryItemFactory(name="Paper", current_stock=0, minimum_stock=5, image=None)
        ItemSupplier.objects.create(
            item=paper, supplier=other, supplier_sku="P1", quantity_per_package=1
        )

        response = client.get(reverse("purchaseorder-reorder-data"))
        assert response.status_code == status.HTTP_200_OK

        for entry in response.data["suppliers"]:
            assert "kits" in entry, f"{entry['name']} is missing the kits key"
            assert isinstance(entry["kits"], list)

        paper_entry = next(e for e in response.data["suppliers"] if e["name"] == "Paper Co")
        assert paper_entry["kits"] == []


@pytest.mark.django_db
def test_explode_kit_receipt_is_driven_by_this_receipt_only(kit, components):
    """The service has no idempotency of its own — that lives in the view guard."""
    from inventory.services.kits import explode_kit_receipt

    credits = explode_kit_receipt(kit, 2)
    assert len(credits) == 5
    for component in components:
        component.refresh_from_db()
        assert component.current_stock == 2

    # Calling it again credits again: it reflects the quantity it was handed.
    explode_kit_receipt(kit, 1)
    for component in components:
        component.refresh_from_db()
        assert component.current_stock == 3


@pytest.mark.django_db
def test_explode_kit_receipt_ignores_non_kits_and_nonpositive_quantities(kit, components):
    from inventory.services.kits import explode_kit_receipt

    ordinary = InventoryItemFactory(current_stock=7, image=None)
    assert explode_kit_receipt(ordinary, 3) == []
    ordinary.refresh_from_db()
    assert ordinary.current_stock == 7

    assert explode_kit_receipt(kit, 0) == []
    for component in components:
        component.refresh_from_db()
        assert component.current_stock == 0


@pytest.mark.django_db
def test_is_kit_line_is_false_for_asset_and_freeform_lines(supplier, kit, receiver):
    purchase_order = make_po(supplier, receiver)
    freeform = PurchaseOrderItem.objects.create(
        purchase_order=purchase_order,
        description="Shipping",
        quantity_ordered=1,
        unit_cost_ordered=Decimal("5.00"),
    )
    assert freeform.is_kit_line is False
    assert add_kit_line(purchase_order, kit).is_kit_line is True


@pytest.mark.django_db
def test_mark_delivered_explodes_kit_lines(client, kit, supplier, components, receiver):
    """Both receipt entry points route through the same service."""
    purchase_order = make_po(supplier, receiver)
    add_kit_line(purchase_order, kit, quantity=2)

    response = client.post(
        reverse("purchaseorder-mark-delivered", args=[purchase_order.pk]),
        {"delivery_date": timezone.now().date().isoformat()},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data

    for component in components:
        component.refresh_from_db()
        assert component.current_stock == 2
    kit.refresh_from_db()
    assert kit.current_stock == 0


@pytest.mark.django_db
def test_kit_is_excluded_from_default_item_list_but_reachable(client, kit):
    """AC-14's contract change, asserted from the purchasing side too."""
    default = client.get(reverse("inventoryitem-list"), {"page_size": 200})
    rows = default.data["results"] if "results" in default.data else default.data
    assert str(kit.pk) not in {str(row["id"]) for row in rows}

    opted_in = client.get(reverse("inventoryitem-list"), {"include_kits": "true", "page_size": 200})
    rows = opted_in.data["results"] if "results" in opted_in.data else opted_in.data
    assert str(kit.pk) in {str(row["id"]) for row in rows}


@pytest.mark.django_db
def test_inventory_item_queryset_still_returns_ordinary_items(client, components):
    """Guard against the kit filter accidentally hiding everything."""
    response = client.get(reverse("inventoryitem-list"), {"page_size": 200})
    rows = response.data["results"] if "results" in response.data else response.data
    assert len(rows) >= len(components)
    assert InventoryItem.objects.filter(is_kit=False).count() >= len(components)
