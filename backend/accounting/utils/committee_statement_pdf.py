"""PDF generation for the committee (SIG) statement.

Renders a treasurer-readable statement of a committee's ledger activity over a
period: a header (committee + window), a lines table with Debit / Credit /
running Balance columns, and a totals footer (Consumed / Purchased / Settled /
Net).

Mirrors the reportlab conventions in ``inventory/utils/cost_recovery_pdf.py``
(SimpleDocTemplate + platypus flowables, built-in Helvetica fonts so no font
files are required, greyscale table styling). Text is kept to plain ASCII/
Latin-1 so the built-in fonts render every glyph — the "DejaVu-safe" practice
from the other generators, achieved here by not emitting characters outside
that range.
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
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_PERIOD_LABELS = {
    "past_week": "Past week",
    "past_month": "Past month",
    "past_year": "Past year",
}

#: Human labels for the ``source_type`` column (unknown types pass through).
_SOURCE_LABELS = {
    "SIG_CHARGE": "Consumed",
    "PO_RECEIPT": "Purchased",
    "SETTLEMENT": "Settled",
    "REVERSAL": "Reversal",
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
    in a committee name or description can't break reportlab's paragraph
    parser."""
    return Paragraph(escape(str(text), quote=False), style)


def _source_label(source_type) -> str:
    return _SOURCE_LABELS.get(source_type, source_type or "")


def _period_display(report: dict) -> str:
    """Human summary of the reporting window for the statement header."""
    start = _fmt_date(report.get("start_date"))
    end = _fmt_date(report.get("end_date"))
    preset = _PERIOD_LABELS.get(report.get("period"))
    if preset:
        return f"{preset} ({start} - {end})"
    return f"{start} - {end}"


def generate_committee_statement_pdf(report: dict) -> bytes:
    """Generate a treasurer-readable committee statement PDF.

    Args:
        report: the assembled report dict produced by
            ``accounting.reports.committee_statement`` — the committee, the
            reporting window, the ordered ``lines`` (each with raw
            ``Decimal``/``None`` money), and the bucketed ``totals``.

    Returns:
        PDF content as bytes.
    """
    buf = io.BytesIO()
    committee = report.get("committee") or {}
    committee_name = committee.get("name") or "(unnamed committee)"

    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title="Committee Statement",
    )

    styles = getSampleStyleSheet()
    heading_style = ParagraphStyle(
        "CSHeading", parent=styles["Heading1"], fontSize=16, spaceAfter=2
    )
    small_style = ParagraphStyle(
        "CSSmall", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#444444")
    )
    cell_style = ParagraphStyle("CSCell", parent=styles["Normal"], fontSize=8, leading=10)

    story = []

    # -- Header ---------------------------------------------------------------
    story.append(_para(f"Committee Statement: {committee_name}", heading_style))
    story.append(Paragraph(f"Period: {_period_display(report)}", small_style))
    story.append(Paragraph(f"Generated: {_fmt_date(report.get('generated_at'))}", small_style))
    story.append(Paragraph(f"{len(report.get('lines', []))} ledger line(s)", small_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
    story.append(Spacer(1, 6))

    # -- Lines table ----------------------------------------------------------
    rows = [["Date", "Type", "Account", "Description", "Debit", "Credit", "Balance"]]
    for line in report.get("lines", []):
        account = line.get("account_code") or ""
        if line.get("account_name"):
            account = f"{account} {line['account_name']}"
        rows.append(
            [
                _fmt_date(line.get("date")),
                _source_label(line.get("source_type")),
                _para(account, cell_style),
                _para(line.get("description") or "", cell_style),
                _money(line.get("debit")),
                _money(line.get("credit")),
                _money(line.get("running_balance")),
            ]
        )
    if len(rows) == 1:
        rows.append(["-", "-", "", Paragraph("No activity in period", cell_style), "", "", ""])

    table = Table(
        rows,
        colWidths=[
            0.85 * inch,
            0.75 * inch,
            1.35 * inch,
            2.0 * inch,
            0.85 * inch,
            0.85 * inch,
            0.85 * inch,
        ],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (4, 0), (6, -1), "RIGHT"),
                ("PADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(table)

    # -- Totals footer --------------------------------------------------------
    totals = report.get("totals") or {}
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
    total_rows = [
        ["Consumed (charges)", _money(totals.get("consumed"))],
        ["Purchased", _money(totals.get("purchased"))],
        ["Settled", _money(totals.get("settled"))],
        ["Net balance", _money(totals.get("net"))],
    ]
    totals_table = Table(total_rows, colWidths=[5.5 * inch, 2.0 * inch])
    totals_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 2), 10),
                ("FONTSIZE", (0, 3), (-1, 3), 13),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("TEXTCOLOR", (0, 3), (-1, 3), colors.HexColor("#0b6b2e")),
                ("LINEABOVE", (0, 3), (-1, 3), 1, colors.black),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(totals_table)
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            "Consumed is the committee's gross supply charges (DR 5100) for the "
            "period. Net balance is the committee's running net position across "
            "all attributed ledger lines, including any reversals or settlements.",
            small_style,
        )
    )

    doc.build(story)
    return buf.getvalue()
