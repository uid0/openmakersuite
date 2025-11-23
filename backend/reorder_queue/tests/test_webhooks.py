"""
Tests for webhook functionality.
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

import pytest

from inventory.models import Category, InventoryItem, Location
from reorder_queue.models import ReorderRequest, WebHook
from reorder_queue.tasks import (
    run_webhook_test,
    send_webhook_notification,
    trigger_reorder_request_webhook,
)

User = get_user_model()


@pytest.mark.django_db
class TestWebHookModel(TestCase):
    """Test cases for WebHook model."""

    def setUp(self):
        """Set up test data."""
        self.webhook = WebHook.objects.create(
            name="Test Webhook",
            description="Test webhook description",
            url="https://example.com/webhook",
            event_type=WebHook.REORDER_REQUEST_CREATED,
            is_active=True,
            secret="test-secret-key",
        )

    def test_webhook_creation(self):
        """Test webhook can be created."""
        self.assertEqual(self.webhook.name, "Test Webhook")
        self.assertEqual(self.webhook.url, "https://example.com/webhook")
        self.assertEqual(self.webhook.event_type, WebHook.REORDER_REQUEST_CREATED)
        self.assertTrue(self.webhook.is_active)
        self.assertEqual(self.webhook.success_count, 0)
        self.assertEqual(self.webhook.failure_count, 0)

    def test_webhook_str_representation(self):
        """Test webhook string representation."""
        expected = f"✓ Test Webhook ({self.webhook.get_event_type_display()})"
        self.assertEqual(str(self.webhook), expected)

    def test_record_success(self):
        """Test recording successful webhook delivery."""
        initial_success = self.webhook.success_count
        self.webhook.record_success()

        self.webhook.refresh_from_db()
        self.assertEqual(self.webhook.success_count, initial_success + 1)
        self.assertIsNotNone(self.webhook.last_triggered_at)
        self.assertEqual(self.webhook.last_error, "")

    def test_record_failure(self):
        """Test recording failed webhook delivery."""
        initial_failure = self.webhook.failure_count
        error_message = "Connection timeout"

        self.webhook.record_failure(error_message)

        self.webhook.refresh_from_db()
        self.assertEqual(self.webhook.failure_count, initial_failure + 1)
        self.assertIsNotNone(self.webhook.last_triggered_at)
        self.assertEqual(self.webhook.last_error, error_message)

    def test_webhook_headers_field(self):
        """Test webhook headers JSON field."""
        headers = {
            "Authorization": "Bearer token123",
            "Content-Type": "application/json",
        }
        webhook = WebHook.objects.create(
            name="Webhook with headers",
            url="https://api.example.com/hook",
            event_type=WebHook.ITEM_LOW_STOCK,
            headers=headers,
        )

        webhook.refresh_from_db()
        self.assertEqual(webhook.headers, headers)


@pytest.mark.django_db
class TestWebHookTasks(TestCase):
    """Test cases for webhook Celery tasks."""

    def setUp(self):
        """Set up test data."""
        self.webhook = WebHook.objects.create(
            name="Test Webhook",
            url="https://example.com/webhook",
            event_type=WebHook.REORDER_REQUEST_CREATED,
            is_active=True,
            secret="test-secret-key",
        )

        # Create test item for reorder requests
        self.category = Category.objects.create(name="Test Category")
        self.location = Location.objects.create(name="Test Location")
        self.item = InventoryItem.objects.create(
            name="Test Item",
            sku="TEST-001",
            description="Test description",
            minimum_stock=10,
            reorder_quantity=20,
            current_stock=5,
            category=self.category,
            location=self.location,
        )

        self.user = User.objects.create_user(username="testuser", password="testpass123")

    @patch("reorder_queue.tasks.requests.post")
    def test_send_webhook_notification_success(self, mock_post):
        """Test sending webhook notification successfully."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        payload = {"event": "test_event", "data": {"id": 1}}
        # Use .run() to call the task function directly without Celery
        result = send_webhook_notification.run(
            event_type=WebHook.REORDER_REQUEST_CREATED, payload=payload
        )

        self.assertEqual(result["event_type"], WebHook.REORDER_REQUEST_CREATED)
        self.assertEqual(result["webhooks_triggered"], 1)
        self.assertEqual(len(result["successful"]), 1)
        self.assertEqual(len(result["failed"]), 0)
        mock_post.assert_called_once()

        # Verify webhook statistics updated
        self.webhook.refresh_from_db()
        self.assertEqual(self.webhook.success_count, 1)
        self.assertEqual(self.webhook.failure_count, 0)

    @patch("reorder_queue.tasks.requests.post")
    def test_send_webhook_notification_failure(self, mock_post):
        """Test webhook notification failure handling."""
        mock_post.side_effect = Exception("Connection error")

        payload = {"event": "test_event", "data": {"id": 1}}
        result = send_webhook_notification.run(
            event_type=WebHook.REORDER_REQUEST_CREATED, payload=payload
        )

        self.assertEqual(result["event_type"], WebHook.REORDER_REQUEST_CREATED)
        self.assertEqual(result["webhooks_triggered"], 1)
        self.assertEqual(len(result["successful"]), 0)
        self.assertEqual(len(result["failed"]), 1)

        # Verify failure was recorded
        self.webhook.refresh_from_db()
        self.assertEqual(self.webhook.failure_count, 1)
        self.assertIn("Connection error", self.webhook.last_error)

    @patch("reorder_queue.tasks.requests.post")
    def test_send_webhook_with_hmac_signature(self, mock_post):
        """Test webhook includes HMAC signature when secret is configured."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        payload = {"event": "test_event"}
        send_webhook_notification.run(event_type=WebHook.REORDER_REQUEST_CREATED, payload=payload)

        # Verify HMAC signature header was added
        call_args = mock_post.call_args
        headers = call_args[1]["headers"]
        self.assertIn("X-Webhook-Signature", headers)

    @patch("reorder_queue.tasks.requests.post")
    def test_inactive_webhook_not_triggered(self, mock_post):
        """Test inactive webhooks are not triggered."""
        self.webhook.is_active = False
        self.webhook.save()

        payload = {"event": "test_event"}
        result = send_webhook_notification.run(
            event_type=WebHook.REORDER_REQUEST_CREATED, payload=payload
        )

        self.assertEqual(result["event_type"], WebHook.REORDER_REQUEST_CREATED)
        self.assertEqual(result["webhooks_triggered"], 0)
        mock_post.assert_not_called()

    @patch("reorder_queue.signals.trigger_reorder_request_webhook")
    @patch("reorder_queue.tasks.send_webhook_notification")
    def test_trigger_reorder_request_webhook(self, mock_send_webhook, mock_trigger_signal):
        """Test triggering webhook for reorder request."""
        # Mock the return value of send_webhook_notification.run()
        mock_send_webhook.run.return_value = {
            "event_type": WebHook.REORDER_REQUEST_CREATED,
            "webhooks_triggered": 1,
            "successful": [],
            "failed": [],
        }

        # Create reorder request (signal mocked so won't trigger webhook)
        reorder_request = ReorderRequest.objects.create(
            item=self.item, quantity=20, requested_by="testuser", status="pending"
        )

        # Call the task function directly
        trigger_reorder_request_webhook.run(reorder_request.id)

        # In eager mode, send_webhook_notification.run() is called instead of .delay()
        mock_send_webhook.run.assert_called_once()
        call_args = mock_send_webhook.run.call_args

        # Verify event type
        self.assertEqual(call_args[0][0], "reorder_request_created")

        # Verify payload structure
        payload = call_args[0][1]
        self.assertEqual(payload["data"]["id"], reorder_request.id)
        self.assertEqual(payload["data"]["item_name"], self.item.name)
        self.assertEqual(payload["data"]["quantity"], 20)

    @patch("reorder_queue.tasks.requests.post")
    def test_webhook_test_task(self, mock_post):
        """Test the webhook test task."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        result = run_webhook_test(self.webhook.id)

        self.assertTrue(result["success"])
        self.assertEqual(result["status_code"], 200)
        self.assertIn("response_body", result)
        mock_post.assert_called_once()


@pytest.mark.django_db
class TestWebHookSignals(TestCase):
    """Test webhook signal handlers."""

    def setUp(self):
        """Set up test data."""
        WebHook.objects.create(
            name="Reorder Request Webhook",
            url="https://example.com/webhook",
            event_type=WebHook.REORDER_REQUEST_CREATED,
            is_active=True,
        )

        self.category = Category.objects.create(name="Test Category")
        self.location = Location.objects.create(name="Test Location")
        self.item = InventoryItem.objects.create(
            name="Test Item",
            sku="TEST-001",
            description="Test description",
            minimum_stock=10,
            reorder_quantity=20,
            current_stock=5,
            category=self.category,
            location=self.location,
        )

    @patch("reorder_queue.signals.trigger_reorder_request_webhook.delay")
    def test_reorder_request_created_triggers_webhook(self, mock_trigger):
        """Test that creating a reorder request triggers webhook."""
        reorder_request = ReorderRequest.objects.create(
            item=self.item, quantity=20, requested_by="testuser", status="pending"
        )

        # Verify webhook task was queued
        mock_trigger.assert_called_once_with(reorder_request.id)

    @patch("reorder_queue.signals.trigger_reorder_request_webhook.delay")
    def test_reorder_request_update_does_not_trigger_webhook(self, mock_trigger):
        """Test that updating a reorder request does not trigger webhook."""
        reorder_request = ReorderRequest.objects.create(
            item=self.item, quantity=20, requested_by="testuser", status="pending"
        )

        # Reset mock to ignore the creation call
        mock_trigger.reset_mock()

        # Update the request
        reorder_request.status = "approved"
        reorder_request.save()

        # Verify webhook was not triggered on update
        mock_trigger.assert_not_called()
