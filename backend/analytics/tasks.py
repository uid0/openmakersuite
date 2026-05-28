"""Celery tasks for the analytics app.

The monthly pulse task is scheduled by Celery Beat to fire at 09:00 on the
1st of each month (see ``CELERY_BEAT_SCHEDULE`` in ``config.settings``).
The same body powers the ``send_monthly_pulse`` management command for
ad-hoc / dry-run use.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from email.mime.image import MIMEImage
from typing import Iterable

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.utils import DataError, OperationalError, ProgrammingError
from django.template.loader import render_to_string

import sentry_sdk
from celery import shared_task

from .services.aggregation import category_spend, top_users, value_summary, wo_volume
from .services.email_charts import render_category_spend_png, render_volume_trend_png
from .services.forecast import maintenance_forecast
from .services.recipients import resolve_pulse_recipients

logger = logging.getLogger(__name__)

DEFAULT_TREND_MONTHS = 12

DUE_REASON_LABELS = {
    "both": "Hours + days",
    "days": "Days",
    "hours": "Hours",
}


def previous_month_window(today: date | None = None) -> tuple[date, date]:
    """Return ``(start, end)`` for the calendar month *before* ``today``.

    End is exclusive; start is the first of that month.
    """
    today = today or date.today()
    end = today.replace(day=1)  # exclusive bound: first of current month
    start = (end - timedelta(days=1)).replace(day=1)
    return start, end


def trend_window_ending(end: date, months: int = DEFAULT_TREND_MONTHS) -> tuple[date, date]:
    start = end
    for _ in range(months):
        start = (start - timedelta(days=1)).replace(day=1)
    return start, end


def _fmt_money(raw: str) -> str:
    try:
        n = Decimal(str(raw))
    except (InvalidOperation, TypeError):
        return str(raw)
    return f"${n:,.2f}"


def _enriched_summary(summary: dict) -> dict:
    """Add ``*_fmt`` USD-formatted strings on top of the raw summary."""
    enriched = dict(summary)
    for key in (
        "internal_estimated_external_cost",
        "internal_estimated_internal_cost",
        "internal_net_value",
        "external_actual_cost",
        "external_estimated_cost",
        "total_value_to_makerspace",
    ):
        enriched[f"{key}_fmt"] = _fmt_money(summary[key])
    return enriched


def _enriched_forecast(forecast_entries: Iterable[dict]) -> list[dict]:
    return [
        {
            **entry,
            "due_reason_label": DUE_REASON_LABELS.get(entry["due_reason"], entry["due_reason"]),
        }
        for entry in forecast_entries
    ]


def _build_payload(period_start: date, period_end: date) -> dict:
    """Pull the same aggregates the live dashboard uses, for one window."""
    trend_start, trend_end = trend_window_ending(period_end)
    return {
        "summary": value_summary(period_start, period_end),
        "trend": wo_volume(trend_start, trend_end, bucket="month"),
        "top_users": top_users(period_start, period_end, limit=5),
        "category_spend": category_spend(period_start, period_end),
        "forecast": maintenance_forecast(),
    }


def _period_label(start: date) -> str:
    return start.strftime("%B %Y")


def _build_email(
    *,
    recipients: list[str],
    period_start: date,
    period_end: date,
    payload: dict,
) -> EmailMultiAlternatives:
    context = {
        "period_label": _period_label(period_start),
        "period_start": period_start,
        "period_end": period_end,
        "summary": _enriched_summary(payload["summary"]),
        "top_users": payload["top_users"],
        "forecast": _enriched_forecast(payload["forecast"]),
        "dashboard_url": getattr(settings, "ANALYTICS_DASHBOARD_URL", ""),
    }
    subject = f"Makerspace pulse — {context['period_label']}"
    text_body = render_to_string("emails/monthly_pulse.txt", context)
    html_body = render_to_string("emails/monthly_pulse.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=recipients,
    )
    message.attach_alternative(html_body, "text/html")
    # Django 6 removed EmailMessage.mixed_subtype. The CIDs below still resolve
    # in every email client we've tested (Postmark renders Gmail / Outlook /
    # Apple Mail correctly) because each attached part carries its own
    # Content-ID + Content-Disposition: inline header — the outer multipart
    # subtype is only the strict-correctness signal, not what most clients
    # rely on.

    for cid, png_bytes in (
        ("volume_trend.png", render_volume_trend_png(payload["trend"])),
        ("category_spend.png", render_category_spend_png(payload["category_spend"])),
    ):
        img = MIMEImage(png_bytes, _subtype="png")
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=cid)
        message.attach(img)

    return message


def send_monthly_pulse(
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    dry_run: bool = False,
) -> dict:
    """Render and (optionally) send the monthly pulse email.

    Returns a dict with ``recipients``, ``period_start``, ``period_end``,
    ``sent`` (bool), and ``reason`` when not sent. Used directly by the
    management command and indirectly by the Celery task.
    """
    if (period_start is None) != (period_end is None):
        raise ValueError("Provide both period_start and period_end, or neither")
    if period_start is None or period_end is None:
        period_start, period_end = previous_month_window()

    recipients = resolve_pulse_recipients()
    if not recipients:
        logger.warning(
            "Monthly pulse not sent for %s..%s — no recipients configured",
            period_start,
            period_end,
        )
        return {
            "recipients": [],
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "sent": False,
            "reason": "no recipients configured",
        }

    payload = _build_payload(period_start, period_end)
    message = _build_email(
        recipients=recipients,
        period_start=period_start,
        period_end=period_end,
        payload=payload,
    )

    if dry_run:
        logger.info(
            "Dry-run: would send monthly pulse for %s..%s to %d recipient(s)",
            period_start,
            period_end,
            len(recipients),
        )
        return {
            "recipients": recipients,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "sent": False,
            "reason": "dry-run",
        }

    sent_count = message.send(fail_silently=False)
    logger.info(
        "Sent monthly pulse for %s..%s to %d recipient(s)",
        period_start,
        period_end,
        sent_count,
    )
    return {
        "recipients": recipients,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "sent": True,
        "reason": None,
    }


@shared_task(bind=True, ignore_result=True, name="analytics.send_monthly_pulse_email")
@sentry_sdk.crons.monitor(
    monitor_slug="analytics-monthly-pulse-email",
    monitor_config={
        # Fires at 09:00 America/Chicago on the 1st of each month. Failure
        # threshold is 1 — board + staff expect this every month, so a
        # missed run should page immediately.
        "schedule": {"type": "crontab", "value": "0 9 1 * *"},
        "timezone": "America/Chicago",
        "checkin_margin": 60,
        "max_runtime": 10,
        "failure_issue_threshold": 1,
        "recovery_threshold": 1,
    },
)
def send_monthly_pulse_email(self):
    """Celery entry-point fired by Beat at 09:00 on the 1st of each month."""
    try:
        return send_monthly_pulse()
    except Exception:
        logger.exception("send_monthly_pulse_email failed")
        # Re-raise so the Celery result backend records the failure.
        raise


# Names emitted by ``emit_metric_snapshot``. Listed here so the test
# suite can assert the canonical set and so a future operator searching
# Sentry Logs has a single grep point. Each value is the literal log
# message; the actual count rides as the ``value`` attribute.
METRIC_SNAPSHOT_NAMES: tuple[str, ...] = (
    "oms.metric.user.total",
    "oms.metric.user.staff",
    "oms.metric.membership.active",
    "oms.metric.inventory.item.total",
    "oms.metric.inventory.asset.total",
    "oms.metric.inventory.location.total",
    "oms.metric.forgekey.device.total",
    "oms.metric.forgekey.device.online",
    "oms.metric.checkin.location.last_24h",
    "oms.metric.checkin.occupancy_event.last_24h",
)


def _safe_count(name: str, fn) -> int:
    """Run ``fn()`` (typically a ``.count()`` queryset call) and return its
    integer result, or 0 if the query raises a database error.

    The snapshot task is best-effort observability — if a gauge can't be
    computed (e.g. the underlying table doesn't exist in this environment
    yet, BACKEND-8) we still want every other gauge to ship rather than
    losing the whole tick. The error is logged so it surfaces in Sentry
    Logs without paging.
    """
    try:
        return int(fn())
    except (ProgrammingError, OperationalError, DataError) as exc:
        logger.warning("metric snapshot: %s failed (%s); reporting 0", name, exc)
        return 0


def _collect_metric_snapshot() -> dict[str, int]:
    """Query the current value of every gauge in ``METRIC_SNAPSHOT_NAMES``.

    Split out so the periodic task can be tested without driving Celery.
    Heavy imports are local so module import stays fast and circular
    imports between ``analytics`` and the app models stay impossible.
    """
    from datetime import timedelta

    from django.contrib.auth import get_user_model
    from django.utils import timezone as dj_tz

    from forgekey.models import ESP32Device, OccupancyEvent
    from inventory.models import Asset, InventoryItem, Location
    from location_checkins.models import LocationCheckIn
    from membership.models import Membership

    User = get_user_model()
    now = dj_tz.now()
    last_24h = now - timedelta(hours=24)

    return {
        "oms.metric.user.total": _safe_count("user.total", User.objects.count),
        "oms.metric.user.staff": _safe_count(
            "user.staff", lambda: User.objects.filter(is_staff=True).count()
        ),
        "oms.metric.membership.active": _safe_count(
            "membership.active",
            lambda: Membership.objects.filter(status=Membership.STATUS_ACTIVE).count(),
        ),
        "oms.metric.inventory.item.total": _safe_count(
            "inventory.item.total", InventoryItem.objects.count
        ),
        "oms.metric.inventory.asset.total": _safe_count(
            "inventory.asset.total", Asset.objects.count
        ),
        "oms.metric.inventory.location.total": _safe_count(
            "inventory.location.total", Location.objects.count
        ),
        "oms.metric.forgekey.device.total": _safe_count(
            "forgekey.device.total", ESP32Device.objects.count
        ),
        "oms.metric.forgekey.device.online": _safe_count(
            "forgekey.device.online",
            lambda: ESP32Device.objects.filter(is_online=True).count(),
        ),
        "oms.metric.checkin.location.last_24h": _safe_count(
            "checkin.location.last_24h",
            lambda: LocationCheckIn.objects.filter(checked_in_at__gte=last_24h).count(),
        ),
        "oms.metric.checkin.occupancy_event.last_24h": _safe_count(
            "checkin.occupancy_event.last_24h",
            lambda: OccupancyEvent.objects.filter(event_timestamp_utc__gte=last_24h).count(),
        ),
    }


def _emit_metric_snapshot_to_sentry(snapshot: dict[str, int]) -> None:
    """Push the supplied gauge values to Sentry Logs.

    Split out from the task body so tests can drive this directly with
    a known input dict — driving the Celery task wrapper from a test
    would fight the bind=True / crons.monitor machinery for no benefit.
    """
    sentry_logger = sentry_sdk.logger
    for name, value in snapshot.items():
        # Each message stays a constant string so the Sentry Logs UI can
        # group on it; the value rides in the structured attribute.
        sentry_logger.info(
            name,
            attributes={
                "value": value,
                "metric.name": name,
                "metric.kind": "gauge",
            },
        )


@shared_task(bind=True, ignore_result=True, name="analytics.emit_metric_snapshot")
@sentry_sdk.crons.monitor(
    monitor_slug="analytics-emit-metric-snapshot",
    monitor_config={
        # Beat schedule fires every 5 minutes; a 1-minute checkin margin
        # absorbs the occasional slow Postgres count without paging.
        "schedule": {"type": "interval", "value": 5, "unit": "minute"},
        "timezone": "UTC",
        "checkin_margin": 1,
        "max_runtime": 2,
        "failure_issue_threshold": 3,
        "recovery_threshold": 1,
    },
)
def emit_metric_snapshot(self):
    """Celery beat task: emit a snapshot of every gauge to Sentry Logs.

    Sentry deprecated its experimental Metrics SDK in 2.x; the supported
    replacement for periodic numeric snapshots is the Logs beta with a
    numeric ``value`` attribute. Failures are logged and re-raised so
    the cron monitor reflects them — silent count drift would defeat the
    purpose of the snapshot.
    """
    try:
        snapshot = _collect_metric_snapshot()
    except Exception:
        logger.exception("emit_metric_snapshot: failed to collect counts")
        raise
    _emit_metric_snapshot_to_sentry(snapshot)
    return snapshot
