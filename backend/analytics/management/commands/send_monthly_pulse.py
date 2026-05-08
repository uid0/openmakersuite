"""``manage.py send_monthly_pulse`` — fire (or dry-run) the monthly pulse email.

The Celery Beat schedule auto-runs ``send_monthly_pulse_email`` at 09:00 on
the 1st of each month over the prior calendar month. This command lets ops
staff run the same body manually for testing, backfill, or to verify
recipient resolution after changing ``BOARD_REPORT_EMAILS`` /
``analytics-recipients`` group membership.

Examples:
    python manage.py send_monthly_pulse --dry-run
    python manage.py send_monthly_pulse --month=2026-04
    python manage.py send_monthly_pulse --month=2026-04 --dry-run
"""

from __future__ import annotations

import argparse
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from analytics.tasks import send_monthly_pulse


def _month_arg(value: str) -> tuple[date, date]:
    try:
        year_str, month_str = value.split("-", 1)
        year = int(year_str)
        month = int(month_str)
    except (ValueError, AttributeError) as exc:
        raise argparse.ArgumentTypeError(f"--month must be YYYY-MM, got {value!r}") from exc
    if not (1 <= month <= 12):
        raise argparse.ArgumentTypeError(f"--month month must be 1-12, got {month}")
    start = date(year, month, 1)
    # End is exclusive: first of the following month.
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


class Command(BaseCommand):
    help = "Send the monthly analytics pulse email (or preview with --dry-run)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--month",
            type=_month_arg,
            default=None,
            help="Override the reporting window in YYYY-MM. Defaults to the prior calendar month.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Resolve recipients + render the email but do not send.",
        )

    def handle(self, *args, **options):
        period_start = period_end = None
        if options["month"]:
            period_start, period_end = options["month"]

        try:
            result = send_monthly_pulse(
                period_start=period_start,
                period_end=period_end,
                dry_run=options["dry_run"],
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        recipients = result["recipients"]
        if not recipients:
            self.stdout.write(
                self.style.WARNING(
                    f"No recipients resolved (BOARD_REPORT_EMAILS empty + analytics-recipients group empty). "
                    f"Window {result['period_start']}..{result['period_end']}."
                )
            )
            return

        if result["sent"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Sent monthly pulse for {result['period_start']}..{result['period_end']} "
                    f"to {len(recipients)} recipient(s)."
                )
            )
        else:
            self.stdout.write(
                self.style.NOTICE(
                    f"Dry-run: rendered monthly pulse for {result['period_start']}..{result['period_end']} "
                    f"for {len(recipients)} recipient(s) ({result['reason']})."
                )
            )
            for addr in recipients:
                self.stdout.write(f"  - {addr}")
