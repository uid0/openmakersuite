from types import ModuleType
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from dashboard import audit_feed, views
from dashboard.views import _parse_audit_feed_dt

User = get_user_model()


def _event(created_at, **overrides):
    row = {
        "domain": "forgekey",
        "action": "updated",
        "actor_id": 1,
        "actor_username": "staff",
        "created_at": created_at,
        "entity_type": "device",
        "entity_id": "1",
        "notes": "",
        "metadata": {},
    }
    row.update(overrides)
    return row


@pytest.mark.django_db
class TestAuditFeedEndpoint:
    def test_audit_feed_unauthenticated_returns_403(self, api_client):
        response = api_client.get(reverse("get_audit_feed"))

        assert response.status_code in {
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        }

    def test_audit_feed_non_staff_returns_403(self):
        user = User.objects.create_user(
            username="regular-user",
            email="regular@example.com",
            password="testpass123",
            is_staff=False,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(reverse("get_audit_feed"))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_audit_feed_staff_returns_200_with_zero_events_on_empty_db(self):
        user = User.objects.create_user(
            username="staff-user",
            email="staff@example.com",
            password="testpass123",
            is_staff=True,
            is_superuser=True,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(reverse("get_audit_feed"))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0
        assert response.data["events"] == []

    def test_audit_feed_limit_capped_to_1000(self):
        user = User.objects.create_user(
            username="limit-user",
            email="limit@example.com",
            password="testpass123",
            is_staff=True,
            is_superuser=True,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        with patch.object(
            views,
            "collect_events",
            return_value=[_event("2026-05-07T10:00:00+00:00")] * 5,
        ) as mock_collect:
            response = client.get(reverse("get_audit_feed"), {"limit": "99999"})

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 5
        assert len(response.data["events"]) == 5
        assert mock_collect.call_args.kwargs["limit"] == 99999

    def test_audit_feed_invalid_limit_falls_back_to_default(self):
        user = User.objects.create_user(
            username="default-limit-user",
            email="default-limit@example.com",
            password="testpass123",
            is_staff=True,
            is_superuser=True,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        with patch.object(views, "collect_events", return_value=[]) as mock_collect:
            response = client.get(reverse("get_audit_feed"), {"limit": "garbage"})

        assert response.status_code == status.HTTP_200_OK
        assert mock_collect.call_args.kwargs["limit"] == 200


class TestParseAuditFeedDatetime:
    def test_parse_audit_feed_dt_handles_z_suffix(self):
        parsed = _parse_audit_feed_dt("2026-05-07T10:00:00Z")

        assert parsed is not None
        assert timezone.is_aware(parsed)
        assert parsed.isoformat() == "2026-05-07T10:00:00+00:00"

    def test_parse_audit_feed_dt_returns_none_for_bad_input(self):
        assert _parse_audit_feed_dt(None) is None
        assert _parse_audit_feed_dt("") is None
        assert _parse_audit_feed_dt("not-a-date") is None

    def test_parse_audit_feed_dt_makes_naive_aware(self):
        parsed = _parse_audit_feed_dt("2026-05-07T10:00:00")

        assert parsed is not None
        assert timezone.is_aware(parsed)


class TestCollectEvents:
    def test_collect_events_combines_and_sorts_across_domains(self):
        collectors = (
            lambda filters: [_event("2026-05-07T09:00:00+00:00", domain="forgekey")],
            lambda filters: [_event("2026-05-07T11:00:00+00:00", domain="vendors")],
            lambda filters: [_event("2026-05-07T10:00:00+00:00", domain="donations")],
        )

        with patch.object(audit_feed, "_COLLECTORS", new=collectors):
            events = audit_feed.collect_events()

        assert [event["domain"] for event in events] == [
            "vendors",
            "donations",
            "forgekey",
        ]
        assert [event["created_at"] for event in events] == [
            "2026-05-07T11:00:00+00:00",
            "2026-05-07T10:00:00+00:00",
            "2026-05-07T09:00:00+00:00",
        ]

    def test_collect_events_filters_by_domain_post_normalization(self):
        collectors = (
            lambda filters: [
                _event("2026-05-07T11:00:00+00:00", domain="forgekey"),
                _event("2026-05-07T10:00:00+00:00", domain="vendors"),
            ],
            lambda filters: [_event("2026-05-07T09:00:00+00:00", domain="forgekey")],
        )

        with patch.object(audit_feed, "_COLLECTORS", new=collectors):
            events = audit_feed.collect_events(domain="forgekey")

        assert len(events) == 2
        assert all(event["domain"] == "forgekey" for event in events)

    def test_collect_events_filters_by_entity_type_and_entity_id(self):
        collectors = (
            lambda filters: [
                _event("2026-05-07T11:00:00+00:00", entity_type="device", entity_id="42"),
                _event("2026-05-07T10:00:00+00:00", entity_type="device", entity_id="7"),
                _event(
                    "2026-05-07T09:00:00+00:00",
                    entity_type="authorization",
                    entity_id="42",
                ),
            ],
        )

        with patch.object(audit_feed, "_COLLECTORS", new=collectors):
            events = audit_feed.collect_events(entity_type="device", entity_id=42)

        assert events == [_event("2026-05-07T11:00:00+00:00", entity_type="device", entity_id="42")]

    def test_collect_events_caps_to_limit(self):
        collectors = (
            lambda filters: [
                _event("2026-05-07T08:00:00+00:00"),
                _event("2026-05-07T11:00:00+00:00"),
            ],
            lambda filters: [
                _event("2026-05-07T10:00:00+00:00"),
                _event("2026-05-07T09:00:00+00:00"),
            ],
        )

        with patch.object(audit_feed, "_COLLECTORS", new=collectors):
            events = audit_feed.collect_events(limit=3)

        assert len(events) == 3
        assert [event["created_at"] for event in events] == [
            "2026-05-07T11:00:00+00:00",
            "2026-05-07T10:00:00+00:00",
            "2026-05-07T09:00:00+00:00",
        ]

    def test_defensive_imports_return_empty_when_module_missing(self, monkeypatch):
        stub_models = ModuleType("forgekey.models")

        monkeypatch.setitem(__import__("sys").modules, "forgekey.models", stub_models)

        events = audit_feed._collect_forgekey({})

        assert events == []
