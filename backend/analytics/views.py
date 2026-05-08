"""Analytics endpoints.

- ``GET /api/analytics/pulse/`` — full aggregate JSON used by the dashboard
  (PR2) and the monthly email (PR3). Cached 60 minutes in django-redis to
  bound DB load when the dashboard is viewed concurrently.
- ``POST /api/analytics/share/`` — staff-only mint of a signed-URL token
  that lets board members without OMS accounts view the dashboard.

Query parameters on pulse:
- ``start`` and ``end`` (ISO date) — summary window. Defaults to the last
  full calendar month.
- ``bucket`` — ``"month"`` (default) or ``"quarter"`` for the trend series.
- ``token`` — optional signed share token (alternative to authenticated
  access; see ``analytics.permissions.IsAnalyticsViewer``).

The 12-month trend series is independent of the summary window so the
chart is stable as users zoom around.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.utils.dateparse import parse_date

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions import IsAnalyticsSharer, IsAnalyticsViewer
from .services.aggregation import category_spend, top_users, utilization, value_summary, wo_volume
from .services.forecast import maintenance_forecast
from .services.share_token import DEFAULT_TTL_DAYS, MAX_TTL_DAYS, mint_share_token

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


def _monthly_budget() -> str | None:
    """Return ``MONTHLY_BUDGET_TOTAL`` as a string Decimal, or None if unset.

    Frontend hides the budget gauge when this is null. We serialize as a
    string for parity with the other money fields in the summary.
    """
    raw = getattr(settings, "MONTHLY_BUDGET_TOTAL", None)
    if raw in (None, ""):
        return None
    try:
        return str(Decimal(str(raw)))
    except InvalidOperation:
        return None


class AnalyticsPulseView(APIView):
    """``GET /api/analytics/pulse/?start=...&end=...&bucket=month&token=...``"""

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

        # Budget is read fresh on every request so config changes take effect
        # without waiting for the cache to expire.
        payload = {**payload, "monthly_budget": _monthly_budget()}
        return Response(payload)


class AnalyticsShareView(APIView):
    """``POST /api/analytics/share/`` — mint a signed-URL token.

    Body: ``{"ttl_days": <1..365>}`` (optional, defaults to 30).
    Response: ``{"token": "...", "ttl_days": N, "expires_in_days": N}``.

    The frontend composes the full shareable URL from the token. We
    deliberately do not include the URL on the server because the public
    base URL varies per deployment.
    """

    permission_classes = [IsAnalyticsSharer]

    def post(self, request):
        ttl_raw = request.data.get("ttl_days", DEFAULT_TTL_DAYS)
        try:
            ttl_days = int(ttl_raw)
        except (TypeError, ValueError):
            return Response(
                {"detail": "ttl_days must be an integer"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if ttl_days <= 0 or ttl_days > MAX_TTL_DAYS:
            return Response(
                {"detail": f"ttl_days must be between 1 and {MAX_TTL_DAYS}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token = mint_share_token(ttl_days=ttl_days)
        return Response(
            {"token": token, "ttl_days": ttl_days, "expires_in_days": ttl_days},
            status=status.HTTP_201_CREATED,
        )
