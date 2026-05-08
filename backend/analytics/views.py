"""Read-only analytics endpoint.

``/api/analytics/pulse/`` returns the full aggregate JSON used by both
the dashboard (PR2) and the monthly email (PR3). Cached 60 minutes in
django-redis to bound DB load when the dashboard is viewed concurrently.

Query parameters:
- ``start`` and ``end`` (ISO date) — summary window. Defaults to the last
  full calendar month.
- ``bucket`` — ``"month"`` (default) or ``"quarter"`` for the trend series.

The 12-month trend series is independent of the summary window so the
chart is stable as users zoom around.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.core.cache import cache
from django.utils.dateparse import parse_date

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions import IsAnalyticsViewer
from .services.aggregation import category_spend, top_users, utilization, value_summary, wo_volume
from .services.forecast import maintenance_forecast

CACHE_TTL_SECONDS = 60 * 60
CACHE_VERSION = "v1"


def _last_full_month() -> tuple[date, date]:
    today = date.today()
    end = today.replace(day=1)  # exclusive
    start = (end - timedelta(days=1)).replace(day=1)
    return start, end


def _last_12_months_window() -> tuple[date, date]:
    today = date.today()
    end = today.replace(day=1)
    start = end
    for _ in range(12):
        start = (start - timedelta(days=1)).replace(day=1)
    return start, end


class AnalyticsPulseView(APIView):
    """``GET /api/analytics/pulse/?start=YYYY-MM-DD&end=YYYY-MM-DD&bucket=month``"""

    permission_classes = [IsAnalyticsViewer]

    def get(self, request):
        bucket = request.query_params.get("bucket", "month")
        if bucket not in ("month", "quarter"):
            return Response(
                {"detail": "bucket must be 'month' or 'quarter'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        start_str = request.query_params.get("start")
        end_str = request.query_params.get("end")
        if start_str or end_str:
            if not (start_str and end_str):
                return Response(
                    {"detail": "Both start and end are required when overriding window"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            start_date = parse_date(start_str)
            end_date = parse_date(end_str)
            if start_date is None or end_date is None:
                return Response(
                    {"detail": "Invalid start/end. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if start_date >= end_date:
                return Response(
                    {"detail": "start must be before end"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            start_date, end_date = _last_full_month()

        trend_start, trend_end = _last_12_months_window()

        cache_key = (
            f"analytics:pulse:{CACHE_VERSION}:"
            f"{start_date.isoformat()}:{end_date.isoformat()}:{bucket}"
        )
        payload = cache.get(cache_key)
        if payload is None:
            payload = {
                "summary": value_summary(start_date, end_date),
                "wo_volume_trend": wo_volume(trend_start, trend_end, bucket=bucket),
                "top_users": top_users(start_date, end_date, limit=10),
                "utilization": utilization(start_date, end_date),
                "category_spend": category_spend(start_date, end_date),
                "maintenance_forecast": maintenance_forecast(),
            }
            cache.set(cache_key, payload, CACHE_TTL_SECONDS)
        return Response(payload)
