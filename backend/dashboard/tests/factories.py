"""
Factory classes for generating test data for dashboard models.
"""

from django.contrib.auth import get_user_model

import factory
from factory.django import DjangoModelFactory

from dashboard.models import DashboardWidget

User = get_user_model()


class UserFactory(DjangoModelFactory):
    """Factory for creating User instances."""

    class Meta:
        model = User
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@example.com")
    password = factory.PostGenerationMethodCall("set_password", "testpass123")


class DashboardWidgetFactory(DjangoModelFactory):
    """Factory for creating DashboardWidget instances."""

    class Meta:
        model = DashboardWidget

    user = factory.SubFactory(UserFactory)
    widget_type = factory.Iterator(
        ["low_stock", "pending_reorders", "asset_problems", "qr_scans", "deliveries"]
    )
    position_x = 0
    position_y = 0
    width = 2
    height = 2
    is_visible = True
    order = 0
    settings = factory.LazyFunction(dict)
