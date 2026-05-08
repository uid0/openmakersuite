"""
Views for location check-in API.
"""

from datetime import datetime, timedelta

from django.conf import settings as django_settings
from django.db.models import Count
from django.db.models.functions import TruncDay, TruncHour, TruncMonth, TruncWeek
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import ErrorCode, error_response
from inventory.models import Location

from .models import LocationCheckIn, LocationFeedback, LocationTask, SecurityReport
from .serializers import (
    LocationCheckInSerializer,
    LocationFeedbackSerializer,
    LocationTaskCompleteSerializer,
    LocationTaskSerializer,
    SecurityReportSerializer,
)
from .tasks import (
    send_location_checkin_webhook,
    send_location_feedback_webhook,
    send_security_report_webhook,
)


class LocationCheckInViewSet(viewsets.ModelViewSet):
    """API endpoint for location check-ins."""

    queryset = LocationCheckIn.objects.select_related("location", "user").all()
    serializer_class = LocationCheckInSerializer

    def get_permissions(self):
        """Allow anyone to check in, require auth for viewing list."""
        if self.action in ["create", "checkin"]:
            return [AllowAny()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        """Set user if authenticated, otherwise leave as anonymous."""
        if self.request.user and self.request.user.is_authenticated:
            serializer.save(user=self.request.user)
        else:
            serializer.save()

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        url_path="checkin",
    )
    def checkin(self, request):
        """
        Public endpoint for checking into a location.
        Can be used by volunteers, contractors, or anonymous users.
        """
        location_id = request.data.get("location_id")
        if not location_id:
            return error_response(ErrorCode.VALIDATION_FAILED, "location_id is required")

        try:
            location = Location.objects.get(id=location_id, is_active=True)
        except Location.DoesNotExist:
            return error_response(
                ErrorCode.NOT_FOUND,
                "Location not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        checkin_type = request.data.get("checkin_type", "anonymous")
        if checkin_type not in ["volunteer", "contractor", "anonymous"]:
            checkin_type = "anonymous"

        # Determine checkin type based on authentication
        if request.user and request.user.is_authenticated:
            if checkin_type == "anonymous":
                # Authenticated users default to volunteer
                checkin_type = "volunteer"

        checkin = LocationCheckIn.objects.create(
            location=location,
            checkin_type=checkin_type,
            user=request.user if request.user.is_authenticated else None,
            notes=request.data.get("notes", ""),
        )

        # Send webhook notification
        send_location_checkin_webhook.delay(str(checkin.id))

        serializer = self.get_serializer(checkin)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class LocationFeedbackViewSet(viewsets.ModelViewSet):
    """API endpoint for location feedback."""

    queryset = LocationFeedback.objects.select_related("location", "user").all()
    serializer_class = LocationFeedbackSerializer

    def get_permissions(self):
        """Allow anyone to submit feedback, require auth for viewing/managing."""
        if self.action in ["create", "submit"]:
            return [AllowAny()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        """Set user if authenticated, otherwise leave as anonymous."""
        if self.request.user and self.request.user.is_authenticated:
            serializer.save(user=self.request.user)
        else:
            serializer.save()

    @action(detail=False, methods=["post"], permission_classes=[AllowAny], url_path="submit")
    def submit(self, request):
        """
        Public endpoint for submitting feedback about a location.
        Negative feedback automatically creates tasks.
        """
        location_id = request.data.get("location_id")
        if not location_id:
            return error_response(ErrorCode.VALIDATION_FAILED, "location_id is required")

        feedback_type = request.data.get("feedback_type")
        if feedback_type not in ["positive", "neutral", "negative"]:
            return Response(
                {"error": "feedback_type must be positive, neutral, or negative"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        message = request.data.get("message", "")
        if not message:
            return error_response(ErrorCode.VALIDATION_FAILED, "message is required")

        try:
            location = Location.objects.get(id=location_id, is_active=True)
        except Location.DoesNotExist:
            return error_response(
                ErrorCode.NOT_FOUND,
                "Location not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        feedback = LocationFeedback.objects.create(
            location=location,
            feedback_type=feedback_type,
            message=message,
            user=request.user if request.user.is_authenticated else None,
        )

        # Create task for negative feedback
        if feedback_type == "negative":
            LocationTask.objects.create(
                location=location,
                title=f"Address feedback: {location.name}",
                description=f"Negative feedback received:\n\n{message}",
                status="pending",
                created_from_feedback=feedback,
                created_by=None,  # System-created
            )

        # Send webhook notification
        send_location_feedback_webhook.delay(str(feedback.id))

        serializer = self.get_serializer(feedback)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SecurityReportViewSet(viewsets.ModelViewSet):
    """API endpoint for security reports."""

    queryset = SecurityReport.objects.select_related("location", "user", "resolved_by").all()
    serializer_class = SecurityReportSerializer

    def get_permissions(self):
        """Allow anyone to submit reports, require auth for viewing/managing."""
        if self.action in ["create", "report"]:
            return [AllowAny()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        """Set user if authenticated, otherwise leave as anonymous."""
        if self.request.user and self.request.user.is_authenticated:
            serializer.save(user=self.request.user)
        else:
            serializer.save()

    @action(detail=False, methods=["post"], permission_classes=[AllowAny], url_path="report")
    def report(self, request):
        """
        Public endpoint for reporting cleaning or safety concerns.
        Automatically creates tasks for all reports.
        """
        location_id = request.data.get("location_id")
        if not location_id:
            return error_response(ErrorCode.VALIDATION_FAILED, "location_id is required")

        report_type = request.data.get("report_type")
        if report_type not in ["cleaning", "safety"]:
            return Response(
                {"error": 'report_type must be "cleaning" or "safety"'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_urgent = request.data.get("is_urgent", False)
        description = request.data.get("description", "")

        try:
            location = Location.objects.get(id=location_id, is_active=True)
        except Location.DoesNotExist:
            return error_response(
                ErrorCode.NOT_FOUND,
                "Location not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        security_report = SecurityReport.objects.create(
            location=location,
            report_type=report_type,
            is_urgent=bool(is_urgent),
            description=description,
            user=request.user if request.user.is_authenticated else None,
        )

        # Create task for security report
        report_type_display = dict(SecurityReport.REPORT_TYPE_CHOICES).get(report_type, report_type)
        urgent_text = " [URGENT]" if is_urgent else ""
        task_description = f"{report_type_display} reported{urgent_text}"
        if description:
            task_description += f"\n\nAdditional details:\n{description}"

        LocationTask.objects.create(
            location=location,
            title=f"{report_type_display}{urgent_text}: {location.name}",
            description=task_description,
            status="pending",
            created_from_security_report=security_report,
            created_by=None,  # System-created
        )

        # Send webhook notification
        send_security_report_webhook.delay(str(security_report.id))

        # Create notifications for admins about security reports
        try:
            from notifications.services import notify_admins

            report_type_display = dict(SecurityReport.REPORT_TYPE_CHOICES).get(
                report_type, report_type
            )
            urgency_text = " (URGENT)" if is_urgent else ""
            notify_admins(
                type="warning" if is_urgent else "info",
                title=f"{report_type_display} Report{urgency_text}",
                message=f'{report_type_display} concern reported at {location.name}. {description[:100] if description else "No description provided."}',
                action_url=f"/inventory/scan/location/{location.id}",
                metadata={
                    "security_report_id": security_report.id,
                    "location_id": location.id,
                    "report_type": report_type,
                    "is_urgent": is_urgent,
                },
            )
        except Exception:  # nosec B110
            # Don't fail the request if notification creation fails
            pass

        serializer = self.get_serializer(security_report)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def resolve(self, request, pk=None):
        """Mark a security report as resolved."""
        report = self.get_object()
        report.is_resolved = True
        report.resolved_at = timezone.now()
        report.resolved_by = request.user
        report.save(update_fields=["is_resolved", "resolved_at", "resolved_by"])

        serializer = self.get_serializer(report)
        return Response(serializer.data)


class LocationTaskViewSet(viewsets.ModelViewSet):
    """API endpoint for location tasks."""

    queryset = LocationTask.objects.select_related(
        "location",
        "created_by",
        "assigned_to",
        "completed_by",
        "created_from_feedback",
        "created_from_security_report",
    ).all()
    serializer_class = LocationTaskSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter tasks based on user permissions."""
        queryset = self.queryset
        # Users can see all tasks, but we could filter by assigned_to if needed
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        location_filter = self.request.query_params.get("location")
        if location_filter:
            queryset = queryset.filter(location_id=location_filter)
        return queryset

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def complete(self, request, pk=None):
        """Mark a task as completed."""
        task = self.get_object()
        serializer = LocationTaskCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        task.mark_completed(request.user)

        # Update related feedback/security report if applicable
        if task.created_from_feedback:
            task.created_from_feedback.is_resolved = True
            task.created_from_feedback.save(update_fields=["is_resolved"])

        if task.created_from_security_report:
            task.created_from_security_report.is_resolved = True
            task.created_from_security_report.resolved_at = timezone.now()
            task.created_from_security_report.resolved_by = request.user
            task.created_from_security_report.save(
                update_fields=["is_resolved", "resolved_at", "resolved_by"]
            )

        serializer = self.get_serializer(task)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# IoT traffic-count webhook + reporting (oms-aib)
# ---------------------------------------------------------------------------

WEBHOOK_DEDUPE_SECONDS = 60


def _resolve_location(payload):
    """Resolve a Location from one of: location_id, access_code."""
    location_id = payload.get("location_id")
    if location_id is not None:
        try:
            return Location.objects.get(pk=location_id, is_active=True)
        except (Location.DoesNotExist, ValueError, TypeError):
            return None

    code = payload.get("access_code")
    if code:
        try:
            return Location.objects.get(access_code=code, is_active=True)
        except Location.DoesNotExist:
            return None
    return None


@api_view(["POST"])
@permission_classes([AllowAny])
def location_ping_webhook(request):
    """
    Anonymous IoT traffic-count ingest.

    Auth: shared secret in `LOCATION_PING_TOKEN` (header `X-Location-Ping-Token`
    or `?token=` query). Body: {location_id|access_code, device_id?, occurred_at?,
    event_id?}. Returns 204 on success or silent dedupe within 60s.
    """
    expected = getattr(django_settings, "LOCATION_PING_TOKEN", "") or ""
    if not expected:
        return Response(
            {"detail": "Location ping endpoint is not configured."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    provided = request.headers.get("X-Location-Ping-Token") or request.GET.get("token") or ""
    if provided != expected:
        return error_response(
            ErrorCode.NOT_AUTHENTICATED,
            "Unauthorized.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    payload = request.data if isinstance(request.data, dict) else {}
    location = _resolve_location(payload)
    if location is None:
        return Response(
            {"detail": "Unknown or inactive location."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    occurred_at_raw = payload.get("occurred_at")
    occurred_at = None
    if occurred_at_raw:
        parsed = parse_datetime(occurred_at_raw)
        if parsed is not None:
            occurred_at = parsed if parsed.tzinfo else timezone.make_aware(parsed)
    if occurred_at is None:
        occurred_at = timezone.now()

    device_id = (payload.get("device_id") or "")[:100]
    event_id = (payload.get("event_id") or "")[:100]

    if event_id:
        cutoff = timezone.now() - timedelta(seconds=WEBHOOK_DEDUPE_SECONDS)
        existing = LocationCheckIn.objects.filter(
            event_id=event_id,
            location=location,
            checked_in_at__gte=cutoff,
        ).first()
        if existing is not None:
            return Response(status=status.HTTP_204_NO_CONTENT)

    LocationCheckIn.objects.create(
        location=location,
        checkin_type="anonymous",
        user=None,
        notes="",
        device_id=device_id,
        source="device",
        event_id=event_id,
        checked_in_at=occurred_at,
    )

    return Response(status=status.HTTP_204_NO_CONTENT)


_BUCKET_TRUNC = {
    "hour": TruncHour,
    "day": TruncDay,
    "week": TruncWeek,
    "month": TruncMonth,
}


def _parse_window(request, default_days=7):
    """Parse ?start=&end= as ISO datetimes (or dates). Defaults to last `default_days`."""
    end_raw = request.query_params.get("end")
    start_raw = request.query_params.get("start")
    end = parse_datetime(end_raw) if end_raw else None
    start = parse_datetime(start_raw) if start_raw else None
    # Allow plain dates (YYYY-MM-DD) by appending T00:00:00.
    if end is None and end_raw:
        end = parse_datetime(f"{end_raw}T23:59:59")
    if start is None and start_raw:
        start = parse_datetime(f"{start_raw}T00:00:00")
    if end is None:
        end = timezone.now()
    if start is None:
        start = end - timedelta(days=default_days)
    if end.tzinfo is None:
        end = timezone.make_aware(end)
    if start.tzinfo is None:
        start = timezone.make_aware(start)
    return start, end


class TrafficReportView(APIView):
    """
    GET /api/location-checkins/reports/traffic/
        ?location=<id>&start=<date>&end=<date>&bucket=hour|day|week|month

    Returns time-bucketed check-in counts. By default scoped to a single
    location; omit `location` to aggregate across all active locations.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        bucket = request.query_params.get("bucket", "day")
        trunc = _BUCKET_TRUNC.get(bucket)
        if trunc is None:
            return Response(
                {"detail": f"bucket must be one of {sorted(_BUCKET_TRUNC)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        start, end = _parse_window(request)

        qs = LocationCheckIn.objects.filter(checked_in_at__gte=start, checked_in_at__lte=end)
        location_id = request.query_params.get("location")
        if location_id:
            qs = qs.filter(location_id=location_id)

        rows = (
            qs.annotate(bucket_start=trunc("checked_in_at"))
            .values("bucket_start")
            .annotate(count=Count("id"))
            .order_by("bucket_start")
        )

        return Response(
            {
                "bucket": bucket,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "results": [
                    {
                        "bucket_start": (
                            r["bucket_start"].isoformat()
                            if isinstance(r["bucket_start"], datetime)
                            else r["bucket_start"]
                        ),
                        "count": r["count"],
                    }
                    for r in rows
                ],
            }
        )


class TopLocationsReportView(APIView):
    """
    GET /api/location-checkins/reports/top/?start=<date>&end=<date>&limit=10

    Returns locations ordered by check-in count for the window.
    Tie-breaker: location name ascending (deterministic).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 100))

        start, end = _parse_window(request)

        rows = (
            LocationCheckIn.objects.filter(checked_in_at__gte=start, checked_in_at__lte=end)
            .values("location_id", "location__name")
            .annotate(count=Count("id"))
            .order_by("-count", "location__name")[:limit]
        )

        return Response(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "results": [
                    {
                        "location_id": r["location_id"],
                        "location_name": r["location__name"],
                        "count": r["count"],
                    }
                    for r in rows
                ],
            }
        )
