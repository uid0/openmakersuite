"""
Factory classes for generating test data for reorder queue models.
"""
import factory
from factory.django import DjangoModelFactory
from factory import Faker, SubFactory
from django.contrib.auth.models import User

from reorder_queue.models import ReorderRequest
from inventory.tests.factories import InventoryItemFactory


class UserFactory(DjangoModelFactory):
    """Factory for creating User instances."""

    class Meta:
        model = User

    username = Faker('user_name')
    email = Faker('email')
    first_name = Faker('first_name')
    last_name = Faker('last_name')

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        """Override the default _create to use create_user."""
        password = kwargs.pop('password', 'testpass123')
        user = model_class._default_manager.create_user(*args, **kwargs)
        user.set_password(password)
        user.save()
        return user


class ReorderRequestFactory(DjangoModelFactory):
    """Factory for creating ReorderRequest instances."""

    class Meta:
        model = ReorderRequest

    item = SubFactory(InventoryItemFactory)
    quantity = Faker('random_int', min=1, max=100)
    status = 'pending'
    priority = factory.Iterator(['low', 'normal', 'high', 'urgent'])
    requested_by = Faker('name')
    request_notes = Faker('text', max_nb_chars=200)
    admin_notes = ''
