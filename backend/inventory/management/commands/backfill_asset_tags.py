"""Backfill existing assets onto the ``DMS-YYANNNSS`` tag format.

Historically ``asset_tag`` was ``DMS-<8 random hex>``. This command reassigns
those legacy tags to the human-meaningful ``DMS-YYANNNSS`` format, processing
assets in ``date_received`` order so each year's counter is built in the order
the assets actually arrived.

The command is idempotent: an asset that already holds a valid new-format tag
is left untouched, and the per-year counters are seeded from (and written back
to) :class:`~inventory.models.AssetTagSequence` so a backfill never collides
with — and always advances — the live counter that ``Asset.save()`` uses.

WARNING: this CHANGES the printed identifier on existing assets. Scanning is
unaffected (the QR still encodes the asset UUID), but any already-printed
physical labels would need reprinting to show the new tag.

Usage::

    python manage.py backfill_asset_tags --dry-run
    python manage.py backfill_asset_tags
    python manage.py backfill_asset_tags --skip-undated
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F

from inventory.models import Asset, AssetTagSequence
from inventory.services.asset_tag_id import compose_asset_tag, validate_asset_tag


class Command(BaseCommand):
    help = "Reassign existing asset_tags to the DMS-YYANNNSS format (ordered by date_received)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )
        parser.add_argument(
            "--skip-undated",
            action="store_true",
            help="Skip assets with no date_received instead of falling back to their created year.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        skip_undated = options["skip_undated"]

        # Seed per-year counters from any existing sequences so we continue
        # past — never collide with — numbers the live counter already handed
        # out. year -> [alpha, number].
        counters = {seq.year: [seq.alpha, seq.number] for seq in AssetTagSequence.objects.all()}
        # Tags already taken (valid new-format tags we will leave in place),
        # so a freshly composed tag never duplicates one of them.
        taken = {
            asset.asset_tag for asset in Asset.objects.all() if validate_asset_tag(asset.asset_tag)
        }

        reassigned = 0
        skipped_valid = 0
        undated_skipped = 0

        assets = Asset.objects.order_by(F("date_received").asc(nulls_last=True), "created_at", "id")

        with transaction.atomic():
            for asset in assets:
                if validate_asset_tag(asset.asset_tag):
                    skipped_valid += 1
                    continue

                if asset.date_received:
                    year = asset.date_received.year
                elif skip_undated:
                    undated_skipped += 1
                    self.stdout.write(
                        self.style.WARNING(f"  skip (no date_received): {asset.id} {asset.name!r}")
                    )
                    continue
                else:
                    year = asset.created_at.year

                new_tag = self._next_tag(year, counters, taken)
                taken.add(new_tag)
                old_tag = asset.asset_tag

                if dry_run:
                    self.stdout.write(f"  {old_tag or '(blank)'} -> {new_tag}  [{asset.name}]")
                else:
                    asset.asset_tag = new_tag
                    asset.save(update_fields=["asset_tag"])
                reassigned += 1

            if dry_run:
                # Nothing is persisted on a dry run; roll the transaction back
                # so the counter rows we may have touched are not written.
                transaction.set_rollback(True)
            else:
                self._persist_counters(counters)

        self.stdout.write("")
        verb = "Would reassign" if dry_run else "Reassigned"
        self.stdout.write(self.style.SUCCESS(f"{verb} {reassigned} asset tag(s)."))
        self.stdout.write(f"Skipped {skipped_valid} already in DMS-YYANNNSS format.")
        if undated_skipped:
            self.stdout.write(f"Skipped {undated_skipped} asset(s) with no date_received.")

    def _next_tag(self, year, counters, taken):
        """Advance the year's counter until it yields a tag not already taken."""
        while True:
            alpha, number = counters.setdefault(year, ["A", 0])
            number += 1
            if number > 999:
                number = 1
                alpha = AssetTagSequence._next_alpha(alpha)
            counters[year] = [alpha, number]
            core = f"{year % 100:02d}{alpha}{number:03d}"
            candidate = compose_asset_tag(core)
            if candidate not in taken:
                return candidate

    def _persist_counters(self, counters):
        """Write the final per-year positions back so the live counter continues."""
        for year, (alpha, number) in counters.items():
            AssetTagSequence.objects.update_or_create(
                year=year, defaults={"alpha": alpha, "number": number}
            )
