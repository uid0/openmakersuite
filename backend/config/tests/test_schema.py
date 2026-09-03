"""AC-10: API schema is validated.

These tests prove three things on every backend CI run:

1. ``manage.py spectacular --validate`` completes without raising — the
   OpenAPI 3.0 document we ship is structurally valid.
2. The schema includes the public and private workflow endpoints we treat
   as supported external contracts (kiosk QR scan, public reporting,
   inventory browse, asset detail). A regression that drops one of these
   paths fails CI.
3. The schema is reachable at ``/api/schema/`` over HTTP, so the swagger
   UI at ``/api/docs/`` keeps working.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from django.core.management import call_command

import pytest
import yaml

# Public + private workflow endpoints that integrators and the frontend
# treat as the external contract. If these disappear from the schema, a
# release is contract-breaking.
EXPECTED_PATHS = {
    "/api/inventory/items/",
    "/api/inventory/items/{id}/",
    "/api/inventory/assets/",
    "/api/inventory/locations/",
    "/api/inventory/suppliers/",
    "/api/reorders/requests/",
    "/api/reorders/purchase-orders/",
    "/api/donations/donations/",
    "/api/screens/screens/",
    "/api/forgekey/devices/",
    "/api/notifications/",
    "/api/maintenance-orders/work-orders/",
    "/api/auth/login/",
    "/api/auth/logout/",
    "/api/auth/refresh/",
}
# Note: /api/health/livez/ and /api/health/readyz/ are plain function-based
# views that don't ship with drf-spectacular hints — they are intentionally
# *not* part of the API contract surface clients should depend on, and so are
# documented in docs/DEPLOYMENT.MD instead. Same for /api/schema/, which
# spectacular does not document about itself.


@pytest.fixture(scope="module")
def schema_document(tmp_path_factory):
    """Run drf-spectacular's generator the same way CI does and parse the
    result. Module-scoped so we pay the cost once and reuse across tests."""
    out_path = Path(tmp_path_factory.mktemp("schema") / "schema.yaml")
    stdout = StringIO()
    # ``--validate`` triggers spectacular's structural OpenAPI 3 validator.
    # It raises ``SchemaGenerationError`` (or sub-exception) on a structural
    # break — the test fails loud if anyone introduces an unsupported view.
    call_command(
        "spectacular",
        "--validate",
        "--file",
        str(out_path),
        stdout=stdout,
        stderr=stdout,
    )
    return yaml.safe_load(out_path.read_text())


def test_schema_is_openapi_3(schema_document):
    assert schema_document.get("openapi", "").startswith("3."), schema_document.get("openapi")
    assert "info" in schema_document
    assert isinstance(schema_document.get("paths"), dict)


def test_schema_includes_critical_workflow_paths(schema_document):
    """Every entry in EXPECTED_PATHS must be present. Adding a new public
    contract endpoint requires extending this list — the schema test is the
    enforcement point."""
    paths = set(schema_document["paths"].keys())
    missing = EXPECTED_PATHS - paths
    assert not missing, f"Schema is missing critical paths: {sorted(missing)}"


def test_schema_documents_public_qr_scan(schema_document):
    """AC-2/AC-7: public QR scan endpoints must remain in the documented
    contract so kiosk and mobile clients can rely on them."""
    paths = schema_document["paths"]
    qr_path_candidates = [p for p in paths if "qr" in p.lower() or "scan" in p.lower()]
    assert qr_path_candidates, "Schema dropped all QR/scan endpoints"


@pytest.mark.django_db
def test_schema_endpoint_serves_yaml(api_client):
    resp = api_client.get("/api/schema/")
    assert resp.status_code == 200
    # SpectacularAPIView returns YAML by default.
    body = resp.content.decode()
    parsed = yaml.safe_load(body)
    assert parsed["openapi"].startswith("3.")
    assert "/api/inventory/items/" in parsed["paths"]


def test_schema_types_the_inventory_item_supplier_choice(schema_document):
    """``supplier_choice`` reaches the OpenAPI document as a typed object.

    ``InventoryItemSerializer.supplier_choice`` is a ``SerializerMethodField``,
    which drf-spectacular renders as an untyped, propertyless blob unless the
    getter is annotated. ``SupplierChoiceSerializer``'s declared fields exist
    for the schema alone — ``to_representation`` is fully overridden, so they
    never run — and a client generating a model off this document is the only
    consumer they have. Without the annotation those declarations are dead and
    the comment above them describes something the code does not do.
    """
    schemas = schema_document["components"]["schemas"]
    supplier_choice = schemas["InventoryItem"]["properties"]["supplier_choice"]

    # Spectacular wraps a $ref that carries sibling keywords (readOnly) in allOf.
    refs = [entry["$ref"] for entry in supplier_choice.get("allOf", []) if "$ref" in entry]
    if "$ref" in supplier_choice:
        refs.append(supplier_choice["$ref"])
    assert refs == ["#/components/schemas/SupplierChoice"], supplier_choice

    choice = schemas["SupplierChoice"]
    assert choice["type"] == "object"
    # The honesty fields are the point of the object; a document that omits
    # them tells a generated client the answer is just a name.
    assert set(choice["properties"]) == {
        "item_supplier_id",
        "supplier_id",
        "supplier_name",
        "basis",
        "reason",
        "flagged_primary_unorderable",
        "scored_without_price",
        "scored_without_history",
        "alternatives",
    }
    assert choice["properties"]["supplier_name"]["nullable"] is True
    assert choice["properties"]["supplier_id"]["nullable"] is True
    assert choice["properties"]["scored_without_price"]["type"] == "boolean"

    # The nested declaration has to be reachable too, or "and two others" has
    # no documented shape.
    assert choice["properties"]["alternatives"]["items"] == {
        "$ref": "#/components/schemas/SupplierChoiceAlternative"
    }
    assert set(schemas["SupplierChoiceAlternative"]["properties"]) == {"id", "supplier_name"}
