"""Bulk-create a rack's storage slots from the command line.

The API's ``POST /api/project-storage/slots/generate/`` does the same thing;
this exists so racking a new aisle is scriptable during setup, before anyone
has a warden login.

    python manage.py generate_storage_slots --rack 1 \
        --level A:12 --level B:12 --level Y:10:jack
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from project_storage.services.storage_slots import LevelSpec, generate_rack_slots


def _parse_level(raw: str) -> LevelSpec:
    """Parse ``<letter>:<positions>[:jack]`` into a :class:`LevelSpec`."""
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        raise CommandError(f"Bad --level {raw!r}: expected LETTER:POSITIONS[:jack], e.g. A:12.")
    level, positions = parts[0], parts[1]
    if len(level) != 1 or not level.isalpha():
        raise CommandError(f"Bad --level {raw!r}: level must be a single letter.")
    if not positions.isdigit() or int(positions) < 1:
        raise CommandError(f"Bad --level {raw!r}: positions must be a positive integer.")
    requires_pallet_jack = len(parts) == 3 and parts[2].lower() in ("jack", "true", "1", "yes")
    if len(parts) == 3 and not requires_pallet_jack:
        raise CommandError(f"Bad --level {raw!r}: third field must be 'jack' if present.")
    return LevelSpec(
        level=level.upper(),
        positions=int(positions),
        requires_pallet_jack=requires_pallet_jack,
    )


class Command(BaseCommand):
    help = "Idempotently generate the storage slots for one pallet rack."

    def add_arguments(self, parser):
        parser.add_argument("--rack", type=int, required=True, help="Pallet rack number.")
        parser.add_argument(
            "--level",
            action="append",
            required=True,
            dest="levels",
            metavar="LETTER:POSITIONS[:jack]",
            help=(
                "A level to fill, repeatable. 'A:12' makes 1A1..1A12; "
                "'Y:10:jack' marks them as needing a pallet jack."
            ),
        )

    def handle(self, *args, **options):
        rack = options["rack"]
        if rack < 1:
            raise CommandError("--rack must be 1 or greater.")
        specs = [_parse_level(raw) for raw in options["levels"]]

        seen = [spec.level for spec in specs]
        duplicates = sorted({level for level in seen if seen.count(level) > 1})
        if duplicates:
            raise CommandError(f"Level(s) {', '.join(duplicates)} given more than once.")

        result = generate_rack_slots(rack=rack, levels=specs)

        self.stdout.write(
            self.style.SUCCESS(
                f"Rack {rack}: {len(result.created)} slot(s) created, "
                f"{len(result.skipped)} already present "
                f"({len(result.slots)} total)."
            )
        )
        if result.without_tag:
            self.stdout.write(
                self.style.WARNING(
                    "AprilTag pool exhausted — no marker for: "
                    + ", ".join(slot.code for slot in result.without_tag)
                )
            )
