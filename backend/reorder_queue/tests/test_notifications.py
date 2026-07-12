"""
Tests for reorder-request email notifications (oms-2q9).

Covers the recipient fallback chain (SIGProfile.email -> SIGAdmin emails ->
staff fallback), the NotificationPreference opt-out filter, and the
signal-triggered send-on-create behavior (including auto-approved requests).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail

import pytest

from inventory.tests.factories import InventoryItemFactory
from membership.models import SIGAdmin, SIGProfile
from notifications.models import NotificationPreference
from reorder_queue.models import ReorderRequest
from reorder_queue.notifications import get_reorder_recipients, send_reorder_request_notification

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def sig_group(db):
    return Group.objects.create(name="Test SIG")


@pytest.fixture
def sig_item(sig_group):
    return InventoryItemFactory(owning_group=sig_group)


def _make_admin(sig_group, email, username=None, is_active=True, **prefs):
    user = User.objects.create_user(
        username=username or email.split("@")[0],
        email=email,
        password="x",
    )
    SIGAdmin.objects.create(user=user, group=sig_group, is_active=is_active)
    if prefs:
        NotificationPreference.objects.create(user=user, **prefs)
    return user


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        username="staff1",
        email="staff1@example.org",
        password="x",
        is_staff=True,
    )


class TestRecipientResolution:
    def test_email_to_group_email_if_set(self, sig_group, sig_item):
        """AC 1: SIGProfile.email wins over admin fan-out."""
        SIGProfile.objects.create(group=sig_group, email="sig-contact@example.org")
        _make_admin(sig_group, "admin1@example.org")

        request = ReorderRequest.objects.create(item=sig_item, quantity=5)

        recipients = get_reorder_recipients(request)
        assert recipients == ["sig-contact@example.org"]

    def test_email_to_admins_if_group_email_missing(self, sig_group, sig_item):
        """AC 1: When SIGProfile.email is blank, fan out to active SIG admins."""
        SIGProfile.objects.create(group=sig_group, email="")
        _make_admin(sig_group, "admin1@example.org")
        _make_admin(sig_group, "admin2@example.org")

        request = ReorderRequest.objects.create(item=sig_item, quantity=3)

        recipients = get_reorder_recipients(request)
        assert sorted(recipients) == ["admin1@example.org", "admin2@example.org"]

    def test_email_to_staff_fallback_if_no_admins(self, sig_group, sig_item, staff_user):
        """AC 1: With no SIGProfile and no admins, fall back to staff."""
        request = ReorderRequest.objects.create(item=sig_item, quantity=2)

        recipients = get_reorder_recipients(request)
        assert staff_user.email in recipients

    def test_respects_notification_preferences_email_disabled(self, sig_group, sig_item):
        """AC 4: Admins who opted out of email notifications are excluded."""
        _make_admin(
            sig_group,
            "opted-out@example.org",
            email_enabled=False,
            order_updates=True,
        )
        _make_admin(
            sig_group,
            "opted-in@example.org",
            email_enabled=True,
            order_updates=True,
        )

        request = ReorderRequest.objects.create(item=sig_item, quantity=1)

        recipients = get_reorder_recipients(request)
        assert "opted-out@example.org" not in recipients
        assert "opted-in@example.org" in recipients

    def test_respects_notification_preferences_order_updates_disabled(self, sig_group, sig_item):
        """AC 4: Admins with order_updates=False are excluded."""
        _make_admin(
            sig_group,
            "no-orders@example.org",
            email_enabled=True,
            order_updates=False,
        )
        _make_admin(sig_group, "yes-orders@example.org")

        request = ReorderRequest.objects.create(item=sig_item, quantity=1)

        recipients = get_reorder_recipients(request)
        assert "no-orders@example.org" not in recipients
        assert "yes-orders@example.org" in recipients


class TestSendReorderRequestNotification:
    def test_send_email_end_to_end(self, sig_group, sig_item):
        """An end-to-end send populates the test outbox with a well-formed message."""
        SIGProfile.objects.create(group=sig_group, email="sig-contact@example.org")
        request = ReorderRequest.objects.create(
            item=sig_item,
            quantity=7,
            requested_by="Alice",
        )
        mail.outbox.clear()

        send_reorder_request_notification(request)

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == ["sig-contact@example.org"]
        assert str(request.quantity) in message.subject
        assert sig_item.name in message.subject
        assert any("html" in alt[1] for alt in message.alternatives)

    def test_returns_none_when_no_recipients(self, sig_group, sig_item):
        """With no SIGProfile, no admins, and no staff, nothing is sent."""
        request = ReorderRequest.objects.create(item=sig_item, quantity=1)
        mail.outbox.clear()

        result = send_reorder_request_notification(request)

        assert result is None
        assert mail.outbox == []


class TestSignalIntegration:
    def test_new_reorder_request_triggers_email(self, sig_group, sig_item):
        """AC 1: Creating a ReorderRequest fires the post_save email task."""
        SIGProfile.objects.create(group=sig_group, email="sig-contact@example.org")
        mail.outbox.clear()

        ReorderRequest.objects.create(item=sig_item, quantity=4, requested_by="Bob")

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["sig-contact@example.org"]

    def test_auto_approved_request_still_notifies(self, sig_group, sig_item):
        """AC 6: A request saved as already-approved still sends on create."""
        SIGProfile.objects.create(group=sig_group, email="sig-contact@example.org")
        mail.outbox.clear()

        ReorderRequest.objects.create(
            item=sig_item,
            quantity=2,
            status=ReorderRequest.Status.APPROVED,
            requested_by="Automation",
        )

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["sig-contact@example.org"]
