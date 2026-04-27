"""
API tests for the screens app.
"""

from django.contrib.auth import get_user_model
from django.utils.crypto import get_random_string

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from screens.models import Screen, ScreenContentBlock, ScreenHeartbeat, SystemMessage
from screens.tests.factories import ScreenContentBlockFactory, ScreenFactory, SystemMessageFactory

User = get_user_model()


@pytest.fixture
def staff_client(db):
    client = APIClient()
    user = User.objects.create_user(
        username="screens-staff",
        password=get_random_string(24),
        is_staff=True,
    )
    client.force_authenticate(user=user)
    return client, user


@pytest.fixture
def regular_client(db):
    client = APIClient()
    user = User.objects.create_user(username="regular", password=get_random_string(24))
    client.force_authenticate(user=user)
    return client, user


@pytest.mark.integration
@pytest.mark.django_db
class TestKioskPayloadEndpoint:
    def test_payload_requires_token(self, api_client):
        screen = ScreenFactory()
        resp = api_client.get(f"/api/screens/kiosk/{screen.slug}/")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_payload_rejects_bad_token(self, api_client):
        screen = ScreenFactory()
        resp = api_client.get(f"/api/screens/kiosk/{screen.slug}/?token=wrong")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_payload_hides_inactive_screen(self, api_client):
        screen = ScreenFactory(is_active=False)
        resp = api_client.get(f"/api/screens/kiosk/{screen.slug}/?token={screen.access_token}")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_payload_returns_enabled_blocks_and_active_messages(self, api_client):
        screen = ScreenFactory()
        enabled = ScreenContentBlockFactory(screen=screen, order=0)
        ScreenContentBlockFactory(screen=screen, order=1, is_enabled=False)
        msg = SystemMessageFactory(level=SystemMessage.LEVEL_WARNING)
        SystemMessageFactory(is_active=False)

        resp = api_client.get(f"/api/screens/kiosk/{screen.slug}/?token={screen.access_token}")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["screen"]["slug"] == screen.slug
        assert len(resp.data["content_blocks"]) == 1
        assert resp.data["content_blocks"][0]["id"] == str(enabled.id)
        assert len(resp.data["system_messages"]) == 1
        assert resp.data["system_messages"][0]["id"] == str(msg.id)

    def test_payload_accepts_header_token(self, api_client):
        screen = ScreenFactory()
        resp = api_client.get(
            f"/api/screens/kiosk/{screen.slug}/",
            HTTP_X_SCREEN_TOKEN=screen.access_token,
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_payload_includes_weather_url(self, api_client, settings):
        settings.WEATHER_URL = "https://example.com/weather/"
        screen = ScreenFactory()
        resp = api_client.get(f"/api/screens/kiosk/{screen.slug}/?token={screen.access_token}")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["weather_url"] == "https://example.com/weather/"

    def test_payload_renders_shared_weather_block(self, api_client):
        screen = ScreenFactory()
        block = ScreenContentBlockFactory(
            screen=screen,
            order=0,
            block_type=ScreenContentBlock.BLOCK_SHARED_WEATHER,
            title="",
            body="",
        )
        resp = api_client.get(f"/api/screens/kiosk/{screen.slug}/?token={screen.access_token}")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data["content_blocks"]) == 1
        assert resp.data["content_blocks"][0]["id"] == str(block.id)
        assert resp.data["content_blocks"][0]["block_type"] == "shared_weather"

    @pytest.mark.django_db
    def test_shared_traffic_block_includes_traffic_url_in_config(self, api_client, settings):
        settings.TRAFFIC_URL = "https://embed.waze.com/iframe?lat=1&lon=2"
        screen = ScreenFactory()
        ScreenContentBlockFactory(
            screen=screen,
            block_type=ScreenContentBlock.BLOCK_SHARED_TRAFFIC,
            title="Site Traffic",
            body="",
            config={},
        )
        resp = api_client.get(f"/api/screens/kiosk/{screen.slug}/?token={screen.access_token}")
        assert resp.status_code == status.HTTP_200_OK
        blocks = resp.data["content_blocks"]
        assert len(blocks) == 1
        assert blocks[0]["block_type"] == "shared_traffic"
        assert blocks[0]["config"]["url"] == "https://embed.waze.com/iframe?lat=1&lon=2"

    @pytest.mark.django_db
    def test_non_shared_traffic_block_config_is_unchanged(self, api_client, settings):
        settings.TRAFFIC_URL = "https://embed.waze.com/iframe?lat=1&lon=2"
        screen = ScreenFactory()
        ScreenContentBlockFactory(
            screen=screen,
            block_type=ScreenContentBlock.BLOCK_CUSTOM_TEXT,
            config={"foo": "bar"},
        )
        resp = api_client.get(f"/api/screens/kiosk/{screen.slug}/?token={screen.access_token}")
        assert resp.status_code == status.HTTP_200_OK
        block = resp.data["content_blocks"][0]
        assert "url" not in block["config"]
        assert block["config"] == {"foo": "bar"}


@pytest.mark.integration
class TestHeartbeatEndpoint:
    def test_heartbeat_requires_token(self, api_client):
        screen = ScreenFactory()
        resp = api_client.post(f"/api/screens/kiosk/{screen.slug}/heartbeat/")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_heartbeat_creates_record(self, api_client):
        screen = ScreenFactory()
        resp = api_client.post(
            f"/api/screens/kiosk/{screen.slug}/heartbeat/?token={screen.access_token}",
            data={"content_version": "abc123"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert ScreenHeartbeat.objects.filter(screen=screen, content_version="abc123").exists()


@pytest.mark.integration
class TestScreenCrudPermissions:
    def test_regular_user_cannot_list(self, regular_client):
        client, _ = regular_client
        ScreenFactory()
        resp = client.get("/api/screens/screens/")
        assert resp.status_code == status.HTTP_200_OK
        assert (
            resp.data["count"] == 0
            if isinstance(resp.data, dict) and "count" in resp.data
            else len(resp.data) == 0
        )

    def test_staff_can_list_all(self, staff_client):
        client, _ = staff_client
        ScreenFactory.create_batch(3)
        resp = client.get("/api/screens/screens/")
        assert resp.status_code == status.HTTP_200_OK
        # Accept either paginated or list response
        count = (
            resp.data.get("count")
            if isinstance(resp.data, dict) and "count" in resp.data
            else len(resp.data)
        )
        assert count == 3

    def test_unauthenticated_cannot_list(self, api_client):
        ScreenFactory()
        resp = api_client.get("/api/screens/screens/")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_staff_can_create(self, staff_client):
        client, _ = staff_client
        resp = client.post(
            "/api/screens/screens/",
            {"slug": "lobby", "name": "Lobby Screen"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert Screen.objects.filter(slug="lobby").exists()

    def test_status_overview_endpoint(self, staff_client):
        client, _ = staff_client
        ScreenFactory.create_batch(2)
        resp = client.get("/api/screens/screens/status/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 2
        assert len(resp.data["screens"]) == 2

    def test_rotate_token_changes_token(self, staff_client):
        client, _ = staff_client
        screen = ScreenFactory()
        old = screen.access_token
        resp = client.post(f"/api/screens/screens/{screen.slug}/rotate-token/")
        assert resp.status_code == status.HTTP_200_OK
        screen.refresh_from_db()
        assert screen.access_token != old
        assert resp.data["access_token"] == screen.access_token


@pytest.mark.integration
class TestSystemMessageEndpoints:
    def test_non_staff_cannot_create(self, regular_client):
        client, _ = regular_client
        resp = client.post(
            "/api/screens/messages/",
            {"title": "Alert", "body": "Something", "level": "info"},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_staff_can_create(self, staff_client):
        client, _ = staff_client
        resp = client.post(
            "/api/screens/messages/",
            {"title": "Alert", "body": "Something", "level": "warning"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert SystemMessage.objects.filter(title="Alert").exists()


@pytest.mark.integration
class TestContentBlockEndpoints:
    def test_staff_can_create_block(self, staff_client):
        client, _ = staff_client
        screen = ScreenFactory()
        resp = client.post(
            "/api/screens/blocks/",
            {
                "screen": str(screen.id),
                "block_type": ScreenContentBlock.BLOCK_CUSTOM_TEXT,
                "title": "Welcome",
                "body": "Be safe",
                "order": 0,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert ScreenContentBlock.objects.filter(screen=screen, title="Welcome").exists()

    def test_non_staff_blocked_from_other_sig(self, regular_client):
        client, _ = regular_client
        screen = ScreenFactory()
        ScreenContentBlockFactory(screen=screen)
        resp = client.get("/api/screens/blocks/")
        assert resp.status_code == status.HTTP_200_OK
        count = (
            resp.data.get("count")
            if isinstance(resp.data, dict) and "count" in resp.data
            else len(resp.data)
        )
        assert count == 0
