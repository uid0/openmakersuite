"""Tests for the Critical Circuit flag on PowerBreaker.

Critical breakers feed life-safety loads (fire alarms, emergency
lighting, exit signs, egress doors). The flag pairs with a category
choice and an optional free-text note; the model + serializer enforce
that the boolean and the category move together.
"""

from django.core.exceptions import ValidationError
from django.urls import reverse

import pytest
from rest_framework import status

from electrical_circuits.models import PowerBreaker, PowerPanel
from inventory.tests.factories import LocationFactory

pytestmark = pytest.mark.django_db


def _panel(name: str = "Main Lobby Panel") -> PowerPanel:
    loc = LocationFactory()
    return PowerPanel.objects.create(location=loc, name=name)


class TestCriticalFlagModel:
    def test_default_is_not_critical(self):
        b = PowerBreaker.objects.create(panel=_panel(), position="2", amperage=20)
        assert b.is_critical is False
        assert b.critical_category == PowerBreaker.CRITICAL_CATEGORY_NONE
        assert b.critical_note == ""

    def test_critical_with_category_validates(self):
        b = PowerBreaker.objects.create(
            panel=_panel(),
            position="4",
            amperage=20,
            is_critical=True,
            critical_category=PowerBreaker.CRITICAL_FIRE_ALARM,
            critical_note="Feeds the panel for the 2nd-floor smoke detectors.",
        )
        b.full_clean()  # Must not raise.
        assert b.is_critical is True
        assert b.critical_category == PowerBreaker.CRITICAL_FIRE_ALARM

    def test_critical_without_category_rejected(self):
        b = PowerBreaker(
            panel=_panel(),
            position="6",
            amperage=20,
            is_critical=True,
            critical_category=PowerBreaker.CRITICAL_CATEGORY_NONE,
        )
        with pytest.raises(ValidationError) as exc:
            b.full_clean()
        assert "critical_category" in exc.value.message_dict

    def test_category_without_critical_rejected(self):
        b = PowerBreaker(
            panel=_panel(),
            position="8",
            amperage=20,
            is_critical=False,
            critical_category=PowerBreaker.CRITICAL_EXIT_SIGN,
        )
        with pytest.raises(ValidationError) as exc:
            b.full_clean()
        assert "is_critical" in exc.value.message_dict


@pytest.mark.integration
class TestCriticalBreakersAction:
    def test_anonymous_cannot_list_critical(self, api_client):
        response = api_client.get(reverse("powerbreaker-critical"))
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_staff_sees_only_critical_breakers(self, api_client, admin_user):
        api_client.force_authenticate(user=admin_user)
        panel = _panel()
        non_critical = PowerBreaker.objects.create(panel=panel, position="10", amperage=20)
        critical = PowerBreaker.objects.create(
            panel=panel,
            position="12",
            amperage=20,
            is_critical=True,
            critical_category=PowerBreaker.CRITICAL_EMERGENCY_LIGHTING,
        )
        response = api_client.get(reverse("powerbreaker-critical"))
        assert response.status_code == status.HTTP_200_OK
        ids = [b["id"] for b in response.data["breakers"]]
        assert critical.id in ids
        assert non_critical.id not in ids
        assert response.data["count"] == 1

    def test_critical_filter_by_location(self, api_client, admin_user):
        api_client.force_authenticate(user=admin_user)
        loc_a = LocationFactory()
        loc_b = LocationFactory()
        panel_a = PowerPanel.objects.create(location=loc_a, name="Panel A")
        panel_b = PowerPanel.objects.create(location=loc_b, name="Panel B")
        crit_a = PowerBreaker.objects.create(
            panel=panel_a,
            position="2",
            amperage=20,
            is_critical=True,
            critical_category=PowerBreaker.CRITICAL_EXIT_SIGN,
        )
        PowerBreaker.objects.create(
            panel=panel_b,
            position="2",
            amperage=20,
            is_critical=True,
            critical_category=PowerBreaker.CRITICAL_EXIT_SIGN,
        )
        response = api_client.get(reverse("powerbreaker-critical") + f"?location={loc_a.id}")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["breakers"][0]["id"] == crit_a.id


@pytest.mark.integration
class TestCriticalFlagSerializer:
    def test_serializer_rejects_critical_without_category(self, api_client, admin_user):
        api_client.force_authenticate(user=admin_user)
        panel = _panel()
        url = reverse("powerbreaker-list")
        response = api_client.post(
            url,
            data={
                "panel": panel.id,
                "position": "14",
                "amperage": 20,
                "phase": "A",
                "is_critical": True,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Project ships errors through the standardized envelope; the
        # field name appears under error.details.
        details = response.data.get("error", {}).get("details", response.data)
        assert "critical_category" in details

    def test_serializer_accepts_paired_flags(self, api_client, admin_user):
        api_client.force_authenticate(user=admin_user)
        panel = _panel()
        url = reverse("powerbreaker-list")
        response = api_client.post(
            url,
            data={
                "panel": panel.id,
                "position": "16",
                "amperage": 20,
                "phase": "A",
                "is_critical": True,
                "critical_category": PowerBreaker.CRITICAL_FIRE_ALARM,
                "critical_note": "Smoke detectors above the wood shop.",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        breaker = PowerBreaker.objects.get(panel=panel, position="16")
        assert breaker.is_critical is True
        assert breaker.critical_category == PowerBreaker.CRITICAL_FIRE_ALARM
