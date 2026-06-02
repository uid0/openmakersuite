"""
Pull current state from every active BMS config into its ThermostatBinding
rows. Wraps bms.services.sync_all; intended to be called from cron / Celery
beat once the schedule is wired up, or interactively for debugging.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from ...services import sync_all


class Command(BaseCommand):
    help = "Sync state for every active BmsConfig into its ThermostatBindings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--config",
            help="Name of a single BmsConfig to sync (skip others). "
            "Default: sync every is_active=True config.",
        )

    def handle(self, *args, **options):
        from ...models import BmsConfig

        if options.get("config"):
            qs = BmsConfig.objects.filter(name=options["config"])
            if not qs.exists():
                self.stderr.write(
                    self.style.ERROR(f"No BmsConfig found with name={options['config']!r}")
                )
                return
            results = sync_all(qs)
        else:
            results = sync_all()

        if not results:
            self.stdout.write("No active BmsConfig rows — nothing to sync.")
            return

        for res in results:
            ok_count = len(res.succeeded)
            fail_count = len(res.failed)
            line = f"{res.config_name}: {ok_count} ok, {fail_count} failed"
            if fail_count:
                self.stdout.write(self.style.WARNING(line))
                for device_id, msg in res.failed:
                    self.stdout.write(f"  - {device_id}: {msg}")
            else:
                self.stdout.write(self.style.SUCCESS(line))
