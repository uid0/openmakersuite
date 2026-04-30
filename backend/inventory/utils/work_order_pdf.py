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
    Flowable,
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


class AcroCheckbox(Flowable):
    """
    A PDF AcroForm checkbox rendered as a platypus flowable.

    Registers an interactive checkbox field on the PDF so that the filled-in
    value can be read back out of the saved PDF via pypdf. The field name is
    used to route the value back to a specific record on ingest.
    """

    def __init__(self, name: str, size: float = 10.0):
        super().__init__()
        self.name = name
        self.size = size
        self.width = size
        self.height = size

    def draw(self) -> None:
        self.canv.acroForm.checkbox(
            name=self.name,
            x=0,
            y=0,
            size=self.size,
            borderWidth=0.75,
            borderColor=colors.black,
            fillColor=colors.white,
            textColor=colors.black,
            forceBorder=True,
            tooltip=self.name,
        )


class WorkOrderIdField(Flowable):
    """
    Invisible AcroForm text field carrying the work-order UUID.

    The value survives PDF re-saves (Adobe Reader, Preview, browser viewers)
    and is the most reliable way to recover the WO ID on ingest — it doesn't
    depend on text extraction working or on the QR code being preserved when
    a paper form is scanned back to PDF.
    """

    def __init__(self, work_order_id: str):
        super().__init__()
        self.work_order_id = work_order_id
        self.width = 0
        self.height = 0

    def draw(self) -> None:
        # Render off-page (negative coords) and zero-size so it's invisible
        # in any viewer but still queryable via pypdf.get_fields().
        self.canv.acroForm.textfield(
            name="work_order_id",
            value=self.work_order_id,
            x=-100,
            y=-100,
            width=1,
            height=1,
            borderWidth=0,
            forceBorder=False,
            fontSize=1,
            tooltip="work_order_id",
        )


def _build_electrical_rows(asset) -> list:
    """
    Build a list of (label, value) rows describing how an asset is powered.

    Returns an empty list when the asset has no electrical / lockout data
    worth surfacing — the caller uses that to skip the section entirely so
    paper work orders for non-powered fixtures (e.g., shelves, soap
    dispensers) stay short.
    """
    rows: list = []

    wiring_display = asset.get_wiring_type_display() if asset.wiring_type else ""
    if asset.wiring_type and asset.wiring_type not in (
        asset.WIRING_NONE,
        asset.WIRING_UNKNOWN,
        "",
    ):
        rows.append(["Wiring", wiring_display])

    if asset.power_draw_watts:
        rows.append(["Rated Power Draw", f"{asset.power_draw_watts} W"])

    if asset.suite:
        rows.append(["Suite", asset.suite])
    if asset.electrical_box:
        rows.append(["Electrical Box", asset.electrical_box])
    if asset.breaker_location:
        rows.append(["Breaker", asset.breaker_location])
    elif asset.circuit:
        # Fall back to the legacy free-text circuit field when no structured
        # breaker location has been recorded yet.
        rows.append(["Circuit", asset.circuit])

    if asset.has_interlock:
        interlock_label = asset.get_interlock_type_display() if asset.interlock_type else "Yes"
        rows.append(["Interlock", interlock_label])
        if asset.interlock_responsible:
            rows.append(["Interlock Responsible", asset.interlock_responsible])

    if asset.lockout_type and asset.lockout_type not in (
        asset.LOCKOUT_NONE,
        asset.LOCKOUT_UNKNOWN,
        "",
    ):
        rows.append(["Lockout Type", asset.get_lockout_type_display()])
    if asset.lockout_responsible:
        rows.append(["Lockout Responsible", asset.lockout_responsible])

    if asset.has_network_drop:
        rows.append(["Network Drop", asset.network_drop_location or "Yes (location not recorded)"])

    return rows


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

    # Hidden AcroForm text field — the most reliable place to recover the WO
    # ID on ingest (survives re-saves and image-based scans).
    story.append(WorkOrderIdField(str(work_order.id)))

    header_content = [
        [
            Paragraph(f"Work Order: {work_order.short_id}", heading_style),
            qr_image,
        ],
        [
            Paragraph(item.title, subheading_style),
            Paragraph(
                "<font size='7' color='#666666'>Scan to open digital version</font>",
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
    story.append(Spacer(1, 2))

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
    created_str = work_order.created_at.strftime("%B %d, %Y")
    if work_order.due_date:
        asset_data.append(
            [
                "Work Order Due",
                work_order.due_date.strftime("%B %d, %Y"),
                "Work Order Created",
                created_str,
            ]
        )
    else:
        asset_data.append(
            [
                "Work Order Created",
                created_str,
                "Due Date",
                "—",
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

    # ── Electrical / Lockout safety section ───────────────────────────────────
    # Surfaced on every work order whose asset uses power so the tech has the
    # information needed to lock the device out before touching it. If the
    # asset is ForgeKey-managed, that is also indicated so the tech knows
    # power can be cut from the digital console.
    electrical_rows = _build_electrical_rows(asset)
    if electrical_rows:
        story.append(Paragraph("Power &amp; Lockout Safety", subheading_style))
        if asset.is_forgekey_managed:
            story.append(
                Paragraph(
                    "<b>This asset is managed by ForgeKey.</b> Use the digital "
                    "console to power the device on/off and verify wiring "
                    "before performing service.",
                    small_style,
                )
            )
        else:
            responsible = asset.interlock_responsible or asset.lockout_responsible
            if responsible:
                story.append(
                    Paragraph(
                        f"<b>Not managed by ForgeKey.</b> Interlock / lockout "
                        f"responsibility: {responsible}",
                        small_style,
                    )
                )
            else:
                story.append(
                    Paragraph(
                        "<b>Not managed by ForgeKey.</b> Confirm interlock "
                        "responsibility before energizing.",
                        small_style,
                    )
                )
        electrical_table = Table(
            electrical_rows,
            colWidths=[1.7 * inch, 5.5 * inch],
        )
        electrical_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#fff4e0")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("PADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(electrical_table)
        if asset.lockout_instructions:
            story.append(Spacer(1, 2))
            story.append(Paragraph("Lockout Procedure:", label_style))
            story.append(Paragraph(asset.lockout_instructions, small_style))
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
    # Prefer per-WorkOrder material-usage rows when present (they carry their
    # own UUIDs we can map back on ingest); fall back to the MaintenanceItem's
    # material spec for work orders created before usage rows were introduced.
    material_usage_rows = list(work_order.material_usage.select_related("material").all())
    if material_usage_rows:
        material_checklist = [
            (f"material_{mu.id}", mu.material_name, mu.quantity_planned, mu.unit, "")
            for mu in material_usage_rows
        ]
    else:
        material_checklist = [
            (f"materialspec_{mat.id}", mat.name, mat.quantity, mat.unit, mat.notes or "")
            for mat in item.materials.all()
        ]

    if material_checklist:
        story.append(Paragraph("Materials Required", subheading_style))
        mat_header = [
            Paragraph("✓", label_style),
            Paragraph("Material", label_style),
            Paragraph("Qty", label_style),
            Paragraph("Unit", label_style),
            Paragraph("Notes", label_style),
        ]
        mat_rows = [mat_header]
        for field_name, name, qty, unit, notes in material_checklist:
            qty_str = str(qty).rstrip("0").rstrip(".") if qty is not None else "—"
            mat_rows.append(
                [
                    AcroCheckbox(name=field_name),
                    Paragraph(name, normal_style),
                    qty_str,
                    unit or "—",
                    Paragraph(notes, small_style),
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
            Paragraph("✓", label_style),
            Paragraph("#", label_style),
            Paragraph("Step", label_style),
            Paragraph("Req.", label_style),
        ]
        task_rows = [task_header]
        for i, tc in enumerate(task_completions, start=1):
            req_marker = "✱" if tc.is_required else ""
            task_rows.append(
                [
                    AcroCheckbox(name=f"task_{tc.id}"),
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
