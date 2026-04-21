"""
Factory-boy factories for the screens app.
"""

from django.contrib.auth.models import Group

import factory

from screens.models import Screen, ScreenContentBlock, ScreenHeartbeat, SystemMessage


class GroupFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Group
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"Screens SIG {n}")


class ScreenFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Screen

    slug = factory.Sequence(lambda n: f"screen-{n}")
    name = factory.Sequence(lambda n: f"Screen {n}")
    description = ""
    sig = factory.SubFactory(GroupFactory)
    is_active = True


class ScreenContentBlockFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ScreenContentBlock

    screen = factory.SubFactory(ScreenFactory)
    block_type = ScreenContentBlock.BLOCK_CUSTOM_TEXT
    title = factory.Sequence(lambda n: f"Block {n}")
    body = factory.Faker("text", max_nb_chars=80)
    order = factory.Sequence(lambda n: n)
    is_enabled = True


class SystemMessageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SystemMessage

    title = factory.Sequence(lambda n: f"System Message {n}")
    body = factory.Faker("sentence")
    level = SystemMessage.LEVEL_INFO
    is_active = True


class ScreenHeartbeatFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ScreenHeartbeat

    screen = factory.SubFactory(ScreenFactory)
    user_agent = "Mozilla/5.0 kiosk"
