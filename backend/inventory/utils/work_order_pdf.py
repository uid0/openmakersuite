"""
PDF generation for preventive maintenance work orders.

Generates a printable work order form that includes:
- Asset summary (name, tag, location, purchase info)
- QR code linking to the digital work order
- Materials checklist
- Task step checklist
- Completion sign-off fields
"""

import io
from typing import TYPE_CHECKING

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

if TYPE_CHECKING:
    from inventory.models import WorkOrder


def _make_qr_image(url: str, size_inches: float = 1.5) -> Image:
    """Generate a QR code image from a URL."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    size = size_inches * inch
    return Image(buf, width=size, height=size)


def generate_work_order_pdf(work_order: "WorkOrder", base_url: str = "") -> bytes:
    """
    Generate a printable PDF work order.

    Args:
        work_order: The WorkOrder instance to generate a form for.
        base_url: Base URL of the application (used for the QR code link).

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
    )

    styles = getSampleStyleSheet()
    story = []

    heading_style = ParagraphStyle(
        "Heading",
        parent=styles["Heading1"],
        fontSize=16,
        spaceAfter=4,
    )
    subheading_style = ParagraphStyle(
        "SubHeading",
        parent=styles["Heading2"],
        fontSize=12,
        spaceBefore=8,
        spaceAfter=4,
    )
    normal_style = styles["Normal"]
    small_style = ParagraphStyle(
        "Small",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#444444"),
    )
    label_style = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontSize=9,
        fontName="Helvetica-Bold",
    )

    item = work_order.maintenance_item
    asset = item.asset

    # ── Header row: title + QR code ──────────────────────────────────────────
    digital_url = f"{base_url}/maintenance/work-orders/{work_order.id}"
    qr_image = _make_qr_image(digital_url, size_inches=1.4)

    header_content = [
        [
            Paragraph(f"Work Order: {work_order.short_id}", heading_style),
            qr_image,
        ],
        [
            Paragraph(item.title, subheading_style),
            Paragraph(
                f"<font size='7' color='#666666'>Scan to open digital version</font>",
                small_style,
            ),
        ],
    ]
    header_table = Table(
        header_content,
        colWidths=[5.5 * inch, 1.6 * inch],
    )
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (1, -1), "CENTER"),
            ]
        )
    )
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
    story.append(Spacer(1, 6))

    # ── Asset summary ─────────────────────────────────────────────────────────
    story.append(Paragraph("Asset Information", subheading_style))

    location_name = asset.location.name if asset.location else "—"
    manufacturer_name = (
        asset.manufacturer.name if asset.manufacturer else (asset.manufacturer_name or "—")
    )
    date_received = asset.date_received.strftime("%B %d, %Y") if asset.date_received else "—"
    amount_paid = f"${asset.amount_paid:.2f}" if asset.amount_paid else "—"

    asset_data = [
        ["Asset Name", asset.name, "Asset Tag", asset.asset_tag or "—"],
        ["Location", location_name, "Serial #", asset.serial_number or "—"],
        ["Manufacturer / Supplier", manufacturer_name, "Date Acquired", date_received],
        ["Category", asset.category.name if asset.category else "—", "Purchase Price", amount_paid],
    ]
    if work_order.due_date:
        asset_data.append(
            [
                "Work Order Due",
                work_order.due_date.strftime("%B %d, %Y"),
                "WO Status",
                work_order.get_status_display(),
            ]
        )
    else:
        asset_data.append(
            [
                "Work Order Created",
                work_order.created_at.strftime("%B %d, %Y"),
                "WO Status",
                work_order.get_status_display(),
            ]
        )

    asset_table = Table(
        asset_data,
        colWidths=[1.5 * inch, 2.2 * inch, 1.5 * inch, 2.0 * inch],
    )
    asset_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f0f0f0")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(asset_table)
    story.append(Spacer(1, 8))

    # ── Task description ──────────────────────────────────────────────────────
    if item.description:
        story.append(Paragraph("Task Description", subheading_style))
        story.append(Paragraph(item.description, normal_style))
        story.append(Spacer(1, 4))

    # ── Estimated time/cost ───────────────────────────────────────────────────
    meta_parts = []
    if item.estimated_time_minutes:
        meta_parts.append(f"Est. Time: {item.estimated_time_minutes} min")
    if item.estimated_cost:
        meta_parts.append(f"Est. Cost: ${item.estimated_cost:.2f}")
    if item.interval_days:
        meta_parts.append(f"Interval: every {item.interval_days} days")
    if meta_parts:
        story.append(Paragraph("  |  ".join(meta_parts), small_style))
        story.append(Spacer(1, 4))

    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))

    # ── Materials checklist ───────────────────────────────────────────────────
    materials = list(item.materials.all())
    if materials:
        story.append(Paragraph("Materials Required", subheading_style))
        mat_header = [
            Paragraph("☐", label_style),
            Paragraph("Material", label_style),
            Paragraph("Qty", label_style),
            Paragraph("Unit", label_style),
            Paragraph("Notes", label_style),
        ]
        mat_rows = [mat_header]
        for mat in materials:
            mat_rows.append(
                [
                    "☐",
                    Paragraph(mat.name, normal_style),
                    str(mat.quantity).rstrip("0").rstrip("."),
                    mat.unit or "—",
                    Paragraph(mat.notes or "", small_style),
                ]
            )
        mat_table = Table(
            mat_rows,
            colWidths=[0.3 * inch, 2.8 * inch, 0.7 * inch, 0.7 * inch, 2.7 * inch],
        )
        mat_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("PADDING", (0, 0), (-1, -1), 4),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ]
            )
        )
        story.append(mat_table)
        story.append(Spacer(1, 8))

    # ── Task steps checklist ──────────────────────────────────────────────────
    task_completions = list(work_order.task_completions.order_by("task_order", "task_title"))

    if task_completions:
        story.append(Paragraph("Task Steps", subheading_style))
        task_header = [
            Paragraph("☐", label_style),
            Paragraph("#", label_style),
            Paragraph("Step", label_style),
            Paragraph("Req.", label_style),
        ]
        task_rows = [task_header]
        for i, tc in enumerate(task_completions, start=1):
            req_marker = "✱" if tc.is_required else ""
            task_rows.append(
                [
                    "☐",
                    str(i),
                    Paragraph(tc.task_title, normal_style),
                    req_marker,
                ]
            )
        task_table = Table(
            task_rows,
            colWidths=[0.3 * inch, 0.4 * inch, 6.0 * inch, 0.5 * inch],
        )
        task_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("PADDING", (0, 0), (-1, -1), 4),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                    ("ALIGN", (3, 0), (3, -1), "CENTER"),
                ]
            )
        )
        story.append(task_table)
        story.append(Paragraph("✱ = Required step", small_style))
        story.append(Spacer(1, 8))
    elif item.instructions:
        # Fall back to instructions text if no structured tasks
        story.append(Paragraph("Instructions", subheading_style))
        story.append(Paragraph(item.instructions, normal_style))
        story.append(Spacer(1, 8))

    story.append(HRFlowable(width="100%", thickness=1, color=colors.black))

    # ── Sign-off section ──────────────────────────────────────────────────────
    story.append(Paragraph("Work Order Sign-Off", subheading_style))

    signoff_data = [
        [
            Paragraph("Completed By (print name):", label_style),
            "_" * 40,
            Paragraph("Date Completed:", label_style),
            "_" * 20,
        ],
        [
            Paragraph("Signature:", label_style),
            "_" * 40,
            Paragraph("Time Spent (min):", label_style),
            "_" * 20,
        ],
        [
            Paragraph("Notes / Observations:", label_style),
            "",
            "",
            "",
        ],
    ]
    signoff_table = Table(
        signoff_data,
        colWidths=[1.8 * inch, 2.5 * inch, 1.5 * inch, 1.4 * inch],
    )
    signoff_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("SPAN", (1, 2), (3, 2)),
            ]
        )
    )
    story.append(signoff_table)

    # Notes lines
    for _ in range(3):
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#aaaaaa")))
        story.append(Spacer(1, 14))

    # Footer
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    story.append(
        Paragraph(
            f"Work Order ID: {work_order.id}  |  Asset: {asset.asset_tag or asset.name}  |  "
            f"Scan the QR code to access the digital version.",
            small_style,
        )
    )

    doc.build(story)
    return buf.getvalue()
