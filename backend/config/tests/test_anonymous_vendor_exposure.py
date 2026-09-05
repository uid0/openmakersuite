"""The gate on the captain's decision: vendor identity and vendor pricing are
never reachable without a login, and anonymous scan-to-reorder still works.

    "Vendor names should not be public, same with Vendor Pricing. They should
    always be behind user auth."

WHY A CRAWL AND NOT A LIST OF ENDPOINTS. A hand-enumerated reader set has gone
stale twice on this repo already (see AGENTS.md, "when a hand sweep misses
TWICE, build the gate instead of sweeping a third time"). This walks the live
URL conf, requests every routed GET with no credentials, and fails on any
response carrying a seeded vendor sentinel — so a viewset added later, or one
whose ``get_permissions`` quietly widens, fails here rather than in production.

WHY IT DOES NOT READ ``permission_classes``. ``docs/API_PERMISSION_MATRIX.md``
and ``api_permission_matrix.yaml`` record only DECLARED classes. Every viewset
that overrides ``get_permissions`` is misreported by construction, which is how
a screen came to be described as anonymously readable when it was not. Only an
actual request settles it.

The honest limit is named and asserted, not hidden: routes the probe cannot
turn into a concrete request are reported by
:func:`~config.tests.vendor_exposure_probe.crawl_anonymously` as *unreachable*,
and the targeted tests below cover the vendor-bearing ones by hand with real
primary keys.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from config.tests.vendor_exposure_probe import (
    PUBLIC_SENTINELS,
    VENDOR_SENTINELS,
    crawl_anonymously,
    seed_vendor_fixture,
    sentinels_in,
)


@pytest.fixture
def vendor_fixture(db):
    return seed_vendor_fixture()


@pytest.fixture
def anonymous():
    return APIClient()


def _fill(objs):
    return {
        "__uuid__": str(objs["item"].id),
        "fixture_pk": str(objs["fixture"].id),
        "__default_pk__": str(objs["supplier"].id),
        # A DRF router pk is untyped, so one value cannot serve every table.
        # Without these the item and kit detail routes 404 and the crawl reports
        # them clean without ever having read one.
        "__pk_by_prefix__": {
            "inventory/^items/": str(objs["item"].id),
            "inventory/^kits/": str(objs["item"].id),
            "inventory/^locations/": str(objs["location"].id),
            "inventory/^fixtures/": str(objs["fixture"].id),
            "inventory/^price-history/": str(objs["price_history"].id),
            "inventory/^item-suppliers/": str(objs["link"].id),
            "inventory/^supplier-agreements/": str(objs["agreement"].id),
            "inventory/^usage-logs/": str(objs["usage_log"].id),
            "reorders/^purchase-orders/": str(objs["po"].id),
            "reorders/^requests/": str(objs["reorder_request"].id),
        },
        "item_id": str(objs["item"].id),
        "location_id": str(objs["location"].id),
        "supplier_id": str(objs["supplier"].id),
    }


@pytest.mark.integration
def test_no_routed_get_discloses_vendor_data_to_an_anonymous_caller(vendor_fixture, anonymous):
    """The whole-surface gate. Before this branch it failed on 21 paths."""
    disclosures, transcript, _unreachable = crawl_anonymously(anonymous, _fill(vendor_fixture))

    assert disclosures == [], "anonymous requests disclosed vendor data:\n" + "\n".join(
        f"  {status} {path}  [{view}#{action}]  {', '.join(leaked)}"
        for path, view, action, status, leaked in disclosures
    )
    # A crawl that reached nothing would pass this vacuously.
    assert len([e for e in transcript if e[3] == 200]) > 50, "the crawl reached almost nothing"


@pytest.mark.integration
def test_the_crawl_would_catch_a_disclosure(vendor_fixture, anonymous):
    """CONTROL. Proves the instrument detects what it claims to detect, so a
    green run above is evidence rather than an artefact of a broken probe."""
    response = anonymous.get(f"/api/inventory/items/{vendor_fixture['item'].id}/")
    assert response.status_code == 200
    assert sentinels_in(response, PUBLIC_SENTINELS) == ["ITEM_NAME", "ITEM_SKU"]


# --- Targeted per-surface closures. Each names one endpoint that served
# --- vendor data to an anonymous caller before this branch.


@pytest.mark.integration
@pytest.mark.parametrize(
    "path_key",
    [
        "suppliers-list",
        "suppliers-detail",
        "supplier-analytics",
        "supplier-agreements-list",
        "supplier-agreements-detail",
        "item-suppliers-list",
        "item-suppliers-detail",
        "item-supplier-price-history",
        "price-history-list",
        "price-history-detail",
        "purchase-orders-list",
        "purchase-orders-detail",
    ],
)
def test_vendor_only_endpoints_require_authentication(vendor_fixture, anonymous, path_key):
    """These endpoints exist only to serve vendor identity or vendor money, so
    they are closed outright rather than field-filtered."""
    objs = vendor_fixture
    paths = {
        "suppliers-list": "/api/inventory/suppliers/",
        "suppliers-detail": f"/api/inventory/suppliers/{objs['supplier'].id}/",
        "supplier-analytics": f"/api/inventory/suppliers/{objs['supplier'].id}/analytics/",
        "supplier-agreements-list": "/api/inventory/supplier-agreements/",
        "supplier-agreements-detail": (
            f"/api/inventory/supplier-agreements/{objs['agreement'].id}/"
        ),
        "item-suppliers-list": "/api/inventory/item-suppliers/",
        "item-suppliers-detail": f"/api/inventory/item-suppliers/{objs['link'].id}/",
        "item-supplier-price-history": (
            f"/api/inventory/item-suppliers/{objs['link'].id}/price_history/"
        ),
        "price-history-list": "/api/inventory/price-history/",
        "price-history-detail": f"/api/inventory/price-history/{objs['price_history'].id}/",
        "purchase-orders-list": "/api/reorders/purchase-orders/",
        "purchase-orders-detail": f"/api/reorders/purchase-orders/{objs['po'].id}/",
    }
    response = anonymous.get(paths[path_key])
    assert response.status_code in (
        401,
        403,
    ), f"{paths[path_key]} answered {response.status_code} to a caller with no session"
    assert sentinels_in(response) == []


@pytest.mark.integration
@pytest.mark.parametrize(
    "path_template",
    [
        "/api/inventory/items/",
        "/api/inventory/items/{item}/",
        "/api/inventory/items/{item}/metrics/",
        "/api/inventory/items/{item}/download_card/",
        "/api/inventory/fixtures/{fixture}/download_card/",
        "/api/inventory/items/{item}/kits/",
        "/api/inventory/items/?with_metrics=1",
        "/api/inventory/items/low_stock/",
        "/api/inventory/items/reordered/",
        "/api/reorders/analytics/transparency/",
    ],
)
def test_public_surfaces_stay_reachable_but_drop_vendor_fields(
    vendor_fixture, anonymous, path_template
):
    """These must stay open — the scan path and the transparency feed run on
    them — so the gate is on the FIELDS, exactly as ``get_inventory_summary``
    already gates its valuation."""
    path = path_template.format(
        item=vendor_fixture["item"].id, fixture=vendor_fixture["fixture"].id
    )
    response = anonymous.get(path)
    assert response.status_code == 200, f"{path} is no longer publicly reachable"
    assert sentinels_in(response) == [], f"{path} still discloses vendor data"


@pytest.mark.integration
def test_an_authenticated_caller_still_sees_vendor_data(vendor_fixture, django_user_model):
    """CONTROL for every closure above: the data is gated, not deleted."""
    client = APIClient()
    client.force_authenticate(user=vendor_fixture["staff"])

    item_detail = client.get(f"/api/inventory/items/{vendor_fixture['item'].id}/")
    assert item_detail.status_code == 200
    leaked = sentinels_in(item_detail)
    for expected in ("VENDOR_NAME", "SUPPLIER_SKU", "UNIT_COST", "LEAD_TIME"):
        assert expected in leaked, f"a signed-in caller lost {expected}"

    suppliers = client.get("/api/inventory/suppliers/")
    assert suppliers.status_code == 200
    assert "VENDOR_NAME" in sentinels_in(suppliers)

    # Every surface this branch gated, proven still whole for a signed-in reader.
    # Withheld, not deleted, is the difference between a gate and a regression.
    for path, expected in [
        (f"/api/inventory/items/{vendor_fixture['item'].id}/metrics/", "UNIT_COST"),
        ("/api/inventory/items/?with_metrics=1", "UNIT_COST"),
        (f"/api/inventory/items/{vendor_fixture['item'].id}/download_card/", "LEAD_TIME"),
        (f"/api/inventory/fixtures/{vendor_fixture['fixture'].id}/download_card/", "LEAD_TIME"),
        (f"/api/inventory/items/{vendor_fixture['item'].id}/kits/", None),
        ("/api/inventory/item-suppliers/", "SUPPLIER_SKU"),
        ("/api/inventory/price-history/", "PRICE_HISTORY_COST"),
        ("/api/inventory/supplier-agreements/", "AGREEMENT_NAME"),
        ("/api/reorders/purchase-orders/", "SUPPLIER_ORDER_NUMBER"),
        ("/api/reorders/analytics/transparency/", "VENDOR_NAME"),
    ]:
        response = client.get(path)
        assert response.status_code == 200, f"{path} broke for a signed-in caller"
        if expected is not None:
            assert expected in sentinels_in(
                response
            ), f"a signed-in caller lost {expected} on {path}"


@pytest.mark.integration
def test_the_withheld_marker_says_the_omission_is_policy(vendor_fixture, anonymous):
    """A missing ``supplier_name`` must not read as "this item has no supplier".

    ``null`` already means "nothing on file" throughout this payload family, so
    the keys are omitted and this flag is what tells a client the difference.
    """
    from inventory.services.vendor_visibility import VENDOR_WITHHELD_KEY

    body = anonymous.get(f"/api/inventory/items/{vendor_fixture['item'].id}/").json()
    assert body[VENDOR_WITHHELD_KEY] is True
    for withheld in ("supplier_name", "suppliers", "supplier_choice", "unit_cost", "total_value"):
        assert withheld not in body, f"{withheld} was nulled rather than omitted"

    signed_in = APIClient()
    signed_in.force_authenticate(user=vendor_fixture["staff"])
    theirs = signed_in.get(f"/api/inventory/items/{vendor_fixture['item'].id}/").json()
    assert VENDOR_WITHHELD_KEY not in theirs, "the marker leaked into an authenticated payload"
    assert theirs["supplier_name"] == VENDOR_SENTINELS["VENDOR_NAME"]


# --- The other half of the decision: what must NOT break.
# --- Anonymous QR scanning is a designed feature, not an oversight.


@pytest.mark.integration
def test_anonymous_scan_to_reorder_still_works_end_to_end(vendor_fixture, anonymous):
    """A member with no account scans a shelf label, reads enough to know they
    have the right item, and files a reorder — the flow the printed QR codes
    exist for. Pinned here because every closure on this branch runs through
    the same serializer this flow reads."""
    from reorder_queue.models import ReorderRequest

    item = vendor_fixture["item"]

    scan = anonymous.post(f"/api/inventory/items/{item.id}/scan/")
    assert scan.status_code == 200, "the QR scan endpoint refused an anonymous caller"

    # They keep everything needed to identify the item and size a request.
    scanned = scan.json()
    assert scanned["name"] == PUBLIC_SENTINELS["ITEM_NAME"]
    assert scanned["sku"] == PUBLIC_SENTINELS["ITEM_SKU"]
    assert scanned["current_stock"] == 1
    assert scanned["reorder_quantity"] == 20
    assert scanned["needs_reorder"] is True
    assert scanned["location"] == "ZZQQ Shelf A"
    # ...and nothing about who we buy it from or what we pay.
    assert sentinels_in(scan) == []

    before = ReorderRequest.objects.count()
    filed = anonymous.post(
        "/api/reorders/requests/",
        {
            "item": str(item.id),
            "quantity": 20,
            "requested_by": "a member with no account",
            "request_notes": "shelf was empty",
        },
        format="json",
    )
    assert filed.status_code == 201, f"anonymous reorder refused: {filed.content!r}"
    assert ReorderRequest.objects.count() == before + 1
    assert sentinels_in(filed) == [], "the reorder receipt disclosed vendor data"


@pytest.mark.integration
def test_no_anonymous_write_names_a_vendor_in_its_reply(vendor_fixture, anonymous):
    """The half the GET crawl cannot reach, exercised by hand.

    A write's REPLY is a read, and this is where the crawl's blind spot cost
    something real: ``dispatch_scan`` answered an anonymous UPC scan with the
    vendor's name. Every anonymous write is issued here and its reply checked.

    A REFUSED REQUEST IS A FAILURE HERE, not an outcome. A reply that never
    reached the view leaks nothing, so it passes the sentinel check while
    proving nothing about the surface it was aimed at. Every write below is
    accepted today; one that must be refused has to say so here rather than
    being absorbed by a permissive allowance.

    This is only half the guard, and the weaker half. When the pk lands on
    another TABLE the request 404s and this catches it; when it lands on a
    real-but-wrong ROW of the right table it succeeds and this cannot. That is
    what happened to both ``LocationViewSet`` writes once paths started coming
    from the route: the fallback pk named a Location that exists, so they
    answered 201 while exercising a row the fixture knows nothing about.
    ``concrete_path(..., strict=True)`` is what refuses that, and
    ``unclassified`` above is where it lands.
    """
    from config.tests.vendor_exposure_probe import anonymous_write_surfaces

    writes, unclassified = anonymous_write_surfaces(vendor_fixture, _fill(vendor_fixture))
    assert not unclassified, (
        "an anonymous write is neither issued nor classified, so nothing here "
        "reads it:\n" + "\n".join(f"  {line}" for line in unclassified)
    )
    assert len(writes) >= 11, f"the derivation found almost nothing: {writes}"

    disclosures = []
    unreached = []
    for path, body, fmt in writes:
        response = anonymous.post(path, body, format=fmt)
        if response.status_code >= 400:
            unreached.append(f"  {response.status_code} POST {path}")
        leaked = [
            name
            for name in sentinels_in(response)
            # ``raw_payload`` echoes the caller's own scan back at them, which
            # they already had. Everything else is the server telling them
            # something new.
            if not (
                path == "/api/scanner/dispatch/"
                and name in ("PACKAGE_UPC", "UNIT_UPC")
                and response.json().get("raw_payload") == VENDOR_SENTINELS[name]
            )
        ]
        if leaked:
            disclosures.append(f"  {response.status_code} POST {path} -> {', '.join(leaked)}")

    assert not unreached, (
        "these anonymous writes were refused, so their replies prove nothing about "
        "the surface they were aimed at:\n" + "\n".join(unreached)
    )
    assert not disclosures, "anonymous writes disclosed vendor data:\n" + "\n".join(disclosures)


@pytest.mark.integration
def test_the_scanner_dispatch_names_no_vendor_to_an_anonymous_caller(vendor_fixture, anonymous):
    """``POST /api/scanner/dispatch/`` — a surface the GET crawl cannot reach.

    It is ``AllowAny`` by design (it is what a barcode gun hits), and scanning a
    supplier's UPC resolves through ``ItemSupplier``, so the reply carried
    ``supplier_name`` and ``item_supplier_id``. A UPC is printed on the box —
    anyone holding one could turn it into the name of the vendor we buy from.

    Found by reading rather than by the crawl, because the crawl issues no
    writes; recorded here so the gap it represents is closed by a check rather
    than by an argument.
    """
    response = anonymous.post(
        "/api/scanner/dispatch/",
        {"payload": VENDOR_SENTINELS["PACKAGE_UPC"]},
        format="json",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    # What the scan is FOR still works: it resolves to the right item.
    assert body["item_name"] == PUBLIC_SENTINELS["ITEM_NAME"]
    assert body["action"] == "inventory_receive"
    # ...without saying whose barcode it was.
    assert "supplier_name" not in body
    assert "item_supplier_id" not in body
    # Everything except the caller's own scan echoed back in ``raw_payload`` —
    # they supplied that, so returning it discloses nothing.
    assert sentinels_in(response) == ["PACKAGE_UPC"]
    assert body["raw_payload"] == VENDOR_SENTINELS["PACKAGE_UPC"]


@pytest.mark.integration
def test_the_scanner_dispatch_still_names_the_vendor_to_an_operator(vendor_fixture):
    """CONTROL: receiving is an operator flow and needs the link it resolved."""
    client = APIClient()
    client.force_authenticate(user=vendor_fixture["staff"])

    response = client.post(
        "/api/scanner/dispatch/",
        {"payload": VENDOR_SENTINELS["PACKAGE_UPC"]},
        format="json",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["supplier_name"] == VENDOR_SENTINELS["VENDOR_NAME"]
    assert body["item_supplier_id"] == vendor_fixture["link"].pk


@pytest.mark.integration
def test_anonymous_issue_reporting_still_works(vendor_fixture, anonymous):
    """The scan codes' other purpose: telling someone a shelf has a problem."""
    from inventory.models import LocationProblem

    location = vendor_fixture["location"]
    before = LocationProblem.objects.count()
    reported = anonymous.post(
        f"/api/inventory/locations/{location.id}/report_problem/",
        {"description": "bin is empty and the label is torn", "severity": "medium"},
        format="multipart",  # the action declares MultiPartParser/FormParser
    )
    assert reported.status_code == 201, f"anonymous problem report refused: {reported.content!r}"
    assert LocationProblem.objects.count() == before + 1
