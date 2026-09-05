"""The instrument behind ``test_anonymous_vendor_exposure``.

Kept beside the test rather than inside it because two things use it: the
crawl-everything gate, and the targeted per-surface tests that name one
endpoint each. Neither reads ``permission_classes``. The whole point of this
module is that a declaration is not evidence — ``docs/API_PERMISSION_MATRIX.md``
records only DECLARED classes, and a viewset that overrides ``get_permissions``
(``InventoryItemViewSet``, ``SupplierViewSet``, ``PurchaseOrderViewSet`` and
others) does not appear in it accurately. So this issues real unauthenticated
requests and greps the bytes that come back.
"""

from __future__ import annotations

import io
import re
from decimal import Decimal

from django.core.files.base import ContentFile
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

#: Sentinel values seeded into the database, one per class of vendor fact the
#: captain's decision names. Each is unmistakable in a response body, so a hit
#: is a disclosure and not a coincidence.
VENDOR_SENTINELS = {
    "VENDOR_NAME": "ZZQQ-VENDOR-IDENTITY-ACME-SUPPLY-CO",
    "VENDOR_NAME_2": "ZZQQ-VENDOR-IDENTITY-BETA-PARTS-LTD",
    "SUPPLIER_SKU": "ZZQQ-SKU-77113",
    "PACKAGE_UPC": "ZZQQ0000000012",
    "UNIT_UPC": "ZZQQ0000000029",
    "UNIT_COST": "313.37",
    "PACKAGE_COST": "3133.70",
    "PRICE_HISTORY_COST": "271.71",
    "LEAD_TIME": 4173,
    "ACCOUNT_NUMBER": "ZZQQ-ACCT-99001",
    "SUPPLIER_WEBSITE": "https://zzqq-vendor-identity.example.com/portal",
    "AGREEMENT_NAME": "ZZQQ-AGREEMENT-2026-NONPROFIT-PRICING",
    "AGREEMENT_DOC": "zzqq-agreement-scan",
    "INVOICE_DOC": "zzqq-invoice-scan",
    "PO_TOTAL": "9313.37",
    "PO_LINE_COST": "313.37",
    # ``ReorderRequest.order_number`` is operator-typed free text with no help
    # text, filed AFTER an order is placed with a vendor — so in practice it
    # holds the vendor's reference as often as anything else. Treated as vendor
    # data because the ambiguity has to fall closed. Its unambiguous sibling
    # ``PurchaseOrder.po_number`` is in PUBLIC_SENTINELS; see there.
    "REORDER_ORDER_NUMBER": "ZZQQ-VENDOR-REF-40021",
    "SUPPLIER_ORDER_NUMBER": "ZZQQ-VENDOR-ORDER-88123",
    "INVOICE_NUMBER": "ZZQQ-INVOICE-55501",
    "INVOICE_URL": "https://zzqq-vendor-identity.example.com/invoice/55501",
    "PAYMENT_TERMS": "ZZQQ-NET-45-TERMS",
}

#: Seeded alongside the vendor facts and deliberately NOT secret. An anonymous
#: visitor scanning a shelf QR code must still be able to identify the item and
#: file a request for it, so these must keep reaching them.
PUBLIC_SENTINELS = {
    "ITEM_NAME": "PublicItemFilamentSpool",
    "ITEM_SKU": "PUBLIC-PART-0001",
    # This makerspace's OWN purchase-order reference, not the vendor's — the
    # vendor's lives beside it in ``PurchaseOrder.supplier_order_number``, which
    # IS a vendor sentinel. It names nobody and quotes no price, and the
    # transparency feed needs some way to refer to an order, so it stays public.
    "PO_NUMBER": "ZZQQ-PO-40021",
}


def seed_vendor_fixture():
    """Create one item with two suppliers, a price history, an agreement,
    a purchase order with an invoice attachment, and a filed reorder request."""
    from django.contrib.auth import get_user_model

    from inventory.models import (
        Category,
        Fixture,
        InventoryItem,
        ItemSupplier,
        Location,
        PriceHistory,
        Supplier,
        SupplierAgreement,
    )
    from reorder_queue.models import (
        PurchaseOrder,
        PurchaseOrderAttachment,
        PurchaseOrderItem,
        ReorderRequest,
    )

    s = VENDOR_SENTINELS
    cat = Category.objects.create(name="ZZQQ Filament", slug="zzqq-filament")
    loc = Location.objects.create(name="ZZQQ Shelf A", is_active=True)

    supplier = Supplier.objects.create(
        name=s["VENDOR_NAME"],
        supplier_type="online",
        website=s["SUPPLIER_WEBSITE"],
        account_number=s["ACCOUNT_NUMBER"],
    )
    supplier_2 = Supplier.objects.create(
        name=s["VENDOR_NAME_2"], supplier_type="local", account_number="ZZQQ-ACCT-2"
    )

    item = InventoryItem.objects.create(
        name=PUBLIC_SENTINELS["ITEM_NAME"],
        sku=PUBLIC_SENTINELS["ITEM_SKU"],
        description="A publicly-scannable item",
        category=cat,
        location=loc,
        current_stock=1,
        minimum_stock=4,
        reorder_quantity=20,
    )

    link = ItemSupplier.objects.create(
        item=item,
        supplier=supplier,
        supplier_sku=s["SUPPLIER_SKU"],
        supplier_url="https://zzqq-vendor-identity.example.com/p/77113",
        package_upc=s["PACKAGE_UPC"],
        unit_upc=s["UNIT_UPC"],
        unit_cost=Decimal(s["UNIT_COST"]),
        package_cost=Decimal(s["PACKAGE_COST"]),
        quantity_per_package=10,
        average_lead_time=s["LEAD_TIME"],
        is_primary=True,
        is_active=True,
    )
    link_2 = ItemSupplier.objects.create(
        item=item,
        supplier=supplier_2,
        supplier_sku="ZZQQ-SKU-2",
        unit_cost=Decimal("999.99"),
        quantity_per_package=1,
        average_lead_time=7,
        is_active=True,
    )

    price_history = PriceHistory.objects.create(
        item_supplier=link,
        unit_cost=Decimal(s["PRICE_HISTORY_COST"]),
        package_cost=Decimal("2717.10"),
        quantity_per_package=10,
        change_type="manual",
    )

    agreement = SupplierAgreement.objects.create(
        supplier=supplier,
        name=s["AGREEMENT_NAME"],
        notes="ZZQQ-AGREEMENT-TERMS-NET30",
        is_active=True,
    )
    agreement.document.save(
        f"{s['AGREEMENT_DOC']}.pdf", ContentFile(b"ZZQQ-AGREEMENT-PDF-BODY"), save=True
    )

    staff = get_user_model().objects.create_user(
        username="zzqq-buyer", password="zzqq-not-a-real-password"  # nosec B106
    )
    po = PurchaseOrder.objects.create(
        po_number=PUBLIC_SENTINELS["PO_NUMBER"],
        supplier=supplier,
        supplier_agreement=agreement,
        status="sent",
        payment_terms=s["PAYMENT_TERMS"],
        supplier_order_number=s["SUPPLIER_ORDER_NUMBER"],
        estimated_total=Decimal(s["PO_TOTAL"]),
        created_by=staff,
    )
    po_line = PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=link,
        description="ZZQQ line",
        quantity_ordered=10,
        unit_cost_ordered=Decimal(s["PO_LINE_COST"]),
    )
    attachment = PurchaseOrderAttachment(
        purchase_order=po, description="ZZQQ supplier invoice", uploaded_by=staff
    )
    attachment.file.save(
        f"{s['INVOICE_DOC']}.pdf", ContentFile(b"ZZQQ-INVOICE-PDF-BODY"), save=True
    )

    # A fixture whose refill item is the seeded one. ``FixtureViewSet`` is
    # ``IsAuthenticatedOrReadOnly`` and its ``download_card`` is a GET, so it
    # renders that item's card — lead times and all — to a caller with no
    # session. Seeded because the crawl cannot invent a fixture: without this
    # row that path answers 404 and would be recorded as clean.
    fixture = Fixture.objects.create(
        name="ZZQQ Glue Dispenser",
        location=loc,
        refill_item=item,
        asset_tag="ZZQQ-FIX-1",
    )

    reorder_request = ReorderRequest.objects.create(
        item=item,
        quantity=20,
        status="ordered",
        requested_by="anonymous scanner",
        order_number=s["REORDER_ORDER_NUMBER"],
        actual_cost=Decimal("3133.70"),
        invoice_number=s["INVOICE_NUMBER"],
        invoice_url=s["INVOICE_URL"],
        supplier_url="https://zzqq-vendor-identity.example.com/p/77113",
        ordered_at=timezone.now(),
    )

    return {
        "agreement": agreement,
        "attachment": attachment,
        "category": cat,
        "fixture": fixture,
        "item": item,
        "link": link,
        "link_2": link_2,
        "location": loc,
        "po": po,
        "po_line": po_line,
        "price_history": price_history,
        "reorder_request": reorder_request,
        "staff": staff,
        "supplier": supplier,
        "supplier_2": supplier_2,
    }


_ROUTE_ARG = re.compile(r"<(?:([^:>]+):)?([^>]+)>")
_REGEX_GROUP = re.compile(r"\(\?P<([^>]+)>[^()]*(?:\([^()]*\)[^()]*)*\)")


def _value_for(name: str, conv: str | None, fill: dict) -> str:
    if name in fill:
        return str(fill[name])
    if conv and f"__{conv}__" in fill:
        return str(fill[f"__{conv}__"])
    if name in ("pk", "id") or name.endswith("_id"):
        return str(fill.get("__default_pk__", "1"))
    return "zzqq"


def concrete_path(route: str, fill: dict) -> str | None:
    """Turn a routed pattern into a requestable path, or ``None``.

    ``None`` means "this probe could not construct a request for it" — which
    the caller must report as *could not tell*, never as *found nothing*.
    """
    out = route
    # Regex groups FIRST: '(?P<pk>...)' contains a '<pk>' that the path()-style
    # pattern below would otherwise match and destroy, silently turning every
    # DRF router route into an unfillable one.
    for match in list(_REGEX_GROUP.finditer(out)):
        out = out.replace(match.group(0), _value_for(match.group(1), None, fill))
    for match in list(_ROUTE_ARG.finditer(out)):
        out = out.replace(match.group(0), _value_for(match.group(2), match.group(1), fill))
    out = out.replace("^", "").replace("$", "")
    if any(char in out for char in "()[]?*+\\|"):
        return None
    return "/" + out.lstrip("/")


def routed_get_urls(fill: dict):
    """Every routed URL that serves GET, as (path, view_path, action, route)."""
    seen, reachable, unreachable = set(), [], []

    def walk(patterns, prefix=""):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, prefix + str(pattern.pattern))
                continue
            if not isinstance(pattern, URLPattern):
                continue
            route = prefix + str(pattern.pattern)
            callback = pattern.callback
            view_cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
            view_path = (
                f"{view_cls.__module__}.{view_cls.__name__}"
                if view_cls
                else f"{callback.__module__}.{getattr(callback, '__name__', callback)}"
            )
            actions = getattr(callback, "actions", None) or {}
            if actions and "get" not in actions:
                unreachable.append(("no-GET", route, view_path))
                continue
            path = concrete_path(route, fill)
            if path is None:
                unreachable.append(("unfillable", route, view_path))
                continue
            action = actions.get("get") if actions else None
            if (path, action) in seen:
                continue
            seen.add((path, action))
            reachable.append((path, view_path, action, route))

    walk(get_resolver().url_patterns)
    return reachable, unreachable


def searchable_bytes(body: bytes, content_type: str = "") -> bytes:
    """The bytes a sentinel search should run against.

    A PDF keeps its text in compressed streams, so grepping the raw response
    finds nothing and would report a leaking download as clean. ``download_card``
    is exactly that case, so decode first.
    """
    if not body[:5] == b"%PDF-" and "pdf" not in content_type:
        return body
    try:
        from pypdf import PdfReader

        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(body)).pages)
        return body + b"\n" + text.encode("utf-8", "replace")
    except Exception:  # a PDF we cannot decode is reported, not silently passed
        return body + b"\n<<UNDECODABLE-PDF>>"


def response_body(response) -> tuple[bytes, str]:
    body = (
        b"".join(response.streaming_content)
        if getattr(response, "streaming", False)
        else response.content
    )
    return body, str(response.headers.get("Content-Type", ""))


def sentinels_in(response, sentinels=None) -> list[str]:
    """Names of the sentinels disclosed by ``response``."""
    body, content_type = response_body(response)
    haystack = searchable_bytes(body, content_type)
    return sorted(
        name
        for name, value in (sentinels or VENDOR_SENTINELS).items()
        if str(value).encode() in haystack
    )


def crawl_anonymously(client, fill: dict):
    """GET every routed URL with no credentials.

    Returns ``(disclosures, transcript, unreachable)``. ``unreachable`` is the
    honest half: routes this probe could not turn into a request. They are
    reported, never counted as clean.
    """
    disclosures, transcript = [], []
    reachable, unreachable = routed_get_urls(fill)
    for path, view_path, action, route in reachable:
        # Django admin has its own login wall and ``flower/`` is a superuser
        # proxy; neither is a project DRF surface.
        if path.startswith(("/admin/", "/flower/")):
            continue
        try:
            response = client.get(path)
            status = response.status_code
            leaked = sentinels_in(response)
        except Exception as exc:  # a 500 is data about the surface, not a crash
            status, leaked = f"EXC:{type(exc).__name__}", []
        transcript.append((path, view_path, action, status, leaked, route))
        if leaked and isinstance(status, int) and status < 400:
            disclosures.append((path, view_path, action, status, leaked))
    return disclosures, transcript, unreachable
