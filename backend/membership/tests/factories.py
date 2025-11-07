"""
Factory classes for generating test data for membership models.
"""

from django.contrib.auth import get_user_model

import factory
from factory import Faker
from factory.django import DjangoModelFactory

from membership.models import Membership

User = get_user_model()


class UserFactory(DjangoModelFactory):
    """Factory for creating User instances."""

    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@example.com")
    first_name = Faker("first_name")
    last_name = Faker("last_name")
    handle = factory.Sequence(lambda n: f"handle{n}")
    active_directory_username = factory.Sequence(lambda n: f"aduser{n}")
    badge_number = factory.Sequence(lambda n: f"BADGE{n:04d}")
    discord_username = factory.Sequence(lambda n: f"discord_user{n}")
    discourse_username = factory.Sequence(lambda n: f"discourse_user{n}")
    is_board_member = False
    is_officer = False
    is_director = False
    is_staff = False
    is_active = True


class MembershipFactory(DjangoModelFactory):
    """Factory for creating Membership instances."""

    class Meta:
        model = Membership

    membership_type = Membership.MEMBERSHIP_TYPE_MONTHLY
    status = Membership.STATUS_ACTIVE
    start_date = factory.LazyFunction(lambda: factory.Faker("date_object").generate({}))
    end_date = None
    notes = Faker("text", max_nb_chars=200)

    @factory.post_generation
    def users(self, create, extracted, **kwargs):
        """Add users to membership after creation."""
        if not create:
            return

        if extracted:
            for user in extracted:
                self.users.add(user)
        else:
            # Create a default user if none provided
            user = UserFactory()
            self.users.add(user)
