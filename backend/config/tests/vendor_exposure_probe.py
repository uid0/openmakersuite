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

from config.permission_matrix import EndpointKey, introspect_endpoints

#: Sentinel values seeded into the database, one per class of vendor fact the
#: captain's decision names. Each is unmistakable in a response body, so a hit
#: is a disclosure and not a coincidence.
VENDOR_SENTINELS = {
    "VENDOR_NAME": "ZZQQ-VENDOR-IDENTITY-ACME-SUPPLY-CO",
    "VENDOR_NAME_2": "ZZQQ-VENDOR-IDENTITY-BETA-PARTS-LTD",
    "SUPPLIER_SKU": "ZZQQ-SKU-77113",
    # Real 12-digit UPCs, not a ZZQQ-prefixed string: ``scanner.resolvers``
    # only treats a PURE-DIGIT payload of length 8/12/13/14 as a barcode, so a
    # prefixed sentinel would never reach the UPC path and that surface would
    # test as clean while being open.
    "PACKAGE_UPC": "991470000012",
    "UNIT_UPC": "991470000029",
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
        UsageLog,
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

    # A PLAIN account: not staff, not a superuser, in no group, with no
    # membership. The captain's line is "behind user auth", so the control that
    # matters is that ANY signed-in account sees vendor data — a staff user
    # would prove a narrower thing than the decision asks for. Named ``staff``
    # only because it is the PO's ``created_by``; it holds no privilege.
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

    # A consumption record carrying the price snapshot. Seeded because
    # ``InventoryItemDetailSerializer.recent_usage`` nests ``UsageLogSerializer``
    # on the anonymous item payload: with no usage row, that nesting serialises
    # to ``[]`` and the crawl reports the surface clean whether or not it is.
    usage_log = UsageLog.objects.create(
        item=item,
        quantity_used=1,
        notes="ZZQQ usage",
        unit_cost=Decimal(s["UNIT_COST"]),
        total_cost=Decimal(s["UNIT_COST"]),
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
        "usage_log": usage_log,
    }


_ROUTE_ARG = re.compile(r"<(?:([^:>]+):)?([^>]+)>")
_REGEX_GROUP = re.compile(r"\(\?P<([^>]+)>[^()]*(?:\([^()]*\)[^()]*)*\)")


class UnmappedRouteArg(Exception):
    """A route needs a pk that ``__pk_by_prefix__`` does not map to a real row.

    Raised only in STRICT mode. The GET crawl tolerates the fallback because it
    reports what it reached and what it could not; a WRITE this probe issues
    cannot, because a 404 leaks nothing and therefore reads as clean.
    """


def _value_for(
    name: str, conv: str | None, fill: dict, route: str = "", strict: bool = False
) -> str:
    if name in fill:
        return str(fill[name])
    # DRF's format-suffix routes ('items.json'). They reach the same view and
    # action as the bare path, so their permissions are the same by
    # construction — but "by construction" is the kind of reasoning this probe
    # exists to replace, and filling them costs one line.
    if name == "format":
        return "json"
    if conv and f"__{conv}__" in fill:
        return str(fill[f"__{conv}__"])
    if name in ("pk", "id") or name.endswith("_id"):
        # A DRF router route spells its pk ``(?P<pk>[^/.]+)`` with NO converter,
        # so ``__uuid__`` never applies and one value cannot serve every table.
        # Getting this wrong is SILENT: the request 404s and the crawl records
        # the surface as clean. ``/api/inventory/items/<a supplier's id>/`` did
        # exactly that, so the item detail payload — the largest vendor surface
        # there is — was never actually fetched by the crawl; later
        # ``/api/inventory/locations/<a supplier's id>/report_problem/`` did it
        # again, to two anonymous WRITE surfaces. The route prefix picks a row
        # that exists, and a STRICT caller refuses the fallback outright.
        for prefix, value in (fill.get("__pk_by_prefix__") or {}).items():
            if prefix in route:
                return str(value)
        if strict:
            raise UnmappedRouteArg(
                f"route {route} needs <{name}> and no `__pk_by_prefix__` entry matches it. "
                "Filling it from `__default_pk__` would point the request at a row in "
                "another table, which 404s and reads as clean."
            )
        return str(fill.get("__default_pk__", "1"))
    return "zzqq"


def concrete_path(route: str, fill: dict, *, strict: bool = False) -> str | None:
    """Turn a routed pattern into a requestable path, or ``None``.

    ``None`` means "this probe could not construct a request for it" — which
    the caller must report as *could not tell*, never as *found nothing*.

    ``strict`` refuses the ``__default_pk__`` fallback and raises
    :class:`UnmappedRouteArg` instead. :func:`anonymous_write_surfaces` is the
    caller that cannot afford a request against the wrong table.
    """
    out = route
    # Regex groups FIRST: '(?P<pk>...)' contains a '<pk>' that the path()-style
    # pattern below would otherwise match and destroy, silently turning every
    # DRF router route into an unfillable one.
    for match in list(_REGEX_GROUP.finditer(out)):
        out = out.replace(match.group(0), _value_for(match.group(1), None, fill, route, strict))
    for match in list(_ROUTE_ARG.finditer(out)):
        out = out.replace(
            match.group(0), _value_for(match.group(2), match.group(1), fill, route, strict)
        )
    out = out.replace("^", "").replace("$", "")
    # A format-suffix route ends '\.json/?' once its group is filled: unescape
    # the dot and drop the optional trailing slash so it becomes requestable.
    out = out.replace("\\.", ".")
    if out.endswith("/?"):
        out = out[:-2]
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


#: Stamped into the searchable bytes when a PDF response could not be decoded.
#:
#: The raw bytes are still searched, but a PDF keeps its text in compressed
#: streams, so that search proves nothing — the response is a "could not tell",
#: and this module runs on the rule that a "could not tell" is never counted as
#: a "found nothing". :func:`crawl_anonymously` carries the flag out on every
#: transcript row and
#: ``test_anonymous_vendor_exposure_coverage.py::test_the_crawl_could_read_every_pdf_it_was_served``
#: fails on any that are set.
UNDECODABLE_PDF = b"<<UNDECODABLE-PDF>>"


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
    except Exception:
        return body + b"\n" + UNDECODABLE_PDF


def response_body(response) -> tuple[bytes, str]:
    body = (
        b"".join(response.streaming_content)
        if getattr(response, "streaming", False)
        else response.content
    )
    return body, str(response.headers.get("Content-Type", ""))


def sentinels_in(response, sentinels=None, haystack: bytes | None = None) -> list[str]:
    """Names of the sentinels disclosed by ``response``.

    ``haystack`` lets a caller that already decoded the body pass it back in, so
    the PDF extraction runs once per response rather than once per question
    asked about it.
    """
    if haystack is None:
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
        undecodable = False
        try:
            response = client.get(path)
            status = response.status_code
            body, content_type = response_body(response)
            haystack = searchable_bytes(body, content_type)
            undecodable = UNDECODABLE_PDF in haystack
            leaked = sentinels_in(response, haystack=haystack)
        except Exception as exc:  # a 500 is data about the surface, not a crash
            status, leaked = f"EXC:{type(exc).__name__}", []
        transcript.append((path, view_path, action, status, leaked, route, undecodable))
        if leaked and isinstance(status, int) and status < 400:
            disclosures.append((path, view_path, action, status, leaked))
    return disclosures, transcript, unreachable


#: Why a derived anonymous write is NOT issued by this probe. An entry is a
#: statement that somebody looked at the route and decided against exercising
#: it — the same shape as ``test_upload_field_classification.OPEN_PREFIXES``,
#: and for the same reason: a route that is neither exercised nor classified is
#: a hole nothing fails on.
NO_FIXTURE_ROW = (
    "This probe seeds no row this route can act on, so the request it could "
    "build would 404 before reaching any serializer — a 'could not tell', not "
    "a 'found nothing'. Its GET twin, where it has one, is covered by the crawl."
)
DEVICE_CHANNEL = (
    "A ForgeKey device/firmware channel. Deliberately AllowAny with a reason "
    "stated at the view, and not a vendor surface: it reads no supplier, no "
    "item-supplier link and no price. Every ForgeKey GET is crawled."
)
CREDENTIAL_CHANNEL = (
    "Takes credentials or a token, not item data, and reads no vendor table. "
    "Issuing it here would either mint auth state the rest of the probe "
    "assumes absent, or exercise a login flow that is its own test suite."
)
INBOUND_WEBHOOK = (
    "A machine-to-machine ingest channel whose caller is a mail relay, an MQTT "
    "bridge or a kiosk, and whose reply is an ack. Reaching it needs a signed "
    "or provider-shaped payload this probe does not construct."
)
MEMBER_REPORT = (
    "Writes a member's own report or check-in. Its reply echoes the submission "
    "and the serializer names no supplier field; the row it writes carries no "
    "vendor foreign key."
)
NOT_OUR_VIEW = (
    "A Django, DRF, drf-spectacular or passkeys view. This repo declares no "
    "permissions for it, which is why the permission snapshot skips it, and it "
    "reads none of our tables — it cannot name a supplier or quote a price."
)

#: Every derived anonymous write this probe does NOT issue, with its reason.
ANONYMOUS_WRITES_NOT_ISSUED: dict[EndpointKey, str] = {
    EndpointKey("auth_views.create_test_invite_code", None): CREDENTIAL_CHANNEL,
    EndpointKey("auth_views.create_test_membership", None): CREDENTIAL_CHANNEL,
    EndpointKey("auth_views.login_user", None): CREDENTIAL_CHANNEL,
    EndpointKey("auth_views.logout_user", None): CREDENTIAL_CHANNEL,
    EndpointKey("auth_views.refresh_token", None): CREDENTIAL_CHANNEL,
    EndpointKey("auth_views.register_user", None): CREDENTIAL_CHANNEL,
    EndpointKey("membership.views.redeem_invite_code", None): CREDENTIAL_CHANNEL,
    EndpointKey("checklists.views.ChecklistCompletionViewSet", "complete"): NO_FIXTURE_ROW,
    EndpointKey("checklists.views.ChecklistCompletionViewSet", "scan"): NO_FIXTURE_ROW,
    EndpointKey("checklists.views.ChecklistViewSet", "start"): NO_FIXTURE_ROW,
    EndpointKey("customization.views.site_settings", None): NO_FIXTURE_ROW,
    EndpointKey("donations.views.lookup_donation_item_by_code", None): NO_FIXTURE_ROW,
    EndpointKey("inventory.views.AssetProblemViewSet", "upload_photo"): NO_FIXTURE_ROW,
    EndpointKey("inventory.views.AssetViewSet", "generate_qr"): NO_FIXTURE_ROW,
    EndpointKey("inventory.views.AssetViewSet", "scan"): NO_FIXTURE_ROW,
    EndpointKey("project_storage.views.ProjectStorageStintViewSet", "mark_printed"): NO_FIXTURE_ROW,
    EndpointKey("project_storage.views.ProjectStorageStintViewSet", "start"): NO_FIXTURE_ROW,
    EndpointKey("forgekey.views.EPaperDisplayBatteryView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.EPaperDisplayCommandAckView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.EPaperDisplayDesiredView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.EPaperDisplayFirmwareStatusView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.EPaperDisplayHealthView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.EPaperDisplayImageView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.EPaperFirmwareCheckView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.EPaperServiceInfoView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.ForgeKeyCertificateRevocationListView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.ForgeKeyDeviceEnrollView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.ForgeKeyDevicePhotoUploadView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.ForgeKeyFirmwareDownloadView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.ForgeKeyFirmwarePublicKeyView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.ForgeKeyJWKSView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.ForgeKeyOmsCommandPublicKeyView", None): DEVICE_CHANNEL,
    EndpointKey("forgekey.views.MqttWebhookView", None): DEVICE_CHANNEL,
    EndpointKey("inventory.views.postmark_inbound_work_order", None): INBOUND_WEBHOOK,
    EndpointKey("location_checkins.views.location_ping_webhook", None): INBOUND_WEBHOOK,
    EndpointKey("screens.views.kiosk_heartbeat", None): INBOUND_WEBHOOK,
    EndpointKey("location_checkins.views.LocationCheckInViewSet", "checkin"): MEMBER_REPORT,
    EndpointKey("location_checkins.views.LocationCheckInViewSet", "create"): MEMBER_REPORT,
    EndpointKey("location_checkins.views.LocationFeedbackViewSet", "create"): MEMBER_REPORT,
    EndpointKey("location_checkins.views.LocationFeedbackViewSet", "submit"): MEMBER_REPORT,
    EndpointKey("location_checkins.views.SecurityReportViewSet", "create"): MEMBER_REPORT,
    EndpointKey("location_checkins.views.SecurityReportViewSet", "report"): MEMBER_REPORT,
    EndpointKey("membership.views.register_user_with_token", None): CREDENTIAL_CHANNEL,
    EndpointKey("membership.views.validate_registration_token", None): CREDENTIAL_CHANNEL,
    EndpointKey("storage_vision.views.VisionCameraViewSet", "heartbeat"): DEVICE_CHANNEL,
}

#: Routes whose permissions this probe could not READ, with the reason each is
#: left alone anyway. Kept apart from :data:`ANONYMOUS_WRITES_NOT_ISSUED`
#: because the two say different things: that one says "anyone can reach it and
#: here is why we do not issue it", this one says "we could not tell who can
#: reach it". Anything not listed here comes back unclassified.
WRITES_WITH_UNREADABLE_PERMISSIONS: dict[EndpointKey, str] = {
    EndpointKey("django.contrib.auth.views.LoginView", None): NOT_OUR_VIEW,
    EndpointKey("django.contrib.auth.views.LogoutView", None): NOT_OUR_VIEW,
    EndpointKey("django.views.generic.base.RedirectView", None): NOT_OUR_VIEW,
    EndpointKey("drf_spectacular.views.SpectacularAPIView", None): NOT_OUR_VIEW,
    EndpointKey("drf_spectacular.views.SpectacularSwaggerView", None): NOT_OUR_VIEW,
    EndpointKey("passkeys.views.PasskeyInfo", None): NOT_OUR_VIEW,
    EndpointKey("passkeys.views.PasskeyLogin", None): NOT_OUR_VIEW,
    EndpointKey("passkeys.views.PasskeyRegister", None): NOT_OUR_VIEW,
    EndpointKey("rest_framework.routers.APIRootView", None): NOT_OUR_VIEW,
}


def _write_bodies(objs) -> dict[EndpointKey, list[tuple[dict, str]]]:
    """The request bodies for the writes this probe DOES issue.

    Keyed by the permission snapshot's own ``(view_path, action)``, because
    that is what the derivation below produces; the PATH comes from the route,
    never from here, so a moved URL cannot leave a request pointing at nothing.

    A derivation can find the routes. It cannot invent a payload a serializer
    will accept, which is why this half is written down — and why a derived
    route with no entry here and no entry in
    :data:`ANONYMOUS_WRITES_NOT_ISSUED` fails.
    """
    item, fixture = objs["item"], objs["fixture"]
    return {
        EndpointKey("scanner.views.dispatch_scan", None): [
            ({"payload": VENDOR_SENTINELS["PACKAGE_UPC"]}, "json"),
            ({"payload": VENDOR_SENTINELS["UNIT_UPC"]}, "json"),
            ({"payload": f"/inventory/scan/{item.id}"}, "json"),
        ],
        EndpointKey("inventory.views.InventoryItemViewSet", "scan"): [({}, "json")],
        EndpointKey("inventory.views.InventoryItemViewSet", "generate_qr"): [({}, "json")],
        EndpointKey("inventory.views.InventoryItemViewSet", "log_usage"): [
            ({"quantity": 1}, "json")
        ],
        EndpointKey("inventory.views.LocationViewSet", "generate_qr"): [({}, "json")],
        EndpointKey("inventory.views.LocationViewSet", "report_problem"): [
            ({"description": "shelf empty", "severity": "medium"}, "multipart")
        ],
        EndpointKey("inventory.views.FixtureViewSet", "scan"): [({}, "json")],
        EndpointKey("inventory.views.FixtureRefillRequestViewSet", "create"): [
            ({"fixture": str(fixture.id), "notes": "empty"}, "json")
        ],
        EndpointKey("reorder_queue.views.ReorderRequestViewSet", "create"): [
            ({"item": str(item.id), "quantity": 5, "requested_by": "anon"}, "json")
        ],
    }


#: HTTP methods that read. Anything else is a write for the purposes below.
_READ_METHODS = ("get", "head", "options")


def _lets_anyone_in(permission_classes: tuple[str, ...]) -> bool:
    """Whether this resolved permission set admits a caller with no credentials.

    NOT ``== ("AllowAny",)``. An EMPTY set runs no permission check at all, so
    it is MORE open than ``AllowAny`` and was being dropped by an equality test
    for being spelled differently —
    ``membership.views.register_user_with_token`` and two others sat outside
    the derived set for exactly that reason.

    ``(unresolved)`` is deliberately NOT handled here: it means
    ``_perm_names`` could not run ``get_permissions`` and fell back to the
    declared classes, so the real answer is unknown.
    :func:`routed_anonymous_writes` reports those separately rather than
    guessing in either direction.
    """
    return set(permission_classes) in (set(), {"AllowAny"})


def routed_anonymous_writes() -> tuple[dict[EndpointKey, str], dict[EndpointKey, str]]:
    """Every routed non-GET surface an anonymous caller can reach.

    Returns ``(anonymous, unreadable)``: the routes whose resolved permissions
    let anyone in, and the routes whose permissions this probe could not read —
    either because ``get_permissions`` did not resolve, or because the endpoint
    sits outside the permission snapshot's own scope
    (``config.permission_matrix`` audits ``api/`` and skips framework views).
    The second dict is returned rather than dropped, because "I could not read
    its permissions" must never be recorded as "it is closed".

    DERIVED, not listed. The routes are walked here rather than taken from
    :func:`~config.permission_matrix.introspect_endpoints` alone because that
    snapshot keeps one example method per action and drops the callback, and
    both are needed: the callback says which HTTP methods a function-based view
    accepts, and without it every AllowAny GET-only function view would be
    counted as a write. The PERMISSION answer still comes from the snapshot,
    which resolves ``get_permissions`` and applies an ``@action`` override the
    way DRF does.
    """
    snapshots = introspect_endpoints()
    found: dict[EndpointKey, str] = {}
    unreadable: dict[EndpointKey, str] = {}

    def walk(patterns, prefix=""):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, prefix + str(pattern.pattern))
                continue
            if not isinstance(pattern, URLPattern):
                continue
            route = prefix + str(pattern.pattern)
            callback = pattern.callback
            view_cls = (
                getattr(callback, "cls", None) or getattr(callback, "view_class", None) or callback
            )
            view_path = (
                f"{getattr(view_cls, '__module__', '?')}.{getattr(view_cls, '__name__', '?')}"
            )
            actions = getattr(callback, "actions", None) or {}
            if actions:
                # HEAD is excluded because DRF ADDS IT: ``ViewSetMixin.as_view``
                # copies ``actions["get"]`` onto ``actions["head"]`` the first
                # time the route is dispatched, in place, on the same dict this
                # reads. Filtering only "get" therefore counted every AllowAny
                # READ as a write — but only once some earlier test in the run
                # had requested it, which is the order-dependence a gate can
                # least afford.
                names = {name for method, name in actions.items() if method not in _READ_METHODS}
            else:
                writes = [
                    method
                    for method in getattr(view_cls, "http_method_names", [])
                    if method not in _READ_METHODS
                ]
                names = {None} if writes else set()
            for name in names:
                key = EndpointKey(view_path, name)
                snapshot = snapshots.get(key)
                if snapshot is None:
                    unreadable.setdefault(key, f"{route} (outside the permission snapshot)")
                elif "(unresolved)" in snapshot.permission_classes:
                    unreadable.setdefault(
                        key,
                        f"{route} (get_permissions did not resolve: {snapshot.permission_classes})",
                    )
                elif _lets_anyone_in(snapshot.permission_classes):
                    found.setdefault(key, route)

    walk(get_resolver().url_patterns)
    return found, unreadable


def anonymous_write_surfaces(objs, fill) -> tuple[list[tuple[str, dict, str]], list[str]]:
    """Every anonymous WRITE, as ``([(path, body, format)], unclassified)``.

    The GET crawl cannot exercise these — it would be writing to the database —
    and that blind spot hid a real disclosure: ``POST /api/scanner/dispatch/``
    is ``AllowAny`` by design (a barcode gun's entry point) and answered a UPC
    scan with the vendor's name. A UPC is printed on the outside of the box.

    THE SET IS DERIVED, THE PAYLOADS ARE NOT, and the halves are held together
    here: :func:`routed_anonymous_writes` finds every non-GET route an
    anonymous caller can reach AND every one whose permissions it could not
    read, :func:`_write_bodies` says what to send to the ones this probe
    issues, and :data:`ANONYMOUS_WRITES_NOT_ISSUED` /
    :data:`WRITES_WITH_UNREADABLE_PERMISSIONS` say why each of the rest is left
    alone. Anything in none of them comes back in ``unclassified`` — a new
    anonymous write cannot be skipped silently, which is what a hand-written
    list of paths allowed.

    A path is built in STRICT mode, so a route whose pk this fixture has not
    mapped is reported rather than filled from another table: that fallback
    404s, and a 404 leaks nothing and so reads as clean. It had silently
    dropped both ``LocationViewSet`` writes.

    A 4xx that reaches the view is a fine outcome for an issued write — the
    point is that whatever comes back names no vendor — but the caller is
    expected to refuse a 404; see
    ``test_anonymous_vendor_exposure.py::test_no_anonymous_write_names_a_vendor_in_its_reply``.
    """
    bodies = _write_bodies(objs)
    requests: list[tuple[str, dict, str]] = []
    unclassified: list[str] = []

    anonymous, unreadable = routed_anonymous_writes()

    for key, why in sorted(unreadable.items(), key=lambda kv: str(kv[0])):
        if key not in WRITES_WITH_UNREADABLE_PERMISSIONS:
            unclassified.append(
                f"{key} ({why}): a non-GET route whose permissions this probe cannot read, "
                "so it cannot be called closed. Say why it is left alone in "
                "WRITES_WITH_UNREADABLE_PERMISSIONS."
            )

    for key, route in sorted(anonymous.items(), key=lambda kv: str(kv[0])):
        if key in bodies:
            try:
                path = concrete_path(route, fill, strict=True)
            except UnmappedRouteArg as exc:
                unclassified.append(f"{key}: {exc}")
                continue
            if path is None:
                unclassified.append(f"{key}: route {route} could not be turned into a request")
                continue
            requests.extend((path, body, fmt) for body, fmt in bodies[key])
        elif key not in ANONYMOUS_WRITES_NOT_ISSUED:
            unclassified.append(
                f"{key} ({route}): an anonymous write with no body in _write_bodies and no "
                "reason in ANONYMOUS_WRITES_NOT_ISSUED. Exercise it, or say why not."
            )

    return requests, unclassified
