"""
Unit tests for reorder queue models.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

import pytest
from freezegun import freeze_time

from inventory.tests.factories import InventoryItemFactory
from reorder_queue.models import ReorderRequest
from reorder_queue.tests.factories import ReorderRequestFactory, UserFactory


@pytest.mark.unit
class TestReorderRequestModel:
    """Tests for the ReorderRequest model."""

    def test_reorder_request_creation(self):
        """Test creating a reorder request."""
        item = InventoryItemFactory(name="Test Item")
        request = ReorderRequestFactory(item=item, quantity=25, requested_by="John Doe")

        assert request.item == item
        assert request.quantity == 25
        assert request.requested_by == "John Doe"
        assert request.status == "pending"
        assert str(request).startswith("Test Item")

    def test_estimated_cost_calculation(self):
        """Test estimated_cost property calculates correctly."""
        item = InventoryItemFactory(unit_cost=Decimal("10.50"))
        request = ReorderRequestFactory(item=item, quantity=10)

        assert request.estimated_cost == Decimal("105.00")

    def test_estimated_cost_without_unit_cost(self):
        """Test estimated_cost returns None when item has no unit_cost."""
        item = InventoryItemFactory(unit_cost=None)
        request = ReorderRequestFactory(item=item, quantity=10)

        assert request.estimated_cost is None

    @freeze_time("2024-01-15 12:00:00")
    def test_days_pending_calculation(self):
        """Test days_pending property calculates correctly."""
        # Create request 5 days ago
        with freeze_time("2024-01-10 12:00:00"):
            request = ReorderRequestFactory(status="pending")

        assert request.days_pending == 5

    def test_days_pending_for_non_pending_status(self):
        """Test days_pending returns 0 for non-pending requests."""
        request = ReorderRequestFactory(status="approved")
        assert request.days_pending == 0

    def test_request_ordering(self):
        """Test requests are ordered by requested_at descending."""
        req1 = ReorderRequestFactory()
        req2 = ReorderRequestFactory()

        requests = ReorderRequest.objects.all()
        assert requests[0] == req2  # Most recent first
        assert requests[1] == req1

    def test_status_choices(self):
        """Test all status choices are valid."""
        statuses = ["pending", "approved", "ordered", "received", "cancelled"]

        for status_choice in statuses:
            request = ReorderRequestFactory(status=status_choice)
            assert request.status == status_choice

    def test_priority_choices(self):
        """Test all priority choices are valid."""
        priorities = ["low", "normal", "high", "urgent"]

        for priority in priorities:
            request = ReorderRequestFactory(priority=priority)
            assert request.priority == priority

    def test_reviewed_by_relationship(self):
        """Test reviewed_by relationship with User."""
        user = UserFactory(username="admin")
        request = ReorderRequestFactory(
            status="approved", reviewed_by=user, reviewed_at=timezone.now()
        )

        assert request.reviewed_by == user
        assert request.reviewed_at is not None

    def test_order_tracking_fields(self):
        """Test order tracking fields."""
        request = ReorderRequestFactory(
            status="ordered",
            ordered_at=timezone.now(),
            order_number="ORD-12345",
            actual_cost=Decimal("125.50"),
        )

        assert request.order_number == "ORD-12345"
        assert request.actual_cost == Decimal("125.50")
        assert request.ordered_at is not None

    def test_delivery_tracking_fields(self):
        """Test delivery tracking fields."""
        estimated = timezone.now().date() + timedelta(days=7)
        actual = timezone.now().date() + timedelta(days=5)

        request = ReorderRequestFactory(
            status="received", estimated_delivery=estimated, actual_delivery=actual
        )

        assert request.estimated_delivery == estimated
        assert request.actual_delivery == actual
