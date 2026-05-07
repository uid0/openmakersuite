"""Tests for donation audit-event emission hooks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.urls import reverse
from django.utils import timezone

import pytest

from donations.audit import record_event
from donations.models import DonationAuditEvent
from donations.tests.factories import (
    DispositionFactory,
    DonationFactory,
    DonationItemFactory,
    TaxReceiptFactory,
)
from forgekey.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# DonationFactory.date_received defaults to ``timezone.now()`` (a
# datetime) but ``Donation.date_received`` is a DateField. DRF serializers
# refuse to coerce datetime -> date on read and 500 the
# generate_tax_receipt response. Pass an explicit date so the codepath
# under test is the audit hook, not a pre-existing factory shape bug.
_TODAY = timezone.now().date()


def test_donation_create_records_audit_event(api_client):
    actor = UserFactory()
    api_client.force_authenticate(user=actor)

    response = api_client.post(
        reverse("donation-list"),
        {
            "donor_name": "Acme",
            "date_received": "2026-05-07",
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    assert DonationAuditEvent.objects.count() == 1

    event = DonationAuditEvent.objects.get(action=DonationAuditEvent.ACTION_DONATION_CREATE)
    assert event.actor == actor
    assert event.donation is not None
    assert event.metadata["donor_name"] == "Acme"
    assert event.metadata["donation_number"]


def test_tax_receipt_issued_records_audit_event(api_client):
    actor = UserFactory()
    donation = DonationFactory(donor_email="", date_received=_TODAY)
    api_client.force_authenticate(user=actor)

    with patch("donations.views.TaxReceiptPDFGenerator") as generator_cls:
        generator = MagicMock()
        generator.generate_and_save.return_value = "tax_receipts/test.pdf"
        generator_cls.return_value = generator

        response = api_client.post(
            reverse("donation-generate-tax-receipt", kwargs={"pk": donation.pk}),
            {},
            format="json",
        )

    assert response.status_code == 201, response.content
    assert DonationAuditEvent.objects.count() == 1

    event = DonationAuditEvent.objects.get(action=DonationAuditEvent.ACTION_TAX_RECEIPT_ISSUED)
    assert event.actor == actor
    assert event.donation == donation
    assert event.tax_receipt is not None
    assert event.metadata["serial_number"] == str(event.tax_receipt.serial_number)


def test_tax_receipt_regenerate_records_audit_event(api_client):
    actor = UserFactory(is_staff=True, is_superuser=True)
    tax_receipt = TaxReceiptFactory(donation__donor_email="", donation__date_received=_TODAY)
    api_client.force_authenticate(user=actor)

    with patch("donations.views.TaxReceiptPDFGenerator") as generator_cls:
        generator = MagicMock()
        generator.generate_and_save.return_value = "tax_receipts/test.pdf"
        generator_cls.return_value = generator

        response = api_client.post(
            reverse("tax-receipt-regenerate", kwargs={"pk": tax_receipt.pk}),
            {},
            format="json",
        )

    assert response.status_code == 200, response.content
    assert DonationAuditEvent.objects.count() == 1

    event = DonationAuditEvent.objects.get(action=DonationAuditEvent.ACTION_TAX_RECEIPT_REGENERATE)
    assert event.actor == actor
    assert event.tax_receipt == tax_receipt
    assert event.donation == tax_receipt.donation
    assert event.metadata["serial_number"] == str(tax_receipt.serial_number)
    assert event.metadata["is_copy"] is tax_receipt.is_copy


def test_disposition_create_records_audit_event(api_client):
    actor = UserFactory()
    donation_item = DonationItemFactory()
    api_client.force_authenticate(user=actor)

    response = api_client.post(
        reverse("disposition-list"),
        {
            "donation_item": donation_item.pk,
            "disposition_type": "disposed",
            "quantity": 1,
            "disposition_date": "2026-05-07",
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    assert DonationAuditEvent.objects.count() == 1

    event = DonationAuditEvent.objects.get(action=DonationAuditEvent.ACTION_ITEM_DISPOSED)
    assert event.actor == actor
    assert event.disposition is not None
    assert event.donation_item == donation_item
    assert event.donation == donation_item.donation
    assert event.metadata["disposition_type"] == "disposed"


def test_record_event_with_anonymous_actor_creates_row_with_null_actor():
    donation = DonationFactory()

    event = record_event(
        action=DonationAuditEvent.ACTION_DONATION_CREATE,
        actor=None,
        donation=donation,
    )

    assert DonationAuditEvent.objects.count() == 1
    assert event.actor is None
    assert event.donation == donation


def test_record_event_derives_donation_from_tax_receipt():
    actor = UserFactory()
    tax_receipt = TaxReceiptFactory()

    event = record_event(
        action=DonationAuditEvent.ACTION_TAX_RECEIPT_ISSUED,
        actor=actor,
        tax_receipt=tax_receipt,
    )

    assert DonationAuditEvent.objects.count() == 1
    assert event.actor == actor
    assert event.tax_receipt == tax_receipt
    assert event.donation == tax_receipt.donation


def test_record_event_derives_donation_from_donation_item():
    actor = UserFactory()
    donation_item = DonationItemFactory()

    event = record_event(
        action=DonationAuditEvent.ACTION_ITEM_DISPOSED,
        actor=actor,
        donation_item=donation_item,
    )

    assert DonationAuditEvent.objects.count() == 1
    assert event.actor == actor
    assert event.donation_item == donation_item
    assert event.donation == donation_item.donation


def test_record_event_derives_donation_from_disposition():
    actor = UserFactory()
    disposition = DispositionFactory()

    event = record_event(
        action=DonationAuditEvent.ACTION_ITEM_DISPOSED,
        actor=actor,
        disposition=disposition,
    )

    assert DonationAuditEvent.objects.count() == 1
    assert event.actor == actor
    assert event.disposition == disposition
    assert event.donation == disposition.donation_item.donation
