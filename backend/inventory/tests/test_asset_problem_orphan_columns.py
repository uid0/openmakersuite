"""Regression for Sentry BACKEND-12.

Reporting an asset problem with only a description must succeed. Production's
``inventory_assetproblem`` table had an orphan ``is_urgent`` column (NOT NULL,
no default) that made every ``report_problem`` INSERT fail with a
NotNullViolation; migration 0098 drops it. This guards the minimal-create path
against future regressions.
"""

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from inventory.models import AssetProblem
from inventory.tests.factories import AssetFactory

User = get_user_model()
pytestmark = pytest.mark.django_db


def test_report_asset_problem_with_only_description_succeeds():
    asset = AssetFactory(name="Roof AHU")
    user = User.objects.create_user(username="reporter", email="r@x.test", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    resp = client.post(
        f"/api/inventory/assets/{asset.id}/report_problem/",
        data={"description": "Leaking from Roof pan"},
        format="json",
    )

    assert resp.status_code == 201, resp.content
    problem = AssetProblem.objects.get(id=resp.data["id"])
    assert problem.description == "Leaking from Roof pan"
    assert problem.asset_id == asset.id
