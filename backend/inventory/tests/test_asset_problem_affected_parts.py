"""
Tests for AssetProblem.affected_parts — the multi-select of asset components
(AssetParts) a reporter flags as needing replacement/fix.

Covers AssetViewSet.report_problem (write + validation) and
AssetProblemSerializer (read exposure), per op-hc6.
"""

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from inventory.models import AssetProblem
from inventory.serializers import AssetProblemSerializer
from inventory.tests.factories import (
    AssetFactory,
    AssetPartFactory,
    AssetProblemFactory,
    InventoryItemFactory,
)

User = get_user_model()
pytestmark = pytest.mark.django_db


def _report_problem_url(asset_id):
    return f"/api/inventory/assets/{asset_id}/report_problem/"


def _client(user=None):
    client = APIClient()
    if user is None:
        user = User.objects.create_user(username="reporter", email="r@x.test", password="x")
    client.force_authenticate(user=user)
    return client


class TestReportProblemAffectedParts:
    def test_report_problem_sets_affected_parts_from_part_ids(self):
        """part_ids on the report set the problem's affected_parts M2M."""
        asset = AssetFactory(name="UV Printer")
        part_a = AssetPartFactory(asset=asset)
        part_b = AssetPartFactory(asset=asset)

        resp = _client().post(
            _report_problem_url(asset.id),
            data={"description": "Heads clogged", "part_ids": [part_a.id, part_b.id]},
            format="json",
        )
        assert resp.status_code == 201, resp.content

        problem = AssetProblem.objects.get(id=resp.data["id"])
        assert set(problem.affected_parts.values_list("id", flat=True)) == {
            part_a.id,
            part_b.id,
        }
        # Response body echoes the affected parts.
        returned_ids = {p["id"] for p in resp.data["affected_parts"]}
        assert returned_ids == {part_a.id, part_b.id}

    def test_report_problem_rejects_part_from_different_asset(self):
        """A part_id belonging to a DIFFERENT asset is a 400 and creates nothing."""
        asset = AssetFactory(name="Lathe")
        other_asset = AssetFactory(name="Mill")
        foreign_part = AssetPartFactory(asset=other_asset)

        resp = _client().post(
            _report_problem_url(asset.id),
            data={"description": "Broken", "part_ids": [foreign_part.id]},
            format="json",
        )
        assert resp.status_code == 400, resp.content
        assert "part_ids" in str(resp.data)
        # Validation happens before creation — no orphaned problem.
        assert AssetProblem.objects.count() == 0

    def test_report_problem_rejects_mixed_valid_and_foreign_part_ids(self):
        """One good + one foreign id rejects the whole request atomically."""
        asset = AssetFactory()
        own_part = AssetPartFactory(asset=asset)
        foreign_part = AssetPartFactory(asset=AssetFactory())

        resp = _client().post(
            _report_problem_url(asset.id),
            data={"description": "x", "part_ids": [own_part.id, foreign_part.id]},
            format="json",
        )
        assert resp.status_code == 400, resp.content
        assert AssetProblem.objects.count() == 0

    def test_report_problem_rejects_nonexistent_part_id(self):
        """An id that is not any AssetPart is rejected with 400."""
        asset = AssetFactory()

        resp = _client().post(
            _report_problem_url(asset.id),
            data={"description": "x", "part_ids": [999999]},
            format="json",
        )
        assert resp.status_code == 400, resp.content
        assert AssetProblem.objects.count() == 0

    def test_report_problem_rejects_non_integer_part_id(self):
        """Non-integer ids are a client error, not a 500."""
        asset = AssetFactory()

        resp = _client().post(
            _report_problem_url(asset.id),
            data={"description": "x", "part_ids": ["not-an-int"]},
            format="json",
        )
        assert resp.status_code == 400, resp.content
        assert AssetProblem.objects.count() == 0

    def test_description_only_report_still_works(self):
        """Omitting part_ids keeps the original description-only flow intact."""
        asset = AssetFactory()

        resp = _client().post(
            _report_problem_url(asset.id),
            data={"description": "Just broken"},
            format="json",
        )
        assert resp.status_code == 201, resp.content

        problem = AssetProblem.objects.get(id=resp.data["id"])
        assert problem.affected_parts.count() == 0
        assert resp.data["affected_parts"] == []

    def test_empty_part_ids_list_is_accepted_as_no_parts(self):
        """An explicit empty list behaves like omitting the field."""
        asset = AssetFactory()

        resp = _client().post(
            _report_problem_url(asset.id),
            data={"description": "Broken", "part_ids": []},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        problem = AssetProblem.objects.get(id=resp.data["id"])
        assert problem.affected_parts.count() == 0


class TestAssetProblemSerializerAffectedParts:
    def test_serializer_exposes_affected_parts_with_part_names(self):
        """affected_parts is a nested list carrying part name/sku + qty/required."""
        item = InventoryItemFactory(name="Print Head X", sku="PH-X-01")
        asset = AssetFactory()
        asset_part = AssetPartFactory(asset=asset, part=item, quantity_needed=3, is_required=True)
        problem = AssetProblemFactory(asset=asset)
        problem.affected_parts.set([asset_part])

        data = AssetProblemSerializer(problem).data

        assert len(data["affected_parts"]) == 1
        entry = data["affected_parts"][0]
        assert entry["id"] == asset_part.id
        assert entry["part_name"] == "Print Head X"
        assert entry["part_sku"] == "PH-X-01"
        assert entry["quantity_needed"] == 3
        assert entry["is_required"] is True

    def test_part_ids_is_write_only(self):
        """The write-side part_ids field is not echoed on read."""
        problem = AssetProblemFactory()
        data = AssetProblemSerializer(problem).data
        assert "part_ids" not in data
        assert data["affected_parts"] == []
