"""
Celery tasks for location check-in webhook notifications.
"""

from django.utils import timezone

from celery import shared_task

from reorder_queue.tasks import send_webhook_notification


@shared_task
def send_location_checkin_webhook(checkin_id: str):
    """
    Send webhook notification for location check-in.

    Args:
        checkin_id: UUID of the LocationCheckIn
    """
    from .models import LocationCheckIn

    try:
        checkin = LocationCheckIn.objects.select_related("location", "user").get(id=checkin_id)
    except LocationCheckIn.DoesNotExist:
        return {"success": False, "error": "Check-in not found"}

    user_str = checkin.user.username if checkin.user else "Anonymous"

    payload = {
        "event": "location_checkin",
        "timestamp": timezone.now().isoformat(),
        "data": {
            "checkin_id": str(checkin.id),
            "location_id": str(checkin.location.id),
            "location_name": checkin.location.name,
            "checkin_type": checkin.checkin_type,
            "user": user_str,
            "checked_in_at": checkin.checked_in_at.isoformat(),
            "notes": checkin.notes,
        },
    }

    return send_webhook_notification.delay("location_checkin", payload)


@shared_task
def send_location_feedback_webhook(feedback_id: str):
    """
    Send webhook notification for location feedback.

    Args:
        feedback_id: UUID of the LocationFeedback
    """
    from .models import LocationFeedback

    try:
        feedback = LocationFeedback.objects.select_related("location", "user").get(id=feedback_id)
    except LocationFeedback.DoesNotExist:
        return {"success": False, "error": "Feedback not found"}

    user_str = feedback.user.username if feedback.user else "Anonymous"

    payload = {
        "event": "location_feedback",
        "timestamp": timezone.now().isoformat(),
        "data": {
            "feedback_id": str(feedback.id),
            "location_id": str(feedback.location.id),
            "location_name": feedback.location.name,
            "feedback_type": feedback.feedback_type,
            "message": feedback.message,
            "user": user_str,
            "submitted_at": feedback.submitted_at.isoformat(),
        },
    }

    return send_webhook_notification.delay("location_feedback", payload)


@shared_task
def send_security_report_webhook(report_id: str):
    """
    Send webhook notification for security report.

    Args:
        report_id: UUID of the SecurityReport
    """
    from .models import SecurityReport

    try:
        report = SecurityReport.objects.select_related("location", "user").get(id=report_id)
    except SecurityReport.DoesNotExist:
        return {"success": False, "error": "Security report not found"}

    user_str = report.user.username if report.user else "Anonymous"
    report_type_display = dict(SecurityReport.REPORT_TYPE_CHOICES).get(
        report.report_type, report.report_type
    )

    payload = {
        "event": "security_report",
        "timestamp": timezone.now().isoformat(),
        "data": {
            "report_id": str(report.id),
            "location_id": str(report.location.id),
            "location_name": report.location.name,
            "report_type": report.report_type,
            "report_type_display": report_type_display,
            "is_urgent": report.is_urgent,
            "description": report.description,
            "user": user_str,
            "reported_at": report.reported_at.isoformat(),
        },
    }

    return send_webhook_notification.delay("security_report", payload)
