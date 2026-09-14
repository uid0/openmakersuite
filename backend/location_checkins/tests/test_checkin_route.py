"""
The public check-in route the web scanner and LocationScanPage call.

The web client hard-codes this path (pinned from its side in
`frontend/src/__tests__/pages/UniversalScannerPage.locationCheckin.test.tsx`).
The hyphenated `check-ins/` it once called was never routed and 404'd.
"""

import pytest
from rest_framework.test import APIClient

from inventory.tests.factories import LocationFactory
from location_checkins.models import LocationCheckIn

WEB_CHECKIN_ROUTE = "/api/location-checkins/checkins/checkin/"


@pytest.mark.django_db
def test_web_checkin_route_records_an_anonymous_checkin():
    location = LocationFactory(is_active=True)

    response = APIClient().post(
        WEB_CHECKIN_ROUTE,
        {"location_id": str(location.id), "checkin_type": "anonymous"},
        format="json",
    )

    assert response.status_code == 201
    checkin = LocationCheckIn.objects.get(id=response.data["id"])
    assert checkin.location == location
    assert checkin.checkin_type == "anonymous"


@pytest.mark.django_db
def test_hyphenated_check_ins_path_is_not_routed():
    location = LocationFactory(is_active=True)

    response = APIClient().post(
        "/api/location-checkins/check-ins/",
        {"location": location.id, "checkin_type": "anonymous"},
        format="json",
    )

    assert response.status_code == 404
    assert not LocationCheckIn.objects.exists()
