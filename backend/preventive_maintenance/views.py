"""Views for the preventive-maintenance app.

ViewSets handle CRUD on schedules and service logs. The board endpoint
returns a flat list optimized for the Inkplate / dashboard renderer:
each entry carries the precomputed days-since/days-until/status fields
so a low-power device doesn't have to do date math.
"""

from django.utils import timezone

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import PMSchedule, PMServiceLog
from .serializers import (
    PMBoardEntrySerializer,
    PMScheduleSerializer,
    PMServiceLogSerializer,
)


class PMSchedulePermissions(permissions.BasePermission):
    """Read for anyone (Inkplate hits the board); writes for staff only."""

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class PMScheduleViewSet(viewsets.ModelViewSet):
    queryset = PMSchedule.objects.select_related("asset", "asset__location").all()
    serializer_class = PMScheduleSerializer
    permission_classes = [PMSchedulePermissions]

    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
    def board(self, request):
        """Compact list every active schedule + its current status.

        AllowAny so the Inkplate firmware can pull on a schedule
        without baking a JWT into flash. The downstream POST endpoints
        (creating a service log when a member taps "I changed the
        filter") still require staff auth.
        """
        schedules = (
            PMSchedule.objects.filter(is_active=True)
            .select_related("asset", "asset__location")
            .prefetch_related("service_logs")
        )
        entries = []
        for s in schedules:
            last_log = s.last_log()
            entries.append(
                {
                    "id": s.id,
                    "asset_id": s.asset_id,
                    "asset_name": s.asset.name,
                    "location_name": s.asset.location.name if s.asset.location else None,
                    "task_name": s.task_name,
                    "interval_days": s.interval_days,
                    "last_performed_at": last_log.performed_at if last_log else None,
                    "days_since_last": s.days_since_last(),
                    "days_until_due": s.days_until_due(),
                    "status": s.status(),
                }
            )
        # Sort: overdue first, then warning, then ok, then never. Within
        # each tier, by days_until_due ascending (most urgent at top).
        status_order = {"overdue": 0, "warning": 1, "ok": 2, "never": 3}
        entries.sort(
            key=lambda e: (
                status_order.get(e["status"], 99),
                e["days_until_due"] if e["days_until_due"] is not None else 1_000_000,
            )
        )
        serializer = PMBoardEntrySerializer(entries, many=True)
        return Response(
            {
                "generated_at": timezone.now(),
                "count": len(entries),
                "schedules": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def log_service(self, request, pk=None):
        """Convenience endpoint: 'just performed this task now'.

        Posts a PMServiceLog with `performed_at=now`, `performed_by=
        request.user`. Frontend buttons (a member tapping "I changed
        the filter" on the asset detail page) use this rather than
        building the full POST themselves.
        """
        schedule = self.get_object()
        log = PMServiceLog.objects.create(
            schedule=schedule,
            performed_at=timezone.now(),
            performed_by=request.user if request.user.is_authenticated else None,
            notes=request.data.get("notes", ""),
        )
        serializer = PMServiceLogSerializer(log)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class PMServiceLogViewSet(viewsets.ModelViewSet):
    queryset = PMServiceLog.objects.select_related("schedule", "performed_by").all()
    serializer_class = PMServiceLogSerializer
    permission_classes = [PMSchedulePermissions]
