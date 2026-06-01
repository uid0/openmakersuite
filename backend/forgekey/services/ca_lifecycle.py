"""Mint / rotate the internal ForgeKey root CA.

Rotation generates a brand-new self-signed root and deactivates the prior one
in the same transaction — devices must be re-flashed (or rebuilt via the
firmware pipeline) to trust the new root. Shared generation logic so the
staff rotate API and the ``forgekey_ca`` bootstrap command behave identically.
"""

from __future__ import annotations

from django.db import transaction

from cryptography.hazmat.primitives import serialization

from ..models import CertificateAuthority
from .ca_key_storage import encrypt_ca_key
from .csr_signing import generate_ca_keypair


def mint_ca(
    *,
    name: str = "forgekey-root",
    cn: str = "ForgeKey Internal Root CA",
    validity_years: int = 10,
    replace_active: bool = True,
) -> CertificateAuthority:
    """Generate a fresh self-signed root CA and persist it as the active CA.

    If a CA is already active and ``replace_active`` is True, it is deactivated
    in the same transaction (the partial-unique constraint permits a single
    active CA). Raises ``RuntimeError`` if an active CA exists and
    ``replace_active`` is False, and ``ValueError`` for a non-positive validity.
    """
    if validity_years <= 0:
        raise ValueError("validity_years must be positive")

    active = CertificateAuthority.get_active()
    if active is not None and not replace_active:
        raise RuntimeError("An active CA already exists; pass replace_active=True to rotate it.")

    private_pem, ca_cert = generate_ca_keypair(cn=cn, validity_days=validity_years * 365)
    ciphertext, kid = encrypt_ca_key(private_pem)

    with transaction.atomic():
        if active is not None:
            # Deactivate first so the single-active partial-unique constraint
            # doesn't collide with the new row.
            CertificateAuthority.objects.filter(pk=active.pk).update(is_active=False)
        return CertificateAuthority.objects.create(
            name=name,
            cert_pem=ca_cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
            encrypted_private_key=ciphertext,
            key_kid=kid,
            not_before=ca_cert.not_valid_before_utc,
            not_after=ca_cert.not_valid_after_utc,
            is_active=True,
        )
