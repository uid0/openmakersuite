"""Unit tests for the pure ``build_order_pad`` helper (op-ls52).

The builder turns ``(name, supplier_sku, quantity)`` order lines into a
vendor-agnostic ``part#,qty`` order pad (CSV + tab-separated copy block) that
pastes into any distributor bulk order pad. These tests exercise it in
isolation — no DB, no view — so the CSV/text shaping and the missing-sku
handling are pinned independently of the API surface that consumes it.
"""

import pytest

from reorder_queue.views import build_order_pad


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
