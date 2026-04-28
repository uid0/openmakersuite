"""Celery tasks for vendor compliance monitoring."""

import logging
from datetime import timedelta
from typing import Dict, List

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.mail import EmailMessage
from django.db.models import Q
from django.utils import timezone

from celery import shared_task

from .models import Vendor

logger = logging.getLogger(__name__)

EXPIRY_WINDOW_DAYS = 30
LOGISTICS_GROUP_NAME = "Logistics"


def _flag_expiring(window_days: int = EXPIRY_WINDOW_DAYS) -> Dict[str, List[Vendor]]:
    """Return vendors whose TDLR or COI is expired or expiring within ``window_days``.

    Returned dict has keys: tdlr_expired, tdlr_expiring, coi_expired, coi_expiring.
    """
    today = timezone.now().date()
    soon = today + timedelta(days=window_days)
    active = Vendor.objects.filter(is_active=True)
    return {
        "tdlr_expired": list(active.filter(tdlr_license_expires_at__lt=today)),
        "tdlr_expiring": list(
            active.filter(tdlr_license_expires_at__gte=today, tdlr_license_expires_at__lte=soon)
        ),
        "coi_expired": list(active.filter(coi_expires_at__lt=today)),
        "coi_expiring": list(active.filter(coi_expires_at__gte=today, coi_expires_at__lte=soon)),
    }


def _logistics_recipients() -> List[str]:
    """Email addresses for users in the Logistics group."""
    try:
        group = Group.objects.get(name=LOGISTICS_GROUP_NAME)
    except Group.DoesNotExist:
        logger.warning("Logistics group not found; vendor compliance digest not sent.")
        return []
    return list(
        group.user_set.filter(is_active=True)
        .exclude(Q(email__isnull=True) | Q(email__exact=""))
        .values_list("email", flat=True)
    )


def _format_digest(buckets: Dict[str, List[Vendor]]) -> str:
    lines = ["Vendor compliance digest", "========================", ""]

    def _section(title: str, vendors: List[Vendor], date_attr: str) -> None:
        lines.append(f"{title} ({len(vendors)})")
        lines.append("-" * len(title))
        if not vendors:
            lines.append("  (none)")
        else:
            for v in vendors:
                d = getattr(v, date_attr)
                lines.append(f"  - {v.name} [{v.get_vendor_kind_display()}] — expires {d}")
        lines.append("")

    _section("TDLR licenses expired", buckets["tdlr_expired"], "tdlr_license_expires_at")
    _section(
        f"TDLR licenses expiring within {EXPIRY_WINDOW_DAYS} days",
        buckets["tdlr_expiring"],
        "tdlr_license_expires_at",
    )
    _section("COI expired", buckets["coi_expired"], "coi_expires_at")
    _section(
        f"COI expiring within {EXPIRY_WINDOW_DAYS} days",
        buckets["coi_expiring"],
        "coi_expires_at",
    )
    return "\n".join(lines)


@shared_task(name="vendors.flag_expiring_compliance")
def flag_expiring_compliance() -> Dict[str, int]:
    """Email Logistics a digest of vendors with expiring/expired TDLR or COI.

    Idempotent: always runs to completion. If no vendors are flagged the task
    skips sending mail rather than emitting an empty digest. Counts are
    returned for observability.
    """
    buckets = _flag_expiring()
    counts = {k: len(v) for k, v in buckets.items()}
    total = sum(counts.values())
    if total == 0:
        logger.info("vendor compliance: nothing to flag")
        return counts

    recipients = _logistics_recipients()
    if not recipients:
        logger.warning("vendor compliance: %d flagged but no Logistics recipients", total)
        return counts

    body = _format_digest(buckets)
    EmailMessage(
        subject=f"[OMS] Vendor compliance: {total} flagged",
        body=body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=recipients,
    ).send(fail_silently=False)
    logger.info("vendor compliance: digest sent to %d recipients (%s)", len(recipients), counts)
    return counts
