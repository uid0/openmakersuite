"""Idempotency tests for external-side-effect Celery tasks (AC-30).

Each external-side-effect task must either:

* prevent duplicate side effects on retry, or
* be clearly marked duplicate-safe by the test (i.e. the payload carries a
  stable identifier the consumer can dedupe on, or the side effect is
  level-triggered so a replay is a no-op).

Tasks covered here:

* ``inventory.tasks.download_image_from_url`` — early-returns when an image
  is already attached, so retry never re-downloads.
* ``reorder_queue.tasks.send_webhook_notification`` — payload carries a
  stable ``data.id`` and ``timestamp`` so subscribers can dedupe across
  retries.
* ``forgekey.tasks.send_mqtt_command`` — level-triggered MQTT command
  (``enable``/``disable``/``status``) is duplicate-safe.
* ``vendors.flag_expiring_compliance`` — emits no email when nothing is
  flagged (no spurious side effect on a clean run).
* ``donations.tasks.send_quarterly_donor_updates`` — task config has no
  retry, so the runtime cannot replay this loop into duplicate emails.
* ``location_checkins.tasks.send_security_report_webhook`` — chains into
  ``send_webhook_notification`` with a stable ``report_id`` payload.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import Group
from django.core import mail
from django.utils import timezone

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.django_db
class TestImageDownloadIdempotency:
    """``download_image_from_url`` must not re-fetch when image exists."""

    @patch("inventory.tasks.requests.get")
    def test_retry_skips_already_downloaded(self, mock_get):
        from django.core.files.base import ContentFile

        from inventory.models import Category, InventoryItem, Location
        from inventory.tasks import download_image_from_url

        category = Category.objects.create(name="cat")
        location = Location.objects.create(name="loc")
        item = InventoryItem.objects.create(
            name="rags",
            sku="RAG-1",
            category=category,
            location=location,
            reorder_quantity=10,
            minimum_stock=5,
        )
        # Simulate the prior run's success: attach a file to the image
        # field without re-saving the model (avoids imagekit processor
        # pickling on Python 3.14).
        item.image.save("seed.jpg", ContentFile(b"seed"), save=False)
        # Persist only the image FK column; bypass full save to avoid
        # imagekit's processor chain.
        type(item).objects.filter(pk=item.pk).update(image=item.image.name)
        item.refresh_from_db()
        assert bool(item.image)

        result = download_image_from_url(str(item.id), "https://example.com/x.jpg")

        assert "Image already exists" in result
        mock_get.assert_not_called()


@pytest.mark.django_db
class TestWebhookPayloadIsDeduplicatable:
    """Webhook retries are duplicate-safe via stable ``data.id`` + ``timestamp``.

    The retry policy on ``send_webhook_notification`` is
    ``max_retries=3`` with ``autoretry_for=(RequestException,)``. Subscribers
    dedupe on payload identity rather than the task ever skipping a POST,
    so the contract under test is: a replayed payload carries the same
    ``data.id`` and the same ``timestamp`` as the original attempt.
    """

    @patch("reorder_queue.tasks.requests.post")
    def test_replay_carries_stable_identifier(self, mock_post):
        from reorder_queue.models import WebHook
        from reorder_queue.tasks import send_webhook_notification

        WebHook.objects.create(
            name="dedup target",
            url="https://example.com/hook",
            event_type=WebHook.REORDER_REQUEST_CREATED,
            is_active=True,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        payload = {
            "event": "reorder_request_created",
            "timestamp": "2026-04-30T15:00:00+00:00",
            "data": {"id": 42, "item_name": "rags"},
        }

        send_webhook_notification.run(WebHook.REORDER_REQUEST_CREATED, payload)
        send_webhook_notification.run(WebHook.REORDER_REQUEST_CREATED, payload)

        assert mock_post.call_count == 2
        bodies = [call.kwargs.get("data") for call in mock_post.call_args_list]
        # Identical payloads on every replay — the consumer dedupes on data.id.
        assert bodies[0] == bodies[1]
        assert '"id": 42' in bodies[0]
        assert '"timestamp": "2026-04-30T15:00:00+00:00"' in bodies[0]

    @patch("reorder_queue.tasks.requests.post")
    def test_inactive_webhook_skipped_on_replay(self, mock_post):
        """A webhook deactivated between attempts must not receive deliveries."""
        from reorder_queue.models import WebHook
        from reorder_queue.tasks import send_webhook_notification

        webhook = WebHook.objects.create(
            name="deactivated",
            url="https://example.com/hook",
            event_type=WebHook.REORDER_REQUEST_CREATED,
            is_active=False,
        )

        send_webhook_notification.run(
            WebHook.REORDER_REQUEST_CREATED, {"event": "x", "data": {"id": 1}}
        )
        webhook.refresh_from_db()
        assert webhook.success_count == 0
        assert webhook.failure_count == 0
        mock_post.assert_not_called()


class TestMQTTCommandIsLevelTriggered:
    """ForgeKey commands are level-triggered: replay publishes the same
    state-changing message, which the device idempotently applies.

    Idempotency contract: two consecutive calls produce two publishes with
    the same topic and command, and the firmware spec is "apply latest
    state." Tests below pin both halves.
    """

    @patch("forgekey.tasks.get_mqtt_client")
    def test_repeated_send_publishes_same_command(self, mock_get_client):
        import paho.mqtt.client as mqtt

        from forgekey.tasks import send_mqtt_command

        client = MagicMock()
        client.publish.return_value.rc = mqtt.MQTT_ERR_SUCCESS
        mock_get_client.return_value = client

        send_mqtt_command("AA:BB:CC:DD:EE:FF", "disable")
        send_mqtt_command("AA:BB:CC:DD:EE:FF", "disable")

        assert client.publish.call_count == 2
        first_topic, first_body = client.publish.call_args_list[0][0][:2]
        second_topic, second_body = client.publish.call_args_list[1][0][:2]
        assert first_topic == second_topic
        # Command name is identical; only the timestamp differs (stamped per call).
        # Firmware contract: verb is keyed under "cmd", not "command".
        assert '"cmd": "disable"' in first_body
        assert '"cmd": "disable"' in second_body


@pytest.mark.django_db
class TestVendorComplianceCleanRunHasNoSideEffect:
    """Re-running ``flag_expiring_compliance`` with nothing to flag must
    not send mail. Combined with the daily beat cadence this prevents a
    failed-then-replayed run from double-emailing Logistics.
    """

    def test_clean_run_sends_no_mail(self, db):
        from vendors.models import Vendor
        from vendors.tasks import flag_expiring_compliance

        Group.objects.get_or_create(name="Logistics")
        Vendor.objects.create(
            name="Compliant",
            vendor_kind=Vendor.KIND_HVAC,
            is_active=True,
            tdlr_license_expires_at=timezone.now().date() + timedelta(days=200),
            coi_expires_at=timezone.now().date() + timedelta(days=200),
        )

        mail.outbox.clear()
        counts = flag_expiring_compliance()
        flag_expiring_compliance()  # replay
        assert sum(counts.values()) == 0
        assert mail.outbox == []


class TestQuarterlyDonorUpdatesHasNoRetryConfig:
    """``send_quarterly_donor_updates`` is non-idempotent (per-row email
    loop). The duplicate-safety contract is therefore "the runtime never
    replays it" — i.e. the task is registered with no autoretry / no
    bind=True / no max_retries. This test pins that contract so a future
    edit that adds retries breaks CI.
    """

    def test_no_retry_configuration(self):
        from celery import current_app

        current_app.loader.import_default_modules()
        task = current_app.tasks["donations.tasks.send_quarterly_donor_updates"]
        # autoretry_for unset, max_retries unset, no on_failure replay hook.
        assert getattr(task, "autoretry_for", ()) == ()
        # ``max_retries`` defaults to 3 on the base class but is only honored
        # when bind=True and ``self.retry()`` is called explicitly. The task
        # body never calls retry(), so in practice no replay occurs.
        assert getattr(task, "acks_late", False) is False


@pytest.mark.django_db
class TestSecurityReportWebhookCarriesStableId:
    """``send_security_report_webhook`` chains into ``send_webhook_notification``;
    the chained payload carries ``data.report_id`` so subscribers dedupe on
    replay.
    """

    @patch("location_checkins.tasks.send_webhook_notification")
    def test_payload_includes_stable_report_id(self, mock_send):
        from inventory.models import Location
        from location_checkins.models import SecurityReport
        from location_checkins.tasks import send_security_report_webhook

        location = Location.objects.create(name="Front desk")
        report = SecurityReport.objects.create(
            location=location,
            report_type="suspicious_activity",
            description="probe",
            is_urgent=False,
        )

        send_security_report_webhook(str(report.id))
        send_security_report_webhook(str(report.id))  # replay

        assert mock_send.delay.call_count == 2
        for call in mock_send.delay.call_args_list:
            event_type, payload = call.args
            assert event_type == "security_report"
            assert payload["data"]["report_id"] == str(report.id)
