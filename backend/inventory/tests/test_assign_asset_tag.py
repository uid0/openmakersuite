"""Tests for :func:`inventory.services.asset_tag_id.assign_asset_tag` — the
empty-guard + collision-retry allocation extracted from ``Asset.save()`` (gh #887).
"""

from datetime import date
from unittest.mock import patch

from django.db import IntegrityError

import pytest

from inventory.services.asset_tag_id import assign_asset_tag, validate_asset_tag
from inventory.tests.factories import AssetFactory

pytestmark = pytest.mark.django_db


class TestAssignAssetTag:
    def test_assigns_valid_tag_when_missing(self):
        asset = AssetFactory.build(asset_tag="", date_received=date(2026, 6, 1))

        tag = assign_asset_tag(asset)

        assert tag == asset.asset_tag
        assert validate_asset_tag(tag)
        assert tag.startswith("DMS-26")

    def test_preserves_existing_tag(self):
        asset = AssetFactory.build(asset_tag="AST-ALREADY-SET")

        result = assign_asset_tag(asset)

        assert result == "AST-ALREADY-SET"
        assert asset.asset_tag == "AST-ALREADY-SET"

    def test_uses_current_year_when_no_date_received(self):
        asset = AssetFactory.build(asset_tag="", date_received=None)

        tag = assign_asset_tag(asset)

        assert validate_asset_tag(tag)

    def test_retries_then_succeeds_on_collision(self):
        # A real asset already holds the first candidate tag, so the DB
        # ``exists()`` check forces one retry before the second is accepted.
        AssetFactory(asset_tag="DMS-26A00111")
        asset = AssetFactory.build(asset_tag="", date_received=date(2026, 6, 1))

        with patch(
            "inventory.services.asset_tag_id.generate_asset_tag",
            side_effect=["DMS-26A00111", "DMS-26A00222"],
        ):
            tag = assign_asset_tag(asset)

        assert tag == "DMS-26A00222"

    def test_raises_after_exhausting_retries(self):
        # Every generated candidate collides with an existing tag.
        AssetFactory(asset_tag="DMS-26A00111")
        asset = AssetFactory.build(asset_tag="", date_received=date(2026, 6, 1))

        with patch(
            "inventory.services.asset_tag_id.generate_asset_tag",
            return_value="DMS-26A00111",
        ):
            with pytest.raises(IntegrityError):
                assign_asset_tag(asset, max_retries=3)

    def test_save_generates_tag_on_create(self):
        asset = AssetFactory(asset_tag="")

        assert asset.asset_tag
        assert validate_asset_tag(asset.asset_tag)
