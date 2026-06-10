"""Manual + smoke-check runner for the storage_vision retention task.

The same task body runs daily via Celery Beat (AC-26). This command
is the operator-facing surface called out by AC-27: a fast way to
exercise the prune in staging, or to recover originals storage after
a retention-policy change in prod.

Example:

    # what would get cleaned right now
    python manage.py prune_storage_vision_captures --dry-run

    # do the cleanup with a one-off retention window (override the setting)
    python manage.py prune_storage_vision_captures --days 7
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Delete VisionCapture.original_image files older than the retention window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override STORAGE_VISION_RETENTION_DAYS for this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be pruned without deleting anything.",
        )

    def handle(self, *args, **options):
        from django.conf import settings

        from storage_vision.models import VisionCapture
        from storage_vision.tasks import prune_original_captures

        days = options.get("days")
        dry_run = options.get("dry_run", False)

        # For visibility — show what the cutoff is and how many rows fall
        # under it regardless of dry-run.
        configured_days = int(getattr(settings, "STORAGE_VISION_RETENTION_DAYS", 30) or 0)
        effective_days = days if days is not None else configured_days
        if effective_days <= 0:
            self.stdout.write(
                self.style.WARNING("Retention is disabled (days=0); no captures would be pruned.")
            )
            return

        cutoff = timezone.now() - timedelta(days=effective_days)
        candidate_qs = VisionCapture.objects.filter(received_at__lt=cutoff).exclude(
            original_image=""
        )
        self.stdout.write(
            f"Cutoff: {cutoff.isoformat()} ({effective_days} days)\n"
            f"Candidates: {candidate_qs.count()}"
        )

        if dry_run:
            self.stdout.write(self.style.NOTICE("--dry-run set; not deleting anything."))
            return

        if days is not None and days != configured_days:
            # Temporarily override the setting so the task body uses the
            # operator's --days value.
            original_setting = settings.STORAGE_VISION_RETENTION_DAYS
            settings.STORAGE_VISION_RETENTION_DAYS = days
            try:
                result = prune_original_captures()
            finally:
                settings.STORAGE_VISION_RETENTION_DAYS = original_setting
        else:
            result = prune_original_captures()

        self.stdout.write(self.style.SUCCESS(f"Prune complete: {result}"))
