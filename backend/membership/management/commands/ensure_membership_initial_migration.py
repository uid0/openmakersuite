"""Management command to resolve inconsistent membership migration history."""

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.utils import ProgrammingError
from django.utils import timezone


class Command(BaseCommand):
    help = (
        "Ensure membership.0001_initial is marked as applied when admin.0001_initial "
        "already exists in the migration history."
    )

    def handle(self, *args, **options):
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM django_migrations WHERE app = %s AND name = %s",
                    ["admin", "0001_initial"],
                )
                admin_applied = cursor.fetchone() is not None

                cursor.execute(
                    "SELECT 1 FROM django_migrations WHERE app = %s AND name = %s",
                    ["membership", "0001_initial"],
                )
                membership_applied = cursor.fetchone() is not None
        except ProgrammingError:
            self.stdout.write(
                "django_migrations table not available yet; skipping migration history check."
            )
            return

        if not admin_applied or membership_applied:
            if admin_applied:
                self.stdout.write("membership.0001_initial already recorded; no changes needed.")
            else:
                self.stdout.write(
                    "admin.0001_initial not recorded yet; no migration history fix required."
                )
            return

        self.stdout.write(
            self.style.WARNING(
                "Detected admin.0001_initial without membership.0001_initial. Applying fix..."
            )
        )

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO django_migrations (app, name, applied) VALUES (%s, %s, %s)",
                    ["membership", "0001_initial", timezone.now()],
                )

        self.stdout.write(self.style.SUCCESS("Marked membership.0001_initial as applied."))
