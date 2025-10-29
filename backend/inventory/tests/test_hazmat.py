"""
Tests for hazardous materials functionality.

This test suite covers:
- Hazmat flag and NFPA Fire Diamond data handling  
- Validation of NFPA ratings (0-4 scale)
- Compliance status calculations
- Admin interface functionality
- API serialization of hazmat data
- Edge cases and error handling
"""

from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from inventory.admin import InventoryItemAdmin
from inventory.models import InventoryItem
from inventory.serializers import InventoryItemSerializer
from inventory.tests.factories import CategoryFactory, InventoryItemFactory, LocationFactory

User = get_user_model()


class TestHazardousMaterialFields(TestCase):
    """Test hazardous material model fields and validation."""

    def setUp(self):
        """Set up test data."""
        self.category = CategoryFactory()
        self.location = LocationFactory()

    def test_non_hazardous_item_defaults(self):
        """Test that non-hazardous items have proper defaults."""
        item = InventoryItemFactory(
            category=self.category, location=self.location, is_hazardous=False
        )

        # Check defaults
        self.assertFalse(item.is_hazardous)
        self.assertEqual(item.msds_url, "")
        self.assertIsNone(item.nfpa_health_hazard)
        self.assertIsNone(item.nfpa_fire_hazard)
        self.assertIsNone(item.nfpa_instability_hazard)
        self.assertEqual(item.nfpa_special_hazards, "")

    def test_hazardous_item_with_complete_data(self):
        """Test hazardous item with complete NFPA data."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="https://example.com/msds/acetone.pdf",
            nfpa_health_hazard=2,
            nfpa_fire_hazard=3,
            nfpa_instability_hazard=0,
            nfpa_special_hazards="OX",
        )

        self.assertTrue(item.is_hazardous)
        self.assertEqual(item.msds_url, "https://example.com/msds/acetone.pdf")
        self.assertEqual(item.nfpa_health_hazard, 2)
        self.assertEqual(item.nfpa_fire_hazard, 3)
        self.assertEqual(item.nfpa_instability_hazard, 0)
        self.assertEqual(item.nfpa_special_hazards, "OX")

    def test_nfpa_rating_validation_valid_values(self):
        """Test that NFPA ratings accept valid values (0-4)."""
        for rating in [0, 1, 2, 3, 4]:
            item = InventoryItemFactory(
                category=self.category,
                location=self.location,
                is_hazardous=True,
                nfpa_health_hazard=rating,
                nfpa_fire_hazard=rating,
                nfpa_instability_hazard=rating,
            )
            # Should save without validation errors
            item.full_clean()
            item.save()

            # Verify values are saved correctly
            reloaded = InventoryItem.objects.get(pk=item.pk)
            self.assertEqual(reloaded.nfpa_health_hazard, rating)
            self.assertEqual(reloaded.nfpa_fire_hazard, rating)
            self.assertEqual(reloaded.nfpa_instability_hazard, rating)

    def test_nfpa_rating_validation_invalid_values(self):
        """Test that NFPA ratings reject invalid values (>4)."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            nfpa_health_hazard=5,  # Invalid - exceeds maximum
        )

        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_msds_url_validation(self):
        """Test MSDS URL field validation."""
        # Valid URL
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="https://example.com/msds/chemical.pdf",
        )
        item.full_clean()  # Should not raise ValidationError

        # Invalid URL format should raise ValidationError
        item.msds_url = "not-a-valid-url"
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_special_hazards_field_length(self):
        """Test that special hazards field respects max length."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            nfpa_special_hazards="W,OX,COR,ACID,BIO",  # Valid length
        )
        item.full_clean()  # Should not raise ValidationError

        # Test field that's too long
        item.nfpa_special_hazards = "W,OX,COR,ACID,BIO,POI,RAD,ALK,SUPER_LONG_TEXT"  # Too long
        with self.assertRaises(ValidationError):
            item.full_clean()


class TestHazmatCalculatedProperties(TestCase):
    """Test calculated properties for hazardous materials."""

    def setUp(self):
        """Set up test data."""
        self.category = CategoryFactory()
        self.location = LocationFactory()

    def test_nfpa_fire_diamond_display_non_hazardous(self):
        """Test NFPA display for non-hazardous items."""
        item = InventoryItemFactory(
            category=self.category, location=self.location, is_hazardous=False
        )

        self.assertEqual(item.nfpa_fire_diamond_display, "Not Hazardous")

    def test_nfpa_fire_diamond_display_complete_data(self):
        """Test NFPA display with complete data."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            nfpa_health_hazard=2,
            nfpa_fire_hazard=3,
            nfpa_instability_hazard=1,
            nfpa_special_hazards="W,OX",
        )

        expected = "Health: 2 | Fire: 3 | Instability: 1 | Special: W,OX"
        self.assertEqual(item.nfpa_fire_diamond_display, expected)

    def test_nfpa_fire_diamond_display_partial_data(self):
        """Test NFPA display with partial data."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            nfpa_health_hazard=2,
            nfpa_fire_hazard=None,
            nfpa_instability_hazard=1,
            nfpa_special_hazards="",
        )

        expected = "Health: 2 | Instability: 1"
        self.assertEqual(item.nfpa_fire_diamond_display, expected)

    def test_nfpa_fire_diamond_display_no_data(self):
        """Test NFPA display with no rating data."""
        item = InventoryItemFactory(
            category=self.category, location=self.location, is_hazardous=True
        )

        expected = "NFPA ratings not specified"
        self.assertEqual(item.nfpa_fire_diamond_display, expected)

    def test_has_complete_nfpa_data_non_hazardous(self):
        """Test complete NFPA data check for non-hazardous items."""
        item = InventoryItemFactory(
            category=self.category, location=self.location, is_hazardous=False
        )

        # Non-hazardous items should always return True
        self.assertTrue(item.has_complete_nfpa_data)

    def test_has_complete_nfpa_data_complete(self):
        """Test complete NFPA data check with all required fields."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            nfpa_health_hazard=2,
            nfpa_fire_hazard=3,
            nfpa_instability_hazard=1,
        )

        self.assertTrue(item.has_complete_nfpa_data)

    def test_has_complete_nfpa_data_incomplete(self):
        """Test complete NFPA data check with missing fields."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            nfpa_health_hazard=2,
            nfpa_fire_hazard=None,  # Missing
            nfpa_instability_hazard=1,
        )

        self.assertFalse(item.has_complete_nfpa_data)

    def test_hazmat_compliance_status_non_hazardous(self):
        """Test compliance status for non-hazardous items."""
        item = InventoryItemFactory(
            category=self.category, location=self.location, is_hazardous=False
        )

        self.assertEqual(item.hazmat_compliance_status, "Not Applicable - Not Hazardous")

    def test_hazmat_compliance_status_complete(self):
        """Test compliance status for complete hazmat data."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="https://example.com/msds.pdf",
            nfpa_health_hazard=2,
            nfpa_fire_hazard=3,
            nfpa_instability_hazard=1,
        )

        self.assertEqual(item.hazmat_compliance_status, "Complete")

    def test_hazmat_compliance_status_missing_msds(self):
        """Test compliance status with missing MSDS URL."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="",  # Missing
            nfpa_health_hazard=2,
            nfpa_fire_hazard=3,
            nfpa_instability_hazard=1,
        )

        expected = "Incomplete - Missing: MSDS/SDS URL"
        self.assertEqual(item.hazmat_compliance_status, expected)

    def test_hazmat_compliance_status_missing_nfpa(self):
        """Test compliance status with missing NFPA data."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="https://example.com/msds.pdf",
            nfpa_health_hazard=2,
            nfpa_fire_hazard=None,  # Missing
            nfpa_instability_hazard=None,  # Missing
        )

        expected = "Incomplete - Missing: NFPA (Fire, Instability)"
        self.assertEqual(item.hazmat_compliance_status, expected)

    def test_hazmat_compliance_status_missing_all(self):
        """Test compliance status with missing MSDS and NFPA data."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="",
            nfpa_health_hazard=None,
            nfpa_fire_hazard=None,
            nfpa_instability_hazard=None,
        )

        status = item.hazmat_compliance_status
        self.assertIn("Incomplete - Missing:", status)
        self.assertIn("MSDS/SDS URL", status)
        self.assertIn("NFPA (Health, Fire, Instability)", status)

    def test_get_nfpa_hazard_level_display(self):
        """Test NFPA hazard level display method."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            nfpa_health_hazard=0,
            nfpa_fire_hazard=1,
            nfpa_instability_hazard=4,
        )

        self.assertEqual(item.get_nfpa_hazard_level_display("health"), "0 - Minimal")
        self.assertEqual(item.get_nfpa_hazard_level_display("fire"), "1 - Slight")
        self.assertEqual(item.get_nfpa_hazard_level_display("instability"), "4 - Extreme")
        self.assertEqual(item.get_nfpa_hazard_level_display("unknown"), "Not specified")


class TestHazmatAdminInterface(TestCase):
    """Test admin interface functionality for hazmat."""

    def setUp(self):
        """Set up test data."""
        self.site = AdminSite()
        self.admin = InventoryItemAdmin(InventoryItem, self.site)

        self.category = CategoryFactory()
        self.location = LocationFactory()

        # Create admin user
        self.admin_user = User.objects.create_superuser(
            username="admin", email="admin@test.com", password="adminpass"
        )

    def test_hazmat_fieldset_exists(self):
        """Test that hazmat fieldset is included in admin."""
        fieldsets = self.admin.fieldsets

        # Find Hazardous Materials fieldset
        hazmat_fieldset = None
        for name, options in fieldsets:
            if name == "Hazardous Materials":
                hazmat_fieldset = options
                break

        self.assertIsNotNone(hazmat_fieldset)

        # Check that hazmat fields are included
        hazmat_fields = hazmat_fieldset["fields"]
        self.assertIn("is_hazardous", hazmat_fields)
        self.assertIn("msds_url", hazmat_fields)
        self.assertIn("nfpa_health_hazard", hazmat_fields)
        self.assertIn("nfpa_fire_hazard", hazmat_fields)
        self.assertIn("nfpa_instability_hazard", hazmat_fields)
        self.assertIn("nfpa_special_hazards", hazmat_fields)
        self.assertIn("nfpa_fire_diamond_display", hazmat_fields)
        self.assertIn("hazmat_compliance_status", hazmat_fields)

    def test_hazmat_status_icon_non_hazardous(self):
        """Test hazmat status icon for non-hazardous items."""
        item = InventoryItemFactory(
            category=self.category, location=self.location, is_hazardous=False
        )

        result = self.admin.hazmat_status_icon(item)
        self.assertIn("✅", result)
        self.assertIn("Not Hazardous", result)

    def test_hazmat_status_icon_complete(self):
        """Test hazmat status icon for complete hazmat data."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="https://example.com/msds.pdf",
            nfpa_health_hazard=2,
            nfpa_fire_hazard=3,
            nfpa_instability_hazard=1,
        )

        result = self.admin.hazmat_status_icon(item)
        self.assertIn("⚠️ HAZMAT", result)
        self.assertIn("Complete", result)

    def test_hazmat_status_icon_incomplete(self):
        """Test hazmat status icon for incomplete hazmat data."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="",  # Missing MSDS
            nfpa_health_hazard=2,
        )

        result = self.admin.hazmat_status_icon(item)
        self.assertIn("❌ INCOMPLETE", result)
        self.assertIn("Incomplete", result)

    def test_nfpa_fire_diamond_display_admin_method(self):
        """Test admin method for displaying NFPA Fire Diamond."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            nfpa_health_hazard=2,
            nfpa_fire_hazard=3,
            nfpa_instability_hazard=1,
            nfpa_special_hazards="W",
        )

        result = self.admin.nfpa_fire_diamond_display(item)
        expected = "Health: 2 | Fire: 3 | Instability: 1 | Special: W"
        self.assertEqual(result, expected)

    def test_hazmat_compliance_status_admin_method(self):
        """Test admin method for displaying compliance status."""
        # Complete status
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="https://example.com/msds.pdf",
            nfpa_health_hazard=2,
            nfpa_fire_hazard=3,
            nfpa_instability_hazard=1,
        )

        result = self.admin.hazmat_compliance_status(item)
        self.assertIn("Complete", result)
        self.assertIn("color: #28a745", result)  # Green color for complete

    def test_hazmat_filter_in_list_filter(self):
        """Test that hazmat filter is available in admin list."""
        list_filters = self.admin.list_filter
        self.assertIn("is_hazardous", list_filters)

    def test_hazmat_readonly_fields(self):
        """Test that calculated hazmat fields are readonly."""
        readonly_fields = self.admin.readonly_fields
        self.assertIn("nfpa_fire_diamond_display", readonly_fields)
        self.assertIn("hazmat_compliance_status", readonly_fields)


class TestHazmatAPISerialization(TestCase):
    """Test API serialization of hazmat data."""

    def setUp(self):
        """Set up test data."""
        self.category = CategoryFactory()
        self.location = LocationFactory()

    def test_non_hazardous_item_serialization(self):
        """Test API serialization for non-hazardous items."""
        item = InventoryItemFactory(
            category=self.category, location=self.location, is_hazardous=False
        )

        serializer = InventoryItemSerializer(item)
        data = serializer.data

        # Check hazmat fields
        self.assertFalse(data["is_hazardous"])
        self.assertEqual(data["msds_url"], "")
        self.assertIsNone(data["nfpa_health_hazard"])
        self.assertIsNone(data["nfpa_fire_hazard"])
        self.assertIsNone(data["nfpa_instability_hazard"])
        self.assertEqual(data["nfpa_special_hazards"], "")

        # Check calculated fields
        self.assertEqual(data["nfpa_fire_diamond_display"], "Not Hazardous")
        self.assertEqual(data["hazmat_compliance_status"], "Not Applicable - Not Hazardous")
        self.assertTrue(data["has_complete_nfpa_data"])

    def test_hazardous_item_serialization(self):
        """Test API serialization for hazardous items."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="https://example.com/msds/acetone.pdf",
            nfpa_health_hazard=2,
            nfpa_fire_hazard=3,
            nfpa_instability_hazard=0,
            nfpa_special_hazards="OX",
        )

        serializer = InventoryItemSerializer(item)
        data = serializer.data

        # Check hazmat fields
        self.assertTrue(data["is_hazardous"])
        self.assertEqual(data["msds_url"], "https://example.com/msds/acetone.pdf")
        self.assertEqual(data["nfpa_health_hazard"], 2)
        self.assertEqual(data["nfpa_fire_hazard"], 3)
        self.assertEqual(data["nfpa_instability_hazard"], 0)
        self.assertEqual(data["nfpa_special_hazards"], "OX")

        # Check calculated fields
        expected_display = "Health: 2 | Fire: 3 | Instability: 0 | Special: OX"
        self.assertEqual(data["nfpa_fire_diamond_display"], expected_display)
        self.assertEqual(data["hazmat_compliance_status"], "Complete")
        self.assertTrue(data["has_complete_nfpa_data"])

    def test_incomplete_hazmat_serialization(self):
        """Test API serialization for incomplete hazmat data."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="",  # Missing MSDS
            nfpa_health_hazard=2,
            nfpa_fire_hazard=None,  # Missing
            nfpa_instability_hazard=1,
        )

        serializer = InventoryItemSerializer(item)
        data = serializer.data

        # Check that incomplete data is properly represented
        self.assertTrue(data["is_hazardous"])
        self.assertEqual(data["msds_url"], "")
        self.assertEqual(data["nfpa_health_hazard"], 2)
        self.assertIsNone(data["nfpa_fire_hazard"])
        self.assertEqual(data["nfpa_instability_hazard"], 1)

        # Check calculated fields reflect incomplete state
        self.assertIn("Missing", data["hazmat_compliance_status"])
        self.assertFalse(data["has_complete_nfpa_data"])


class TestHazmatEdgeCases(TestCase):
    """Test edge cases and error conditions for hazmat functionality."""

    def setUp(self):
        """Set up test data."""
        self.category = CategoryFactory()
        self.location = LocationFactory()

    def test_all_nfpa_ratings_zero(self):
        """Test item with all NFPA ratings as zero (minimal hazard)."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="https://example.com/msds/water.pdf",
            nfpa_health_hazard=0,
            nfpa_fire_hazard=0,
            nfpa_instability_hazard=0,
            nfpa_special_hazards="",
        )

        # Should still be considered complete
        self.assertTrue(item.has_complete_nfpa_data)
        self.assertEqual(item.hazmat_compliance_status, "Complete")

        # Display should show zeros
        expected_display = "Health: 0 | Fire: 0 | Instability: 0"
        self.assertEqual(item.nfpa_fire_diamond_display, expected_display)

    def test_maximum_nfpa_ratings(self):
        """Test item with maximum NFPA ratings (4)."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="https://example.com/msds/dangerous.pdf",
            nfpa_health_hazard=4,
            nfpa_fire_hazard=4,
            nfpa_instability_hazard=4,
            nfpa_special_hazards="W,OX,COR",
        )

        # Verify display formats correctly
        expected_display = "Health: 4 | Fire: 4 | Instability: 4 | Special: W,OX,COR"
        self.assertEqual(item.nfpa_fire_diamond_display, expected_display)

        # Verify hazard level displays
        self.assertEqual(item.get_nfpa_hazard_level_display("health"), "4 - Extreme")
        self.assertEqual(item.get_nfpa_hazard_level_display("fire"), "4 - Extreme")
        self.assertEqual(item.get_nfpa_hazard_level_display("instability"), "4 - Extreme")

    def test_special_hazards_with_spaces_and_commas(self):
        """Test special hazards field with various formatting."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            nfpa_special_hazards="W, OX, COR, ACID",
        )

        # Should preserve exactly as entered
        self.assertEqual(item.nfpa_special_hazards, "W, OX, COR, ACID")
        display = item.nfpa_fire_diamond_display
        self.assertIn("Special: W, OX, COR, ACID", display)

    def test_data_persistence_through_save_reload(self):
        """Test that hazmat data persists through save/reload cycles."""
        item = InventoryItemFactory(
            category=self.category,
            location=self.location,
            is_hazardous=True,
            msds_url="https://example.com/msds/test.pdf",
            nfpa_health_hazard=2,
            nfpa_fire_hazard=1,
            nfpa_instability_hazard=3,
            nfpa_special_hazards="W,POI",
        )

        # Save and reload
        item.save()
        reloaded = InventoryItem.objects.get(pk=item.pk)

        # Verify all data persisted correctly
        self.assertTrue(reloaded.is_hazardous)
        self.assertEqual(reloaded.msds_url, "https://example.com/msds/test.pdf")
        self.assertEqual(reloaded.nfpa_health_hazard, 2)
        self.assertEqual(reloaded.nfpa_fire_hazard, 1)
        self.assertEqual(reloaded.nfpa_instability_hazard, 3)
        self.assertEqual(reloaded.nfpa_special_hazards, "W,POI")

        # Verify calculated properties work correctly
        self.assertTrue(reloaded.has_complete_nfpa_data)
        self.assertEqual(reloaded.hazmat_compliance_status, "Complete")
