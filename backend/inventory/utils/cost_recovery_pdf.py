"""
PDF generation for the asset cost-recovery statement.

Renders a landlord-ready statement of per-asset service history with
Estimated and Actual (vendor-invoice) cost columns. The Actual total is the
recoverable amount ("Amount to recover").

Mirrors the reportlab conventions used in ``work_order_pdf.py`` (SimpleDoc-
Template + platypus flowables, built-in Helvetica fonts so no font files are
required, greyscale table styling). Text is kept to plain ASCII/Latin-1 so the
built-in fonts render every glyph — the "DejaVu-safe" practice from the other
generators, achieved here by not emitting characters outside that range.
"""

import io
from datetime import date, datetime
from decimal import Decimal
from html import escape
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_SOURCE_LABELS = {
    "pm": "Internal PM",
    "vendor": "Vendor",
    "manual": "Manual",
}

_PERIOD_LABELS = {
    "past_week": "Past week",
    "past_month": "Past month",
    "past_year": "Past year",
}


def _money(value: Optional[Decimal]) -> str:
    """Render a Decimal as ``$X.XX``; blank for a missing (null) value."""
    if value is None:
        return ""
    return f"${Decimal(value):,.2f}"


def _fmt_date(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, (date, datetime)):
        return value.strftime("%b %d, %Y")
    return str(value)


def _para(text, style) -> Paragraph:
    """Wrap free text in a Paragraph, XML-escaping first so an ``&``/``<``/``>``
    in an asset name, vendor name, or description can't break reportlab's
    paragraph parser."""
    return Paragraph(escape(str(text), quote=False), style)


def _period_display(report: dict) -> str:
    """Human summary of the reporting window for the statement header."""
    start = _fmt_date(report.get("start_date"))
    end = _fmt_date(report.get("end_date"))
    preset = _PERIOD_LABELS.get(report.get("period"))
    if preset:
        return f"{preset} ({start} - {end})"
    return f"{start} - {end}"


def generate_cost_recovery_pdf(report: dict) -> bytes:
    """Generate a landlord-ready cost-recovery statement PDF.

    Args:
        report: The assembled report dict produced by
            ``AssetReportViewSet.cost_recovery`` — carries the reporting
            window, the selected assets (each with its ``services`` list and
            subtotals), and the grand totals. Costs are raw ``Decimal``/``None``
            (not yet serialized to strings).

    Returns:
        PDF content as bytes.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title="Asset Cost-Recovery Statement",
    )

    styles = getSampleStyleSheet()
    heading_style = ParagraphStyle(
        "CRHeading", parent=styles["Heading1"], fontSize=16, spaceAfter=2
    )
    subheading_style = ParagraphStyle(
        "CRSubHeading", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=2
    )
    small_style = ParagraphStyle(
        "CRSmall", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#444444")
    )
    asset_info_style = ParagraphStyle(
        "CRAssetInfo", parent=styles["Normal"], fontSize=9, spaceBefore=2
    )
    cell_style = ParagraphStyle("CRCell", parent=styles["Normal"], fontSize=8, leading=10)

    story = []

    # ── Header ───────────────────────────────────────────────────────────────
    story.append(Paragraph("Asset Cost-Recovery Statement", heading_style))
    story.append(Paragraph(f"Period: {_period_display(report)}", small_style))
    generated_at = report.get("generated_at")
    story.append(Paragraph(f"Generated: {_fmt_date(generated_at)}", small_style))

    selection_bits = []
    asset_ids = report.get("asset_ids") or []
    category_ids = report.get("category_ids") or []
    if asset_ids:
        selection_bits.append(f"{len(asset_ids)} asset(s) selected")
    if category_ids:
        selection_bits.append(f"{len(category_ids)} category(ies) selected")
    selection_bits.append(f"{report.get('asset_count', 0)} asset(s) in statement")
    selection_bits.append(f"{report.get('service_count', 0)} service line(s)")
    story.append(Paragraph("; ".join(selection_bits), small_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
    story.append(Spacer(1, 4))

    # ── Per-asset sections ───────────────────────────────────────────────────
    for asset in report.get("assets", []):
        block = []
        title_bits = [asset.get("name") or "(unnamed asset)"]
        if asset.get("asset_tag"):
            title_bits.append(f"[{asset['asset_tag']}]")
        block.append(_para(" ".join(title_bits), subheading_style))

        info_bits = [
            f"Serial: {asset.get('serial_number') or '-'}",
            f"Status: {asset.get('status_display') or asset.get('status') or '-'}",
            f"Category: {asset.get('category') or '-'}",
            f"Date installed: {_fmt_date(asset.get('date_received'))}",
        ]
        block.append(_para(" | ".join(info_bits), asset_info_style))
        block.append(Spacer(1, 2))

        rows = [["Date", "Source", "Description", "Estimated", "Actual"]]
        for svc in asset.get("services", []):
            rows.append(
                [
                    _fmt_date(svc.get("date")),
                    _SOURCE_LABELS.get(svc.get("source"), svc.get("source") or ""),
                    _para(svc.get("description") or "", cell_style),
                    _money(svc.get("estimated_cost")),
                    _money(svc.get("actual_cost")),
                ]
            )
        if len(rows) == 1:
            rows.append(["-", "-", Paragraph("No services in period", cell_style), "", ""])
        rows.append(
            [
                "",
                "",
                "Subtotal",
                _money(asset.get("subtotal_estimated")),
                _money(asset.get("subtotal_actual")),
            ]
        )

        table = Table(
            rows,
            colWidths=[0.9 * inch, 0.9 * inch, 3.3 * inch, 1.1 * inch, 1.1 * inch],
        )
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (3, 0), (4, -1), "RIGHT"),
                    ("PADDING", (0, 0), (-1, -1), 3),
                    # Subtotal row emphasis.
                    ("FONTNAME", (2, -1), (-1, -1), "Helvetica-Bold"),
                    ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.black),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f5f5f5")),
                ]
            )
        )
        block.append(table)
        block.append(Spacer(1, 6))
        # Keep each asset's heading + table together where it fits on a page.
        story.append(KeepTogether(block))

    # ── Grand total ──────────────────────────────────────────────────────────
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
    grand_rows = [
        ["Grand total estimated", _money(report.get("grand_total_estimated"))],
        ["Amount to recover (Actual)", _money(report.get("grand_total_actual"))],
    ]
    grand_table = Table(grand_rows, colWidths=[5.3 * inch, 2.0 * inch])
    grand_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("FONTSIZE", (0, 1), (-1, 1), 13),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor("#0b6b2e")),
                ("LINEABOVE", (0, 1), (-1, 1), 1, colors.black),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(grand_table)
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            "The Actual column reflects vendor invoices and recorded actual costs "
            "and is the amount billed to the landlord for recovery. Internal "
            "preventive-maintenance estimates are shown for reference only and are "
            "not part of the recoverable total.",
            small_style,
        )
    )

    doc.build(story)
    return buf.getvalue()
