"""Tests for the asset document library (AssetDocumentViewSet).

Covers the EAM P1.3 acceptance surface: upload, per-asset + per-category +
is_current listing, lightweight versioning (the ``supersede`` action and the
``supersedes`` create field flip ``is_current`` and link lineage), permission
gating (read open, write requires auth), and delete.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.crypto import get_random_string

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import AssetDocument
from inventory.tests.factories import AssetFactory

User = get_user_model()
pytestmark = pytest.mark.django_db

LIST_URL = "/api/inventory/asset-documents/"


def _detail_url(doc_id):
    return f"{LIST_URL}{doc_id}/"


def _supersede_url(doc_id):
    return f"{LIST_URL}{doc_id}/supersede/"


def _make_file(name="manual.pdf", content=b"%PDF-1.4 fake bytes", content_type="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def _member_user():
    username = f"member_{get_random_string(6)}"
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=get_random_string(24),
    )


def _results(resp):
    """Return the row list whether or not the response is paginated."""
    data = resp.json()
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return data


@pytest.fixture(autouse=True)
def _isolated_media(settings, tmp_path):
    """Keep uploaded test files out of the tracked backend/media tree."""
    settings.MEDIA_ROOT = str(tmp_path)


def _make_document(asset, **kwargs):
    """Create an AssetDocument row directly (used to seed list/supersede setup)."""
    kwargs.setdefault("title", "Seed doc")
    kwargs.setdefault("category", AssetDocument.MANUAL)
    kwargs.setdefault("file", _make_file())
    return AssetDocument.objects.create(asset=asset, **kwargs)


@pytest.mark.integration
class TestAssetDocumentUpload:
    def test_authenticated_upload_creates_document(self):
        """An authenticated user can upload a document; row + metadata persist."""
        asset = AssetFactory()
        user = _member_user()
        client = APIClient()
        client.force_authenticate(user=user)

        resp = client.post(
            LIST_URL,
            data={
                "asset": str(asset.id),
                "file": _make_file(name="lathe-manual.pdf"),
                "category": AssetDocument.MANUAL,
                "title": "Lathe Manual",
                "description": "Operator's guide",
            },
            format="multipart",
        )

        assert resp.status_code == status.HTTP_201_CREATED, resp.content
        body = resp.json()
        assert body["title"] == "Lathe Manual"
        assert body["category"] == "manual"
        assert body["category_display"] == "Manual / Documentation"
        assert body["version"] == 1
        assert body["is_current"] is True
        assert body["supersedes"] is None
        assert body["file_url"]
        assert body["uploaded_by"] == user.id
        assert body["uploaded_by_name"]

        doc = AssetDocument.objects.get(id=body["id"])
        assert doc.asset_id == asset.id
        assert doc.uploaded_by_id == user.id
        assert doc.description == "Operator's guide"

    def test_cut_ready_template_extension_not_restricted(self):
        """Maker-native cut-ready files (DXF/SVG/G-code/STL) upload fine —
        extensions are deliberately unrestricted."""
        asset = AssetFactory()
        client = APIClient()
        client.force_authenticate(user=_member_user())

        resp = client.post(
            LIST_URL,
            data={
                "asset": str(asset.id),
                "file": _make_file(
                    name="front-panel.dxf",
                    content=b"0\nSECTION\n2\nHEADER\n",
                    content_type="application/dxf",
                ),
                "category": AssetDocument.CUT_READY_TEMPLATE,
                "title": "Front panel DXF",
            },
            format="multipart",
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.content
        assert resp.json()["category"] == "cut_ready_template"

    def test_upload_requires_title_and_file(self):
        asset = AssetFactory()
        client = APIClient()
        client.force_authenticate(user=_member_user())
        resp = client.post(LIST_URL, data={"asset": str(asset.id)}, format="multipart")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert AssetDocument.objects.count() == 0


@pytest.mark.integration
class TestAssetDocumentListFilters:
    def test_list_filtered_by_asset(self):
        asset_a = AssetFactory()
        asset_b = AssetFactory()
        _make_document(asset_a, title="A doc")
        _make_document(asset_b, title="B doc")

        client = APIClient()
        resp = client.get(LIST_URL, {"asset": str(asset_a.id)})
        assert resp.status_code == status.HTTP_200_OK
        results = _results(resp)
        assert [r["title"] for r in results] == ["A doc"]

    def test_list_filtered_by_category(self):
        asset = AssetFactory()
        _make_document(asset, title="Manual", category=AssetDocument.MANUAL)
        _make_document(asset, title="Wiring", category=AssetDocument.WIRING_DIAGRAM)

        client = APIClient()
        resp = client.get(
            LIST_URL, {"asset": str(asset.id), "category": AssetDocument.WIRING_DIAGRAM}
        )
        assert resp.status_code == status.HTTP_200_OK
        results = _results(resp)
        assert [r["title"] for r in results] == ["Wiring"]

    def test_list_filtered_by_is_current(self):
        asset = AssetFactory()
        _make_document(asset, title="Current")
        _make_document(asset, title="Stale", is_current=False)

        client = APIClient()
        current = _results(client.get(LIST_URL, {"asset": str(asset.id), "is_current": "true"}))
        assert {r["title"] for r in current} == {"Current"}

        stale = _results(client.get(LIST_URL, {"asset": str(asset.id), "is_current": "false"}))
        assert {r["title"] for r in stale} == {"Stale"}


@pytest.mark.integration
class TestAssetDocumentSupersede:
    def test_supersede_action_flips_is_current_and_links(self):
        """POST /<id>/supersede/ creates v2, flips v1.is_current, links lineage."""
        asset = AssetFactory()
        client = APIClient()
        client.force_authenticate(user=_member_user())

        r1 = client.post(
            LIST_URL,
            data={
                "asset": str(asset.id),
                "file": _make_file(name="v1.pdf"),
                "category": AssetDocument.MANUAL,
                "title": "Manual",
            },
            format="multipart",
        )
        assert r1.status_code == status.HTTP_201_CREATED, r1.content
        v1_id = r1.json()["id"]

        r2 = client.post(
            _supersede_url(v1_id),
            data={"file": _make_file(name="v2.pdf")},
            format="multipart",
        )
        assert r2.status_code == status.HTTP_201_CREATED, r2.content
        v2 = r2.json()
        assert v2["version"] == 2
        assert v2["is_current"] is True
        assert v2["supersedes"] == v1_id
        # Title/category inherited from the prior version when not overridden.
        assert v2["title"] == "Manual"
        assert v2["category"] == "manual"

        v1 = AssetDocument.objects.get(id=v1_id)
        assert v1.is_current is False
        v2_obj = AssetDocument.objects.get(id=v2["id"])
        assert v2_obj.supersedes_id == v1.id
        assert v2_obj.asset_id == asset.id
        # The reverse relation exposes what replaced v1.
        assert list(v1.superseded_by.values_list("id", flat=True)) == [v2_obj.id]

    def test_create_with_supersedes_bumps_version_and_flips(self):
        """The create endpoint also supports 'upload a new version of <doc>'."""
        asset = AssetFactory()
        prior = _make_document(asset, title="Manual", category=AssetDocument.MANUAL)
        client = APIClient()
        client.force_authenticate(user=_member_user())

        resp = client.post(
            LIST_URL,
            data={
                "asset": str(asset.id),
                "file": _make_file(name="v2.pdf"),
                "category": AssetDocument.MANUAL,
                "title": "Manual v2",
                "supersedes": str(prior.id),
            },
            format="multipart",
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.content
        body = resp.json()
        assert body["version"] == 2
        assert body["supersedes"] == str(prior.id)

        prior.refresh_from_db()
        assert prior.is_current is False

    def test_supersede_requires_authentication(self):
        asset = AssetFactory()
        prior = _make_document(asset, title="Manual")
        client = APIClient()  # anonymous
        resp = client.post(
            _supersede_url(prior.id), data={"file": _make_file()}, format="multipart"
        )
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        prior.refresh_from_db()
        assert prior.is_current is True
        assert AssetDocument.objects.filter(asset=asset).count() == 1

    def test_create_supersedes_cross_asset_rejected(self):
        """A document may only supersede another document on the same asset."""
        asset_a = AssetFactory()
        asset_b = AssetFactory()
        prior_b = _make_document(asset_b, title="B manual")
        client = APIClient()
        client.force_authenticate(user=_member_user())

        resp = client.post(
            LIST_URL,
            data={
                "asset": str(asset_a.id),
                "file": _make_file(),
                "category": AssetDocument.MANUAL,
                "title": "cross-asset",
                "supersedes": str(prior_b.id),
            },
            format="multipart",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        prior_b.refresh_from_db()
        assert prior_b.is_current is True


@pytest.mark.integration
class TestAssetDocumentPermissionsAndDelete:
    def test_anonymous_can_read(self):
        asset = AssetFactory()
        _make_document(asset, title="Public manual")
        client = APIClient()
        resp = client.get(LIST_URL, {"asset": str(asset.id)})
        assert resp.status_code == status.HTTP_200_OK
        assert len(_results(resp)) == 1

    def test_anonymous_cannot_upload(self):
        asset = AssetFactory()
        client = APIClient()
        resp = client.post(
            LIST_URL,
            data={
                "asset": str(asset.id),
                "file": _make_file(),
                "category": AssetDocument.MANUAL,
                "title": "unauth",
            },
            format="multipart",
        )
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert AssetDocument.objects.count() == 0

    def test_authenticated_delete(self):
        asset = AssetFactory()
        doc = _make_document(asset, title="Manual")
        client = APIClient()
        client.force_authenticate(user=_member_user())
        resp = client.delete(_detail_url(doc.id))
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not AssetDocument.objects.filter(id=doc.id).exists()

    def test_anonymous_cannot_delete(self):
        asset = AssetFactory()
        doc = _make_document(asset, title="Manual")
        client = APIClient()
        resp = client.delete(_detail_url(doc.id))
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert AssetDocument.objects.filter(id=doc.id).exists()

    def test_patch_updates_metadata(self):
        asset = AssetFactory()
        doc = _make_document(asset, title="Manual", description="")
        client = APIClient()
        client.force_authenticate(user=_member_user())
        resp = client.patch(
            _detail_url(doc.id),
            data={"title": "Renamed Manual", "description": "now with notes"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        doc.refresh_from_db()
        assert doc.title == "Renamed Manual"
        assert doc.description == "now with notes"
