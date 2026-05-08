"""Server-side PNG chart generation for the monthly pulse email.

We use matplotlib's headless ``Agg`` backend so this runs in worker
containers without GUI deps. Each function returns ``bytes`` ready to
attach inline via the ``cid:`` reference in the HTML template.

Charts are deliberately simple:
- Email clients are a hostile rendering target; rich interactivity
  belongs on the live dashboard, which the email links to.
- Fonts are matplotlib defaults (DejaVu) — no external font dependency.
- Color palette mirrors the dashboard so screenshots feel familiar.
"""

from __future__ import annotations

import io
from typing import Sequence

import matplotlib

matplotlib.use("Agg")  # noqa: E402 — must precede pyplot import.

import matplotlib.pyplot as plt  # noqa: E402

CHART_DPI = 110
CHART_FIGSIZE = (7.0, 3.2)  # inches; renders ~770x352 at 110 DPI
COLOR_INTERNAL = "#1971C2"  # Mantine blue-7
COLOR_EXTERNAL = "#E8590C"  # Mantine orange-7
COLOR_VOLUME = "#1864AB"


def _figure_to_png_bytes(fig) -> bytes:
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=CHART_DPI, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def render_volume_trend_png(trend: Sequence[dict]) -> bytes:
    """Return PNG bytes for the 12-month WO volume line chart."""
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, dpi=CHART_DPI)
    if not trend:
        ax.text(
            0.5,
            0.5,
            "No completed work orders in the trend window.",
            ha="center",
            va="center",
            color="#888",
        )
        ax.axis("off")
        return _figure_to_png_bytes(fig)

    labels = [_short_period(row["period"]) for row in trend]
    counts = [int(row["count"]) for row in trend]

    ax.plot(labels, counts, color=COLOR_VOLUME, marker="o", linewidth=2)
    ax.fill_between(range(len(counts)), counts, alpha=0.1, color=COLOR_VOLUME)
    ax.set_title("Work order volume — last 12 months", fontsize=12, color="#222")
    ax.set_ylabel("Completed WOs")
    ax.grid(True, axis="y", linestyle="--", alpha=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.autofmt_xdate(rotation=30, ha="right")
    return _figure_to_png_bytes(fig)


def render_category_spend_png(rows: Sequence[dict]) -> bytes:
    """Return PNG bytes for the grouped-bar 'Spend by category' chart."""
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, dpi=CHART_DPI)
    if not rows:
        ax.text(
            0.5,
            0.5,
            "No work orders attributed to a category in this window.",
            ha="center",
            va="center",
            color="#888",
        )
        ax.axis("off")
        return _figure_to_png_bytes(fig)

    labels = [row["category_name"] or "Uncategorized" for row in rows]
    internal = [float(row["internal_estimated"]) for row in rows]
    external = [float(row["external_actual"]) for row in rows]

    x = list(range(len(labels)))
    width = 0.4
    ax.bar(
        [i - width / 2 for i in x],
        internal,
        width,
        label="Internal estimated",
        color=COLOR_INTERNAL,
    )
    ax.bar(
        [i + width / 2 for i in x], external, width, label="External actual", color=COLOR_EXTERNAL
    )
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylabel("USD")
    ax.set_title("Spend by category", fontsize=12, color="#222")
    ax.grid(True, axis="y", linestyle="--", alpha=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    return _figure_to_png_bytes(fig)


def _short_period(iso_date: str) -> str:
    """``2026-04-01`` → ``Apr '26`` for compact x-axis ticks."""
    from datetime import date

    try:
        d = date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return iso_date
    return d.strftime("%b '%y")
