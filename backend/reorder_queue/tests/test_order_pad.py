"""Unit tests for the pure ``build_order_pad`` helper (op-ls52).

The builder turns ``(name, supplier_sku, quantity)`` order lines into a
vendor-agnostic ``part#,qty`` order pad (CSV + tab-separated copy block) that
pastes into any distributor bulk order pad. These tests exercise it in
isolation — no DB, no view — so the CSV/text shaping and the missing-sku
handling are pinned independently of the API surface that consumes it.
"""

import pytest

from reorder_queue.views import (
    AMAZON_CART_BASE,
    AMAZON_URL_MAX_LEN,
    build_amazon_cart,
    build_order_pad,
    is_valid_asin,
    is_valid_hdsupply_part,
)


@pytest.mark.unit
class TestBuildOrderPad:
    def test_emits_header_and_part_qty_rows(self):
        pad = build_order_pad([("Widget", "W-1", 2), ("Gadget", "G-2", 5)])

        assert pad["csv"] == "part#,qty\nW-1,2\nG-2,5\n"
        assert pad["text"] == "W-1\t2\nG-2\t5"
        assert pad["line_count"] == 2
        assert pad["missing_sku"] == []

    def test_blank_sku_is_surfaced_not_dropped(self):
        pad = build_order_pad([("Good", "OK-1", 1), ("Empty", "", 3), ("Spaces", "   ", 4)])

        # Only the usable line lands in the pad...
        assert pad["line_count"] == 1
        assert pad["csv"] == "part#,qty\nOK-1,1\n"
        assert pad["text"] == "OK-1\t1"
        # ...and the blank/whitespace-only SKUs are reported, never silently lost.
        assert pad["missing_sku"] == ["Empty", "Spaces"]

    def test_sku_is_trimmed(self):
        pad = build_order_pad([("Padded", "  P-9  ", 2)])

        assert pad["csv"] == "part#,qty\nP-9,2\n"
        assert pad["text"] == "P-9\t2"

    def test_empty_input_yields_header_only(self):
        pad = build_order_pad([])

        assert pad["csv"] == "part#,qty\n"
        assert pad["text"] == ""
        assert pad["line_count"] == 0
        assert pad["missing_sku"] == []

    def test_part_number_with_comma_is_quoted_in_csv_only(self):
        pad = build_order_pad([("Comma", "A,B", 1)])

        # csv.writer quotes a field that contains the delimiter...
        assert pad["csv"] == 'part#,qty\n"A,B",1\n'
        # ...but the tab-separated copy block needs no quoting.
        assert pad["text"] == "A,B\t1"

    def test_no_validator_never_reports_invalid_sku(self):
        # The generic (validator-less) path accepts any non-blank SKU, so
        # invalid_sku is always present-but-empty in the unified shape (op-svpq).
        pad = build_order_pad([("Weird", "@@no-vendor@@", 1)])

        assert pad["line_count"] == 1
        assert pad["invalid_sku"] == []

    def test_custom_header_row(self):
        # HD Supply's Saved-List upload wants a "Part Number,Quantity" header;
        # the builder is reused with that header (op-svpq).
        pad = build_order_pad([("Part", "555", 2)], header=("Part Number", "Quantity"))

        assert pad["csv"] == "Part Number,Quantity\n555,2\n"
        # The copy block never carries a header regardless.
        assert pad["text"] == "555\t2"

    def test_validator_routes_bad_sku_to_invalid_not_the_pad(self):
        pad = build_order_pad(
            [("Good", "123", 1), ("Bad", "12X", 2), ("Blank", "", 3)],
            validate=is_valid_hdsupply_part,
        )

        # Only the SKU that passes the validator lands in the pad...
        assert pad["line_count"] == 1
        assert "123,1" in pad["csv"]
        assert "12X" not in pad["csv"]
        # ...the invalid one is surfaced separately from the merely-missing one.
        assert pad["invalid_sku"] == ["Bad"]
        assert pad["missing_sku"] == ["Blank"]


@pytest.mark.unit
class TestSkuValidators:
    @pytest.mark.parametrize("value", ["B07X1234YZ", "1234567890", "ABCDEFGHIJ"])
    def test_valid_asin(self, value):
        assert is_valid_asin(value) is True

    @pytest.mark.parametrize(
        "value",
        ["b07x1234yz", "B07X1234Y", "B07X1234YZZ", "B07-1234YZ", "", "  ", None],
    )
    def test_invalid_asin(self, value):
        assert is_valid_asin(value) is False

    def test_asin_is_trimmed_before_validation(self):
        assert is_valid_asin("  B07X1234YZ  ") is True

    @pytest.mark.parametrize("value", ["1", "0042", "123456789012"])
    def test_valid_hdsupply_part(self, value):
        assert is_valid_hdsupply_part(value) is True

    @pytest.mark.parametrize("value", ["12A", "12.5", "-5", "", "  ", None])
    def test_invalid_hdsupply_part(self, value):
        assert is_valid_hdsupply_part(value) is False


@pytest.mark.unit
class TestBuildAmazonCart:
    def test_single_url_indexes_asin_and_quantity_pairs(self):
        cart = build_amazon_cart([("Widget", "B07X1234YZ", 2), ("Gadget", "B00ABCDE12", 5)])

        assert len(cart["cart_urls"]) == 1
        url = cart["cart_urls"][0]
        assert url.startswith(AMAZON_CART_BASE + "?")
        # 1-indexed ASIN.i / Quantity.i pairs, in line order.
        assert "ASIN.1=B07X1234YZ&Quantity.1=2" in url
        assert "ASIN.2=B00ABCDE12&Quantity.2=5" in url
        assert cart["line_count"] == 2
        assert cart["missing_sku"] == []
        assert cart["invalid_sku"] == []

    def test_no_associate_tag_or_signature(self):
        cart = build_amazon_cart([("Widget", "B07X1234YZ", 1)])
        url = cart["cart_urls"][0]

        # Plain unsigned GET — no affiliate tag, no signature params.
        assert "AssociateTag" not in url
        assert "Signature" not in url
        assert "AWSAccessKeyId" not in url

    def test_blank_and_invalid_asins_are_surfaced_not_carted(self):
        cart = build_amazon_cart(
            [
                ("Good", "B07X1234YZ", 1),
                ("BadFormat", "not-an-asin", 2),
                ("Blank", "  ", 3),
            ]
        )

        assert cart["line_count"] == 1
        assert cart["missing_sku"] == ["Blank"]
        assert cart["invalid_sku"] == ["BadFormat"]
        # The bad/blank lines never reach a cart URL.
        assert "not-an-asin" not in cart["cart_urls"][0]

    def test_no_valid_lines_yields_no_urls(self):
        cart = build_amazon_cart([("Blank", "", 1), ("Bad", "xx", 2)])

        assert cart["cart_urls"] == []
        assert cart["line_count"] == 0

    def test_long_order_chunks_into_multiple_urls_each_under_the_cap(self):
        # Enough lines that a single URL would blow past the length cap.
        lines = [(f"Item {i}", "B07X1234YZ", 1) for i in range(300)]
        cart = build_amazon_cart(lines)

        assert len(cart["cart_urls"]) > 1
        assert cart["line_count"] == 300
        # No chunk exceeds the cap...
        assert all(len(url) <= AMAZON_URL_MAX_LEN for url in cart["cart_urls"])
        # ...and every chunk restarts its index at 1 (per-URL indexing).
        for url in cart["cart_urls"]:
            assert "ASIN.1=" in url
        # Every valid line is represented exactly once across all chunks.
        total_pairs = sum(url.count("ASIN.") for url in cart["cart_urls"])
        assert total_pairs == 300
