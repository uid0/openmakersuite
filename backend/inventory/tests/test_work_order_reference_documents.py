"""Tests for the work order's "Documentation & References" surface (op-pzae).

Whoever performs and signs a work order needs the manual — and needs to know
which revision is current — without leaving the job. Rather than new link
fields on the work order, this reuses the per-asset document library:
``AssetDocument`` already carries ``version`` / ``is_current`` / ``supersedes``,
so "revision history" is that chain and a freshly uploaded manual is reachable
the moment it lands.

Covers the pinned ``WorkOrderSerializer.reference_documents`` contract (ScanTTY
decodes these exact keys), the prefetch that keeps a deep chain from becoming an
N+1, the printed block above the sign-off, and the invariant that a
reference-only section leaves the OMR targets alone.
"""

from __future__ import annotations

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext

import pytest
from pypdf import PdfReader

from inventory.models import AssetDocument
from inventory.serializers import WorkOrderSerializer
from inventory.services.work_order_context import (
    MAX_REVISION_DEPTH,
    build_reference_documents_context,
)
from inventory.services.work_order_omr import compute_template_version, dynamic_target_ids
from inventory.tests.test_work_order_ingest import _make_work_order
from inventory.utils.work_order_pdf import generate_work_order_pdf
from inventory.views import WorkOrderViewSet

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_media(settings, tmp_path):
    """Keep uploaded test files out of the tracked backend/media tree."""
    settings.MEDIA_ROOT = str(tmp_path)


def _make_file(name="manual.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 fake", content_type="application/pdf")


def _make_document(asset, **kwargs) -> AssetDocument:
    kwargs.setdefault("title", "Operator manual")
    kwargs.setdefault("category", AssetDocument.Category.MANUAL)
    kwargs.setdefault("file", _make_file())
    return AssetDocument.objects.create(asset=asset, **kwargs)


def _supersede(asset, older: AssetDocument, **kwargs) -> AssetDocument:
    """Upload a newer version of ``older``, exactly as the viewset does."""
    newer = _make_document(
        asset,
        title=kwargs.pop("title", older.title),
        category=kwargs.pop("category", older.category),
        version=older.version + 1,
        supersedes=older,
        **kwargs,
    )
    older.is_current = False
    older.save(update_fields=["is_current"])
    return newer


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _serialize(work_order, request=None) -> dict:
    return WorkOrderSerializer(work_order, context={"request": request}).data["reference_documents"]


# ─────────────────────────────────────────────────────────────────────────────
# Serializer — pinned shape
# ─────────────────────────────────────────────────────────────────────────────


class TestReferenceDocumentsShape:
    def test_exposes_the_pinned_keys(self, rf):
        wo = _make_work_order(num_tasks=1)
        _make_document(wo.maintenance_item.asset, title="Bandsaw manual")

        payload = _serialize(wo, rf.get("/api/inventory/work-orders/"))

        assert set(payload) == {"documents", "links"}
        assert set(payload["documents"][0]) == {
            "id",
            "category",
            "category_display",
            "title",
            "version",
            "file_url",
            "uploaded_at",
            "revisions",
        }

    def test_only_current_documents_head_the_list(self, rf):
        """A superseded row is history, not something to hand a tech."""
        wo = _make_work_order(num_tasks=1)
        asset = wo.maintenance_item.asset
        v1 = _make_document(asset, title="Bandsaw manual")
        _supersede(asset, v1)

        payload = _serialize(wo, rf.get("/api/inventory/work-orders/"))

        assert [doc["version"] for doc in payload["documents"]] == [2]

    def test_revisions_are_the_supersedes_chain_newest_first(self, rf):
        wo = _make_work_order(num_tasks=1)
        asset = wo.maintenance_item.asset
        v1 = _make_document(asset, title="Bandsaw manual")
        v2 = _supersede(asset, v1)
        _supersede(asset, v2)

        payload = _serialize(wo, rf.get("/api/inventory/work-orders/"))

        (manual,) = payload["documents"]
        assert manual["version"] == 3
        assert [rev["version"] for rev in manual["revisions"]] == [2, 1]
        assert set(manual["revisions"][0]) == {"id", "version", "file_url", "uploaded_at"}
        assert manual["revisions"][0]["file_url"].endswith(".pdf")

    def test_manual_sorts_first_then_category_and_title(self, rf):
        wo = _make_work_order(num_tasks=1)
        asset = wo.maintenance_item.asset
        _make_document(asset, title="Zeta wiring", category=AssetDocument.Category.WIRING_DIAGRAM)
        _make_document(
            asset, title="Alpha cut sheet", category=AssetDocument.Category.CUT_SHEET_SPEC
        )
        _make_document(asset, title="Operator manual")

        payload = _serialize(wo, rf.get("/api/inventory/work-orders/"))

        assert [doc["title"] for doc in payload["documents"]] == [
            "Operator manual",
            "Alpha cut sheet",
            "Zeta wiring",
        ]

    def test_file_urls_are_absolute_against_the_request(self, rf):
        wo = _make_work_order(num_tasks=1)
        _make_document(wo.maintenance_item.asset)

        payload = _serialize(wo, rf.get("/api/inventory/work-orders/"))

        assert payload["documents"][0]["file_url"].startswith("http://testserver/")

    def test_a_document_without_a_file_reads_back_null(self, rf):
        """A row can outlive its file — the page must not 500 over it."""
        wo = _make_work_order(num_tasks=1)
        _make_document(wo.maintenance_item.asset, file="")

        payload = _serialize(wo, rf.get("/api/inventory/work-orders/"))

        assert payload["documents"][0]["file_url"] is None


class TestReferenceDocumentsLinks:
    def test_only_the_links_that_are_set_are_listed(self, rf):
        wo = _make_work_order(num_tasks=1)
        asset = wo.maintenance_item.asset
        asset.product_url = "https://example.com/bandsaw"
        asset.wiki_page_url = ""
        asset.manual_pdf = _make_file("legacy-manual.pdf")
        asset.save(update_fields=["product_url", "wiki_page_url", "manual_pdf"])

        payload = _serialize(wo, rf.get("/api/inventory/work-orders/"))

        assert payload["documents"] == []
        assert [link["label"] for link in payload["links"]] == [
            "Manual (PDF)",
            "Product / documentation page",
        ]
        assert payload["links"][0]["url"].startswith("http://testserver/")
        assert payload["links"][1]["url"] == "https://example.com/bandsaw"

    def test_all_three_quick_links_when_all_are_set(self, rf):
        wo = _make_work_order(num_tasks=1)
        asset = wo.maintenance_item.asset
        asset.product_url = "https://example.com/bandsaw"
        asset.wiki_page_url = "https://wiki.example.com/bandsaw"
        asset.manual_pdf = _make_file("legacy-manual.pdf")
        asset.save(update_fields=["product_url", "wiki_page_url", "manual_pdf"])

        payload = _serialize(wo, rf.get("/api/inventory/work-orders/"))

        assert [link["label"] for link in payload["links"]] == [
            "Manual (PDF)",
            "Product / documentation page",
            "Wiki",
        ]

    def test_asset_with_nothing_returns_the_empty_shape(self, rf):
        wo = _make_work_order(num_tasks=1)

        payload = _serialize(wo, rf.get("/api/inventory/work-orders/"))

        assert payload == {"documents": [], "links": []}


class TestReferenceDocumentsRobustness:
    def test_a_supersedes_cycle_terminates(self):
        """Bad data (only reachable around the serializer's guard) must not spin."""
        wo = _make_work_order(num_tasks=1)
        asset = wo.maintenance_item.asset
        v1 = _make_document(asset, title="Looping manual")
        v2 = _supersede(asset, v1)
        v1.supersedes = v2
        v1.save(update_fields=["supersedes"])

        (manual,) = build_reference_documents_context(asset)["documents"]

        assert [rev["version"] for rev in manual["revisions"]] == [1]

    def test_a_long_chain_is_capped(self):
        wo = _make_work_order(num_tasks=1)
        asset = wo.maintenance_item.asset
        doc = _make_document(asset, title="Much-revised manual")
        for _ in range(MAX_REVISION_DEPTH + 3):
            doc = _supersede(asset, doc)

        (manual,) = build_reference_documents_context(asset)["documents"]

        assert len(manual["revisions"]) == MAX_REVISION_DEPTH

    def test_a_link_to_another_assets_document_does_not_leak(self):
        wo = _make_work_order(num_tasks=1)
        other = _make_work_order(num_tasks=1).maintenance_item.asset
        stray = _make_document(other, title="Someone else's manual")
        _make_document(wo.maintenance_item.asset, title="Our manual", supersedes=stray)

        (manual,) = build_reference_documents_context(wo.maintenance_item.asset)["documents"]

        assert manual["revisions"] == []

    def test_the_pdf_builder_absolutizes_against_base_url(self):
        wo = _make_work_order(num_tasks=1)
        _make_document(wo.maintenance_item.asset)

        payload = build_reference_documents_context(
            wo.maintenance_item.asset, base_url="https://oms.example.org"
        )

        assert payload["documents"][0]["file_url"].startswith("https://oms.example.org/")


class TestReferenceDocumentsQueryCount:
    def test_the_prefetch_absorbs_a_deep_revision_chain(self, rf):
        """Chain depth must not add queries — the walk runs off one prefetch."""

        def documented_work_order(revisions: int):
            wo = _make_work_order(num_tasks=1)
            asset = wo.maintenance_item.asset
            doc = _make_document(asset, title="Bandsaw manual")
            for _ in range(revisions):
                doc = _supersede(asset, doc)
            return wo

        shallow = documented_work_order(0)
        deep = documented_work_order(5)

        request = rf.get("/api/inventory/work-orders/")

        def render(work_order):
            with CaptureQueriesContext(connection) as captured:
                instance = WorkOrderViewSet.queryset.get(id=work_order.id)
                payload = WorkOrderSerializer(instance, context={"request": request}).data[
                    "reference_documents"
                ]
            return len(captured), payload

        shallow_queries, shallow_payload = render(shallow)
        deep_queries, deep_payload = render(deep)

        assert deep_queries == shallow_queries
        assert shallow_payload["documents"][0]["revisions"] == []
        assert len(deep_payload["documents"][0]["revisions"]) == 5


# ─────────────────────────────────────────────────────────────────────────────
# Printed form — the block above the sign-off
# ─────────────────────────────────────────────────────────────────────────────


class TestWorkOrderPdfReferences:
    def test_documents_print_above_the_sign_off(self):
        wo = _make_work_order(num_tasks=1)
        asset = wo.maintenance_item.asset
        v1 = _make_document(asset, title="Bandsaw manual")
        _supersede(asset, v1)
        asset.wiki_page_url = "https://wiki.example.com/bandsaw"
        asset.save(update_fields=["wiki_page_url"])

        text = _pdf_text(generate_work_order_pdf(wo, base_url="https://oms.example.org"))

        assert "Documentation & References" in text
        assert "Bandsaw manual" in text
        assert "Manual / Documentation" in text
        assert "rev 2" in text
        assert "supersedes rev 1" in text
        assert "https://wiki.example.com/bandsaw" in text
        assert "https://oms.example.org/" in text
        assert text.index("Documentation & References") < text.index("Work Order Sign-Off")

    def test_an_asset_without_documents_prints_the_empty_state(self):
        wo = _make_work_order(num_tasks=1)

        text = _pdf_text(generate_work_order_pdf(wo, base_url="https://oms.example.org"))

        assert "No linked documents." in text

    def test_xml_metacharacters_in_a_title_do_not_break_the_pdf(self):
        wo = _make_work_order(num_tasks=1)
        _make_document(wo.maintenance_item.asset, title="Manual <b>v2</b> & addendum")

        text = _pdf_text(generate_work_order_pdf(wo, base_url="https://oms.example.org"))

        assert "Manual <b>v2</b> & addendum" in text

    def test_references_add_no_omr_marks_and_no_drift(self):
        """Reference-only: a sheet printed before the docs landed still scans."""
        wo = _make_work_order(num_tasks=2)
        before_version = compute_template_version(wo)
        before_targets = set(dynamic_target_ids(wo))

        asset = wo.maintenance_item.asset
        _make_document(asset, title="Bandsaw manual")
        asset.wiki_page_url = "https://wiki.example.com/bandsaw"
        asset.save(update_fields=["wiki_page_url"])

        assert compute_template_version(wo) == before_version
        assert set(dynamic_target_ids(wo)) == before_targets
