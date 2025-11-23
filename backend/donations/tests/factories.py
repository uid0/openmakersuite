"""
Factory classes for generating test data for donations models.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

import factory
from factory import Faker, SubFactory
from factory.django import DjangoModelFactory

from donations.models import Disposition, Donation, DonationItem, TaxReceipt
from forgekey.tests.factories import UserFactory

User = get_user_model()


class DonationFactory(DjangoModelFactory):
    """Factory for creating Donation instances."""

    class Meta:
        model = Donation

    donor_name = Faker("name")
    donor_email = Faker("email")
    donor_phone = Faker("phone_number")
    donor_address = Faker("address")
    date_received = factory.LazyFunction(timezone.now)
    received_by = SubFactory(UserFactory)
    status = Donation.PENDING
    estimated_value = Decimal("100.00")
    associated_costs = Decimal("0.00")


class DonationItemFactory(DjangoModelFactory):
    """Factory for creating DonationItem instances."""

    class Meta:
        model = DonationItem

    donation = SubFactory(DonationFactory)
    name = Faker("word")
    description = Faker("text", max_nb_chars=200)
    quantity = 1
    condition = DonationItem.GOOD
    status = DonationItem.PENDING_REVIEW

    @factory.post_generation
    def access_code(self, create, extracted, **kwargs):
        """Generate unique access code if not provided."""
        if not extracted and create:
            from inventory.utils.code_generator import generate_unique_code

            self.access_code = generate_unique_code(DonationItem, "access_code")
            self.save(update_fields=["access_code"])


class TaxReceiptFactory(DjangoModelFactory):
    """Factory for creating TaxReceipt instances."""

    class Meta:
        model = TaxReceipt

    donation = SubFactory(DonationFactory)
    issued_by = SubFactory(UserFactory)
    issued_date = factory.LazyFunction(timezone.now)
    is_copy = False


class DispositionFactory(DjangoModelFactory):
    """Factory for creating Disposition instances."""

    class Meta:
        model = Disposition

    donation_item = SubFactory(DonationItemFactory)
    disposition_type = Disposition.KEPT
    quantity = 1
    disposition_date = factory.LazyFunction(timezone.now)
    disposed_by = SubFactory(UserFactory)
