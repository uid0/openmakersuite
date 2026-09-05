"""gh #328 — guard against drift between docs/API_PERMISSION_MATRIX.md and
the actual ``permission_classes`` enforced by every DRF view.

The Markdown matrix is the human-readable contract; ``api_permission_matrix.yaml``
is the machine-readable snapshot that this test pins. When a PR changes a
view's ``permission_classes``, the developer must regenerate the YAML
(``python manage.py check_permission_matrix --write``) and update the
Markdown matrix to match. Otherwise this test fails CI.
"""

from __future__ import annotations

import pytest

from config.permission_matrix import (
    EndpointKey,
    diff,
    introspect_endpoints,
    load_matrix,
)


@pytest.mark.unit
def test_permission_matrix_in_sync():
    actual = introspect_endpoints()
    expected = load_matrix()
    missing, stale, mismatched = diff(actual, expected)

    if missing or stale or mismatched:
        sections = []
        if missing:
            sections.append(
                "Endpoints missing from api_permission_matrix.yaml — every "
                "URL-routed DRF view must be classified:\n" + "\n".join(missing)
            )
        if stale:
            sections.append(
                "Stale entries in api_permission_matrix.yaml — the listed view "
                "is no longer routed and should be removed:\n" + "\n".join(stale)
            )
        if mismatched:
            sections.append(
                "permission_classes drift — code disagrees with the YAML "
                "snapshot. Update both the YAML and "
                "docs/API_PERMISSION_MATRIX.md in the same PR:\n" + "\n".join(mismatched)
            )
        sections.append(
            "Run `python manage.py check_permission_matrix --write` to "
            "refresh the YAML, then update the Markdown matrix."
        )
        pytest.fail("\n\n".join(sections))


@pytest.mark.unit
def test_permission_matrix_covers_documented_apps():
    """Every app the Markdown matrix calls out must appear at least once
    in the YAML, so newly-mounted apps cannot ship without a matrix entry."""
    expected = load_matrix()
    documented_modules = {
        "auth_views",
        "checklists.views",
        "customization.views",
        "dashboard.views",
        "donations.views",
        "electrical_circuits.views",
        "forgekey.views",
        "index_cards.views",
        "inventory.views",
        "location_checkins.views",
        "loto.views",
        "maintenance_orders.views",
        "maker_boxes.views",
        "membership.views",
        "notifications.views",
        "reorder_queue.views",
        "resilience.views",
        "screens.views",
        "search.views",
        "vendors.views",
    }
    seen = {key.view_path.rsplit(".", 1)[0] for key in expected}
    missing = sorted(documented_modules - seen)
    assert not missing, (
        "Apps documented in docs/API_PERMISSION_MATRIX.md are absent from "
        f"api_permission_matrix.yaml: {missing}. Either remove them from the "
        "Markdown matrix or run `manage.py check_permission_matrix --write`."
    )


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.parametrize(
    "view_path,action,expected,path,anonymous_status",
    [
        # Declares no ``permission_classes`` at all and returns AllowAny from
        # ``get_permissions``. The old snapshot recorded the DRF default.
        (
            "inventory.views.InventoryItemViewSet",
            "list",
            ("AllowAny",),
            "/api/inventory/items/",
            200,
        ),
        # Same class, same method, opposite answer — which is the whole reason
        # a per-view entry cannot describe a ``get_permissions`` view.
        (
            "inventory.views.InventoryItemViewSet",
            "download_blank_card",
            ("IsAuthenticated",),
            None,
            None,
        ),
        # Closed on this branch. Was recorded ``IsAuthenticatedOrReadOnly``
        # while ``get_permissions`` returned ``AllowAny`` for reads.
        (
            "reorder_queue.views.PurchaseOrderViewSet",
            "list",
            ("IsAuthenticated",),
            "/api/reorders/purchase-orders/",
            401,
        ),
        # An ``@action`` override on a class that ALSO overrides
        # ``get_permissions``: DRF applies the action's list as initkwargs, so
        # the resolution must too. Recording the class answer here would say
        # this public feed is authenticated.
        (
            "reorder_queue.views.AnalyticsViewSet",
            "transparency",
            ("AllowAny",),
            "/api/reorders/analytics/transparency/",
            200,
        ),
        # The anonymous QR-scan reorder, which must stay open.
        (
            "reorder_queue.views.ReorderRequestViewSet",
            "create",
            ("AllowAny",),
            None,
            None,
        ),
        (
            "inventory.views.SupplierViewSet",
            "list",
            ("IsAuthenticated",),
            "/api/inventory/suppliers/",
            401,
        ),
    ],
)
def test_the_matrix_records_what_is_enforced_not_what_is_declared(
    view_path, action, expected, path, anonymous_status, client
):
    """The matrix's own correctness, checked against the server.

    ``docs/API_PERMISSION_MATRIX.md`` and its YAML used to snapshot DECLARED
    ``permission_classes``, so every view overriding ``get_permissions`` was
    recorded wrongly — not vaguely, but with the opposite answer for the six
    rows below. That is a defect in its own right: this document is read as
    evidence about who can reach what, and it was cited to conclude a screen was
    anonymously readable when it was not (op-anonymous-read-posture).

    Each case pins the snapshot AND, where the endpoint can be exercised, what
    an unauthenticated request actually gets — so the document cannot drift back
    into describing something the server does not do.
    """
    snapshot = introspect_endpoints()[EndpointKey(view_path, action)]
    assert snapshot.permission_classes == expected

    stored = load_matrix()[EndpointKey(view_path, action)]
    assert stored == expected, "api_permission_matrix.yaml disagrees with the code"

    if path is not None:
        assert client.get(path).status_code == anonymous_status
