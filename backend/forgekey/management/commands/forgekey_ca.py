"""
ForgeKey CA management command.

Subcommands:
  * ``init``         — generate a fresh root CA and persist it.
  * ``status``       — print summary of the active CA.
  * ``export-cert``  — write/print the public CA certificate (PEM).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from ...models import CertificateAuthority, DeviceCertificate
from ...services.ca_key_storage import CaKeyStorageError, encrypt_ca_key
from ...services.csr_signing import generate_ca_keypair


class Command(BaseCommand):
    help = "Bootstrap and inspect the internal ForgeKey CA."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="subcommand", required=True)

        init_p = sub.add_parser("init", help="Generate and persist a fresh CA.")
        init_p.add_argument("--name", default="forgekey-root")
        init_p.add_argument("--cn", default="ForgeKey Internal Root CA")
        init_p.add_argument("--validity-years", type=int, default=10)
        init_p.add_argument(
            "--force",
            action="store_true",
            help="Replace the currently active CA. The prior CA is deactivated.",
        )

        sub.add_parser("status", help="Print the active CA's CN, fingerprint, and counts.")

        export_p = sub.add_parser("export-cert", help="Write the active CA cert PEM.")
        export_p.add_argument("--out", help="Output path; stdout if omitted.")

    def handle(self, *args, **options) -> None:
        sub = options["subcommand"]
        if sub == "init":
            self._init(
                name=options["name"],
                cn=options["cn"],
                validity_years=options["validity_years"],
                force=options["force"],
            )
        elif sub == "status":
            self._status()
        elif sub == "export-cert":
            self._export_cert(out=options.get("out"))
        else:  # pragma: no cover
            raise CommandError(f"Unknown subcommand {sub!r}")

    # ----- init -----------------------------------------------------------

    def _init(self, *, name: str, cn: str, validity_years: int, force: bool) -> None:
        if validity_years <= 0:
            raise CommandError("--validity-years must be positive.")

        active = CertificateAuthority.get_active()
        if active is not None and not force:
            raise CommandError(
                f"An active CA already exists (name={active.name!r}). "
                "Re-run with --force to deactivate it and bootstrap a replacement."
            )

        try:
            private_pem, ca_cert = generate_ca_keypair(
                cn=cn, validity_days=validity_years * 365
            )
        except Exception as exc:
            raise CommandError(f"Failed to generate CA keypair: {exc}") from exc

        try:
            ciphertext, kid = encrypt_ca_key(private_pem)
        except CaKeyStorageError as exc:
            raise CommandError(f"Cannot encrypt CA key: {exc}") from exc

        with transaction.atomic():
            if active is not None:
                # Deactivate the prior row first so the partial unique
                # constraint (single active=True) doesn't collide.
                CertificateAuthority.objects.filter(pk=active.pk).update(is_active=False)
            CertificateAuthority.objects.create(
                name=name,
                cert_pem=ca_cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
                encrypted_private_key=ciphertext,
                key_kid=kid,
                not_before=ca_cert.not_valid_before_utc,
                not_after=ca_cert.not_valid_after_utc,
                is_active=True,
            )

        fingerprint = ca_cert.fingerprint(hashes.SHA256()).hex()
        self.stdout.write(self.style.SUCCESS(f"Bootstrapped CA {name!r}."))
        self.stdout.write(f"  CN:          {cn}")
        self.stdout.write(f"  Fingerprint: {fingerprint}")
        self.stdout.write(
            f"  Validity:    {ca_cert.not_valid_before_utc.isoformat()} "
            f"to {ca_cert.not_valid_after_utc.isoformat()}"
        )
        if active is not None:
            self.stdout.write(
                self.style.WARNING(f"  Replaced prior active CA {active.name!r}.")
            )

    # ----- status ---------------------------------------------------------

    def _status(self) -> None:
        active = CertificateAuthority.get_active()
        if active is None:
            self.stdout.write(self.style.WARNING("No active CA configured."))
            return
        try:
            ca_cert = x509.load_pem_x509_certificate(active.cert_pem.encode("utf-8"))
            fingerprint = ca_cert.fingerprint(hashes.SHA256()).hex()
            cn = next(
                (a.value for a in ca_cert.subject if a.oid.dotted_string == "2.5.4.3"),
                "<unknown>",
            )
        except Exception as exc:
            raise CommandError(f"Active CA certificate is unreadable: {exc}") from exc

        issued = DeviceCertificate.objects.filter(revoked_at__isnull=True).count()
        revoked = DeviceCertificate.objects.filter(revoked_at__isnull=False).count()

        self.stdout.write(f"Active CA: {active.name}")
        self.stdout.write(f"  CN:           {cn}")
        self.stdout.write(f"  Fingerprint:  {fingerprint}")
        self.stdout.write(
            f"  Validity:     {active.not_before.isoformat()} to {active.not_after.isoformat()}"
        )
        self.stdout.write(f"  Key kid:      {active.key_kid or '<unset>'}")
        self.stdout.write(f"  Active certs: {issued}")
        self.stdout.write(f"  Revoked:      {revoked}")

    # ----- export-cert ----------------------------------------------------

    def _export_cert(self, *, out: str | None) -> None:
        active = CertificateAuthority.get_active()
        if active is None:
            raise CommandError("No active CA configured.")
        pem = active.cert_pem
        if not pem.endswith("\n"):
            pem += "\n"
        if out:
            Path(out).write_text(pem, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Wrote {len(pem)} bytes to {out}"))
        else:
            sys.stdout.write(pem)
            sys.stdout.flush()
