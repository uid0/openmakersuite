"""
Tests for in-app notifications fired when a ReorderRequest is created.

Creating a reorder request must produce one Notification row per active
staff user, so that the bell badge in the UI surfaces the request.
"""

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.tests.factories import InventoryItemFactory
from notifications.models import Notification

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.mark.integration
class TestReorderRequestInAppNotification:
    def test_create_reorder_creates_in_app_notifications_for_admins(self):
        admin = User.objects.create_user(
            username="admin", email="a@x.test", password="x", is_staff=True
        )
        non_admin = User.objects.create_user(username="regular", email="r@x.test", password="x")
        requester = User.objects.create_user(username="requester", email="req@x.test", password="x")
        item = InventoryItemFactory(name="Widget A")

        client = APIClient()
        client.force_authenticate(user=requester)
        resp = client.post(
            reverse("reorderrequest-list"),
            data={
                "item": str(item.id),
                "quantity": 5,
                "requested_by": "Alice",
                "priority": "high",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.content

        admin_notifs = Notification.objects.filter(user=admin)
        assert admin_notifs.count() == 1
        notif = admin_notifs.get()
        assert "Widget A" in notif.message
        assert "5" in notif.message
        assert notif.action_url == "/inventory/admin"
        assert notif.metadata.get("reorder_request_id") == resp.data["id"]
        assert notif.read is False

        # Non-admin users do NOT receive the notification
        assert not Notification.objects.filter(user=non_admin).exists()
