"""
Re-derive electrical AssetEnergySource rows from the current power topology.

Idempotent: running it twice produces the same rows. Useful after bulk
imports of cables / breakers, or when LOTO requirements on a breaker
change in a way that didn't go through the admin signal path (e.g.,
direct SQL fixups).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from loto.services.power_derivation import rederive_all


class Command(BaseCommand):
    help = "Re-derive AssetEnergySource rows from the power topology."

    def handle(self, *args, **options):
        summary = rederive_all()
        self.stdout.write(
            self.style.SUCCESS(
                "Derived {derived}, skipped {skipped}, marked stale {marked_stale}.".format(
                    **summary
                )
            )
        )
