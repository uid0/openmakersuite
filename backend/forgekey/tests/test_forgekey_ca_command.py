"""
Tests for ``python manage.py forgekey_ca`` (oms-d2axqu).

Covers:
  * ``init`` creates an active row and refuses re-init without --force.
  * ``status`` prints the active CA summary.
  * ``export-cert`` writes a parseable PEM.
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

import pytest
from cryptography import x509
from cryptography.fernet import Fernet

from forgekey.models import CertificateAuthority

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _ca_kek(settings):
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


def test_init_creates_active_ca():
    out = StringIO()
    call_command("forgekey_ca", "init", "--validity-years", "1", stdout=out)
    assert CertificateAuthority.objects.filter(is_active=True).count() == 1
    active = CertificateAuthority.get_active()
    assert active.name == "forgekey-root"
    assert active.cert_pem.startswith("-----BEGIN CERTIFICATE-----")


def test_init_refuses_double_init_without_force():
    call_command("forgekey_ca", "init", "--validity-years", "1")
    with pytest.raises(CommandError):
        call_command("forgekey_ca", "init", "--validity-years", "1")


def test_init_with_force_replaces_active_ca():
    call_command("forgekey_ca", "init", "--validity-years", "1")
    first = CertificateAuthority.get_active()

    call_command("forgekey_ca", "init", "--validity-years", "1", "--force")
    second = CertificateAuthority.get_active()
    assert second.pk != first.pk

    first.refresh_from_db()
    assert first.is_active is False
    assert CertificateAuthority.objects.filter(is_active=True).count() == 1


def test_status_prints_fields():
    call_command("forgekey_ca", "init", "--validity-years", "1")
    out = StringIO()
    call_command("forgekey_ca", "status", stdout=out)
    rendered = out.getvalue()
    assert "Active CA" in rendered
    assert "Fingerprint" in rendered
    assert "Validity" in rendered


def test_status_handles_no_active_ca():
    out = StringIO()
    call_command("forgekey_ca", "status", stdout=out)
    assert "No active CA" in out.getvalue()


def test_export_cert_writes_parseable_pem(tmp_path):
    call_command("forgekey_ca", "init", "--validity-years", "1")
    target = tmp_path / "ca.pem"
    call_command("forgekey_ca", "export-cert", "--out", str(target))
    pem = target.read_text(encoding="utf-8")
    assert "-----BEGIN CERTIFICATE-----" in pem
    cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
    assert cert.subject.rfc4514_string().startswith("CN=ForgeKey Internal Root CA")
