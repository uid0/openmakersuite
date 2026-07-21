"""Tests for the per-step photo pair (op-syov).

Two halves, deliberately asymmetric:

- **reference** — an instructional photo set once on the template step
  (``MaintenanceTask.reference_image``): "here is what this step should look
  like". It prints on the blank work-order form and shows on the digital WO.
- **evidence** — photo(s) a tech pins to a specific step while performing the
  work (``WorkOrderPhoto.task_completion``): "here is what I did". Electronic
  only — the sheet is already printed by then.

Covers the pinned serializer contract (ScanTTY decodes these exact keys), the
``add_photo`` cross-work-order guard, evidence grouping, the printed thumbnail,
and the invariant that neither half touches the OMR drift signature.
"""

from __future__ import annotations

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.crypto import get_random_string

import pytest
from PIL import Image as PILImage
from pypdf import PdfReader
from reportlab.lib.units import inch
from rest_framework import status

from inventory.models import MaintenanceTask, WorkOrderPhoto
from inventory.serializers import (
    MaintenanceTaskSerializer,
    WorkOrderSerializer,
    WorkOrderTaskCompletionSerializer,
)
from inventory.services.work_order_omr import (
    build_and_persist_omr_template,
    compute_template_version,
)
from inventory.tests.test_work_order_ingest import _make_work_order
from inventory.tests.test_work_order_omr import _staff_client
from inventory.utils.work_order_pdf import (
    STEP_PHOTO_DPI,
    STEP_PHOTO_MAX_WIDTH,
    generate_work_order_pdf,
)
from inventory.views import WorkOrderViewSet

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_media(settings, tmp_path):
    """Keep uploaded test images out of the tracked backend/media tree."""
    settings.MEDIA_ROOT = str(tmp_path)


def _image_bytes(size=(24, 18), color=(0, 128, 255)) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", size, color=color).save(buf, format="JPEG")
    buf.seek(0)
    return buf.read()


def _image_file(name="step.jpg", **kwargs) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, _image_bytes(**kwargs), content_type="image/jpeg")


def _pdf_images(pdf_bytes: bytes) -> list:
    """Every image XObject the PDF draws (the QR code is always one)."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    images = []
    for page in reader.pages:
        resources = page.get("/Resources") or {}
        xobjects = resources.get("/XObject")
        if xobjects is None:
            continue
        for ref in xobjects.get_object().values():
            obj = ref.get_object()
            if obj.get("/Subtype") == "/Image":
                images.append(obj)
    return images


def _pdf_image_count(pdf_bytes: bytes) -> int:
    return len(_pdf_images(pdf_bytes))


def _pdf_image_widths(pdf_bytes: bytes) -> list:
    """Pixel width of each embedded image — the *stored* size, not the drawn one."""
    return [int(obj["/Width"]) for obj in _pdf_images(pdf_bytes)]


def _add_photo_url(work_order) -> str:
    return reverse("workorder-add-photo", kwargs={"pk": work_order.id})


# ─────────────────────────────────────────────────────────────────────────────
# MaintenanceTaskSerializer — the template (reference) half
# ─────────────────────────────────────────────────────────────────────────────


class TestMaintenanceTaskSerializerReferenceImage:
    def test_exposes_the_pinned_field_set(self):
        wo = _make_work_order(num_tasks=1)
        task = MaintenanceTask.objects.get(maintenance_item=wo.maintenance_item)

        data = MaintenanceTaskSerializer(task).data

        # reference_image is write-only (file in, URL out), so it is absent from
        # the read payload while reference_image_url is present.
        assert set(data) == {
            "id",
            "maintenance_item",
            "order",
            "title",
            "description",
            "is_required",
            "reference_image_url",
            "created_at",
        }
        assert data["reference_image_url"] is None

    def test_reference_image_url_is_absolute_when_set(self, rf):
        wo = _make_work_order(num_tasks=1)
        task = MaintenanceTask.objects.get(maintenance_item=wo.maintenance_item)
        task.reference_image = _image_file()
        task.save(update_fields=["reference_image"])

        request = rf.get("/api/inventory/maintenance-tasks/")
        data = MaintenanceTaskSerializer(task, context={"request": request}).data

        assert data["reference_image_url"].startswith("http://")
        assert "maintenance_task_reference/" in data["reference_image_url"]

    def test_create_accepts_a_multipart_reference_image(self):
        client, _ = _staff_client()
        wo = _make_work_order(num_tasks=1)

        resp = client.post(
            "/api/inventory/maintenance-tasks/",
            data={
                "maintenance_item": str(wo.maintenance_item.id),
                "order": 5,
                "title": "Photograph the belt path",
                "description": "Match the reference photo before closing the guard.",
                "is_required": True,
                "reference_image": _image_file(),
            },
            format="multipart",
        )

        assert resp.status_code == status.HTTP_201_CREATED, resp.content
        body = resp.json()
        assert body["reference_image_url"]
        task = MaintenanceTask.objects.get(id=body["id"])
        assert task.reference_image.name.startswith("maintenance_task_reference/")
        assert task.title == "Photograph the belt path"

    def test_update_can_attach_and_clear_the_reference_image(self):
        client, _ = _staff_client()
        wo = _make_work_order(num_tasks=1)
        task = MaintenanceTask.objects.get(maintenance_item=wo.maintenance_item)
        url = f"/api/inventory/maintenance-tasks/{task.id}/"

        attached = client.patch(url, data={"reference_image": _image_file()}, format="multipart")
        assert attached.status_code == status.HTTP_200_OK, attached.content
        task.refresh_from_db()
        assert task.reference_image

        cleared = client.patch(url, data={"reference_image": None}, format="json")
        assert cleared.status_code == status.HTTP_200_OK, cleared.content
        task.refresh_from_db()
        assert not task.reference_image

    def test_other_task_fields_stay_writable(self):
        """The image is additive — the step's own fields still round-trip."""
        client, _ = _staff_client()
        wo = _make_work_order(num_tasks=1)
        task = MaintenanceTask.objects.get(maintenance_item=wo.maintenance_item)

        resp = client.patch(
            f"/api/inventory/maintenance-tasks/{task.id}/",
            data={"title": "Renamed step", "order": 9, "is_required": False},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.content
        task.refresh_from_db()
        assert (task.title, task.order, task.is_required) == ("Renamed step", 9, False)

    def test_maintenance_item_detail_carries_the_step_photo_urls(self):
        """The template editor loads its rows from here, photos included."""
        client, _ = _staff_client()
        wo = _make_work_order(num_tasks=1)
        task = MaintenanceTask.objects.get(maintenance_item=wo.maintenance_item)
        task.reference_image = _image_file()
        task.save(update_fields=["reference_image"])

        resp = client.get(f"/api/inventory/maintenance-items/{wo.maintenance_item.id}/")

        assert resp.status_code == status.HTTP_200_OK, resp.content
        step = resp.json()["tasks"][0]
        assert "maintenance_task_reference/" in step["reference_image_url"]
        assert "reference_image" not in step

    def test_list_filters_by_maintenance_item(self):
        client, _ = _staff_client()
        mine = _make_work_order(num_tasks=2)
        _make_work_order(num_tasks=3)

        resp = client.get(
            "/api/inventory/maintenance-tasks/",
            {"maintenance_item": str(mine.maintenance_item.id)},
        )

        assert resp.status_code == status.HTTP_200_OK, resp.content
        body = resp.json()
        rows = body["results"] if isinstance(body, dict) and "results" in body else body
        assert len(rows) == 2
        assert {row["maintenance_item"] for row in rows} == {str(mine.maintenance_item.id)}


# ─────────────────────────────────────────────────────────────────────────────
# add_photo — the evidence half
# ─────────────────────────────────────────────────────────────────────────────


class TestAddPhotoTaskCompletion:
    def test_photo_pinned_to_a_step_on_this_work_order(self):
        client, user = _staff_client()
        wo = _make_work_order(num_tasks=2)
        step = wo.task_completions.order_by("task_order").first()

        resp = client.post(
            _add_photo_url(wo),
            data={
                "image": _image_file("evidence.jpg"),
                "work_order": str(wo.id),
                "task_completion": str(step.id),
                "caption": "Belt seated",
            },
            format="multipart",
        )

        assert resp.status_code == status.HTTP_201_CREATED, resp.content
        assert resp.json()["task_completion"] == str(step.id)
        photo = WorkOrderPhoto.objects.get(work_order=wo)
        assert photo.task_completion_id == step.id
        assert photo.uploaded_by_id == user.id

    def test_photo_without_task_completion_stays_work_order_level(self):
        """Unchanged behaviour for every caller that predates per-step photos."""
        client, _ = _staff_client()
        wo = _make_work_order(num_tasks=1)

        resp = client.post(
            _add_photo_url(wo),
            data={"image": _image_file(), "work_order": str(wo.id)},
            format="multipart",
        )

        assert resp.status_code == status.HTTP_201_CREATED, resp.content
        assert resp.json()["task_completion"] is None
        assert WorkOrderPhoto.objects.get(work_order=wo).task_completion_id is None

    def test_blank_task_completion_is_treated_as_absent(self):
        """Multipart posts an empty string for an unset field."""
        client, _ = _staff_client()
        wo = _make_work_order(num_tasks=1)

        resp = client.post(
            _add_photo_url(wo),
            data={"image": _image_file(), "work_order": str(wo.id), "task_completion": ""},
            format="multipart",
        )

        assert resp.status_code == status.HTTP_201_CREATED, resp.content
        assert WorkOrderPhoto.objects.get(work_order=wo).task_completion_id is None

    def test_step_from_another_work_order_is_rejected(self):
        client, _ = _staff_client()
        wo = _make_work_order(num_tasks=1)
        other = _make_work_order(num_tasks=1)
        foreign_step = other.task_completions.first()

        resp = client.post(
            _add_photo_url(wo),
            data={
                "image": _image_file(),
                "work_order": str(wo.id),
                "task_completion": str(foreign_step.id),
            },
            format="multipart",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.content
        assert "task_completion" in resp.json()
        # Nothing was written to either work order.
        assert not WorkOrderPhoto.objects.filter(work_order__in=[wo, other]).exists()

    def test_garbage_task_completion_is_rejected(self):
        client, _ = _staff_client()
        wo = _make_work_order(num_tasks=1)

        resp = client.post(
            _add_photo_url(wo),
            data={
                "image": _image_file(),
                "work_order": str(wo.id),
                "task_completion": "not-a-uuid",
            },
            format="multipart",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.content
        assert not WorkOrderPhoto.objects.filter(work_order=wo).exists()


# ─────────────────────────────────────────────────────────────────────────────
# WorkOrderTaskCompletionSerializer — both halves surface on the WO
# ─────────────────────────────────────────────────────────────────────────────


class TestWorkOrderTaskCompletionPhotos:
    def test_reference_url_reads_through_the_template_step(self, rf):
        wo = _make_work_order(num_tasks=2)
        step = wo.task_completions.order_by("task_order").first()
        step.task.reference_image = _image_file()
        step.task.save(update_fields=["reference_image"])

        request = rf.get("/api/inventory/work-orders/")
        data = WorkOrderTaskCompletionSerializer(step, context={"request": request}).data

        assert "maintenance_task_reference/" in data["task_reference_image_url"]
        # The sibling step has no photo of its own.
        sibling = wo.task_completions.order_by("-task_order").first()
        sibling_data = WorkOrderTaskCompletionSerializer(sibling, context={"request": request}).data
        assert sibling_data["task_reference_image_url"] is None

    def test_reference_url_is_none_when_the_template_step_was_deleted(self, rf):
        wo = _make_work_order(num_tasks=1)
        step = wo.task_completions.first()
        step.task.delete()
        step.refresh_from_db()

        request = rf.get("/api/inventory/work-orders/")
        data = WorkOrderTaskCompletionSerializer(step, context={"request": request}).data

        assert step.task is None
        assert data["task_reference_image_url"] is None

    def test_evidence_photos_group_under_their_own_step(self, rf):
        client, user = _staff_client()
        wo = _make_work_order(num_tasks=2)
        first, second = list(wo.task_completions.order_by("task_order"))

        for caption in ("Before", "After"):
            client.post(
                _add_photo_url(wo),
                data={
                    "image": _image_file(),
                    "work_order": str(wo.id),
                    "task_completion": str(first.id),
                    "caption": caption,
                },
                format="multipart",
            )
        # …plus one work-order-level photo, which belongs to no step.
        client.post(
            _add_photo_url(wo),
            data={"image": _image_file(), "work_order": str(wo.id)},
            format="multipart",
        )

        request = rf.get("/api/inventory/work-orders/")
        first_data = WorkOrderTaskCompletionSerializer(first, context={"request": request}).data
        second_data = WorkOrderTaskCompletionSerializer(second, context={"request": request}).data

        assert {p["caption"] for p in first_data["evidence_photos"]} == {"Before", "After"}
        assert second_data["evidence_photos"] == []
        # Pinned contract: exactly these five keys per evidence photo.
        photo = first_data["evidence_photos"][0]
        assert set(photo) == {"id", "image_url", "caption", "uploaded_at", "uploaded_by_name"}
        assert photo["image_url"].startswith("http://")
        assert photo["uploaded_by_name"] == user.username
        # The WO-level photo still shows in the work order's own gallery.
        assert wo.photos.count() == 3

    def test_work_order_detail_carries_both_halves(self):
        client, _ = _staff_client()
        wo = _make_work_order(num_tasks=1)
        step = wo.task_completions.first()
        step.task.reference_image = _image_file()
        step.task.save(update_fields=["reference_image"])
        client.post(
            _add_photo_url(wo),
            data={
                "image": _image_file(),
                "work_order": str(wo.id),
                "task_completion": str(step.id),
            },
            format="multipart",
        )

        resp = client.get(f"/api/inventory/work-orders/{wo.id}/")

        assert resp.status_code == status.HTTP_200_OK, resp.content
        row = resp.json()["task_completions"][0]
        assert "maintenance_task_reference/" in row["task_reference_image_url"]
        assert len(row["evidence_photos"]) == 1

    def test_evidence_photos_do_not_add_a_query_per_step(self, rf):
        """The gallery is prefetched — a 4-step work order costs what a 1-step one does.

        Asserting equality *between two sizes* rather than a magic number keeps
        the test about the N+1 itself; an absolute count moves whenever an
        unrelated prefetch is added to the viewset queryset.
        """
        client, _ = _staff_client()

        def photographed_work_order(num_tasks):
            wo = _make_work_order(num_tasks=num_tasks)
            for step in wo.task_completions.all():
                client.post(
                    _add_photo_url(wo),
                    data={
                        "image": _image_file(),
                        "work_order": str(wo.id),
                        "task_completion": str(step.id),
                    },
                    format="multipart",
                )
            return wo

        small = photographed_work_order(1)
        large = photographed_work_order(4)

        request = rf.get("/api/inventory/work-orders/")

        def render(work_order):
            with CaptureQueriesContext(connection) as captured:
                instance = WorkOrderViewSet.queryset.get(id=work_order.id)
                rows = WorkOrderTaskCompletionSerializer(
                    instance.task_completions.all(), many=True, context={"request": request}
                ).data
            return len(captured), rows

        small_queries, small_rows = render(small)
        large_queries, large_rows = render(large)

        assert large_queries == small_queries
        assert sum(len(row["evidence_photos"]) for row in small_rows) == 1
        assert sum(len(row["evidence_photos"]) for row in large_rows) == 4


# ─────────────────────────────────────────────────────────────────────────────
# Printed form — reference photos only
# ─────────────────────────────────────────────────────────────────────────────


class TestWorkOrderPdfStepPhotos:
    def test_step_reference_photo_is_embedded(self):
        wo = _make_work_order(num_tasks=2)
        baseline = _pdf_image_count(generate_work_order_pdf(wo))

        step = wo.task_completions.order_by("task_order").first()
        step.task.reference_image = _image_file()
        step.task.save(update_fields=["reference_image"])

        pdf_bytes = generate_work_order_pdf(wo)

        assert _pdf_image_count(pdf_bytes) == baseline + 1
        text = "\n".join(
            page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf_bytes)).pages
        )
        assert "Photo" in text
        assert "Step 1" in text

    def test_form_without_step_photos_gains_no_photo_column(self):
        wo = _make_work_order(num_tasks=2)

        text = "\n".join(
            page.extract_text() or ""
            for page in PdfReader(io.BytesIO(generate_work_order_pdf(wo))).pages
        )

        assert "Task Steps" in text
        assert "Photo" not in text

    def test_a_big_photo_is_downsampled_before_it_is_embedded(self):
        """A phone photo prints at 1.2in — the form must not carry the original."""
        wo = _make_work_order(num_tasks=1)
        # The form already draws a QR code, whose own pixel size is unrelated —
        # diff against the photoless render so this measures only the photo.
        baseline = sorted(_pdf_image_widths(generate_work_order_pdf(wo)))
        step = wo.task_completions.first()
        step.task.reference_image = _image_file(size=(3000, 2000))
        step.task.save(update_fields=["reference_image"])

        widths = sorted(_pdf_image_widths(generate_work_order_pdf(wo)))

        added = list(widths)
        for width in baseline:
            added.remove(width)
        assert len(added) == 1
        # 1.2in at STEP_PHOTO_DPI, aspect-preserved — nowhere near 3000px.
        assert added[0] <= round(STEP_PHOTO_MAX_WIDTH / inch * STEP_PHOTO_DPI)

    def test_missing_image_file_does_not_break_the_form(self):
        """A photo deleted off disk prints as a blank cell, not a 500."""
        wo = _make_work_order(num_tasks=1)
        step = wo.task_completions.first()
        step.task.reference_image = _image_file()
        step.task.save(update_fields=["reference_image"])
        step.task.reference_image.storage.delete(step.task.reference_image.name)

        pdf_bytes = generate_work_order_pdf(wo)

        assert pdf_bytes.startswith(b"%PDF")
        text = "\n".join(
            page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf_bytes)).pages
        )
        assert "Step 1" in text
        # The step is the only one, and its file is gone — so no empty column.
        assert "Photo" not in text

    def test_evidence_photos_are_never_printed(self):
        """Evidence is captured after printing, so the blank form ignores it."""
        client, _ = _staff_client()
        wo = _make_work_order(num_tasks=1)
        baseline = _pdf_image_count(generate_work_order_pdf(wo))
        step = wo.task_completions.first()
        client.post(
            _add_photo_url(wo),
            data={
                "image": _image_file(),
                "work_order": str(wo.id),
                "task_completion": str(step.id),
            },
            format="multipart",
        )

        assert _pdf_image_count(generate_work_order_pdf(wo)) == baseline


class TestOmrSignatureUnchanged:
    def test_scan_form_still_maps_every_step_checkbox(self):
        """The photo column shifts the table — the mark rects must follow it.

        Checkbox rects are captured at draw time, so a re-laid-out Task Steps
        table is exactly where a stale hard-coded geometry would break the
        scan-to-complete path.
        """
        wo = _make_work_order(num_tasks=3)
        for step in wo.task_completions.all()[:2]:
            step.task.reference_image = _image_file(f"ref-{step.task_order}.jpg")
            step.task.save(update_fields=["reference_image"])

        _pdf, template = build_and_persist_omr_template(wo, base_url="https://x")

        regions = {r["target_id"]: r for r in template.regions_json}
        for step in wo.task_completions.all():
            target_id = f"task_{step.id}"
            assert target_id in regions
            # Rects are normalized against the 4 fiducial centers, so a real
            # box on the page is non-degenerate and lands inside 0..1.
            x0, y0, x1, y1 = regions[target_id]["rect_norm"]
            assert 0 <= x0 < x1 <= 1, target_id
            assert 0 <= y0 < y1 <= 1, target_id

    def test_step_photos_add_no_marks_and_no_drift(self):
        """Photos carry no checkbox, so a sheet printed earlier still scans."""
        client, _ = _staff_client()
        wo = _make_work_order(num_tasks=2)
        before = compute_template_version(wo)

        step = wo.task_completions.first()
        step.task.reference_image = _image_file(f"ref-{get_random_string(4)}.jpg")
        step.task.save(update_fields=["reference_image"])
        client.post(
            _add_photo_url(wo),
            data={
                "image": _image_file(),
                "work_order": str(wo.id),
                "task_completion": str(step.id),
            },
            format="multipart",
        )

        assert compute_template_version(wo) == before


class TestWorkOrderSerializerPhotoShape:
    def test_work_order_photos_expose_their_step_link(self, rf):
        """A WO-level photo reads back with the step it is pinned to (or null)."""
        client, _ = _staff_client()
        wo = _make_work_order(num_tasks=1)
        step = wo.task_completions.first()
        for payload in (
            {"work_order": str(wo.id), "task_completion": str(step.id)},
            {"work_order": str(wo.id)},
        ):
            client.post(
                _add_photo_url(wo),
                data={"image": _image_file(), **payload},
                format="multipart",
            )

        request = rf.get("/api/inventory/work-orders/")
        data = WorkOrderSerializer(wo, context={"request": request}).data

        links = {
            str(photo["task_completion"]) if photo["task_completion"] else None
            for photo in data["photos"]
        }
        assert links == {str(step.id), None}
