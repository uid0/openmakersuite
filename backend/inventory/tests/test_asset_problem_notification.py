"""
Tests for in-app notifications fired by AssetViewSet.report_problem.

Reporting a problem on an asset must create an in-app Notification row
for every active staff user (notify_admins fan-out), with metadata that
points reviewers back at the problem.
"""

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from inventory.tests.factories import AssetFactory
from notifications.models import Notification

User = get_user_model()
pytestmark = pytest.mark.django_db


def _report_problem_url(asset_id):
    return f"/api/inventory/assets/{asset_id}/report_problem/"


class TestAssetProblemInAppNotification:
    def test_report_problem_creates_notification_for_each_admin(self):
        """Each active staff user receives a warning-typed in-app notification."""
        admin1 = User.objects.create_user(
            username="admin1", email="a1@x.test", password="x", is_staff=True
        )
        admin2 = User.objects.create_user(
            username="admin2", email="a2@x.test", password="x", is_staff=True
        )
        regular = User.objects.create_user(username="regular", email="r@x.test", password="x")
        asset = AssetFactory(name="Lathe-3")

        client = APIClient()
        client.force_authenticate(user=regular)
        resp = client.post(
            _report_problem_url(asset.id),
            data={"description": "Belt broken"},
            format="json",
        )
        assert resp.status_code == 201, resp.content

        admin_notifs = Notification.objects.filter(user__in=[admin1, admin2])
        assert admin_notifs.count() == 2
        assert not Notification.objects.filter(user=regular).exists()

        for n in admin_notifs:
            assert n.type == "warning"
            assert "Lathe-3" in n.title
            assert "Belt broken" in n.message
            assert n.action_url == f"/inventory/assets/{asset.id}"
            assert n.metadata.get("asset_id") == str(asset.id)
            assert "asset_problem_id" in n.metadata
            assert n.read is False

    def test_report_problem_includes_reporter_in_message(self):
        """When a logged-in user reports the problem their username appears."""
        admin = User.objects.create_user(
            username="admin", email="a@x.test", password="x", is_staff=True
        )
        reporter = User.objects.create_user(username="reporter-bob", email="b@x.test", password="x")
        asset = AssetFactory(name="Drill")

        client = APIClient()
        client.force_authenticate(user=reporter)
        resp = client.post(
            _report_problem_url(asset.id),
            data={"description": "Chuck loose"},
            format="json",
        )
        assert resp.status_code == 201, resp.content

        notif = Notification.objects.get(user=admin)
        assert "reporter-bob" in notif.message

    def test_report_problem_truncates_long_descriptions(self):
        """Long descriptions are truncated in the notification message body."""
        admin = User.objects.create_user(
            username="admin", email="a@x.test", password="x", is_staff=True
        )
        reporter = User.objects.create_user(username="reporter", email="r@x.test", password="x")
        asset = AssetFactory(name="Mill")
        long_desc = "x" * 500

        client = APIClient()
        client.force_authenticate(user=reporter)
        resp = client.post(
            _report_problem_url(asset.id),
            data={"description": long_desc},
            format="json",
        )
        assert resp.status_code == 201, resp.content

        notif = Notification.objects.get(user=admin)
        # Truncated to 200 chars of the description body — the full message
        # still has prefix text, so just sanity-check that we didn't pass through
        # the full 500-char string.
        assert len(notif.message) < 400

    def test_inactive_admins_do_not_receive_notifications(self):
        """notify_admins skips inactive staff users."""
        active_admin = User.objects.create_user(
            username="active",
            email="active@x.test",
            password="x",
            is_staff=True,
            is_active=True,
        )
        inactive_admin = User.objects.create_user(
            username="inactive",
            email="inactive@x.test",
            password="x",
            is_staff=True,
            is_active=False,
        )
        reporter = User.objects.create_user(username="rep", email="r@x.test", password="x")
        asset = AssetFactory()

        client = APIClient()
        client.force_authenticate(user=reporter)
        resp = client.post(
            _report_problem_url(asset.id),
            data={"description": "Issue"},
            format="json",
        )
        assert resp.status_code == 201, resp.content

        assert Notification.objects.filter(user=active_admin).exists()
        assert not Notification.objects.filter(user=inactive_admin).exists()

    def test_report_problem_succeeds_when_no_admins_exist(self):
        """No staff users → no notifications, but the problem still saves."""
        reporter = User.objects.create_user(username="rep", email="r@x.test", password="x")
        asset = AssetFactory()

        client = APIClient()
        client.force_authenticate(user=reporter)
        resp = client.post(
            _report_problem_url(asset.id),
            data={"description": "Issue"},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        assert Notification.objects.count() == 0
