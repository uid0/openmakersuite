"""
Tests for AssetProblemViewSet query-param filtering.

Regression coverage for the bug where ``/api/inventory/asset-problems/``
ignored the ``?asset=`` filter and returned every problem for every asset
(so every asset's detail showed the same global problem list). The viewset
now honors ``?asset=``, ``?status=``, and ``?part=`` in ``get_queryset``.
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import AssetProblem
from inventory.tests.factories import (
    AssetFactory,
    AssetPartFactory,
    AssetProblemFactory,
)

pytestmark = pytest.mark.django_db

LIST_URL = "/api/inventory/asset-problems/"


def _ids(response):
    """Return the set of problem ids in a (paginated) list response."""
    assert response.status_code == status.HTTP_200_OK, response.content
    return {row["id"] for row in response.json()["results"]}


@pytest.mark.integration
class TestAssetProblemFiltering:
    def test_filter_by_asset_returns_only_that_asset(self):
        """``?asset={id}`` returns only that asset's problems, not every one."""
        asset_a = AssetFactory()
        asset_b = AssetFactory()
        problem_a = AssetProblemFactory(asset=asset_a)
        problem_b = AssetProblemFactory(asset=asset_b)

        resp = APIClient().get(LIST_URL, {"asset": str(asset_a.id)})

        ids = _ids(resp)
        assert str(problem_a.id) in ids
        assert str(problem_b.id) not in ids
        assert resp.json()["count"] == 1

    def test_filter_by_status(self):
        """``?status=`` narrows to problems in that status."""
        asset = AssetFactory()
        reported = AssetProblemFactory(asset=asset, status=AssetProblem.Status.REPORTED)
        resolved = AssetProblemFactory(asset=asset, status=AssetProblem.Status.RESOLVED)

        resp = APIClient().get(LIST_URL, {"status": AssetProblem.Status.RESOLVED})

        ids = _ids(resp)
        assert str(resolved.id) in ids
        assert str(reported.id) not in ids

    def test_filter_by_part(self):
        """``?part={id}`` narrows to problems flagged against that part."""
        asset = AssetFactory()
        part = AssetPartFactory(asset=asset)
        with_part = AssetProblemFactory(asset=asset, part=part)
        without_part = AssetProblemFactory(asset=asset, part=None)

        resp = APIClient().get(LIST_URL, {"part": str(part.id)})

        ids = _ids(resp)
        assert str(with_part.id) in ids
        assert str(without_part.id) not in ids

    def test_asset_and_status_combine(self):
        """Filters combine: ``?asset=A&status=reported`` intersects both."""
        asset_a = AssetFactory()
        asset_b = AssetFactory()
        target = AssetProblemFactory(asset=asset_a, status=AssetProblem.Status.REPORTED)
        AssetProblemFactory(asset=asset_a, status=AssetProblem.Status.RESOLVED)
        AssetProblemFactory(asset=asset_b, status=AssetProblem.Status.REPORTED)

        resp = APIClient().get(
            LIST_URL, {"asset": str(asset_a.id), "status": AssetProblem.Status.REPORTED}
        )

        ids = _ids(resp)
        assert ids == {str(target.id)}

    def test_no_filter_returns_all(self):
        """No query params still returns every problem (dashboard/global use)."""
        problem_a = AssetProblemFactory(asset=AssetFactory())
        problem_b = AssetProblemFactory(asset=AssetFactory())

        resp = APIClient().get(LIST_URL)

        ids = _ids(resp)
        assert {str(problem_a.id), str(problem_b.id)} <= ids
        assert resp.json()["count"] == 2
