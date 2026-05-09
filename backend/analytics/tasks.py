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
from django.template.loader import render_to_string

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
    message.mixed_subtype = "related"  # so cid: refs work in HTML

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
def send_monthly_pulse_email(self):
    """Celery entry-point fired by Beat at 09:00 on the 1st of each month."""
    try:
        return send_monthly_pulse()
    except Exception:
        logger.exception("send_monthly_pulse_email failed")
        # Re-raise so the Celery result backend records the failure.
        raise
