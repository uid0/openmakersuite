"""Tests for :func:`donations.services.numbering.next_donation_number` — the
``DON-YYYY-NNN`` composition extracted from ``Donation.save()`` (gh #887).
"""

from datetime import date

import pytest

from donations.services.numbering import next_donation_number
from donations.tests.factories import DonationFactory

pytestmark = pytest.mark.django_db


class TestNextDonationNumber:
    def test_first_number_for_year(self):
        assert next_donation_number(2099) == "DON-2099-001"

    def test_increments_from_latest(self):
        DonationFactory(donation_number="DON-2099-005", date_received=date(2099, 1, 1))
        assert next_donation_number(2099) == "DON-2099-006"

    def test_unparseable_counter_falls_back_to_one(self):
        DonationFactory(donation_number="DON-2099-BAD", date_received=date(2099, 1, 1))
        assert next_donation_number(2099) == "DON-2099-001"

    def test_scoped_to_year(self):
        DonationFactory(donation_number="DON-2098-050", date_received=date(2098, 1, 1))
        assert next_donation_number(2099) == "DON-2099-001"


class TestDonationSaveNumbering:
    """Donation.save() delegates to the helper when no number is supplied."""

    def test_save_generates_number_when_blank(self):
        donation = DonationFactory(date_received=date(2099, 3, 1))
        assert donation.donation_number == "DON-2099-001"

    def test_save_preserves_explicit_number(self):
        donation = DonationFactory(donation_number="DON-2099-042", date_received=date(2099, 3, 1))
        assert donation.donation_number == "DON-2099-042"
