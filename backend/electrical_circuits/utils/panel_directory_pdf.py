"""
PDF reports for the electrical_circuits app (oms-zde).

Two reports are produced for maintenance use:

* ``generate_panel_directory_pdf`` — for one or all panels, lists every
  breaker on the panel and, under each breaker, the outlets and light
  switches it feeds (with location and identifier).
* ``generate_network_drop_list_pdf`` — for one or all locations, lists
  network drops with patch-panel/port and connected device notes.

Mirrors the layout patterns of ``inventory/utils/work_order_pdf.py``
(letter page, half-inch margins, bold-label tables on a light grey grid).
"""

from __future__ import annotations

import io
from typing import Iterable, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..models import Breaker, LightSwitch, NetworkDrop, Outlet


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "heading": ParagraphStyle("Heading", parent=base["Heading1"], fontSize=16, spaceAfter=4),
        "subheading": ParagraphStyle(
            "SubHeading",
            parent=base["Heading2"],
            fontSize=12,
            spaceBefore=8,
            spaceAfter=4,
        ),
        "panel_heading": ParagraphStyle(
            "PanelHeading",
            parent=base["Heading2"],
            fontSize=13,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "normal": base["Normal"],
        "small": ParagraphStyle(
            "Small",
            parent=base["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#444444"),
        ),
        "label": ParagraphStyle(
            "Label",
            parent=base["Normal"],
            fontSize=9,
            fontName="Helvetica-Bold",
        ),
    }


def _build_doc(buf: io.BytesIO) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buf,
        pagesize=letter,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )


def _format_breaker_summary(b: Breaker) -> str:
    parts = [f"{b.amperage}A", f"{b.voltage}V"]
    if b.poles and b.poles > 1:
        parts.append(f"{b.poles}-pole")
    return ", ".join(parts)


def _outlets_for_breaker(breaker: Breaker) -> list[Outlet]:
    return list(
        Outlet.objects.filter(breaker=breaker)
        .select_related("location")
        .order_by("location__name", "identifier")
    )


def _switches_for_breaker(breaker: Breaker) -> list[LightSwitch]:
    return list(
        LightSwitch.objects.filter(breaker=breaker)
        .select_related("location", "controls_location")
        .order_by("location__name", "identifier")
    )


def _orphan_outlets(panel: Optional[str]) -> list[Outlet]:
    """Outlets without a breaker assigned.

    Only included on the all-panels report so they aren't lost — when a
    specific panel is requested, an orphan has no panel and isn't relevant.
    """
    if panel:
        return []
    return list(
        Outlet.objects.filter(breaker__isnull=True)
        .select_related("location")
        .order_by("location__name", "identifier")
    )


def _orphan_switches(panel: Optional[str]) -> list[LightSwitch]:
    if panel:
        return []
    return list(
        LightSwitch.objects.filter(breaker__isnull=True)
        .select_related("location", "controls_location")
        .order_by("location__name", "identifier")
    )


def _outlet_row(o: Outlet) -> list:
    type_label = o.get_outlet_type_display()
    plugged = o.plugged_in_notes or ""
    return [
        Paragraph("Outlet", _styles()["label"]),
        Paragraph(o.identifier, _styles()["normal"]),
        Paragraph(o.location.name if o.location else "—", _styles()["normal"]),
        Paragraph(type_label, _styles()["small"]),
        Paragraph(plugged, _styles()["small"]),
    ]


def _switch_row(sw: LightSwitch) -> list:
    controls = sw.controls_location.name if sw.controls_location else ""
    return [
        Paragraph("Switch", _styles()["label"]),
        Paragraph(sw.identifier, _styles()["normal"]),
        Paragraph(sw.location.name if sw.location else "—", _styles()["normal"]),
        Paragraph(f"controls: {controls}" if controls else "—", _styles()["small"]),
        Paragraph(sw.description or "", _styles()["small"]),
    ]


def _breaker_section(breaker: Breaker, styles: dict) -> list:
    """Render one breaker's heading, metadata, and feeder list."""
    elements: list = []
    title = f"Breaker {breaker.breaker_number} — {_format_breaker_summary(breaker)}"
    if breaker.description:
        title += f" — {breaker.description}"
    elements.append(Paragraph(title, styles["label"]))

    feeders: list = []
    feeders.extend(_outlet_row(o) for o in _outlets_for_breaker(breaker))
    feeders.extend(_switch_row(sw) for sw in _switches_for_breaker(breaker))

    if not feeders:
        elements.append(
            Paragraph(
                "<i>No outlets or switches recorded on this breaker.</i>",
                styles["small"],
            )
        )
        elements.append(Spacer(1, 4))
        return elements

    header = [
        Paragraph("Type", styles["label"]),
        Paragraph("ID", styles["label"]),
        Paragraph("Location", styles["label"]),
        Paragraph("Detail", styles["label"]),
        Paragraph("Notes", styles["label"]),
    ]
    table = Table(
        [header, *feeders],
        colWidths=[0.7 * inch, 1.4 * inch, 1.7 * inch, 1.6 * inch, 1.8 * inch],
    )
    table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 6))
    return elements


def _panel_section(panel_name: str, breakers: Iterable[Breaker], styles: dict) -> list:
    elements: list = [Paragraph(f"Panel: {panel_name}", styles["panel_heading"])]
    breakers = list(breakers)
    if not breakers:
        elements.append(Paragraph("<i>No breakers recorded.</i>", styles["small"]))
        elements.append(Spacer(1, 6))
        return elements
    for breaker in breakers:
        elements.extend(_breaker_section(breaker, styles))
    return elements


def generate_panel_directory_pdf(panel: Optional[str] = None) -> bytes:
    """
    Generate a panel-directory PDF.

    Args:
        panel: Restrict the report to a single panel name. ``None`` means
            include every panel known to the system.

    Returns:
        PDF content as bytes.
    """
    buf = io.BytesIO()
    doc = _build_doc(buf)
    styles = _styles()
    story: list = []

    breakers_qs = Breaker.objects.select_related("location").order_by("panel", "breaker_number")
    if panel:
        breakers_qs = breakers_qs.filter(panel=panel)

    title = f"Panel Directory — Panel {panel}" if panel else "Panel Directory — All Panels"
    story.append(Paragraph(title, styles["heading"]))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
    story.append(Spacer(1, 4))

    breakers = list(breakers_qs)
    if not breakers and not panel:
        story.append(Paragraph("No breakers have been recorded yet.", styles["normal"]))

    grouped: dict[str, list[Breaker]] = {}
    for b in breakers:
        grouped.setdefault(b.panel, []).append(b)

    panel_names = sorted(grouped.keys())
    for idx, name in enumerate(panel_names):
        if idx > 0 and not panel:
            story.append(PageBreak())
        story.extend(_panel_section(name, grouped[name], styles))

    if panel and not breakers:
        story.append(Paragraph(f"No breakers recorded for panel '{panel}'.", styles["normal"]))

    orphan_outlets = _orphan_outlets(panel)
    orphan_switches = _orphan_switches(panel)
    if orphan_outlets or orphan_switches:
        story.append(PageBreak())
        story.append(Paragraph("Unassigned (no breaker recorded)", styles["panel_heading"]))
        rows: list = [
            [
                Paragraph("Type", styles["label"]),
                Paragraph("ID", styles["label"]),
                Paragraph("Location", styles["label"]),
                Paragraph("Detail", styles["label"]),
                Paragraph("Notes", styles["label"]),
            ]
        ]
        rows.extend(_outlet_row(o) for o in orphan_outlets)
        rows.extend(_switch_row(sw) for sw in orphan_switches)
        table = Table(
            rows,
            colWidths=[0.7 * inch, 1.4 * inch, 1.7 * inch, 1.6 * inch, 1.8 * inch],
        )
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("PADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)

    doc.build(story)
    return buf.getvalue()


def generate_network_drop_list_pdf(location_id: Optional[int] = None) -> bytes:
    """
    Generate a network-drop-list PDF.

    Args:
        location_id: Restrict to a single Location id. ``None`` means
            include every drop, grouped by location.

    Returns:
        PDF content as bytes.
    """
    buf = io.BytesIO()
    doc = _build_doc(buf)
    styles = _styles()
    story: list = []

    drops_qs = NetworkDrop.objects.select_related("location").order_by(
        "location__name", "identifier"
    )
    if location_id is not None:
        drops_qs = drops_qs.filter(location_id=location_id)

    title = "Network Drop List"
    if location_id is not None:
        # Use the first drop's location name when available so the heading
        # is meaningful even when the queryset is empty.
        first = drops_qs.first()
        if first and first.location:
            title = f"Network Drop List — {first.location.name}"
    story.append(Paragraph(title, styles["heading"]))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
    story.append(Spacer(1, 4))

    drops = list(drops_qs)
    if not drops:
        story.append(Paragraph("No network drops recorded for this filter.", styles["normal"]))
        doc.build(story)
        return buf.getvalue()

    grouped: dict[str, list[NetworkDrop]] = {}
    for d in drops:
        loc_name = d.location.name if d.location else "—"
        grouped.setdefault(loc_name, []).append(d)

    header = [
        Paragraph("ID", styles["label"]),
        Paragraph("Type", styles["label"]),
        Paragraph("Patch Panel / Port", styles["label"]),
        Paragraph("MAC / IP", styles["label"]),
        Paragraph("Notes", styles["label"]),
    ]

    location_names = sorted(grouped.keys())
    for idx, name in enumerate(location_names):
        if idx > 0:
            story.append(Spacer(1, 8))
        story.append(Paragraph(f"Location: {name}", styles["panel_heading"]))
        rows: list = [header]
        for d in grouped[name]:
            patch = ""
            if d.patch_panel and d.patch_port:
                patch = f"{d.patch_panel} / {d.patch_port}"
            elif d.patch_panel:
                patch = d.patch_panel
            elif d.patch_port:
                patch = f"port {d.patch_port}"
            mac_ip_parts: list[str] = []
            if d.mac_address:
                mac_ip_parts.append(d.mac_address)
            if d.ip_address:
                mac_ip_parts.append(str(d.ip_address))
            mac_ip = "\n".join(mac_ip_parts)
            rows.append(
                [
                    Paragraph(d.identifier, styles["normal"]),
                    Paragraph(d.get_drop_type_display(), styles["small"]),
                    Paragraph(patch or "—", styles["small"]),
                    Paragraph(mac_ip or "—", styles["small"]),
                    Paragraph(d.notes or d.description or "", styles["small"]),
                ]
            )
        table = Table(
            rows,
            colWidths=[1.2 * inch, 1.0 * inch, 1.7 * inch, 1.5 * inch, 1.8 * inch],
        )
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("PADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)

    doc.build(story)
    return buf.getvalue()
