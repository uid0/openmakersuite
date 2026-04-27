"""
Tests for notifications app.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Notification, NotificationPreference
from .services import create_bulk_notifications, create_notification, notify_admins

User = get_user_model()


class NotificationModelTest(TestCase):
    """Tests for Notification model."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )

    def test_notification_creation(self):
        """Test creating a notification."""
        notification = Notification.objects.create(
            user=self.user,
            type="info",
            title="Test Notification",
            message="This is a test notification",
        )
        self.assertEqual(notification.user, self.user)
        self.assertEqual(notification.type, "info")
        self.assertEqual(notification.title, "Test Notification")
        self.assertFalse(notification.read)

    def test_mark_as_read(self):
        """Test marking a notification as read."""
        notification = Notification.objects.create(
            user=self.user,
            type="info",
            title="Test Notification",
            message="This is a test notification",
        )
        self.assertFalse(notification.read)
        notification.mark_as_read()
        self.assertTrue(notification.read)

    def test_notification_str(self):
        """Test notification string representation."""
        notification = Notification.objects.create(
            user=self.user,
            type="error",
            title="Error Notification",
            message="Something went wrong",
        )
        self.assertIn("error", str(notification).lower())
        self.assertIn("error notification", str(notification).lower())
        self.assertIn(self.user.username, str(notification))


class NotificationServicesTest(TestCase):
    """Tests for notification service functions."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )
        self.user2 = User.objects.create_user(
            username="testuser2", email="test2@example.com", password="testpass123"
        )

    def test_create_notification(self):
        """Test create_notification service function."""
        notification = create_notification(
            user=self.user,
            type="success",
            title="Success!",
            message="Operation completed successfully",
            action_url="/some/url",
        )
        self.assertEqual(notification.user, self.user)
        self.assertEqual(notification.type, "success")
        self.assertEqual(notification.title, "Success!")
        self.assertEqual(notification.action_url, "/some/url")
        self.assertFalse(notification.read)

    def test_create_bulk_notifications(self):
        """Test create_bulk_notifications service function."""
        users = [self.user, self.user2]
        notifications = create_bulk_notifications(
            users=users,
            type="warning",
            title="Warning",
            message="This is a warning",
        )
        self.assertEqual(len(notifications), 2)
        self.assertEqual(notifications[0].user, self.user)
        self.assertEqual(notifications[1].user, self.user2)
        self.assertEqual(notifications[0].type, "warning")

    def test_notify_admins(self):
        """Test notify_admins service function."""
        # Create admin users
        User.objects.create_user(
            username="admin1",
            email="admin1@example.com",
            password="testpass123",
            is_staff=True,
        )
        User.objects.create_user(
            username="admin2",
            email="admin2@example.com",
            password="testpass123",
            is_staff=True,
        )
        # Create non-admin user (should not receive notification)
        User.objects.create_user(
            username="regular",
            email="regular@example.com",
            password="testpass123",
            is_staff=False,
        )

        notifications = notify_admins(
            type="info",
            title="Admin Notification",
            message="This is for admins only",
        )
        # Should notify all staff users
        admin_users = User.objects.filter(is_staff=True, is_active=True)
        self.assertEqual(len(notifications), admin_users.count())
        self.assertTrue(all(n.user.is_staff for n in notifications))


class NotificationViewSetTest(TestCase):
    """Tests for NotificationViewSet API."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )
        self.other_user = User.objects.create_user(
            username="otheruser", email="other@example.com", password="testpass123"
        )
        # Create notifications for both users
        self.user_notification = Notification.objects.create(
            user=self.user,
            type="info",
            title="User Notification",
            message="This is for the user",
        )
        self.other_notification = Notification.objects.create(
            user=self.other_user,
            type="info",
            title="Other User Notification",
            message="This is for another user",
        )

    def test_list_notifications_requires_auth(self):
        """Test that listing notifications requires authentication."""
        from rest_framework.test import APIClient

        client = APIClient()
        response = client.get("/api/notifications/")
        self.assertEqual(response.status_code, 401)

    def test_list_own_notifications(self):
        """Test that users can only see their own notifications."""
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken

        client = APIClient()
        refresh = RefreshToken.for_user(self.user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        response = client.get("/api/notifications/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        # ID might be returned as int or string depending on serializer
        notification_id = response.data["results"][0]["id"]
        self.assertEqual(int(notification_id), self.user_notification.id)

    def test_mark_as_read(self):
        """Test marking a notification as read."""
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken

        client = APIClient()
        refresh = RefreshToken.for_user(self.user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        response = client.post(f"/api/notifications/{self.user_notification.id}/mark-read/")
        self.assertEqual(response.status_code, 200)
        self.user_notification.refresh_from_db()
        self.assertTrue(self.user_notification.read)

    def test_mark_all_as_read(self):
        """Test marking all notifications as read."""
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken

        # Create another unread notification
        Notification.objects.create(
            user=self.user,
            type="warning",
            title="Another Notification",
            message="Another message",
        )

        client = APIClient()
        refresh = RefreshToken.for_user(self.user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        response = client.post("/api/notifications/mark-all-read/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated"], 2)

        # Verify all notifications are now read
        unread_count = Notification.objects.filter(user=self.user, read=False).count()
        self.assertEqual(unread_count, 0)

    def test_delete_notification(self):
        """Test deleting a notification."""
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken

        client = APIClient()
        refresh = RefreshToken.for_user(self.user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        notification_id = self.user_notification.id
        response = client.delete(f"/api/notifications/{notification_id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Notification.objects.filter(id=notification_id).exists())


class NotificationPreferenceViewTest(TestCase):
    """Tests for NotificationPreferenceView API."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="prefuser", email="pref@example.com", password="testpass123"
        )

    def _authed_client(self):
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken

        client = APIClient()
        refresh = RefreshToken.for_user(self.user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        return client

    def test_get_preferences_requires_auth(self):
        from rest_framework.test import APIClient

        response = APIClient().get("/api/notifications/preferences/")
        self.assertEqual(response.status_code, 401)

    def test_get_preferences_creates_defaults_for_fresh_user(self):
        self.assertFalse(NotificationPreference.objects.filter(user=self.user).exists())

        response = self._authed_client().get("/api/notifications/preferences/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["email_enabled"])
        self.assertTrue(response.data["in_app_enabled"])
        self.assertEqual(response.data["recent_pages_limit"], 8)
        self.assertTrue(NotificationPreference.objects.filter(user=self.user).exists())

    def test_put_preferences_updates_and_persists(self):
        response = self._authed_client().put(
            "/api/notifications/preferences/",
            data={
                "email_enabled": False,
                "in_app_enabled": False,
                "supply_alerts": False,
                "maintenance_alerts": False,
                "order_updates": False,
                "system_notifications": False,
                "recent_pages_limit": 5,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["email_enabled"])
        self.assertEqual(response.data["recent_pages_limit"], 5)

        prefs = NotificationPreference.objects.get(user=self.user)
        self.assertFalse(prefs.email_enabled)
        self.assertFalse(prefs.in_app_enabled)
        self.assertEqual(prefs.recent_pages_limit, 5)

    def test_patch_preferences_partial_update(self):
        NotificationPreference.objects.create(
            user=self.user, email_enabled=True, recent_pages_limit=8
        )

        response = self._authed_client().patch(
            "/api/notifications/preferences/",
            data={"recent_pages_limit": 20},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["recent_pages_limit"], 20)
        self.assertTrue(response.data["email_enabled"])  # unchanged

        prefs = NotificationPreference.objects.get(user=self.user)
        self.assertEqual(prefs.recent_pages_limit, 20)
        self.assertTrue(prefs.email_enabled)

    def test_put_preferences_validation_error(self):
        response = self._authed_client().put(
            "/api/notifications/preferences/",
            data={"recent_pages_limit": 999},  # exceeds MaxValueValidator(50)
            format="json",
        )

        self.assertEqual(response.status_code, 400)


class NotificationListBehaviorTest(TestCase):
    """Coverage for ordering, pagination, and read filtering of GET /api/notifications/."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="lister", email="lister@example.com", password="testpass123"
        )

    def _client(self):
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken

        client = APIClient()
        refresh = RefreshToken.for_user(self.user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        return client

    def test_list_returns_paginated_response_ordered_by_newest(self):
        # Create 30 notifications so pagination kicks in regardless of page-size config
        for i in range(30):
            Notification.objects.create(
                user=self.user,
                type="info",
                title=f"Notif {i}",
                message="m",
            )

        response = self._client().get("/api/notifications/")
        self.assertEqual(response.status_code, 200)
        # Paginated payload exposes "results" + "count"
        self.assertIn("results", response.data)
        self.assertIn("count", response.data)
        self.assertEqual(response.data["count"], 30)

        # Newest first (Notification.Meta.ordering = ["-created_at"])
        results = response.data["results"]
        self.assertGreater(len(results), 0)
        titles = [r["title"] for r in results]
        self.assertEqual(titles[0], "Notif 29")

    def test_filter_by_unread(self):
        Notification.objects.create(
            user=self.user, type="info", title="Read", message="m", read=True
        )
        Notification.objects.create(
            user=self.user, type="info", title="Unread", message="m", read=False
        )

        response = self._client().get("/api/notifications/?read=false")
        self.assertEqual(response.status_code, 200)
        titles = [r["title"] for r in response.data["results"]]
        self.assertEqual(titles, ["Unread"])

    def test_mark_read_cannot_target_other_users_notification(self):
        other = User.objects.create_user(
            username="other2", email="other2@example.com", password="testpass123"
        )
        other_notif = Notification.objects.create(
            user=other, type="info", title="Theirs", message="m"
        )

        response = self._client().post(f"/api/notifications/{other_notif.id}/mark-read/")
        # Queryset filters to current user, so the row is "not found"
        self.assertEqual(response.status_code, 404)
        other_notif.refresh_from_db()
        self.assertFalse(other_notif.read)
