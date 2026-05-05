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

from config.permission_matrix import diff, introspect_endpoints, load_matrix


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
