"""Tests for the InventorySafetyProfile split (#885).

Covers the pieces that are *new* with the 1:1 profile (the hazmat behaviour that
was already covered by ``test_hazmat.py`` still passes there unchanged):

- the profile model + its ``has_hazmat_data`` lazy predicate,
- the InventoryItem compat accessors (null-safe reads, lazy-creation, write via
  ``objects.create`` kwargs and via attribute assignment),
- the serializer write-through (create-on-first-write, partial-update-safe),
- the data migration round-trip (seed pre-migration → lands in the profile).
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

import pytest

from inventory.models import InventoryItem, InventorySafetyProfile
from inventory.serializers import InventoryItemSerializer
from inventory.tests.factories import CategoryFactory, InventoryItemFactory, LocationFactory


class TestInventorySafetyProfileModel(TestCase):
    """The profile model itself."""

    def test_has_hazmat_data_predicate(self):
        # Empty profile → no data.
        self.assertFalse(InventorySafetyProfile().has_hazmat_data())
        # Any single non-default value → data.
        self.assertTrue(InventorySafetyProfile(is_hazardous=True).has_hazmat_data())
        self.assertTrue(InventorySafetyProfile(msds_url="https://x/m.pdf").has_hazmat_data())
        self.assertTrue(InventorySafetyProfile(nfpa_special_hazards="OX").has_hazmat_data())
        # A zero NFPA rating is real data (not swallowed by ``0 == False``).
        self.assertTrue(InventorySafetyProfile(nfpa_health_hazard=0).has_hazmat_data())
        self.assertTrue(InventorySafetyProfile(nfpa_fire_hazard=0).has_hazmat_data())

    def test_str(self):
        item = InventoryItemFactory(name="Acetone", is_hazardous=True)
        self.assertEqual(str(item.safety_profile), f"Safety profile for {item}")


class TestCompatAccessors(TestCase):
    """The ``item.<hazmat_field>`` read/write compat layer on InventoryItem."""

    def setUp(self):
        self.category = CategoryFactory()
        self.location = LocationFactory()

    def test_reads_defaults_when_no_profile(self):
        """A plain item with no profile reads every field's historical default."""
        item = InventoryItemFactory(category=self.category, location=self.location)

        self.assertIsNone(item._get_safety_profile())
        self.assertFalse(item.is_hazardous)
        self.assertEqual(item.msds_url, "")
        self.assertIsNone(item.msds_file)
        self.assertIsNone(item.nfpa_health_hazard)
        self.assertIsNone(item.nfpa_fire_hazard)
        self.assertIsNone(item.nfpa_instability_hazard)
        self.assertEqual(item.nfpa_special_hazards, "")

    def test_non_hazmat_item_has_no_profile_row(self):
        """Lazy: ordinary items never materialise a safety-profile row."""
        item = InventoryItemFactory(category=self.category, location=self.location)
        self.assertFalse(InventorySafetyProfile.objects.filter(item=item).exists())

    def test_objects_create_with_hazmat_kwargs_persists(self):
        """``InventoryItem.objects.create(is_hazardous=True, ...)`` → profile row."""
        item = InventoryItem.objects.create(
            name="Solvent",
            description="d",
            reorder_quantity=1,
            is_hazardous=True,
            msds_url="https://example.com/msds.pdf",
            nfpa_health_hazard=2,
            nfpa_fire_hazard=3,
            nfpa_instability_hazard=0,
            nfpa_special_hazards="OX",
        )
        self.assertTrue(InventorySafetyProfile.objects.filter(item=item).exists())

        reloaded = InventoryItem.objects.get(pk=item.pk)
        self.assertTrue(reloaded.is_hazardous)
        self.assertEqual(reloaded.msds_url, "https://example.com/msds.pdf")
        self.assertEqual(reloaded.nfpa_health_hazard, 2)
        self.assertEqual(reloaded.nfpa_fire_hazard, 3)
        self.assertEqual(reloaded.nfpa_instability_hazard, 0)
        self.assertEqual(reloaded.nfpa_special_hazards, "OX")

    def test_attribute_assignment_then_save_persists(self):
        """Setting ``item.<field>`` then ``save()`` writes through to the profile."""
        item = InventoryItemFactory(category=self.category, location=self.location)
        item.is_hazardous = True
        item.nfpa_health_hazard = 4
        item.msds_url = "https://example.com/later.pdf"
        # Not persisted until save — but readable in-memory immediately.
        self.assertTrue(item.is_hazardous)
        self.assertFalse(InventorySafetyProfile.objects.filter(item=item).exists())

        item.save()
        reloaded = InventoryItem.objects.get(pk=item.pk)
        self.assertTrue(reloaded.is_hazardous)
        self.assertEqual(reloaded.nfpa_health_hazard, 4)
        self.assertEqual(reloaded.msds_url, "https://example.com/later.pdf")

    def test_assigning_only_defaults_creates_no_row(self):
        """Writing default values to a plain item stays lazy (no profile row)."""
        item = InventoryItemFactory(category=self.category, location=self.location)
        item.is_hazardous = False
        item.save()

        reloaded = InventoryItem.objects.get(pk=item.pk)
        self.assertFalse(reloaded.is_hazardous)
        self.assertFalse(InventorySafetyProfile.objects.filter(item=item).exists())


class TestSerializerWriteThrough(TestCase):
    """InventoryItemSerializer create()/update() upsert the profile."""

    def setUp(self):
        self.category = CategoryFactory()
        self.location = LocationFactory()

    def _create_payload(self, **overrides):
        data = {"name": "Chem", "description": "d", "reorder_quantity": 5}
        data.update(overrides)
        return data

    def test_create_on_first_write(self):
        serializer = InventoryItemSerializer(
            data=self._create_payload(
                is_hazardous=True,
                msds_url="https://example.com/m.pdf",
                nfpa_health_hazard=1,
                nfpa_fire_hazard=2,
                nfpa_instability_hazard=3,
                nfpa_special_hazards="OX",
            )
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        item = serializer.save()

        self.assertTrue(InventorySafetyProfile.objects.filter(item=item).exists())
        data = InventoryItemSerializer(item).data
        self.assertTrue(data["is_hazardous"])
        self.assertEqual(data["msds_url"], "https://example.com/m.pdf")
        self.assertEqual(data["nfpa_fire_hazard"], 2)
        self.assertEqual(data["nfpa_special_hazards"], "OX")
        self.assertEqual(data["hazmat_compliance_status"], "Complete")

    def test_create_non_hazmat_makes_no_profile(self):
        serializer = InventoryItemSerializer(data=self._create_payload(is_hazardous=False))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        item = serializer.save()

        self.assertFalse(InventorySafetyProfile.objects.filter(item=item).exists())
        self.assertFalse(InventoryItemSerializer(item).data["is_hazardous"])

    def test_partial_update_preserves_omitted_fields(self):
        """A PATCH that omits a hazmat field must not wipe it."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="https://example.com/old.pdf",
            nfpa_health_hazard=2,
            nfpa_fire_hazard=3,
            nfpa_instability_hazard=1,
        )
        serializer = InventoryItemSerializer(
            item, data={"msds_url": "https://example.com/new.pdf"}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

        item.refresh_from_db()
        self.assertEqual(item.msds_url, "https://example.com/new.pdf")
        # Omitted fields untouched.
        self.assertTrue(item.is_hazardous)
        self.assertEqual(item.nfpa_health_hazard, 2)
        self.assertEqual(item.nfpa_fire_hazard, 3)
        self.assertEqual(item.nfpa_instability_hazard, 1)

    def test_partial_update_can_clear_a_field(self):
        item = InventoryItemFactory(
            category=self.category, location=self.location, is_hazardous=True, nfpa_fire_hazard=3
        )
        serializer = InventoryItemSerializer(item, data={"nfpa_fire_hazard": None}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

        item.refresh_from_db()
        self.assertIsNone(item.nfpa_fire_hazard)

    def test_serializer_rejects_out_of_range_nfpa(self):
        serializer = InventoryItemSerializer(
            data=self._create_payload(is_hazardous=True, nfpa_health_hazard=5)
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("nfpa_health_hazard", serializer.errors)


START = [("inventory", "0084_inventorysafetyprofile")]
END = [("inventory", "0086_remove_inventoryitem_is_hazardous_and_more")]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    executor.loader.build_graph()
    return executor


@pytest.mark.django_db(transaction=True)
def test_data_migration_copies_hazmat_to_profile():
    """0085 copies each item's hazmat columns onto its InventorySafetyProfile.

    Rewinds to just after 0084 (profile model exists, columns still on the
    item), seeds pre-migration data with the historical models, runs forward
    through 0086 (copy + column removal), and asserts the values landed on the
    profile — with lazy rows (no profile for plain items) and ``nfpa_* == 0``
    treated as real data.
    """
    executor = _migrate(START)
    old_apps = executor.loader.project_state(START).apps
    try:
        InventoryItemOld = old_apps.get_model("inventory", "InventoryItem")

        haz = InventoryItemOld.objects.create(
            name="Acetone",
            sku="MIG-HAZ",
            description="",
            reorder_quantity=1,
            is_hazardous=True,
            msds_url="https://example.com/msds/acetone.pdf",
            nfpa_health_hazard=2,
            nfpa_fire_hazard=3,
            nfpa_instability_hazard=0,
            nfpa_special_hazards="OX",
        )
        # Flagged non-hazardous but carries a zero NFPA rating → still real data.
        zero = InventoryItemOld.objects.create(
            name="Water",
            sku="MIG-ZERO",
            description="",
            reorder_quantity=1,
            is_hazardous=False,
            nfpa_health_hazard=0,
        )
        # Plain item → no profile row.
        plain = InventoryItemOld.objects.create(
            name="Widget", sku="MIG-PLAIN", description="", reorder_quantity=1
        )

        executor2 = _migrate(END)
        new_apps = executor2.loader.project_state(END).apps
        Profile = new_apps.get_model("inventory", "InventorySafetyProfile")

        p = Profile.objects.get(item_id=haz.id)
        assert p.is_hazardous is True
        assert p.msds_url == "https://example.com/msds/acetone.pdf"
        assert p.nfpa_health_hazard == 2
        assert p.nfpa_fire_hazard == 3
        assert p.nfpa_instability_hazard == 0
        assert p.nfpa_special_hazards == "OX"

        pz = Profile.objects.get(item_id=zero.id)
        assert pz.is_hazardous is False
        assert pz.nfpa_health_hazard == 0

        assert not Profile.objects.filter(item_id=plain.id).exists()
    finally:
        _migrate(executor.loader.graph.leaf_nodes())
