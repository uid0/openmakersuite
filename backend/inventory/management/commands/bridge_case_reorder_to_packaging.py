"""Move legacy case-based items onto the unified packaging model (op-es7c).

Before the UoM work an item could be reordered "by the case" via three columns
of its own — ``use_case_based_reorder`` / ``minimum_cases`` / ``reorder_cases``
— with the case size read off the *primary supplier's*
``quantity_per_package``. Phase 1 introduced the item's own packaging chain and
``count_mode``, and phase 2a made ``needs_reorder`` and the suggested reorder
quantity honour it. This command bridges the former into the latter, on demand:
it is NOT a data migration and nothing runs it automatically.

For each eligible item it writes a two-rung chain (case = the supplier's
quantity per package, base = 1), points ``count_mode``/``count_level`` at the
case rung, and copies ``minimum_cases``/``reorder_cases`` into
``minimum_stock``/``reorder_quantity`` — which for a pack-counting item are read
in cases. The legacy columns are deliberately left in place; deprecating them is
a later step.

⚠️ THE ONE BEHAVIOUR SHIFT, reported per item so it is never a surprise: the
legacy trigger compares a FRACTIONAL case count (``current_stock / qpp <=
minimum_cases``) while a pack-counting item compares WHOLE packs
(``floor(current_stock / qpp) <= minimum_stock``). They agree whenever stock is
a whole number of cases; with a part-used case sitting just above the threshold
the bridged item trips one partial case earlier — which is the point of counting
in whole packs, but it is a change. The report prints ``needs reorder: X -> Y``
for every item and flags the ones that flip.

Eligibility (everything else is skipped, with the reason):

* ``use_case_based_reorder`` is set;
* the primary supplier link has ``quantity_per_package > 1`` (at 1 there is no
  case to model, and a 1-unit "case" rung would be an invalid chain);
* the item has no packaging levels yet — an item someone already configured is
  never rewritten, which is what makes re-running safe.

Usage::

    python manage.py bridge_case_reorder_to_packaging            # dry run
    python manage.py bridge_case_reorder_to_packaging --apply    # write
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from inventory.models import InventoryItem, PackagingLevel
from inventory.services.packaging import validate_packaging_chain

CASE_LEVEL_NAME = "case"


class Command(BaseCommand):
    help = (
        "Bridge legacy case-based reorder items onto packaging levels + count_mode "
        "(dry run unless --apply)."
    )

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without it the command only reports what it would do.",
        )
        group.add_argument(
            "--dry-run",
            action="store_true",
            help="Report without writing. This is the default; the flag just says so out loud.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]

        items = (
            InventoryItem.objects.filter(use_case_based_reorder=True)
            .select_related("count_level")
            .prefetch_related("item_suppliers__supplier", "packaging_levels")
            .order_by("name", "id")
        )

        bridged = 0
        skipped: dict[str, int] = {}
        flipped = 0

        with transaction.atomic():
            for item in items:
                reason = self._skip_reason(item)
                if reason:
                    skipped[reason] = skipped.get(reason, 0) + 1
                    self.stdout.write(f"  skip ({reason}): {item.name}")
                    continue

                case_size = item.case_pack_size
                before = item.needs_reorder
                # What the trigger will say once bridged, computed without
                # writing: WHOLE cases against minimum_cases, which is the
                # value minimum_stock is about to take. Retirement still wins
                # over stock in both modes, so it short-circuits here too —
                # otherwise a retired item would be reported as flipping.
                after = not item.is_retired and (item.current_stock // case_size) <= (
                    item.minimum_cases
                )

                self._report(item, case_size, before, after)
                if before != after:
                    flipped += 1

                if apply_changes:
                    self._bridge(item, case_size)
                bridged += 1

            if not apply_changes:
                # Nothing should have been written, but roll back regardless so a
                # future edit to _report cannot leak a write out of a dry run.
                transaction.set_rollback(True)

        self.stdout.write("")
        verb = "Bridged" if apply_changes else "Would bridge"
        self.stdout.write(self.style.SUCCESS(f"{verb} {bridged} item(s)."))
        if flipped:
            self.stdout.write(
                self.style.WARNING(
                    f"{flipped} item(s) change whether they currently need reordering "
                    "(whole-pack vs fractional-case comparison) — marked CHANGES above."
                )
            )
        for reason, count in sorted(skipped.items()):
            self.stdout.write(f"Skipped {count} item(s): {reason}.")
        if not apply_changes:
            self.stdout.write("Dry run — nothing written. Re-run with --apply to commit.")

    def _skip_reason(self, item: InventoryItem) -> str | None:
        """Why ``item`` cannot be bridged, or ``None`` when it can.

        The case-size half reads ``InventoryItem.case_pack_size`` — the SAME
        predicate ``current_cases`` divides by — so this command's refusal to
        migrate an item and that item's own inability to report a case count
        can never contradict each other (op-2rsp).
        """
        if item.packaging_levels.all():
            return "already has packaging levels"
        if item.case_pack_size is None:
            return "no supplier to take a case size from"
        if item.case_pack_size <= 1:
            return "supplier quantity_per_package is not more than 1"
        return None

    def _report(self, item: InventoryItem, case_size: int, before: bool, after: bool) -> None:
        marker = self.style.WARNING("  CHANGES") if before != after else ""
        self.stdout.write(
            f"  {item.name} [{item.sku}]: 1 case = {case_size} {item.base_unit}"
            f" | minimum_stock {item.minimum_stock} -> {item.minimum_cases}"
            f" | reorder_quantity {item.reorder_quantity} -> {item.reorder_cases}"
            f" | count_mode {item.count_mode} -> {InventoryItem.CountMode.BY_LEVEL}"
            f" | needs reorder: {before} -> {after}{marker}"
        )

    def _bridge(self, item: InventoryItem, case_size: int) -> None:
        """Write the two-rung chain and point the item's counting at the case rung."""
        case_level = PackagingLevel.objects.create(
            item=item,
            name=CASE_LEVEL_NAME,
            sort_order=0,
            base_units=case_size,
        )
        PackagingLevel.objects.create(
            item=item,
            name=item.base_unit or "unit",
            sort_order=1,
            base_units=1,
        )
        # Validate the chain as a SET — PackagingLevel.clean() rejects a rung
        # judged alone (the first rung of a new chain has no base rung yet), so
        # the set-level validator is the only correct check here. Queried fresh
        # rather than through ``item.packaging_levels``, whose prefetch cache
        # was filled (empty) before these two rows existed.
        validate_packaging_chain(list(PackagingLevel.objects.filter(item=item)))

        item.count_mode = InventoryItem.CountMode.BY_LEVEL
        item.count_level = case_level
        item.minimum_stock = item.minimum_cases
        item.reorder_quantity = item.reorder_cases
        item.save(
            update_fields=[
                "count_mode",
                "count_level",
                "minimum_stock",
                "reorder_quantity",
                "updated_at",
            ]
        )
