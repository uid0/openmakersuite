"""Admin wiring tests (oms-06x AC-3)."""

from django.contrib import admin

from devices.admin import InterfaceInline, NetworkDropAdmin
from devices.models import Interface, NetworkDrop
from inventory.admin import AssetAdmin
from inventory.models import Asset


def test_asset_admin_includes_interface_inline():
    assert any(issubclass(inline, InterfaceInline) for inline in AssetAdmin.inlines)


def test_models_are_registered_in_admin():
    assert Interface in admin.site._registry
    assert NetworkDrop in admin.site._registry
    assert isinstance(admin.site._registry[NetworkDrop], NetworkDropAdmin)


def test_asset_admin_still_registered():
    # Sanity: appending the inline did not unregister the Asset model.
    assert Asset in admin.site._registry
