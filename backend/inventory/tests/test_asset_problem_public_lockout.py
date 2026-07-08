"""
Tests for the PUBLIC (unauthenticated) asset-scan report path — op-jhlt.

An anonymous scanner who lands on the public asset-scan page must be able to
report a problem, request supplies, or request a lockout. This pins:

* ``AssetViewSet.report_problem`` is ``AllowAny`` (anon POST → 201), and
* the ``lockout_requested`` flag is recorded so staff can act on it (a REQUEST,
  never a direct device actuation).
"""

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from inventory.models import AssetProblem
from inventory.serializers import AssetProblemSerializer
from inventory.tests.factories import AssetFactory, AssetPartFactory

User = get_user_model()
pytestmark = pytest.mark.django_db


def _report_problem_url(asset_id):
    return f"/api/inventory/assets/{asset_id}/report_problem/"


class TestPublicReportProblem:
    def test_anonymous_can_report_problem(self):
        """An unauthenticated scanner can file a report (AllowAny)."""
        asset = AssetFactory(name="Laser Cutter")

        resp = APIClient().post(
            _report_problem_url(asset.id),
            data={"description": "Reported not working from asset scan"},
            format="json",
        )

        assert resp.status_code == 201, resp.content
        problem = AssetProblem.objects.get(id=resp.data["id"])
        # Anonymous reports are stored with an empty reporter.
        assert problem.reported_by == ""
        assert problem.lockout_requested is False

    def test_anonymous_lockout_request_is_recorded(self):
        """`lockout_requested=true` is stored and echoed — a staff-actioned request."""
        asset = AssetFactory(name="Table Saw")

        resp = APIClient().post(
            _report_problem_url(asset.id),
            data={
                "description": "Lockout requested from asset scan",
                "lockout_requested": True,
            },
            format="json",
        )

        assert resp.status_code == 201, resp.content
        problem = AssetProblem.objects.get(id=resp.data["id"])
        assert problem.lockout_requested is True
        # The serializer exposes the flag so staff dashboards can surface it.
        assert resp.data["lockout_requested"] is True

    def test_lockout_requested_defaults_false(self):
        """A plain problem report leaves lockout_requested False."""
        asset = AssetFactory(name="3D Printer")

        resp = APIClient().post(
            _report_problem_url(asset.id),
            data={"description": "Nozzle jammed"},
            format="json",
        )

        assert resp.status_code == 201, resp.content
        assert resp.data["lockout_requested"] is False

    def test_lockout_requested_string_is_coerced(self):
        """Form-encoded 'true'/'false' strings coerce to booleans."""
        asset = AssetFactory(name="CNC Router")

        resp = APIClient().post(
            _report_problem_url(asset.id),
            data={"description": "Lock it out", "lockout_requested": "true"},
        )

        assert resp.status_code == 201, resp.content
        problem = AssetProblem.objects.get(id=resp.data["id"])
        assert problem.lockout_requested is True

    def test_anonymous_item_request_flags_parts(self):
        """Anon 'Submit item request' flags the asset's parts as needing replace."""
        asset = AssetFactory(name="Vinyl Cutter")
        blade = AssetPartFactory(asset=asset)

        resp = APIClient().post(
            _report_problem_url(asset.id),
            data={
                "description": "Item/supplies request from asset scan",
                "part_ids": [blade.id],
            },
            format="json",
        )

        assert resp.status_code == 201, resp.content
        problem = AssetProblem.objects.get(id=resp.data["id"])
        assert set(problem.affected_parts.values_list("id", flat=True)) == {blade.id}
        assert problem.lockout_requested is False

    def test_serializer_exposes_lockout_requested(self):
        """AssetProblemSerializer includes the lockout_requested field."""
        assert "lockout_requested" in AssetProblemSerializer().fields
