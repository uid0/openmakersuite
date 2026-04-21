"""
Unit tests for dashboard models.
"""

import pytest

from dashboard.models import DashboardWidget
from dashboard.tests.factories import DashboardWidgetFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.mark.unit
class TestDashboardWidgetModel:
    """Tests for the DashboardWidget model."""

    def test_widget_creation(self):
        """Test creating a dashboard widget instance."""
        user = UserFactory()
        widget = DashboardWidgetFactory(user=user, widget_type="low_stock")
        assert widget.user == user
        assert widget.widget_type == "low_stock"
        assert widget.position_x == 0
        assert widget.position_y == 0
        assert widget.width == 2
        assert widget.height == 2
        assert widget.is_visible is True

    def test_widget_str(self):
        """Test widget string representation."""
        user = UserFactory(username="testuser")
        widget = DashboardWidgetFactory(user=user, widget_type="low_stock")
        assert "testuser" in str(widget)
        assert "Low Stock" in str(widget)

    def test_create_default_widgets(self):
        """Test creating default widgets for a user."""
        user = UserFactory()
        widgets = DashboardWidget.create_default_widgets(user)

        assert len(widgets) == 5
        widget_types = {w.widget_type for w in widgets}
        assert widget_types == {
            "low_stock",
            "pending_reorders",
            "asset_problems",
            "qr_scans",
            "deliveries",
        }

        # Verify all widgets belong to the user
        for widget in widgets:
            assert widget.user == user
            assert widget.is_visible is True

    def test_widget_unique_together(self):
        """Test that user and widget_type combination is unique."""
        from django.db import IntegrityError

        user = UserFactory()
        DashboardWidgetFactory(user=user, widget_type="low_stock")

        # Creating another widget with same user and type should fail
        with pytest.raises(IntegrityError):
            DashboardWidget.objects.create(
                user=user,
                widget_type="low_stock",
                position_x=0,
                position_y=0,
                width=2,
                height=2,
            )
