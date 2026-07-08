"""Tests for the per-supplier ordering adapters (op-svpq).

A supplier's ``ordering_adapter`` selects what the ``export-order`` action emits
from a PO's lines:

- ``amazon``   → Amazon add-to-cart URL(s) via :func:`build_amazon_cart`.
- ``hdsupply`` → a ``Part Number,Quantity`` CSV via :func:`build_order_pad`
  (HD Supply Saved-List / Quick Order format) with a numeric-part validator.
- ``generic_csv`` / ``none`` → the vendor-agnostic ``part#,qty`` pad (#855).

The unit classes pin the pure builders in isolation (no DB, no view); the
integration class pins the ``export-order`` routing so each adapter returns the
right shape; a final class pins the model field's default + choices.
"""

from decimal import Decimal

from django.urls import reverse

import pytest
from rest_framework import status

from inventory.models import Supplier
from inventory.tests.factories import InventoryItemFactory, SupplierFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.views import (
    AMAZON_CART_BASE,
    AMAZON_URL_MAX_LEN,
    build_amazon_cart,
    build_order_pad,
    is_valid_asin,
    is_valid_hdsupply_part,
)


@pytest.mark.unit
class TestBuildAmazonCart:
    """The Amazon adapter builder: (name, ASIN, qty) triples → add-to-cart URLs."""

    def test_emits_asin_quantity_pairs_indexed_from_1(self):
        result = build_amazon_cart([("Widget", "B07X1234YZ", 2), ("Gadget", "B08ABCDE12", 5)])

        # A single URL carries every line as 1-indexed ASIN.i/Quantity.i pairs.
        assert result["cart_urls"] == [
            f"{AMAZON_CART_BASE}?ASIN.1=B07X1234YZ&Quantity.1=2" "&ASIN.2=B08ABCDE12&Quantity.2=5"
        ]
        assert result["line_count"] == 2
        assert result["missing_sku"] == []
        assert result["invalid_sku"] == []

    def test_asin_is_trimmed_before_validation(self):
        result = build_amazon_cart([("Padded", "  B07X1234YZ  ", 1)])

        assert result["cart_urls"] == [f"{AMAZON_CART_BASE}?ASIN.1=B07X1234YZ&Quantity.1=1"]
        assert result["invalid_sku"] == []

    def test_invalid_asin_surfaced_and_kept_out_of_url(self):
        result = build_amazon_cart(
            [
                ("Good", "B07X1234YZ", 1),
                ("Too short", "ABC123", 2),  # < 10 chars
                ("Lowercase", "abcdefghij", 3),  # not uppercase
                ("Blank", "", 4),  # no part number at all
            ]
        )

        # Only the valid ASIN reaches the cart — one 1-indexed pair.
        assert result["line_count"] == 1
        assert result["cart_urls"] == [f"{AMAZON_CART_BASE}?ASIN.1=B07X1234YZ&Quantity.1=1"]
        assert "ABC123" not in result["cart_urls"][0]
        assert "abcdefghij" not in result["cart_urls"][0]
        # Bad ASINs are surfaced by line name (invalid), blank SKUs separately.
        assert result["invalid_sku"] == ["Too short", "Lowercase"]
        assert result["missing_sku"] == ["Blank"]

    def test_no_valid_lines_yields_no_cart_urls(self):
        result = build_amazon_cart([("Bad", "nope", 1)])

        assert result["cart_urls"] == []
        assert result["line_count"] == 0
        assert result["invalid_sku"] == ["Bad"]

    def test_long_po_is_chunked_under_the_length_cap_reindexed_from_1(self):
        # A big all-valid PO: each ASIN is a unique 10-char [A-Z0-9] value.
        lines = [(f"Item {i}", f"B{i:09d}", 1) for i in range(1, 201)]

        result = build_amazon_cart(lines)

        # It spreads across more than one URL...
        assert len(result["cart_urls"]) > 1
        for url in result["cart_urls"]:
            # ...each stays under the length cap...
            assert len(url) <= AMAZON_URL_MAX_LEN
            # ...and re-indexes its pairs from 1 (never continuing across chunks).
            assert url.startswith(f"{AMAZON_CART_BASE}?ASIN.1=")
        # Every valid line is represented exactly once across the chunks.
        assert result["line_count"] == 200
        assert result["missing_sku"] == []
        assert result["invalid_sku"] == []
        total_pairs = sum(url.count("ASIN.") for url in result["cart_urls"])
        assert total_pairs == 200

    def test_single_oversized_chunk_still_gets_its_own_url(self):
        # One valid line can never exceed the cap, but the chunker must not loop
        # forever trying to shrink a chunk that already holds a single line.
        result = build_amazon_cart([("Solo", "B07X1234YZ", 1)])

        assert result["cart_urls"] == [f"{AMAZON_CART_BASE}?ASIN.1=B07X1234YZ&Quantity.1=1"]


@pytest.mark.unit
class TestIsValidAsin:
    def test_ten_uppercase_alphanumerics_is_valid(self):
        assert is_valid_asin("B07X1234YZ") is True
        assert is_valid_asin("1234567890") is True

    def test_wrong_length_or_case_or_symbols_is_invalid(self):
        assert is_valid_asin("B07X123") is False  # too short
        assert is_valid_asin("B07X1234YZ1") is False  # too long
        assert is_valid_asin("b07x1234yz") is False  # lowercase
        assert is_valid_asin("B07X-234YZ") is False  # symbol
        assert is_valid_asin("") is False
        assert is_valid_asin(None) is False


@pytest.mark.unit
class TestBuildOrderPadHDSupply:
    """The HD Supply adapter reuses build_order_pad with a custom header +
    numeric-part validator."""

    def test_emits_part_number_quantity_header_and_rows(self):
        pad = build_order_pad(
            [("Widget", "12345", 2), ("Gadget", "67890", 5)],
            header=("Part Number", "Quantity"),
            validate=is_valid_hdsupply_part,
        )

        assert pad["csv"] == "Part Number,Quantity\n12345,2\n67890,5\n"
        # The copy block (for their Quick Order pad) stays header-less + tab-sep.
        assert pad["text"] == "12345\t2\n67890\t5"
        assert pad["line_count"] == 2
        assert pad["invalid_sku"] == []
        assert pad["missing_sku"] == []

    def test_non_numeric_part_number_goes_to_invalid_sku(self):
        pad = build_order_pad(
            [("Good", "12345", 1), ("Bad", "AB-99", 2), ("Blank", "  ", 3)],
            header=("Part Number", "Quantity"),
            validate=is_valid_hdsupply_part,
        )

        # Only the numeric part number lands in the pad.
        assert pad["line_count"] == 1
        assert pad["csv"] == "Part Number,Quantity\n12345,1\n"
        # Non-numeric part is invalid; whitespace-only is missing — both surfaced.
        assert pad["invalid_sku"] == ["Bad"]
        assert pad["missing_sku"] == ["Blank"]

    def test_is_valid_hdsupply_part(self):
        assert is_valid_hdsupply_part("12345") is True
        assert is_valid_hdsupply_part("  42  ") is True  # trimmed
        assert is_valid_hdsupply_part("AB12") is False
        assert is_valid_hdsupply_part("12-34") is False
        assert is_valid_hdsupply_part("") is False
        assert is_valid_hdsupply_part(None) is False

    def test_generic_pad_without_validator_accepts_any_nonblank_sku(self):
        # The generic_csv / none adapters pass no validator, so invalid_sku is
        # always empty and any non-blank part number is kept.
        pad = build_order_pad([("Anything", "AB-99/x", 1)])

        assert pad["line_count"] == 1
        assert pad["csv"] == "part#,qty\nAB-99/x,1\n"
        assert pad["invalid_sku"] == []


@pytest.mark.django_db
@pytest.mark.integration
class TestExportOrderAdapterRouting:
    """GET purchase-orders/<id>/export-order/ dispatches on the supplier's
    ordering_adapter and returns the adapter-appropriate shape (op-svpq)."""

    def _po_with_line(self, user, *, adapter, sku, qty=1, name="Widget"):
        supplier = SupplierFactory(name="Adapter Co", ordering_adapter=adapter)
        purchase_order = PurchaseOrder.objects.create(
            po_number="PO-2026-0100",
            supplier=supplier,
            status=PurchaseOrder.DRAFT,
            created_by=user,
        )
        item = InventoryItemFactory(name=name, supplier=supplier, supplier_sku=sku)
        PurchaseOrderItem.objects.create(
            purchase_order=purchase_order,
            item_supplier=item.primary_item_supplier,
            quantity_ordered=qty,
            unit_cost_ordered=Decimal("1.00"),
        )
        return purchase_order

    def test_amazon_adapter_returns_cart_urls_not_a_file(self, authenticated_client):
        client, user = authenticated_client
        purchase_order = self._po_with_line(
            user, adapter=Supplier.ADAPTER_AMAZON, sku="B07X1234YZ", qty=2
        )

        url = reverse("purchaseorder-export-order", kwargs={"pk": purchase_order.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["adapter"] == "amazon"
        assert response.data["supplier"] == "Adapter Co"
        assert len(response.data["cart_urls"]) == 1
        assert "ASIN.1=B07X1234YZ" in response.data["cart_urls"][0]
        assert "Quantity.1=2" in response.data["cart_urls"][0]
        assert response.data["invalid_sku"] == []
        # The Amazon adapter emits URLs, never a downloadable file.
        assert "csv" not in response.data
        assert "text" not in response.data
        assert "filename" not in response.data

    def test_amazon_adapter_surfaces_invalid_asin(self, authenticated_client):
        client, user = authenticated_client
        purchase_order = self._po_with_line(
            user,
            adapter=Supplier.ADAPTER_AMAZON,
            sku="NOT-AN-ASIN",
            name="Bad ASIN Line",
        )

        url = reverse("purchaseorder-export-order", kwargs={"pk": purchase_order.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["adapter"] == "amazon"
        # A mis-typed ASIN is surfaced by line name and kept out of any cart.
        assert response.data["invalid_sku"] == ["Bad ASIN Line"]
        assert response.data["cart_urls"] == []
        assert response.data["line_count"] == 0

    def test_hdsupply_adapter_returns_part_number_csv(self, authenticated_client):
        client, user = authenticated_client
        purchase_order = self._po_with_line(
            user, adapter=Supplier.ADAPTER_HDSUPPLY, sku="12345", qty=3
        )

        url = reverse("purchaseorder-export-order", kwargs={"pk": purchase_order.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["adapter"] == "hdsupply"
        csv_lines = response.data["csv"].splitlines()
        assert csv_lines[0] == "Part Number,Quantity"
        assert "12345,3" in csv_lines
        assert response.data["text"] == "12345\t3"
        assert response.data["filename"] == "PO-2026-0100-order.csv"
        assert response.data["invalid_sku"] == []
        # HD Supply emits a CSV, not Amazon URLs.
        assert "cart_urls" not in response.data

    def test_hdsupply_adapter_surfaces_non_numeric_part(self, authenticated_client):
        client, user = authenticated_client
        purchase_order = self._po_with_line(
            user,
            adapter=Supplier.ADAPTER_HDSUPPLY,
            sku="ABC-9",
            name="Non-numeric Part",
        )

        url = reverse("purchaseorder-export-order", kwargs={"pk": purchase_order.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["adapter"] == "hdsupply"
        assert response.data["invalid_sku"] == ["Non-numeric Part"]
        assert response.data["line_count"] == 0

    def test_generic_adapter_returns_part_qty_csv(self, authenticated_client):
        client, user = authenticated_client
        purchase_order = self._po_with_line(
            user, adapter=Supplier.ADAPTER_NONE, sku="ANY-SKU", qty=4
        )

        url = reverse("purchaseorder-export-order", kwargs={"pk": purchase_order.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["adapter"] == "none"
        csv_lines = response.data["csv"].splitlines()
        assert csv_lines[0] == "part#,qty"
        assert "ANY-SKU,4" in csv_lines
        assert response.data["filename"] == "PO-2026-0100-order.csv"
        # The generic pad applies no format validator, so nothing is "invalid".
        assert response.data["invalid_sku"] == []
        assert "cart_urls" not in response.data


@pytest.mark.django_db
@pytest.mark.unit
class TestSupplierOrderingAdapterField:
    """The Supplier.ordering_adapter field default + choices (op-svpq)."""

    def test_defaults_to_none(self):
        supplier = SupplierFactory()

        assert supplier.ordering_adapter == Supplier.ADAPTER_NONE

    def test_choices_cover_the_four_adapters(self):
        field = Supplier._meta.get_field("ordering_adapter")
        choice_values = {value for value, _label in field.choices}

        assert choice_values == {
            Supplier.ADAPTER_NONE,
            Supplier.ADAPTER_GENERIC_CSV,
            Supplier.ADAPTER_AMAZON,
            Supplier.ADAPTER_HDSUPPLY,
        }
        assert field.default == Supplier.ADAPTER_NONE

    def test_serializer_exposes_supplier_ordering_adapter(self, authenticated_client):
        from reorder_queue.serializers import PurchaseOrderSerializer

        client, user = authenticated_client
        supplier = SupplierFactory(ordering_adapter=Supplier.ADAPTER_AMAZON)
        purchase_order = PurchaseOrder.objects.create(
            po_number="PO-2026-0200",
            supplier=supplier,
            status=PurchaseOrder.DRAFT,
            created_by=user,
        )

        data = PurchaseOrderSerializer(purchase_order).data

        # The web reads the adapter off the PO without a second request.
        assert data["supplier_ordering_adapter"] == "amazon"
