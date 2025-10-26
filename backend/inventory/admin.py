"""
Admin configuration for inventory app.
"""

from django.contrib import admin

from .models import Category, InventoryItem, ItemSupplier, Location, Supplier, UsageLog


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "supplier_type", "website"]
    list_filter = ["supplier_type"]
    search_fields = ["name"]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "parent", "slug"]
    list_filter = ["parent"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "description"]


class ItemSupplierInline(admin.TabularInline):
    model = ItemSupplier
    extra = 1
    fields = [
        "supplier",
        "supplier_sku",
        "supplier_url",
        "package_upc",
        "unit_upc",
        "quantity_per_package",
        "package_cost",
        "unit_cost_display",
        "average_lead_time",
        "is_primary",
        "is_active",
    ]
    readonly_fields = ["unit_cost_display"]

    @admin.display(description="Unit cost (calculated)")
    def unit_cost_display(self, obj):
        """Readable representation of the cost per individual unit."""

        if not obj or obj.unit_cost is None:
            return "—"
        return f"${obj.unit_cost:.4f}"


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "sku",
        "category",
        "location",
        "current_stock",
        "minimum_stock",
        "needs_reorder",
        "is_active",
    ]
    list_filter = ["category", "location", "is_active"]
    search_fields = ["name", "sku", "description"]
    readonly_fields = ["id", "sku", "created_at", "updated_at", "qr_code", "thumbnail"]
    inlines = [ItemSupplierInline]
    fieldsets = (
        (
            "Basic Information",
            {"fields": ("name", "description", "sku", "category", "location", "is_active")},
        ),
        ("Images", {"fields": ("image", "image_url", "thumbnail", "qr_code")}),
        ("Stock Information", {"fields": ("current_stock", "minimum_stock", "reorder_quantity")}),
        (
            "Additional Information",
            {"fields": ("notes", "created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )


@admin.register(UsageLog)
class UsageLogAdmin(admin.ModelAdmin):
    list_display = ["item", "quantity_used", "usage_date"]
    list_filter = ["usage_date", "item"]
    search_fields = ["item__name", "notes"]
    readonly_fields = ["usage_date"]
    date_hierarchy = "usage_date"
