"""
Views for location check-in API.
"""

from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

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
            return Response(
                {"error": "location_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            location = Location.objects.get(id=location_id, is_active=True)
        except Location.DoesNotExist:
            return Response({"error": "Location not found"}, status=status.HTTP_404_NOT_FOUND)

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
            return Response(
                {"error": "location_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        feedback_type = request.data.get("feedback_type")
        if feedback_type not in ["positive", "neutral", "negative"]:
            return Response(
                {"error": "feedback_type must be positive, neutral, or negative"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        message = request.data.get("message", "")
        if not message:
            return Response({"error": "message is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            location = Location.objects.get(id=location_id, is_active=True)
        except Location.DoesNotExist:
            return Response({"error": "Location not found"}, status=status.HTTP_404_NOT_FOUND)

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
            return Response(
                {"error": "location_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )

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
            return Response({"error": "Location not found"}, status=status.HTTP_404_NOT_FOUND)

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
