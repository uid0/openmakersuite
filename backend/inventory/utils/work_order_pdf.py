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
from typing import TYPE_CHECKING, Optional

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
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

from fiducials.services.apriltag_render import (
    FAMILY_ARUCO_4X4_50,
    FORM_FIDUCIAL_IDS,
    build_form_fiducials,
)

if TYPE_CHECKING:
    from inventory.models import WorkOrder

# ── OMR (scan-to-complete) form geometry ─────────────────────────────────────
# The OMR variant of the work-order form adds 4 corner AprilTag fiducials and
# records the absolute page rect of every mark (checkbox / ink region) so
# bead-2's reader can align a *scanned* form and threshold each mark. The
# fiducials are DICT_4X4_50 ids 0..3 at fixed page positions (see
# ``_fiducial_layout``); every mark rect is normalized against the 4 fiducial
# centers into a resolution-independent template map.

# Fixed corner-fiducial size and how far the marker's outer edge sits from the
# page edge, both in points. 30pt fills to the 0.5in content margin so the
# markers frame the content without overlapping it, and stay inside the
# printable area on a laser printer.
_FIDUCIAL_SIZE_PT = 30.0
_FIDUCIAL_MARGIN_PT = 6.0
# Render the marker bitmap at a high DPI, then let reportlab scale it down to
# _FIDUCIAL_SIZE_PT so the printed modules stay crisp (30pt @ 600dpi = 250px).
_FIDUCIAL_RENDER_DPI = 600


class RegionCollector:
    """Accumulates OMR mark regions as they are drawn (capture-at-draw).

    Option A from the design: rather than re-deriving box positions from the
    layout, each mark flowable records its own absolute page rect at
    ``draw()`` time via ``canvas.absolutePosition``. The collector holds those
    raw rects (PDF points, origin bottom-left); the caller normalizes them
    against the 4 fiducial centers into a resolution-independent template map
    (see :func:`build_omr_template_map`).
    """

    def __init__(self) -> None:
        # Each entry: {"target_id", "kind", "page", "rect": (x0, y0, x1, y1)}.
        self.regions: list[dict] = []

    def add(self, target_id: str, kind: str, page: int, rect: tuple) -> None:
        self.regions.append(
            {"target_id": target_id, "kind": kind, "page": page, "rect": tuple(rect)}
        )


class AcroCheckbox(Flowable):
    """
    A PDF AcroForm checkbox rendered as a platypus flowable.

    Registers an interactive checkbox field on the PDF so that the filled-in
    value can be read back out of the saved PDF via pypdf. The field name is
    used to route the value back to a specific record on ingest.

    When a ``collector`` is supplied (OMR variant), the box also records its
    absolute page rect so a scanned copy — whose interactive layer is gone —
    can be thresholded at the same spot.
    """

    def __init__(
        self,
        name: str,
        size: float = 10.0,
        *,
        collector: "Optional[RegionCollector]" = None,
        kind: str = "checkbox",
    ):
        super().__init__()
        self.name = name
        self.size = size
        self.width = size
        self.height = size
        self._collector = collector
        self._kind = kind

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
        if self._collector is not None:
            x0, y0 = self.canv.absolutePosition(0, 0)
            x1, y1 = self.canv.absolutePosition(self.width, self.height)
            self._collector.add(self.name, self._kind, self.canv.getPageNumber(), (x0, y0, x1, y1))


class InkRegion(Flowable):
    """A bordered write-in box (tech initials / date) for the OMR variant.

    Unlike a checkbox there is no interactive field — the tech writes ink and
    bead-2 reads *ink presence* over the captured rect via ``detect_signature``.
    Records its absolute page rect into the collector at draw time.
    """

    def __init__(
        self,
        name: str,
        width: float,
        height: float,
        *,
        collector: "Optional[RegionCollector]" = None,
    ):
        super().__init__()
        self.name = name
        self.width = width
        self.height = height
        self._collector = collector

    def draw(self) -> None:
        self.canv.setLineWidth(0.75)
        self.canv.setStrokeColor(colors.black)
        self.canv.rect(0, 0, self.width, self.height, stroke=1, fill=0)
        if self._collector is not None:
            x0, y0 = self.canv.absolutePosition(0, 0)
            x1, y1 = self.canv.absolutePosition(self.width, self.height)
            self._collector.add(self.name, "ink", self.canv.getPageNumber(), (x0, y0, x1, y1))


def _fiducial_layout(page_w: float, page_h: float) -> dict:
    """Fixed page positions of the 4 corner fiducials (PDF points, y-up).

    Returns ``{corner: {"id", "x0", "y0", "cx", "cy"}}`` where (x0, y0) is the
    marker's draw origin (bottom-left) and (cx, cy) is its center — the anchor
    the reader maps a detected marker center onto.
    """
    s = _FIDUCIAL_SIZE_PT
    m = _FIDUCIAL_MARGIN_PT
    origins = {
        "tl": (m, page_h - m - s),
        "tr": (page_w - m - s, page_h - m - s),
        "br": (page_w - m - s, m),
        "bl": (m, m),
    }
    return {
        corner: {
            "id": FORM_FIDUCIAL_IDS[corner],
            "x0": x0,
            "y0": y0,
            "cx": x0 + s / 2.0,
            "cy": y0 + s / 2.0,
        }
        for corner, (x0, y0) in origins.items()
    }


def _draw_fiducials(canvas, doc) -> None:
    """``onPage`` hook: stamp the 4 corner fiducials at fixed page positions."""
    page_w, page_h = doc.pagesize
    layout = _fiducial_layout(page_w, page_h)
    px = max(1, int(round(_FIDUCIAL_SIZE_PT / 72.0 * _FIDUCIAL_RENDER_DPI)))
    images = build_form_fiducials(px)
    for corner, spec in layout.items():
        canvas.drawImage(
            ImageReader(images[corner]),
            spec["x0"],
            spec["y0"],
            width=_FIDUCIAL_SIZE_PT,
            height=_FIDUCIAL_SIZE_PT,
        )


def build_omr_template_map(collector: "RegionCollector", page_w: float, page_h: float) -> dict:
    """Turn captured rects + the fiducial layout into a resolution-independent map.

    Every rect is normalized against the bounding box of the 4 fiducial
    *centers*, so a reader that recovers the 4 marker centers from a scan (at
    any DPI / skew, post-warp) can map ``rect_norm`` straight back to pixels.
    """
    layout = _fiducial_layout(page_w, page_h)
    fx0 = layout["bl"]["cx"]
    fx1 = layout["br"]["cx"]
    fy0 = layout["bl"]["cy"]
    fy1 = layout["tl"]["cy"]
    span_x = fx1 - fx0
    span_y = fy1 - fy0

    regions = []
    for r in collector.regions:
        x0, y0, x1, y1 = r["rect"]
        nx0 = (x0 - fx0) / span_x
        ny0 = (y0 - fy0) / span_y
        nx1 = (x1 - fx0) / span_x
        ny1 = (y1 - fy0) / span_y
        regions.append(
            {
                "target_id": r["target_id"],
                "kind": r["kind"],
                "page": r["page"],
                "rect_norm": [min(nx0, nx1), min(ny0, ny1), max(nx0, nx1), max(ny0, ny1)],
            }
        )

    fiducials = {
        corner: {"id": spec["id"], "cx": spec["cx"], "cy": spec["cy"]}
        for corner, spec in layout.items()
    }
    return {
        "page_w_pt": page_w,
        "page_h_pt": page_h,
        "fiducial_dict": FAMILY_ARUCO_4X4_50,
        "fiducials": fiducials,
        "regions": regions,
    }


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
    Compatibility wrapper — delegates to the shared work-order context
    service so digital + PDF render the same data (oms-2da AC-1/AC-2).

    Includes the asset-level rows plus LOTO rows so the PDF section
    behaviour stays identical to the pre-refactor output.
    """
    from inventory.services.work_order_context import build_electrical_context, build_loto_context

    elec = build_electrical_context(asset)
    rows: list = list(elec["rows"])  # copy so callers can mutate

    loto = build_loto_context(asset)
    if loto["is_required"]:
        rows.append(["Lockout Type", loto["lockout_type"]])
    if loto["lockout_responsible"]:
        rows.append(["Lockout Responsible", loto["lockout_responsible"]])
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


def generate_work_order_pdf(
    work_order: "WorkOrder",
    base_url: str = "",
    *,
    region_collector: "Optional[RegionCollector]" = None,
) -> bytes:
    """
    Generate a printable PDF work order.

    Args:
        work_order: The WorkOrder instance to generate a form for.
        base_url: Base URL of the application (used for the QR code link).
        region_collector: When supplied, produces the *OMR* (scan-to-complete)
            variant — every task/material checkbox records its absolute page
            rect into the collector, extra completion marks (work-complete,
            pass/fail, tech initials/date) are added, and 4 corner fiducials
            are stamped on every page. When ``None`` the output is byte-for-byte
            the original digital-completion form.

    Returns:
        PDF content as bytes.
    """
    omr = region_collector is not None
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

        # AC-1 (oms-2da): include joined electrical_circuits records so the
        # printed form matches the digital view.
        from inventory.services.work_order_context import build_electrical_context

        elec_ctx = build_electrical_context(asset)
        circuits_rows: list = []
        for outlet in elec_ctx["outlets"]:
            br = outlet.get("breaker") or {}
            br_label = br.get("label") or "—"
            circuits_rows.append(
                ["Outlet", outlet["identifier"], outlet["outlet_type_display"], br_label]
            )
        for drop in elec_ctx["network_drops"]:
            patch = drop["patch_panel"] or "—"
            if drop["patch_port"]:
                patch = f"{patch} / port {drop['patch_port']}"
            circuits_rows.append(
                ["Network Drop", drop["identifier"], drop["drop_type_display"], patch]
            )
        if circuits_rows:
            story.append(Spacer(1, 4))
            story.append(Paragraph("Circuits at this location:", label_style))
            circuits_table = Table(
                [["Kind", "Identifier", "Type", "Feeds / Patch"]] + circuits_rows,
                colWidths=[1.0 * inch, 2.0 * inch, 1.7 * inch, 2.5 * inch],
            )
            circuits_table.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("PADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            story.append(circuits_table)
        story.append(Spacer(1, 8))

    # ── Lockout / Tagout energy-source checklist (OMR-readable) ───────────────
    # One checkbox per energy source on the asset, keyed ``loto_<completion id>``
    # and drawn exactly like a task box so a scanned copy reads it back the same
    # way. The tech isolates + locks out each source, then checks its box. The
    # free-text "Lockout Procedure" paragraph above stays the human-readable
    # reference (handwriting isn't OMR'd). Rows are the WorkOrderLotoCompletion
    # set materialized at WO generation — absent (no boxes) when the asset has no
    # recorded energy sources, which is not an error.
    loto_completions = list(work_order.loto_completions.all())
    if loto_completions:
        story.append(Paragraph("Lockout / Tagout — Isolate Each Energy Source", subheading_style))
        story.append(
            Paragraph(
                "<font size='7' color='#666666'>Lock out and verify each energy "
                "source is de-energized before servicing, then check its box.</font>",
                small_style,
            )
        )
        loto_header = [
            Paragraph("✓", label_style),
            Paragraph("Energy Source", label_style),
            Paragraph("Isolation Point", label_style),
            Paragraph("Lockout Devices", label_style),
        ]
        loto_rows = [loto_header]
        for lc in loto_completions:
            loto_rows.append(
                [
                    AcroCheckbox(name=f"loto_{lc.id}", collector=region_collector),
                    Paragraph(lc.source_label, normal_style),
                    Paragraph(lc.isolation_point or "—", small_style),
                    Paragraph(lc.required_devices or "—", small_style),
                ]
            )
        loto_table = Table(
            loto_rows,
            colWidths=[0.3 * inch, 2.2 * inch, 2.5 * inch, 2.2 * inch],
        )
        loto_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#fff4e0")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("PADDING", (0, 0), (-1, -1), 4),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ]
            )
        )
        story.append(loto_table)
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
                    AcroCheckbox(name=field_name, collector=region_collector),
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
                    AcroCheckbox(name=f"task_{tc.id}", collector=region_collector),
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

    # ── OMR completion marks (scan-to-complete variant only) ──────────────────
    # The paper-only marks a scan needs to close out the form: an overall
    # "work complete" box, a pass/fail result, and an ink initials/date region.
    # Each records its page rect via the collector so bead-2 thresholds the
    # same spot on a scanned copy (checkboxes via fill %, ink via presence).
    if omr:
        story.append(Spacer(1, 6))
        story.append(Paragraph("Scan-to-Complete Marks", subheading_style))
        story.append(
            Paragraph(
                "<font size='7' color='#666666'>Fill these by hand — this form "
                "is read back by scanning the completed sheet.</font>",
                small_style,
            )
        )
        omr_rows = [
            [
                AcroCheckbox(name="work_complete", collector=region_collector),
                Paragraph("Work complete", label_style),
                AcroCheckbox(name="result_pass", collector=region_collector),
                Paragraph("Pass", label_style),
                AcroCheckbox(name="result_fail", collector=region_collector),
                Paragraph("Fail", label_style),
            ],
            [
                Paragraph("Tech initials:", label_style),
                InkRegion("tech_initials", 1.1 * inch, 0.3 * inch, collector=region_collector),
                Paragraph("Date:", label_style),
                InkRegion("tech_date", 1.1 * inch, 0.3 * inch, collector=region_collector),
                "",
                "",
            ],
        ]
        omr_table = Table(
            omr_rows,
            colWidths=[0.3 * inch, 1.3 * inch, 0.3 * inch, 1.3 * inch, 0.3 * inch, 1.3 * inch],
        )
        omr_table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("PADDING", (0, 0), (-1, -1), 4),
                    ("ALIGN", (0, 0), (0, 0), "CENTER"),
                    ("ALIGN", (2, 0), (2, 0), "CENTER"),
                    ("ALIGN", (4, 0), (4, 0), "CENTER"),
                    ("SPAN", (4, 1), (5, 1)),
                ]
            )
        )
        story.append(omr_table)
        story.append(Spacer(1, 4))

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

    if omr:
        doc.build(story, onFirstPage=_draw_fiducials, onLaterPages=_draw_fiducials)
    else:
        doc.build(story)
    return buf.getvalue()


def generate_work_order_omr_pdf(work_order: "WorkOrder", base_url: str = "") -> tuple[bytes, dict]:
    """Generate the OMR (scan-to-complete) form variant and its template map.

    Returns ``(pdf_bytes, template_map)`` where ``template_map`` is the
    resolution-independent region map (page size, fiducial anchors, and the
    normalized rect of every task/material checkbox plus the completion marks)
    that bead-2's reader consumes to threshold a scanned copy.
    """
    collector = RegionCollector()
    pdf_bytes = generate_work_order_pdf(work_order, base_url, region_collector=collector)
    page_w, page_h = letter
    template_map = build_omr_template_map(collector, page_w, page_h)
    return pdf_bytes, template_map
