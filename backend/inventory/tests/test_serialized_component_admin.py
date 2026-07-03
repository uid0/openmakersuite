"""Admin smoke tests for the serialized-component surface.

Verifies that the SerializedComponent / ComponentUsageEvent registrations and
the InventoryItem serial-tracking fieldset + inline load without error for a
staff user (changelist / add / change views return the expected status).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from inventory.models import ComponentUsageEvent, SerializedComponent
from inventory.tests.factories import InventoryItemFactory, SerializedComponentFactory

User = get_user_model()


class SerializedComponentAdminSmokeTest(TestCase):
    """Changelist / add / change views load for the new admin registrations."""

    @classmethod
    def setUpTestData(cls):
        cls.item = InventoryItemFactory(is_serialized=True)
        cls.component = SerializedComponentFactory(item=cls.item)
        cls.event = ComponentUsageEvent.objects.create(
            component=cls.component,
            action=SerializedComponent.ACTION_RECEIVE,
        )

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="serial-admin",
            email="serial-admin@test.com",
            password="pw",
        )
        self.client.force_login(self.admin_user)

    def test_inventory_item_change_view_has_serial_fields_and_inline(self):
        """Item change page renders the serial-tracking fieldset + inline."""
        url = reverse("admin:inventory_inventoryitem_change", args=[self.item.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "is_serialized")
        self.assertContains(response, "serial_tracking_mode")
        # Inline formset for serial-numbered units is present on the page.
        self.assertContains(response, "serialized_components")

    def test_serialized_component_changelist_loads(self):
        url = reverse("admin:inventory_serializedcomponent_changelist")
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_serialized_component_add_view_loads(self):
        url = reverse("admin:inventory_serializedcomponent_add")
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_serialized_component_change_view_loads(self):
        url = reverse("admin:inventory_serializedcomponent_change", args=[self.component.pk])
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_component_usage_event_changelist_loads(self):
        url = reverse("admin:inventory_componentusageevent_changelist")
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_component_usage_event_is_read_only(self):
        """Audit log disallows adds but still renders its view-only detail page."""
        add_url = reverse("admin:inventory_componentusageevent_add")
        self.assertEqual(self.client.get(add_url).status_code, 403)
        change_url = reverse("admin:inventory_componentusageevent_change", args=[self.event.pk])
        self.assertEqual(self.client.get(change_url).status_code, 200)
