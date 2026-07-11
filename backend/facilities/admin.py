from django.contrib import admin

from .models import AssetSiteRequirements


@admin.register(AssetSiteRequirements)
class AssetSiteRequirementsAdmin(admin.ModelAdmin):
    """Standalone admin for the asset site-requirements profile.

    Day-to-day editing happens inline on the asset page (see the
    ``AssetSiteRequirementsInline`` on ``inventory.admin.AssetAdmin``); this
    registration exists so the model is reachable on its own and so
    ``autocomplete_fields=["asset"]`` elsewhere has a searchable target.
    """

    list_display = [
        "asset",
        "breaker",
        "disconnect",
        "needs_compressed_air",
        "generates_heat_or_flame",
        "needs_ventilation",
        "needs_chilling",
        "updated_at",
    ]
    list_filter = [
        "needs_compressed_air",
        "generates_heat_or_flame",
        "needs_ventilation",
        "needs_chilling",
    ]
    search_fields = [
        "asset__name",
        "asset__asset_tag",
        "asset__serial_number",
    ]
    autocomplete_fields = ["asset", "breaker", "disconnect"]
    readonly_fields = ["created_at", "updated_at"]
