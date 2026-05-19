from django.contrib import admin

from .models import Thermostat


@admin.register(Thermostat)
class ThermostatAdmin(admin.ModelAdmin):
    list_display = (
        "label",
        "location",
        "controls_location",
        "controlled_asset",
        "needs_review",
    )
    list_filter = ("needs_review", "location", "controls_location")
    search_fields = ("label", "manufacturer", "model", "notes")
    autocomplete_fields = ("location", "controls_location", "controlled_asset")
    readonly_fields = ("created_at", "updated_at")
