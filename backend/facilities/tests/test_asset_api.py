"""AssetViewSet API smoke tests for the #880 site-requirements refactor.

Guards two things the serializer-level tests can't:
* the viewset queryset's ``select_related`` now traverses
  ``site_requirements__breaker__panel`` (a stale ``breaker__panel`` path would
  raise FieldError on every list/detail call), and
* the flattened profile keys round-trip through the real HTTP endpoint.
"""

from django.urls import reverse

import pytest
from rest_framework import status

from electrical_circuits.models import PowerBreaker, PowerPanel
from inventory.tests.factories import AssetFactory

pytestmark = pytest.mark.django_db


def _breaker(location):
    panel = PowerPanel.objects.create(location=location, name="Panel A")
    return PowerBreaker.objects.create(panel=panel, position="12", amperage=20)


def _results(data):
    return data["results"] if isinstance(data, dict) and "results" in data else data


def test_asset_list_renders_breaker_through_profile(authenticated_client):
    client, _user = authenticated_client
    asset = AssetFactory()
    breaker = _breaker(asset.location)
    asset.breaker = breaker
    asset.needs_compressed_air = True

    resp = client.get(reverse("asset-list"))
    assert resp.status_code == status.HTTP_200_OK

    row = next(r for r in _results(resp.data) if r["id"] == str(asset.id))
    assert row["breaker"] == breaker.id
    assert row["needs_compressed_air"] is True
    assert row["breaker_summary"]["id"] == breaker.id


def test_asset_patch_routes_profile_keys(authenticated_client):
    client, _user = authenticated_client
    asset = AssetFactory()
    breaker = _breaker(asset.location)

    resp = client.patch(
        reverse("asset-detail", args=[asset.id]),
        {
            "breaker": breaker.id,
            "generates_heat_or_flame": True,
            "work_safety_notes": "Lock out at Panel A",
        },
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK, resp.data
    assert resp.data["breaker"] == breaker.id
    assert resp.data["generates_heat_or_flame"] is True
    assert resp.data["work_safety_notes"] == "Lock out at Panel A"

    asset.refresh_from_db()
    assert asset.breaker_id == breaker.id
    assert asset.generates_heat_or_flame is True
