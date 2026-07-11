"""Admin wiring for the #880 refactor.

The moved fields must be editable on the asset page via a StackedInline, the
standalone AssetSiteRequirements admin must be registered, and the old
"Operational Requirements" fieldset must no longer list the relocated fields.
"""

from django.contrib import admin

import facilities.admin  # noqa: F401  (ensure registration)
import inventory.admin  # noqa: F401  (ensure registration)
from facilities.models import AssetSiteRequirements
from inventory.admin import AssetSiteRequirementsInline
from inventory.models import Asset


def test_site_requirements_admin_registered():
    assert AssetSiteRequirements in admin.site._registry


def test_inline_present_on_asset_admin():
    asset_admin = admin.site._registry[Asset]
    assert AssetSiteRequirementsInline in asset_admin.inlines
    assert AssetSiteRequirements in [inline.model for inline in asset_admin.inlines]


def test_moved_fields_removed_from_asset_fieldsets():
    asset_admin = admin.site._registry[Asset]
    listed = []
    for _label, opts in asset_admin.fieldsets:
        listed.extend(opts.get("fields", ()))
    for moved in (
        "circuit",
        "needs_compressed_air",
        "needs_ventilation",
        "breaker",
        "disconnect",
    ):
        assert moved not in listed
