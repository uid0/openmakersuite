"""Tests for the standard work order's generic attachments list (op-7pjj).

Before this the internal ``WorkOrder`` could only carry per-step evidence
photos and a scan of its own paper form — there was no place for a receipt, a
datasheet page, or any other loose file. ``WorkOrderAttachment`` is the same
shape as the sibling lists on third-party work orders and purchase orders, so
the ScanTTY ``wo_attachments`` screen and the web WO page can share one
contract.

Covers the round trip (upload → list → delete), the ``?work_order=`` /
``?kind=`` filters that scope the list to one job, the server-stamped
``uploaded_by``, and the permission gate: reads follow the parent work order
(volunteers can see standard PM work orders, gh #374) while writes and deletes
are staff / Logistics / SIG-admin only.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from inventory.models import MaintenanceItem, WorkOrder, WorkOrderAttachment
from inventory.tests.factories import AssetFactory
from membership.models import SIGAdmin

User = get_user_model()

pytestmark = pytest.mark.django_db

LIST_URL = "/api/inventory/work-order-attachments/"


@pytest.fixture(autouse=True)
def _isolated_media(settings, tmp_path):
    """Keep uploaded test files out of the tracked backend/media tree."""
    settings.MEDIA_ROOT = str(tmp_path)


def _user(username, **flags):
    return User.objects.create_user(
        username=f"{username}_{get_random_string(6)}",
        email=f"{username}@example.com",
        password=get_random_string(24),
        **flags,
    )


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _work_order() -> WorkOrder:
    item = MaintenanceItem.objects.create(
        asset=AssetFactory(),
        title="Monthly inspection",
        description="Routine",
        interval_days=30,
    )
    return WorkOrder.objects.create(
        maintenance_item=item,
        due_date=date.today() + timedelta(days=7),
    )


def _upload(name="receipt.pdf", content=b"%PDF-1.4 fake", content_type="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def _attachment(work_order, *, kind=WorkOrderAttachment.Kind.OTHER, description="", **kwargs):
    return WorkOrderAttachment.objects.create(
        work_order=work_order,
        file=_upload(**kwargs),
        kind=kind,
        description=description,
    )


class TestUpload:
    """Multipart upload hangs a file off one work order."""

    def test_staff_uploads_and_server_stamps_uploader(self):
        wo = _work_order()
        staff = _user("staff", is_staff=True)

        resp = _client(staff).post(
            LIST_URL,
            data={
                "work_order": str(wo.id),
                "file": _upload(),
                "kind": WorkOrderAttachment.Kind.DOCUMENT,
                "description": "Supplier receipt",
            },
            format="multipart",
        )

        assert resp.status_code == 201, resp.data
        body = resp.json()
        assert body["work_order"] == str(wo.id)
        assert body["kind"] == "document"
        assert body["kind_display"] == "Document"
        assert body["description"] == "Supplier receipt"
        # uploaded_by is read-only on the serializer and stamped in the view.
        assert body["uploaded_by"] == staff.id
        assert body["uploaded_by_name"] == staff.username
        assert body["file_name"].startswith("receipt")
        assert body["file_url"].startswith("http")

        attachment = WorkOrderAttachment.objects.get(id=body["id"])
        assert attachment.work_order_id == wo.id
        assert attachment.uploaded_by_id == staff.id

    def test_kind_defaults_to_other(self):
        wo = _work_order()

        resp = _client(_user("staff", is_staff=True)).post(
            LIST_URL,
            data={"work_order": str(wo.id), "file": _upload()},
            format="multipart",
        )

        assert resp.status_code == 201, resp.data
        assert resp.json()["kind"] == WorkOrderAttachment.Kind.OTHER

    def test_client_cannot_attribute_upload_to_someone_else(self):
        wo = _work_order()
        staff = _user("staff", is_staff=True)
        someone_else = _user("volunteer")

        resp = _client(staff).post(
            LIST_URL,
            data={
                "work_order": str(wo.id),
                "file": _upload(),
                "uploaded_by": someone_else.id,
            },
            format="multipart",
        )

        assert resp.status_code == 201, resp.data
        assert WorkOrderAttachment.objects.get(id=resp.json()["id"]).uploaded_by_id == staff.id

    def test_file_is_required(self):
        wo = _work_order()

        resp = _client(_user("staff", is_staff=True)).post(
            LIST_URL,
            data={"work_order": str(wo.id), "description": "no file"},
            format="multipart",
        )

        assert resp.status_code == 400
        # Project-wide DRF errors are wrapped in an {"error": {...}} envelope.
        assert "file" in resp.json()["error"]["details"]


class TestList:
    """The list is scoped to one work order — that is how a client reads it."""

    def test_returns_the_standard_page_envelope(self):
        """No pagination override, so the shared list contract applies."""
        _attachment(_work_order(), description="only")

        resp = _client(_user("staff", is_staff=True)).get(LIST_URL)

        assert resp.status_code == 200
        assert {"count", "next", "previous", "results"}.issubset(resp.json())

    def test_filters_by_work_order(self):
        mine, theirs = _work_order(), _work_order()
        _attachment(mine, description="mine-1")
        _attachment(mine, description="mine-2")
        _attachment(theirs, description="theirs")

        resp = _client(_user("staff", is_staff=True)).get(LIST_URL, {"work_order": str(mine.id)})

        assert resp.status_code == 200
        assert {r["description"] for r in resp.json()["results"]} == {"mine-1", "mine-2"}

    def test_filters_by_kind(self):
        wo = _work_order()
        _attachment(wo, kind=WorkOrderAttachment.Kind.PHOTO, description="nameplate")
        _attachment(wo, kind=WorkOrderAttachment.Kind.DOCUMENT, description="datasheet")

        resp = _client(_user("staff", is_staff=True)).get(
            LIST_URL, {"work_order": str(wo.id), "kind": WorkOrderAttachment.Kind.PHOTO}
        )

        assert resp.status_code == 200
        assert [r["description"] for r in resp.json()["results"]] == ["nameplate"]

    def test_unfiltered_list_returns_every_attachment(self):
        _attachment(_work_order(), description="a")
        _attachment(_work_order(), description="b")

        resp = _client(_user("staff", is_staff=True)).get(LIST_URL)

        assert resp.status_code == 200
        assert {r["description"] for r in resp.json()["results"]} == {"a", "b"}

    def test_malformed_work_order_filter_is_a_400_not_a_500(self):
        """A bad UUID reaches the ORM; the project error handler maps it."""
        resp = _client(_user("staff", is_staff=True)).get(LIST_URL, {"work_order": "not-a-uuid"})

        assert resp.status_code == 400


class TestDelete:
    def test_staff_can_delete(self):
        attachment = _attachment(_work_order())

        resp = _client(_user("staff", is_staff=True)).delete(f"{LIST_URL}{attachment.id}/")

        assert resp.status_code == 204
        assert not WorkOrderAttachment.objects.filter(id=attachment.id).exists()

    def test_deleting_the_work_order_cascades(self):
        wo = _work_order()
        _attachment(wo)

        wo.delete()

        assert not WorkOrderAttachment.objects.filter(work_order_id=wo.id).exists()


class TestPermissions:
    """Read follows the parent work order; write and delete are staff-gated."""

    def test_anonymous_cannot_list(self):
        _attachment(_work_order())

        resp = APIClient().get(LIST_URL)

        assert resp.status_code in (401, 403)

    def test_anonymous_cannot_upload(self):
        wo = _work_order()

        resp = APIClient().post(
            LIST_URL,
            data={"work_order": str(wo.id), "file": _upload()},
            format="multipart",
        )

        assert resp.status_code in (401, 403)
        assert not WorkOrderAttachment.objects.exists()

    def test_volunteer_can_read(self):
        wo = _work_order()
        _attachment(wo, description="visible")

        resp = _client(_user("volunteer")).get(LIST_URL, {"work_order": str(wo.id)})

        assert resp.status_code == 200
        assert [r["description"] for r in resp.json()["results"]] == ["visible"]

    def test_volunteer_cannot_upload(self):
        wo = _work_order()

        resp = _client(_user("volunteer")).post(
            LIST_URL,
            data={"work_order": str(wo.id), "file": _upload()},
            format="multipart",
        )

        assert resp.status_code == 403
        assert not WorkOrderAttachment.objects.exists()

    def test_volunteer_cannot_delete(self):
        attachment = _attachment(_work_order())

        resp = _client(_user("volunteer")).delete(f"{LIST_URL}{attachment.id}/")

        assert resp.status_code == 403
        assert WorkOrderAttachment.objects.filter(id=attachment.id).exists()

    def test_volunteer_cannot_patch(self):
        attachment = _attachment(_work_order())

        resp = _client(_user("volunteer")).patch(
            f"{LIST_URL}{attachment.id}/",
            data={"description": "edited"},
            format="json",
        )

        assert resp.status_code == 403

    def test_sig_admin_can_upload(self):
        wo = _work_order()
        user = _user("sigleader")
        SIGAdmin.objects.create(user=user, group=Group.objects.create(name="3D Printing SIG"))

        resp = _client(user).post(
            LIST_URL,
            data={"work_order": str(wo.id), "file": _upload()},
            format="multipart",
        )

        assert resp.status_code == 201, resp.data

    def test_logistics_member_can_upload(self):
        wo = _work_order()
        user = _user("logistics")
        user.groups.add(Group.objects.create(name="Logistics"))

        resp = _client(user).post(
            LIST_URL,
            data={"work_order": str(wo.id), "file": _upload()},
            format="multipart",
        )

        assert resp.status_code == 201, resp.data


class TestModel:
    def test_related_name_and_ordering(self):
        wo = _work_order()
        older = _attachment(wo, description="older")
        newer = _attachment(wo, description="newer")
        # auto_now_add can land both rows in the same tick on a fast box;
        # force the order the Meta claims (newest first).
        WorkOrderAttachment.objects.filter(id=older.id).update(
            uploaded_at=newer.uploaded_at - timedelta(hours=1)
        )

        assert [a.description for a in wo.attachments.all()] == ["newer", "older"]

    def test_str_names_the_work_order_and_kind(self):
        wo = _work_order()
        attachment = _attachment(wo, kind=WorkOrderAttachment.Kind.PHOTO, description="Nameplate")

        rendered = str(attachment)

        assert wo.short_id in rendered
        assert "Photo" in rendered
        assert "Nameplate" in rendered
