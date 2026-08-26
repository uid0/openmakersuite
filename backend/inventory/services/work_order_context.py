"""
Shared electrical / LOTO context for work-order parity.

Both the digital work-order detail view (via the API serializer) and the
printable PDF render the same electrical and lockout/tagout (LOTO) data. To
keep them honest — and to make the parity test in
``inventory/tests/test_work_order_parity.py`` straightforward — the data is
built once here and consumed by both surfaces.

Returned shape (deterministic dict ordering — same keys always present so
the digital view shows the empty-state messages required by AC-1 / AC-2):

    {
        "electrical": {
            "rows":        list[(label, value)]   # asset-level CharField stubs
            "outlets":     list[OutletDict]       # joined via asset.location
            "breakers":    list[BreakerDict]      # panels feeding those outlets
            "network_drops": list[NetworkDropDict]
            "is_empty":    bool                    # True when nothing electrical
        },
        "loto": {
            "lockout_type":          str           # human display
            "lockout_type_code":     str           # raw choice value
            "lockout_instructions":  str
            "lockout_responsible":   str
            "is_required":           bool          # True iff lockout_type is a
                                                   # real LOTO/lockout choice
            "is_empty":              bool          # True iff is_required False
        },
        "tools": list[ToolDict]                    # required-first, then name
        "reference_documents": {                   # asset doc library + quick links
            "documents": list[DocumentDict],
            "links":     list[{"label", "url"}],
        }
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from inventory.models import Asset, MaintenanceItem, MaintenanceTool, WorkOrder, WorkOrderTool

# A ``supersedes`` chain is linear in practice (each upload points at the one
# version it replaced), but the FK is unconstrained — cap the walk so bad data
# can never spin the renderer.
MAX_REVISION_DEPTH = 20


def _build_electrical_rows(asset: "Asset") -> list[list[str]]:
    """Asset-level electrical/lockout summary rows (label + value pairs).

    Mirrors the historical PDF helper — kept here so the digital view shows
    the same labels.
    """
    rows: list[list[str]] = []

    if asset.wiring_type and asset.wiring_type not in (
        asset.WiringType.NONE,
        asset.WiringType.UNKNOWN,
        "",
    ):
        rows.append(["Wiring", asset.get_wiring_type_display()])

    if asset.power_draw_watts:
        rows.append(["Rated Power Draw", f"{asset.power_draw_watts} W"])

    if asset.suite:
        rows.append(["Suite", asset.suite])
    if asset.electrical_box:
        rows.append(["Electrical Box", asset.electrical_box])
    if asset.breaker_location:
        rows.append(["Breaker", asset.breaker_location])
    elif asset.circuit:
        rows.append(["Circuit", asset.circuit])

    if asset.has_interlock:
        interlock_label = asset.get_interlock_type_display() if asset.interlock_type else "Yes"
        rows.append(["Interlock", interlock_label])
        if asset.interlock_responsible:
            rows.append(["Interlock Responsible", asset.interlock_responsible])

    if asset.has_network_drop:
        rows.append(
            [
                "Network Drop",
                asset.network_drop_location or "Yes (location not recorded)",
            ]
        )

    # Site-requirements safety guidance (facilities.AssetSiteRequirements, #880).
    # High-value for a tech about to work on the asset — surfaced on both the
    # digital view and the printed work order.
    if asset.special_requirements:
        rows.append(["Special Requirements", asset.special_requirements])
    if asset.work_safety_notes:
        rows.append(["Crew Should Know", asset.work_safety_notes])

    return rows


def _serialize_outlet(outlet) -> dict[str, Any]:
    breaker = outlet.breaker
    return {
        "id": outlet.id,
        "identifier": outlet.identifier,
        "outlet_type": outlet.outlet_type,
        "outlet_type_display": outlet.get_outlet_type_display(),
        "description": outlet.description,
        "plugged_in_notes": outlet.plugged_in_notes,
        "breaker": (
            {
                "id": breaker.id,
                "panel": breaker.panel,
                "breaker_number": breaker.breaker_number,
                "amperage": breaker.amperage,
                "voltage": breaker.voltage,
                "label": f"{breaker.panel} / {breaker.breaker_number}",
            }
            if breaker
            else None
        ),
    }


def _serialize_breaker(breaker) -> dict[str, Any]:
    return {
        "id": breaker.id,
        "panel": breaker.panel,
        "breaker_number": breaker.breaker_number,
        "amperage": breaker.amperage,
        "voltage": breaker.voltage,
        "poles": breaker.poles,
        "description": breaker.description,
        "label": f"{breaker.panel} / {breaker.breaker_number} ({breaker.amperage}A)",
    }


def _serialize_network_drop(drop) -> dict[str, Any]:
    return {
        "id": drop.id,
        "identifier": drop.identifier,
        "drop_type": drop.drop_type,
        "drop_type_display": drop.get_drop_type_display(),
        "patch_panel": drop.patch_panel,
        "patch_port": drop.patch_port,
        "ip_address": drop.ip_address,
        "description": drop.description,
    }


def _real_lockout(asset: "Asset") -> bool:
    """True iff the asset has a meaningful lockout requirement to display."""
    return bool(
        asset.lockout_type
        and asset.lockout_type not in (asset.LockoutType.NONE, asset.LockoutType.UNKNOWN, "")
    )


def build_loto_context(asset: "Asset") -> dict[str, Any]:
    is_required = _real_lockout(asset)
    return {
        "lockout_type": asset.get_lockout_type_display() if is_required else "",
        "lockout_type_code": asset.lockout_type or "",
        "lockout_instructions": asset.lockout_instructions or "",
        "lockout_responsible": asset.lockout_responsible or "",
        "is_required": is_required,
        "is_empty": not is_required,
    }


def sorted_maintenance_tools(
    maintenance_item: "MaintenanceItem | None",
) -> list["MaintenanceTool"]:
    """A PM template's tools in display order: required first, then by name.

    Sorted in Python off ``.all()`` so a ``tools`` prefetch is used rather than
    a query per work order — an ``order_by()`` here would bypass that cache.
    Case-folded so ``Wrench`` and ``wrench`` sort together (a DB collation would
    do the same); the raw name breaks ties so the order is total.

    Corrective work has no template, hence no tool list — ``None`` yields ``[]``.
    """
    if maintenance_item is None:
        return []
    return sorted(
        maintenance_item.tools.all(),
        key=lambda tool: (not tool.is_required, tool.name.casefold(), tool.name),
    )


def sorted_work_order_tools(work_order: "WorkOrder") -> list["WorkOrderTool"]:
    """This work order's own tool rows, in display order.

    Sorted in Python off ``.all()`` for the same reason
    :func:`sorted_maintenance_tools` is — an ``order_by()`` would bypass the
    ``tools`` prefetch the WO viewset primes — and with the identical key, so
    the two branches of :func:`build_tools_context` order the same way.
    """
    return sorted(
        work_order.tools.all(),
        key=lambda tool: (not tool.is_required, tool.name.casefold(), tool.name),
    )


def resolve_tool_location(tool: "MaintenanceTool | WorkOrderTool") -> str:
    """Where to find a tool: its explicit hint, else its inventory location.

    One rule for both kinds of row (they carry the same two fields), so the
    printed form cannot resolve a location differently from the screen.
    :attr:`WorkOrderTool.resolved_location` is the same rule expressed on the
    model — read either, never the underlying fields.
    """
    if tool.location_hint:
        return tool.location_hint
    item = tool.inventory_item
    if item is not None and item.location is not None:
        return item.location.name
    return ""


def build_tools_context(work_order: "WorkOrder") -> list[dict[str, Any]]:
    """The tool list both the WO detail page and the printed form render.

    Keys are a pinned contract — ScanTTY decodes this payload for the e-paper
    work order, so do not rename them, and both branches below emit exactly
    this key set. Only the primary maintenance item's tools: work orders that
    bundle ``additional_maintenance_items`` keep the up-front list short and
    unambiguous.

    Two branches, the established rows-if-any-else-template fallback (compare
    ``work_order_omr.dynamic_target_ids``):

    * the work order's **own** :class:`WorkOrderTool` rows when it has any —
      the per-job list, the only kind a corrective work order can have, and
      the one whose ``location_hint`` a tech can set for this job alone; or
    * the PM template's tools, for a work order generated before per-job tools
      existed. Legacy rows render byte-identically to what they always did,
      which is why no backfill migration was needed.

    Read off ``tools.all()`` rather than ``.exists()`` so a prefetch is used
    instead of a query per work order.
    """
    rows = sorted_work_order_tools(work_order)
    if rows:
        return [
            {
                "id": str(row.id),
                "name": row.name,
                "quantity": row.quantity,
                # The per-job staging spot, falling back to the linked item's
                # storage location — carried under the pinned key name.
                "location_hint": row.resolved_location,
                "is_required": row.is_required,
                "notes": row.notes,
            }
            for row in rows
        ]
    return [
        {
            "id": str(tool.id),
            "name": tool.name,
            "quantity": tool.quantity,
            "location_hint": tool.location_hint,
            "is_required": tool.is_required,
            "notes": tool.notes,
        }
        for tool in sorted_maintenance_tools(work_order.maintenance_item)
    ]


def build_electrical_context(asset: "Asset") -> dict[str, Any]:
    """Combine asset CharField stubs with electrical_circuits records.

    The join is location-based: all Outlets / Breakers / NetworkDrops at the
    asset's current location are surfaced. Per the Mayor decision (oms-2da)
    this is the intended path until a direct asset FK is added.
    """
    # electrical_circuits is a separate app; import lazily so this module can
    # be imported during tests that don't include the electrical_circuits app.
    try:
        from electrical_circuits.models import Breaker, NetworkDrop, Outlet
    except ImportError:  # pragma: no cover — electrical_circuits is in INSTALLED_APPS
        Outlet = Breaker = NetworkDrop = None  # type: ignore[assignment]

    outlets: list[dict[str, Any]] = []
    breakers: list[dict[str, Any]] = []
    drops: list[dict[str, Any]] = []

    if asset.location_id and Outlet is not None:
        outlet_qs = Outlet.objects.filter(
            location_id=asset.location_id, is_active=True
        ).select_related("breaker")
        outlets = [_serialize_outlet(o) for o in outlet_qs]

        breaker_ids = [o.breaker_id for o in outlet_qs if o.breaker_id]
        if breaker_ids:
            breaker_qs = Breaker.objects.filter(id__in=breaker_ids, is_active=True)
            breakers = [_serialize_breaker(b) for b in breaker_qs]

        drop_qs = NetworkDrop.objects.filter(location_id=asset.location_id, is_active=True)
        drops = [_serialize_network_drop(d) for d in drop_qs]

    rows = _build_electrical_rows(asset)
    is_empty = not rows and not outlets and not breakers and not drops
    return {
        "rows": rows,
        "outlets": outlets,
        "breakers": breakers,
        "network_drops": drops,
        "is_empty": is_empty,
    }


def _absolute_url(url: str, request=None, base_url: str = "") -> str:
    """Absolutize a media URL for whichever surface is rendering.

    The API has a request to build against (mirroring
    ``WorkOrderPhotoSerializer.image_url``); the printed form only knows the
    deployment's ``base_url``. Storage backends that already hand back an
    absolute URL (S3 and friends) are left alone by both paths —
    ``build_absolute_uri`` returns a fully-qualified location unchanged.
    """
    if not url:
        return ""
    if request is not None:
        return request.build_absolute_uri(url)
    if base_url and url.startswith("/"):
        return f"{base_url.rstrip('/')}{url}"
    return url


def _file_url(field_file, request=None, base_url: str = "") -> str | None:
    """Absolute URL for a FileField, or None when there is no usable file.

    A row can outlive its file (storage pruned, bad import); ``.url`` raises
    for those, and neither the work-order page nor the printed form should
    500 over a missing manual.
    """
    if not field_file:
        return None
    try:
        url = field_file.url
    except (ValueError, NotImplementedError):  # pragma: no cover — storage-dependent
        return None
    return _absolute_url(url, request=request, base_url=base_url) or None


def _revision_chain(document, by_id: dict) -> list:
    """Older versions behind ``document``, newest-first.

    Walks ``supersedes`` through ``by_id`` — a map of the asset's documents
    that the caller built from one prefetched queryset — so a chain of any
    length still costs zero extra queries. A link that points outside the
    asset (only reachable by editing around ``AssetDocumentSerializer``'s
    same-asset check) ends the walk rather than firing a lazy fetch.
    """
    revisions: list = []
    seen = {document.id}
    prior_id = document.supersedes_id
    while prior_id and len(revisions) < MAX_REVISION_DEPTH:
        prior = by_id.get(prior_id)
        if prior is None or prior.id in seen:
            break
        seen.add(prior.id)
        revisions.append(prior)
        prior_id = prior.supersedes_id
    return revisions


def _asset_links(asset: "Asset", *, request=None, base_url: str = "") -> list[dict[str, str]]:
    """The asset's quick links that are actually set (blank ones are omitted)."""
    links: list[dict[str, str]] = []

    manual_url = _file_url(asset.manual_pdf, request=request, base_url=base_url)
    if manual_url:
        links.append({"label": "Manual (PDF)", "url": manual_url})
    if asset.product_url:
        links.append({"label": "Product / documentation page", "url": asset.product_url})
    if asset.wiki_page_url:
        links.append({"label": "Wiki", "url": asset.wiki_page_url})

    return links


def build_reference_documents_context(
    asset: "Asset",
    *,
    request=None,
    base_url: str = "",
) -> dict[str, Any]:
    """Docs a tech can reach while performing/signing the work order.

    Reuses the per-asset document library (``AssetDocument``) rather than
    inventing work-order link fields: "revision history" is that model's
    ``supersedes`` chain, so uploading a new manual keeps the old one reachable
    without anybody re-attaching it. Only ``is_current`` documents head the
    list; superseded ones appear as each document's ``revisions``.

    Keys are a pinned contract — ScanTTY decodes this payload — so do not
    rename them. Reads ``asset.documents.all()`` unfiltered/unordered so the
    ``maintenance_item__asset__documents`` prefetch is used instead of a query
    per work order.
    """
    from inventory.models import AssetDocument

    documents = list(asset.documents.all())
    by_id = {doc.id: doc for doc in documents}

    def sort_key(doc) -> tuple:
        # The manual is what someone reaches for first; everything else falls
        # back to a stable category/title order.
        return (
            doc.category != AssetDocument.Category.MANUAL,
            doc.category or "",
            doc.title.casefold(),
            doc.title,
        )

    current = sorted((doc for doc in documents if doc.is_current), key=sort_key)

    return {
        "documents": [
            {
                "id": str(doc.id),
                "category": doc.category,
                "category_display": doc.get_category_display(),
                "title": doc.title,
                "version": doc.version,
                "file_url": _file_url(doc.file, request=request, base_url=base_url),
                "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
                "revisions": [
                    {
                        "id": str(prior.id),
                        "version": prior.version,
                        "file_url": _file_url(prior.file, request=request, base_url=base_url),
                        "uploaded_at": (
                            prior.uploaded_at.isoformat() if prior.uploaded_at else None
                        ),
                    }
                    for prior in _revision_chain(doc, by_id)
                ],
            }
            for doc in current
        ],
        "links": _asset_links(asset, request=request, base_url=base_url),
    }


def build_purchase_lines_context(work_order: "WorkOrder") -> list[dict[str, Any]]:
    """The PO lines bought to do this job — "ordered for this WO" (op-bu80).

    Every live purchase-order line tagged with this work order, whether still
    on order or already received, newest purchase order first. Voided lines are
    dropped: a voided line was un-ordered, so showing it as "on order" would be
    a lie. A received line also has a material row on the work order (posted by
    :mod:`inventory.services.work_order_purchase_bridge`) — this list is the
    *ordering* view of the same fact, which is what tells a tech that the part
    they are waiting on is still in transit.

    Carries the line's SETTLEMENT (``is_settled`` / ``receipt_state``) and not
    only ``is_fully_received``, because what this panel is really asking is "is
    receiving finished with this line?". Those differ: a line closed short is
    finished with and did NOT get its ordered quantity, and rendering it off
    ``is_fully_received`` alone told a tech that units written off as never
    arriving were still in transit, with an expected date, for ever. See
    ``PurchaseOrderItem.ReceiptState`` for the distinction.

    Reads ``purchase_order_items.all()`` unfiltered/unordered so the viewset's
    prefetch is used instead of a query per work order; the filter and sort
    happen in Python over that cache.
    """
    from .work_order_purchase_bridge import purchase_line_name, purchase_line_unit_cost

    lines = [line for line in work_order.purchase_order_items.all() if not line.is_voided]
    lines.sort(
        key=lambda line: (line.purchase_order.order_date, line.created_at),
        reverse=True,
    )
    return [
        {
            "id": str(line.id),
            "purchase_order_id": str(line.purchase_order_id),
            "po_number": line.purchase_order.po_number,
            "po_status": line.purchase_order.status,
            "supplier_name": (
                line.purchase_order.supplier.name if line.purchase_order.supplier_id else None
            ),
            "name": purchase_line_name(line),
            "item_type": line.target_type,
            "quantity_ordered": line.quantity_ordered,
            "quantity_received": line.quantity_received,
            "quantity_pending": line.quantity_pending,
            "is_fully_received": line.is_fully_received,
            "is_settled": line.is_settled,
            "receipt_state": line.receipt_state,
            "receipt_state_label": line.receipt_state_label,
            "quantity_variance": line.quantity_variance,
            "unit_cost": str(purchase_line_unit_cost(line)),
            "expected_delivery_date": (
                line.purchase_order.expected_delivery_date.isoformat()
                if line.purchase_order.expected_delivery_date
                else None
            ),
            "expected_shipment_date": (
                line.expected_shipment_date.isoformat() if line.expected_shipment_date else None
            ),
        }
        for line in lines
    ]


def build_work_order_context(work_order: "WorkOrder") -> dict[str, Any]:
    """Single source of truth for digital + PDF feature parity."""
    asset = work_order.asset
    return {
        "electrical": build_electrical_context(asset),
        "loto": build_loto_context(asset),
        "tools": build_tools_context(work_order),
        "reference_documents": build_reference_documents_context(asset),
    }
