"""
Celery tasks for reorder queue operations.
"""

import hashlib
import hmac
import json
import logging
from typing import Any, Dict

from django.utils import timezone

import requests
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,  # Retry after 1 minute
    autoretry_for=(requests.exceptions.RequestException,),
)
def send_webhook_notification(
    self, event_type: str, payload: Dict[str, Any]
) -> Dict[str, Any]:  # noqa: C901
    """
    Send webhook notifications for a specific event type.

    This task finds all active webhooks for the given event type and sends
    HTTP POST requests with the payload. It handles retries automatically
    and records success/failure statistics.

    Args:
        event_type: Type of event (e.g., "reorder_request_created")
        payload: Data to send in the webhook (will be JSON-encoded)

    Returns:
        Dictionary with results for each webhook

    Example payload for reorder_request_created:
        {
            "event": "reorder_request_created",
            "timestamp": "2025-11-05T12:34:56Z",
            "data": {
                "id": 123,
                "item_name": "Paper Towels",
                "quantity": 10,
                "requested_by": "John Doe",
                "status": "pending",
                "url": "https://dms.example.com/admin/reorder_queue/reorderrequest/123/"
            }
        }
    """
    from .models import WebHook

    # Find all active webhooks for this event type
    webhooks = WebHook.objects.filter(event_type=event_type, is_active=True)

    if not webhooks.exists():
        logger.info(f"No active webhooks found for event type: {event_type}")
        return {"event_type": event_type, "webhooks_triggered": 0}

    results = {
        "event_type": event_type,
        "webhooks_triggered": webhooks.count(),
        "successful": [],
        "failed": [],
    }

    # Add event type and timestamp to payload if not present
    if "event" not in payload:
        payload["event"] = event_type
    if "timestamp" not in payload:
        payload["timestamp"] = timezone.now().isoformat()

    for webhook in webhooks:
        try:
            # Prepare request
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "DMS-Inventory-Webhook/1.0",
            }

            # Add custom headers from webhook configuration
            if webhook.headers:
                headers.update(webhook.headers)

            # Prepare payload
            json_payload = json.dumps(payload)

            # Add HMAC signature if secret is configured
            if webhook.secret:
                signature = hmac.new(
                    webhook.secret.encode("utf-8"),
                    json_payload.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                headers["X-Webhook-Signature"] = f"sha256={signature}"

            # Send webhook
            response = requests.post(
                webhook.url,
                data=json_payload,
                headers=headers,
                timeout=30,  # 30 second timeout
            )

            # Check response
            response.raise_for_status()

            # Record success
            webhook.record_success()

            results["successful"].append(
                {
                    "webhook_id": webhook.id,
                    "webhook_name": webhook.name,
                    "status_code": response.status_code,
                }
            )

            logger.info(
                f"Webhook '{webhook.name}' delivered successfully "
                f"(status: {response.status_code})"
            )

        except requests.exceptions.RequestException as e:
            # Record failure
            error_message = str(e)
            webhook.record_failure(error_message)

            results["failed"].append(
                {
                    "webhook_id": webhook.id,
                    "webhook_name": webhook.name,
                    "error": error_message,
                }
            )

            logger.error(f"Webhook '{webhook.name}' delivery failed: {error_message}")

            # Raise exception to trigger Celery retry
            if self.request.retries < self.max_retries:
                raise

        except Exception as e:
            # Catch any other exceptions
            error_message = f"Unexpected error: {str(e)}"
            webhook.record_failure(error_message)

            results["failed"].append(
                {
                    "webhook_id": webhook.id,
                    "webhook_name": webhook.name,
                    "error": error_message,
                }
            )

            logger.exception(f"Unexpected error delivering webhook '{webhook.name}': {e}")

    return results


@shared_task
def send_reorder_request_notification_email(request_id: int) -> Dict[str, Any]:
    """
    Celery task: send a new-reorder-request email notification.

    Looks up the ReorderRequest and delegates to notifications.send_reorder_request_notification.
    Swallows non-existence so a race condition (request deleted before the task
    runs) never raises.
    """
    from .models import ReorderRequest
    from .notifications import send_reorder_request_notification

    try:
        reorder_request = ReorderRequest.objects.select_related("item", "item__owning_group").get(
            id=request_id
        )
    except ReorderRequest.DoesNotExist:
        logger.warning(
            "send_reorder_request_notification_email: ReorderRequest %s not found", request_id
        )
        return {"sent": 0, "reason": "not-found"}

    sent = send_reorder_request_notification(reorder_request)
    return {"sent": sent or 0, "request_id": request_id}


@shared_task
def trigger_reorder_request_webhook(request_id: int) -> Dict[str, Any]:
    """
    Trigger webhook notification for a reorder request.

    This task is called when a reorder request is created or updated.
    It prepares the payload and triggers the webhook notification.

    Args:
        request_id: ID of the ReorderRequest

    Returns:
        Results from webhook delivery
    """
    from .models import ReorderRequest

    try:
        request = ReorderRequest.objects.select_related("item").get(id=request_id)
    except ReorderRequest.DoesNotExist:
        logger.error(f"ReorderRequest {request_id} not found")
        return {"error": "ReorderRequest not found"}

    # Prepare payload
    payload = {
        "event": "reorder_request_created",
        "timestamp": timezone.now().isoformat(),
        "data": {
            "id": request.id,
            "item_id": str(request.item.id),
            "item_name": request.item.name,
            "item_sku": request.item.sku,
            "quantity": request.quantity,
            "status": request.status,
            "priority": request.priority,
            "requested_by": request.requested_by,
            "requested_at": request.requested_at.isoformat(),
            "request_notes": request.request_notes,
            "estimated_cost": (float(request.estimated_cost) if request.estimated_cost else None),
            # Admin URL for viewing the request
            "admin_url": f"/admin/reorder_queue/reorderrequest/{request.id}/",
        },
    }

    # Trigger webhook - call directly instead of using .delay() to avoid nesting tasks
    # In production, this runs in a Celery worker, so it's still async from the web request
    from celery import current_app

    if current_app.conf.task_always_eager:
        # In eager mode (tests), call the function directly
        return send_webhook_notification.run("reorder_request_created", payload)
    else:
        # In production, queue the webhook task
        send_webhook_notification.delay("reorder_request_created", payload)
        return {"queued": True, "event_type": "reorder_request_created"}


@shared_task
def run_webhook_test(webhook_id: int) -> Dict[str, Any]:
    """
    Test a webhook configuration by sending a test payload.

    Args:
        webhook_id: ID of the WebHook to test

    Returns:
        Test result with status, including webhook metadata
    """
    import time

    from .models import WebHook

    start_time = time.time()

    try:
        webhook = WebHook.objects.get(id=webhook_id)
    except WebHook.DoesNotExist:
        return {
            "success": False,
            "error": "Webhook not found",
            "webhook_id": webhook_id,
            "tested_at": timezone.now().isoformat(),
        }

    # Prepare test payload
    test_payload = {
        "event": webhook.event_type,
        "test": True,
        "timestamp": timezone.now().isoformat(),
        "data": {
            "message": "This is a test webhook notification",
            "webhook_id": webhook.id,
            "webhook_name": webhook.name,
        },
    }

    try:
        # Prepare request
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "DMS-Inventory-Webhook/1.0",
        }

        # Add custom headers
        if webhook.headers:
            headers.update(webhook.headers)

        # Prepare payload
        json_payload = json.dumps(test_payload)

        # Add HMAC signature if configured
        if webhook.secret:
            signature = hmac.new(
                webhook.secret.encode("utf-8"),
                json_payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={signature}"

        # Send test webhook
        response = requests.post(webhook.url, data=json_payload, headers=headers, timeout=30)

        response_time_ms = (time.time() - start_time) * 1000
        response.raise_for_status()

        return {
            "success": True,
            "webhook_id": webhook.id,
            "webhook_name": webhook.name,
            "status_code": response.status_code,
            "response_time_ms": round(response_time_ms, 2),
            "response_body": response.text[:500],  # First 500 chars of response
            "tested_at": timezone.now().isoformat(),
        }

    except requests.exceptions.RequestException as e:
        response_time_ms = (time.time() - start_time) * 1000
        return {
            "success": False,
            "webhook_id": webhook.id,
            "webhook_name": webhook.name,
            "error_message": str(e),
            "response_time_ms": round(response_time_ms, 2),
            "tested_at": timezone.now().isoformat(),
        }
    except Exception as e:
        response_time_ms = (time.time() - start_time) * 1000
        return {
            "success": False,
            "webhook_id": webhook.id,
            "webhook_name": webhook.name,
            "error_message": f"Unexpected error: {str(e)}",
            "response_time_ms": round(response_time_ms, 2),
            "tested_at": timezone.now().isoformat(),
        }


@shared_task
def send_fixture_refill_webhook(refill_request_id: str) -> Dict[str, Any]:
    """
    Trigger webhook notification for a fixture refill request.

    This task is called when a fixture refill request is created.
    It prepares the payload and triggers the webhook notification.

    Args:
        refill_request_id: UUID of the FixtureRefillRequest

    Returns:
        Results from webhook delivery
    """
    from inventory.models import FixtureRefillRequest

    try:
        refill_request = FixtureRefillRequest.objects.select_related(
            "fixture", "fixture__location", "fixture__refill_item"
        ).get(id=refill_request_id)
    except FixtureRefillRequest.DoesNotExist:
        logger.error(f"FixtureRefillRequest {refill_request_id} not found")
        return {"error": "FixtureRefillRequest not found"}

    # Prepare payload
    payload = {
        "event": "fixture_refill_requested",
        "timestamp": timezone.now().isoformat(),
        "data": {
            "id": str(refill_request.id),
            "fixture_id": str(refill_request.fixture.id),
            "fixture_name": refill_request.fixture.name,
            "fixture_location": refill_request.fixture.location.name,
            "refill_item_id": str(refill_request.fixture.refill_item.id),
            "refill_item_name": refill_request.fixture.refill_item.name,
            "refill_item_sku": refill_request.fixture.refill_item.sku,
            "current_stock": refill_request.fixture.refill_item.current_stock,
            "status": refill_request.status,
            "requested_by": refill_request.requested_by,
            "requested_at": refill_request.requested_at.isoformat(),
            # Admin URL for viewing the request
            "admin_url": f"/admin/inventory/fixturerefillrequest/{refill_request.id}/",
        },
    }

    # Trigger webhook - call directly instead of using .delay() to avoid nesting tasks
    from celery import current_app

    if current_app.conf.task_always_eager:
        # In eager mode (tests), call the function directly
        return send_webhook_notification.run("fixture_refill_requested", payload)
    else:
        # In production, queue the webhook task
        send_webhook_notification.delay("fixture_refill_requested", payload)
        return {"queued": True, "event_type": "fixture_refill_requested"}


@shared_task
def send_asset_problem_webhook(problem_id: str) -> Dict[str, Any]:
    """
    Trigger webhook notification for an asset problem report.

    This task is called when an asset problem is reported.
    It prepares the payload and triggers the webhook notification.

    Args:
        problem_id: UUID of the AssetProblem

    Returns:
        Results from webhook delivery
    """
    from inventory.models import AssetProblem

    try:
        problem = AssetProblem.objects.select_related("asset", "asset__location").get(id=problem_id)
    except AssetProblem.DoesNotExist:
        logger.error(f"AssetProblem {problem_id} not found")
        return {"error": "AssetProblem not found"}

    # Prepare payload
    payload = {
        "event": "asset_problem_reported",
        "timestamp": timezone.now().isoformat(),
        "data": {
            "id": str(problem.id),
            "asset_id": str(problem.asset.id),
            "asset_name": problem.asset.name,
            "asset_tag": problem.asset.asset_tag,
            "asset_location": (problem.asset.location.name if problem.asset.location else None),
            "status": problem.status,
            "reported_by": problem.reported_by,
            "description": problem.description,
            "created_at": problem.created_at.isoformat(),
            # Admin URL for viewing the problem
            "admin_url": f"/admin/inventory/assetproblem/{problem.id}/",
        },
    }

    # Trigger webhook - call directly instead of using .delay() to avoid nesting tasks
    from celery import current_app

    if current_app.conf.task_always_eager:
        # In eager mode (tests), call the function directly
        return send_webhook_notification.run("asset_problem_reported", payload)
    else:
        # In production, queue the webhook task
        send_webhook_notification.delay("asset_problem_reported", payload)
        return {"queued": True, "event_type": "asset_problem_reported"}


@shared_task
def send_location_problem_webhook(problem_id: str) -> Dict[str, Any]:
    """Trigger the ``location_problem_reported`` webhook for a LocationProblem.

    Mirrors :func:`send_asset_problem_webhook`. Called from the Location
    ``report_problem`` action so subscribers (logistics, facilities tools)
    can react to new reports — including urgent leak / lighting / HVAC
    issues raised through the new oms-0yz flow.
    """
    from inventory.models import LocationProblem

    try:
        problem = LocationProblem.objects.select_related("location").get(id=problem_id)
    except LocationProblem.DoesNotExist:
        logger.error(f"LocationProblem {problem_id} not found")
        return {"error": "LocationProblem not found"}

    payload = {
        "event": "location_problem_reported",
        "timestamp": timezone.now().isoformat(),
        "data": {
            "id": str(problem.id),
            "location_id": problem.location_id,
            "location_name": problem.location.name,
            "status": problem.status,
            "severity": problem.severity,
            "reported_by": problem.reported_by,
            "description": problem.description,
            "reported_at": problem.reported_at.isoformat(),
            "admin_url": f"/admin/inventory/locationproblem/{problem.id}/",
            "frontend_url": f"/maintenance/location-problems/{problem.id}",
        },
    }

    from celery import current_app

    if current_app.conf.task_always_eager:
        return send_webhook_notification.run("location_problem_reported", payload)
    else:
        send_webhook_notification.delay("location_problem_reported", payload)
        return {"queued": True, "event_type": "location_problem_reported"}
