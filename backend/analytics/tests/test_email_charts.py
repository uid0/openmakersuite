"""Smoke tests for the matplotlib PNG renderers.

We assert byte-level basics (PNG signature, non-empty) rather than visual
properties — the email layout is the integration test.
"""

from __future__ import annotations

from analytics.services.email_charts import render_category_spend_png, render_volume_trend_png

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class TestVolumeTrend:
    def test_returns_png_bytes(self):
        out = render_volume_trend_png(
            [
                {"period": "2026-04-01", "count": 5},
                {"period": "2026-05-01", "count": 8},
            ]
        )
        assert out.startswith(PNG_MAGIC)
        assert len(out) > 200

    def test_handles_empty_input(self):
        out = render_volume_trend_png([])
        assert out.startswith(PNG_MAGIC)


class TestCategorySpend:
    def test_returns_png_bytes(self):
        out = render_category_spend_png(
            [
                {
                    "category_name": "CNC",
                    "internal_estimated": "30.00",
                    "external_actual": "800.00",
                },
                {
                    "category_name": "3D Printers",
                    "internal_estimated": "120.00",
                    "external_actual": "0.00",
                },
            ]
        )
        assert out.startswith(PNG_MAGIC)
        assert len(out) > 200

    def test_handles_empty_input(self):
        out = render_category_spend_png([])
        assert out.startswith(PNG_MAGIC)

    def test_uncategorized_label(self):
        out = render_category_spend_png(
            [
                {"category_name": None, "internal_estimated": "10.00", "external_actual": "20.00"},
            ]
        )
        assert out.startswith(PNG_MAGIC)
