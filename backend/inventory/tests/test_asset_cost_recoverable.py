"""``Asset.is_cost_recoverable`` — the flag, its bulk-set paths and its seed (op-srrv, B5).

The flag decides whether in-house repair cost on an asset is landlord-billable.
Covered here: the model default, the API round-trip, the ``AssetAdmin`` bulk
actions that flag a whole category at once, and the HVAC seed data migration.
The report behaviour it drives lives in ``test_cost_recovery.py``.
"""

from importlib import import_module
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

import pytest
from rest_framework import status

from inventory.admin import AssetAdmin
from inventory.models import Asset, Category
from inventory.tests.factories import AssetFactory, CategoryFactory

User = get_user_model()


@pytest.mark.integration
@pytest.mark.django_db
class TestAssetCostRecoverableField:
    def test_defaults_to_false(self):
        assert AssetFactory().is_cost_recoverable is False

    def test_exposed_and_writable_on_the_api(self, authenticated_client):
        """The web toggle needs the field on the asset payload, both ways."""
        client, _ = authenticated_client
        asset = AssetFactory()

        detail_url = f"/api/inventory/assets/{asset.id}/"
        response = client.get(detail_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_cost_recoverable"] is False

        response = client.patch(detail_url, {"is_cost_recoverable": True}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_cost_recoverable"] is True
        asset.refresh_from_db()
        assert asset.is_cost_recoverable is True


class AssetCostRecoverableAdminActionTest(TestCase):
    """The changelist bulk actions — filter by category, select all, flag."""

    def setUp(self):
        self.admin = AssetAdmin(Asset, AdminSite())
        self.request = RequestFactory().post("/admin/inventory/asset/")
        self.request.user = User.objects.create_superuser(
            username="cr-admin", email="cr-admin@test.com", password="pw"
        )

    def test_mark_cost_recoverable_flags_a_whole_category(self):
        hvac = CategoryFactory(name="HVAC")
        rooftop = AssetFactory(category=hvac)
        split = AssetFactory(category=hvac)
        already = AssetFactory(category=hvac, is_cost_recoverable=True)

        queryset = Asset.objects.filter(category=hvac)
        with patch.object(self.admin, "message_user"):
            self.admin.mark_cost_recoverable(self.request, queryset)

        for asset in (rooftop, split, already):
            asset.refresh_from_db()
            self.assertTrue(asset.is_cost_recoverable)

    def test_mark_cost_recoverable_leaves_unselected_assets_alone(self):
        selected = AssetFactory()
        other = AssetFactory()

        with patch.object(self.admin, "message_user"):
            self.admin.mark_cost_recoverable(self.request, Asset.objects.filter(pk=selected.pk))

        other.refresh_from_db()
        self.assertFalse(other.is_cost_recoverable)

    def test_unmark_cost_recoverable_clears_the_flag(self):
        flagged = AssetFactory(is_cost_recoverable=True)

        with patch.object(self.admin, "message_user"):
            self.admin.unmark_cost_recoverable(self.request, Asset.objects.filter(pk=flagged.pk))

        flagged.refresh_from_db()
        self.assertFalse(flagged.is_cost_recoverable)


class _SeedApps:
    """Minimal ``apps`` shim: the seed only asks for these two models."""

    @staticmethod
    def get_model(app_label, model_name):
        return {"Asset": Asset, "Category": Category}[model_name]


def _run_hvac_seed():
    """Call the 0104 data function directly.

    Running it through the migration executor would have to unapply and reapply
    real schema on the test database; calling the function is the same code with
    none of that, and it is how the other data migrations here are covered.
    """
    module = import_module("inventory.migrations.0104_seed_hvac_cost_recoverable")
    module.seed_hvac_cost_recoverable(_SeedApps, None)


@pytest.mark.django_db
class TestHvacSeedDataMigration:
    def test_flags_assets_in_the_hvac_category(self):
        hvac = CategoryFactory(name="HVAC")
        rooftop = AssetFactory(category=hvac)
        other = AssetFactory(category=CategoryFactory(name="Woodshop"))

        _run_hvac_seed()

        rooftop.refresh_from_db()
        other.refresh_from_db()
        assert rooftop.is_cost_recoverable is True
        assert other.is_cost_recoverable is False

    def test_matches_the_category_name_case_insensitively(self):
        rooftop = AssetFactory(category=CategoryFactory(name="Building HVAC Units"))

        _run_hvac_seed()

        rooftop.refresh_from_db()
        assert rooftop.is_cost_recoverable is True

    def test_no_hvac_category_is_a_no_op(self):
        asset = AssetFactory(category=CategoryFactory(name="Metalshop"))

        _run_hvac_seed()

        asset.refresh_from_db()
        assert asset.is_cost_recoverable is False

    def test_leaves_a_hand_flagged_asset_alone(self):
        """The seed only ever sets the flag — it never clears one."""
        flagged = AssetFactory(category=CategoryFactory(name="Metalshop"), is_cost_recoverable=True)

        _run_hvac_seed()

        flagged.refresh_from_db()
        assert flagged.is_cost_recoverable is True
