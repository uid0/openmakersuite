"""Model tests for location_checkins.

Currently covers the LocationTask ``origin`` / ``origin_type`` read accessor —
the at-most-one (zero-or-one) typed-target variant added in #884.
"""

import pytest

from inventory.tests.factories import LocationFactory
from location_checkins.models import LocationFeedback, LocationTask, SecurityReport

pytestmark = pytest.mark.django_db


@pytest.mark.unit
class TestLocationTaskOrigin:
    """LocationTask.origin / .origin_type (#884)."""

    def test_manual_task_has_no_origin(self):
        """A manually created task has neither origin FK -> origin is None."""
        loc = LocationFactory()
        task = LocationTask.objects.create(location=loc, title="t", description="d")
        assert task.origin is None
        assert task.origin_type is None

    def test_feedback_origin(self):
        loc = LocationFactory()
        feedback = LocationFeedback.objects.create(
            location=loc, feedback_type="negative", message="dirty"
        )
        task = LocationTask.objects.create(
            location=loc, title="t", description="d", created_from_feedback=feedback
        )
        assert task.origin == feedback
        assert task.origin_type == "feedback"

    def test_security_report_origin(self):
        loc = LocationFactory()
        report = SecurityReport.objects.create(location=loc)
        task = LocationTask.objects.create(
            location=loc,
            title="t",
            description="d",
            created_from_security_report=report,
        )
        assert task.origin == report
        assert task.origin_type == "security_report"
