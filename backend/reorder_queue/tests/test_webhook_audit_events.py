from django.urls import reverse

import pytest
from rest_framework import status

from reorder_queue.models import WebHook, WebhookAuditEvent
from reorder_queue.webhook_audit import diff_audited_fields, record_event

pytestmark = pytest.mark.django_db


def _webhook_payload(**overrides):
    payload = {
        "name": "Inventory Alerts",
        "url": "https://example.com/webhooks/inventory",
        "event_type": WebHook.REORDER_REQUEST_CREATED,
    }
    payload.update(overrides)
    return payload


def _create_webhook(**overrides):
    defaults = {
        "name": "Inventory Alerts",
        "url": "https://example.com/webhooks/inventory",
        "event_type": WebHook.REORDER_REQUEST_CREATED,
        "is_active": True,
        "secret": "initial-secret",
    }
    defaults.update(overrides)
    return WebHook.objects.create(**defaults)


def test_webhook_create_records_audit_event(authenticated_client):
    client, user = authenticated_client

    response = client.post(reverse("webhook-list"), _webhook_payload(), format="json")

    assert response.status_code == status.HTTP_201_CREATED, response.content
    # Test DB is fresh per @django_db, so the test just created the only
    # WebHook row. Look it up by query rather than relying on the
    # serializer's id-key shape (which may be `id` / `uuid` / etc).
    webhook = WebHook.objects.get()
    event = WebhookAuditEvent.objects.get()
    assert event.action == WebhookAuditEvent.ACTION_WEBHOOK_CREATE
    assert event.actor == user
    assert event.webhook == webhook
    assert event.metadata == {
        "name": webhook.name,
        "url": webhook.url,
        "event_type": webhook.event_type,
    }


def test_webhook_update_records_diff(authenticated_client):
    client, user = authenticated_client
    webhook = _create_webhook()

    response = client.patch(
        reverse("webhook-detail", kwargs={"pk": webhook.pk}),
        {
            "name": "Updated Inventory Alerts",
            "url": "https://example.com/webhooks/updated",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    event = WebhookAuditEvent.objects.get()
    assert event.action == WebhookAuditEvent.ACTION_WEBHOOK_UPDATE
    assert event.actor == user
    assert event.webhook == webhook
    assert event.metadata["changes"] == {
        "name": {
            "before": "Inventory Alerts",
            "after": "Updated Inventory Alerts",
        },
        "url": {
            "before": "https://example.com/webhooks/inventory",
            "after": "https://example.com/webhooks/updated",
        },
    }
    assert "is_active" not in event.metadata["changes"]


def test_webhook_secret_rotate_emits_distinct_event(authenticated_client):
    client, _user = authenticated_client
    webhook = _create_webhook()

    response = client.patch(
        reverse("webhook-detail", kwargs={"pk": webhook.pk}),
        {"secret": "new-secret"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    event = WebhookAuditEvent.objects.get()
    assert event.action == WebhookAuditEvent.ACTION_WEBHOOK_SECRET_ROTATE
    assert "secret" not in event.metadata
    assert "new-secret" not in str(event.metadata)
    assert "new-secret" not in event.notes
    assert not WebhookAuditEvent.objects.filter(
        action=WebhookAuditEvent.ACTION_WEBHOOK_UPDATE
    ).exists()


def test_webhook_disable_records_audit(authenticated_client):
    client, user = authenticated_client
    webhook = _create_webhook(is_active=True)

    response = client.patch(
        reverse("webhook-detail", kwargs={"pk": webhook.pk}),
        {"is_active": False},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    event = WebhookAuditEvent.objects.get()
    assert event.action == WebhookAuditEvent.ACTION_WEBHOOK_DISABLE
    assert event.actor == user
    assert event.webhook == webhook


def test_webhook_enable_records_audit(authenticated_client):
    client, user = authenticated_client
    webhook = _create_webhook(is_active=False)

    response = client.patch(
        reverse("webhook-detail", kwargs={"pk": webhook.pk}),
        {"is_active": True},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    event = WebhookAuditEvent.objects.get()
    assert event.action == WebhookAuditEvent.ACTION_WEBHOOK_ENABLE
    assert event.actor == user
    assert event.webhook == webhook


def test_webhook_update_does_not_include_is_active_in_diff(authenticated_client):
    client, user = authenticated_client
    webhook = _create_webhook(is_active=True)

    response = client.patch(
        reverse("webhook-detail", kwargs={"pk": webhook.pk}),
        {
            "name": "Updated Inventory Alerts",
            "is_active": False,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert WebhookAuditEvent.objects.count() == 2

    update_event = WebhookAuditEvent.objects.get(action=WebhookAuditEvent.ACTION_WEBHOOK_UPDATE)
    disable_event = WebhookAuditEvent.objects.get(action=WebhookAuditEvent.ACTION_WEBHOOK_DISABLE)

    assert update_event.actor == user
    assert update_event.webhook == webhook
    assert update_event.metadata["changes"] == {
        "name": {
            "before": "Inventory Alerts",
            "after": "Updated Inventory Alerts",
        }
    }
    assert "is_active" not in update_event.metadata["changes"]
    assert disable_event.actor == user
    assert disable_event.webhook == webhook


def test_webhook_delete_records_audit_before_delete(authenticated_client):
    client, user = authenticated_client
    webhook = _create_webhook()

    response = client.delete(reverse("webhook-detail", kwargs={"pk": webhook.pk}))

    assert response.status_code == status.HTTP_204_NO_CONTENT
    event = WebhookAuditEvent.objects.get()
    assert event.action == WebhookAuditEvent.ACTION_WEBHOOK_DELETE
    assert event.actor == user
    assert event.metadata == {
        "name": "Inventory Alerts",
        "url": "https://example.com/webhooks/inventory",
        "event_type": WebHook.REORDER_REQUEST_CREATED,
    }
    event.refresh_from_db()
    assert event.webhook is None


def test_diff_audited_fields_excludes_secret():
    before = WebHook(
        name="Inventory Alerts",
        url="https://example.com/webhooks/inventory",
        event_type=WebHook.REORDER_REQUEST_CREATED,
        is_active=True,
        secret="old-secret",
    )
    after = WebHook(
        name="Inventory Alerts",
        url="https://example.com/webhooks/inventory",
        event_type=WebHook.REORDER_REQUEST_CREATED,
        is_active=True,
        secret="new-secret",
    )

    assert diff_audited_fields(before, after) == {}


def test_record_event_with_anonymous_actor_creates_row_with_null_actor():
    webhook = _create_webhook()

    event = record_event(
        action=WebhookAuditEvent.ACTION_WEBHOOK_CREATE,
        webhook=webhook,
        actor=None,
    )

    assert event.actor is None
    assert event.webhook == webhook
    assert event.action == WebhookAuditEvent.ACTION_WEBHOOK_CREATE
