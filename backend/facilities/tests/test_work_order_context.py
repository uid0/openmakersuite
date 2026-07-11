"""work_order_context surfaces the site-requirements safety guidance (#880)."""

import pytest

from inventory.services.work_order_context import build_electrical_context
from inventory.tests.factories import AssetFactory

pytestmark = pytest.mark.django_db


def test_safety_notes_surface_in_electrical_context():
    asset = AssetFactory(
        special_requirements="Requires 220V single-phase",
        work_safety_notes="Bleed the compressed-air line and lock out at Panel A",
    )
    ctx = build_electrical_context(asset)
    rows = {r[0]: r[1] for r in ctx["rows"]}
    assert rows["Special Requirements"] == "Requires 220V single-phase"
    assert rows["Crew Should Know"].startswith("Bleed the compressed-air line")
    assert ctx["is_empty"] is False


def test_no_safety_notes_no_rows():
    asset = AssetFactory()
    labels = [r[0] for r in build_electrical_context(asset)["rows"]]
    assert "Special Requirements" not in labels
    assert "Crew Should Know" not in labels
