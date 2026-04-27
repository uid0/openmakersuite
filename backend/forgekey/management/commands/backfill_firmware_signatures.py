"""
Backfill ECDSA(P-256) signatures on existing FirmwareVersion rows.

Idempotent: rows that already have a non-empty ``signature`` are skipped
unless ``--force`` is passed. A missing ``firmware_file`` is also skipped
with a warning. Requires FORGEKEY_FIRMWARE_SIGNING_KEY to be configured.
"""

from __future__ import annotations

import hashlib

from django.core.management.base import BaseCommand, CommandError

from forgekey.models import FirmwareVersion
from forgekey.services.firmware_signing import (
    FirmwareSigningError,
    is_signing_configured,
    sign_firmware_bytes,
)


class Command(BaseCommand):
    help = "Compute and persist ECDSA(P-256) signatures for FirmwareVersion rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-sign rows that already have a signature.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without writing to the database.",
        )
        parser.add_argument(
            "--firmware-version",
            dest="firmware_version",
            help="Only sign the FirmwareVersion with this version string.",
        )

    def handle(self, *args, **options):
        if not is_signing_configured():
            raise CommandError(
                "FORGEKEY_FIRMWARE_SIGNING_KEY is not configured; cannot sign firmware."
            )

        qs = FirmwareVersion.objects.all().order_by("created_at")
        if options.get("firmware_version"):
            qs = qs.filter(version=options["firmware_version"])

        force = options["force"]
        dry_run = options["dry_run"]

        signed = 0
        skipped = 0
        errors = 0

        for fw in qs:
            if fw.signature and not force:
                skipped += 1
                continue
            if not fw.firmware_file:
                self.stderr.write(
                    self.style.WARNING(f"skip {fw.version}: no firmware_file attached")
                )
                skipped += 1
                continue
            try:
                data = fw._read_firmware_bytes()
                if data is None:
                    raise RuntimeError("firmware_file has no readable content")
                signature = sign_firmware_bytes(data)
            except (FirmwareSigningError, OSError, RuntimeError) as exc:
                self.stderr.write(self.style.ERROR(f"fail {fw.version}: {exc}"))
                errors += 1
                continue

            if dry_run:
                self.stdout.write(f"[dry-run] would sign {fw.version}")
            else:
                update_fields = ["signature"]
                fw.signature = signature
                if not fw.sha256:
                    fw.sha256 = hashlib.sha256(data).hexdigest()
                    update_fields.append("sha256")
                FirmwareVersion.objects.filter(pk=fw.pk).update(
                    **{f: getattr(fw, f) for f in update_fields}
                )
                self.stdout.write(self.style.SUCCESS(f"signed {fw.version}"))
            signed += 1

        summary = f"signed={signed} skipped={skipped} errors={errors}" + (
            " (dry-run)" if dry_run else ""
        )
        self.stdout.write(self.style.NOTICE(summary))
        if errors:
            raise CommandError(f"{errors} firmware row(s) failed to sign")
