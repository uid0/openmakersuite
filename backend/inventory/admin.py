"""
Admin configuration for inventory app.
"""

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import Category, InventoryItem, ItemSupplier, Location, Supplier, UsageLog, PriceHistory


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "supplier_type", "website"]
    list_filter = ["supplier_type"]
    search_fields = ["name"]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "parent", "slug", "color"]
    list_filter = ["parent"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}
    fields = ["name", "slug", "description", "color", "parent"]


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
        "package_dimensions_display",
        "package_cost",
        "unit_cost_display",
        "average_lead_time",
        "is_primary",
        "is_active",
    ]
    readonly_fields = ["unit_cost_display", "package_dimensions_display"]

    @admin.display(description="Package Dimensions")
    def package_dimensions_display(self, obj):
        """Display package dimensions in a compact format."""
        if obj:
            return obj.package_dimensions_display
        return "—"

    @admin.display(description="Unit cost (calculated)")
    def unit_cost_display(self, obj):
        """Readable representation of the cost per individual unit."""

        if not obj or obj.unit_cost is None:
            return "—"
        return f"${obj.unit_cost:.4f}"


@admin.register(ItemSupplier)
class ItemSupplierAdmin(admin.ModelAdmin):
    """Admin interface for managing item-supplier relationships and pricing."""
    
    list_display = [
        "item_link",
        "supplier",
        "supplier_sku", 
        "package_cost",
        "unit_cost",
        "quantity_per_package",
        "package_dimensions_display",
        "is_primary",
        "is_active",
        "api_link"
    ]
    list_filter = ["supplier", "is_primary", "is_active", "item__category"]
    search_fields = [
        "item__name", 
        "item__sku", 
        "supplier__name", 
        "supplier_sku"
    ]
    readonly_fields = ["unit_cost", "created_at", "updated_at", "api_link", "price_history_link", "package_dimensions_display", "package_volume_display", "unit_weight_display"]
    
    fieldsets = (
        (
            "Basic Information",
            {"fields": ("item", "supplier", "supplier_sku", "supplier_url", "is_primary", "is_active")},
        ),
        (
            "Product Details", 
            {"fields": ("package_upc", "unit_upc", "quantity_per_package")}
        ),
        (
            "Package Dimensions",
            {"fields": ("package_height", "package_width", "package_length", "package_weight")},
        ),
        (
            "Calculated Dimensions",
            {"fields": ("package_dimensions_display", "package_volume_display", "unit_weight_display"), "classes": ("collapse",)},
        ),
        (
            "Pricing Information",
            {"fields": ("package_cost", "unit_cost", "average_lead_time")},
        ),
        (
            "API & History",
            {"fields": ("api_link", "price_history_link")},
        ),
        (
            "Additional Information",
            {"fields": ("notes", "created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )
    
    def item_link(self, obj):
        """Create a clickable link to the item admin page."""
        if obj.item:
            url = reverse("admin:inventory_inventoryitem_change", args=[obj.item.pk])
            return format_html('<a href="{}">{}</a>', url, obj.item.name)
        return "—"
    item_link.short_description = "Item"
    
    def api_link(self, obj):
        """Create a link to the DRF API endpoint for this ItemSupplier."""
        if obj.pk:
            api_url = f"/api/inventory/item-suppliers/{obj.pk}/"
            return format_html(
                '<a href="{}" target="_blank" style="background: #007cba; color: white; padding: 4px 8px; text-decoration: none; border-radius: 3px;">📡 See API Object</a>',
                api_url
            )
        return "—"
    api_link.short_description = "API Link"
    
    def price_history_link(self, obj):
        """Create a link to the price history for this item-supplier relationship."""
        if obj.pk:
            api_url = f"/api/inventory/item-suppliers/{obj.pk}/price_history/"
            return format_html(
                '<a href="{}" target="_blank" style="background: #417690; color: white; padding: 4px 8px; text-decoration: none; border-radius: 3px;">📊 Price History</a>',
                api_url
            )
        return "—"
    price_history_link.short_description = "Price History"
    
    def package_dimensions_display(self, obj):
        """Display package dimensions in a readable format."""
        if obj:
            return obj.package_dimensions_display
        return "—"
    package_dimensions_display.short_description = "Package Dimensions"
    
    def package_volume_display(self, obj):
        """Display calculated package volume."""
        if obj and obj.package_volume:
            return f"{obj.package_volume:,.2f} in³"
        return "—"
    package_volume_display.short_description = "Package Volume"
    
    def unit_weight_display(self, obj):
        """Display calculated weight per unit."""
        if obj and obj.unit_weight:
            return f"{obj.unit_weight:.3f} oz"
        return "—"
    unit_weight_display.short_description = "Unit Weight"


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
        "api_link",
        "reorder_link",
    ]
    list_filter = ["category", "location", "is_active"]
    search_fields = ["name", "sku", "description"]
    readonly_fields = ["id", "sku", "created_at", "updated_at", "qr_code", "thumbnail", "api_link", "reorder_link"]
    inlines = [ItemSupplierInline]
    fieldsets = (
        (
            "Basic Information",
            {"fields": ("name", "description", "sku", "category", "location", "is_active")},
        ),
        ("Images", {"fields": ("image", "image_url", "thumbnail", "qr_code")}),
        ("Stock Information", {"fields": ("current_stock", "minimum_stock", "reorder_quantity")}),
        (
            "Frontend Links",
            {"fields": ("api_link", "reorder_link")},
        ),
        (
            "Additional Information",
            {"fields": ("notes", "created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )
    
    def api_link(self, obj):
        """Create a link to the DRF API endpoint for this InventoryItem."""
        if obj.pk:
            api_url = f"/api/inventory/items/{obj.pk}/"
            return format_html(
                '<a href="{}" target="_blank" style="background: #007cba; color: white; padding: 4px 8px; text-decoration: none; border-radius: 3px;">📡 See API Object</a>',
                api_url
            )
        return "—"
    api_link.short_description = "API Link"
    
    def reorder_link(self, obj):
        """Create a link to request reorder on the frontend application."""
        if obj.pk:
            from django.conf import settings
            frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
            # Use the existing scan page which has reorder functionality
            reorder_url = f"{frontend_url}/scan/{obj.pk}"
            return format_html(
                '<a href="{}" target="_blank" style="background: #28a745; color: white; padding: 4px 8px; text-decoration: none; border-radius: 3px;">🔄 Request Reorder</a>',
                reorder_url
            )
        return "—"
    reorder_link.short_description = "Reorder Request"


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    """Admin interface for viewing price history records."""
    
    list_display = [
        "item_supplier",
        "unit_cost",
        "package_cost", 
        "quantity_per_package",
        "change_type",
        "recorded_at",
        "price_change_percentage"
    ]
    list_filter = ["change_type", "recorded_at", "item_supplier__supplier"]
    search_fields = [
        "item_supplier__item__name", 
        "item_supplier__supplier__name"
    ]
    readonly_fields = [
        "item_supplier", 
        "unit_cost", 
        "package_cost", 
        "quantity_per_package",
        "change_type", 
        "recorded_at",
        "price_change_percentage"
    ]
    date_hierarchy = "recorded_at"
    
    def has_add_permission(self, request):
        """Price history records are auto-generated, don't allow manual creation."""
        return False
    
    def has_change_permission(self, request, obj=None):
        """Price history records should not be editable."""
        return False


@admin.register(UsageLog)
class UsageLogAdmin(admin.ModelAdmin):
    list_display = ["item", "quantity_used", "usage_date"]
    list_filter = ["usage_date", "item"]
    search_fields = ["item__name", "notes"]
    readonly_fields = ["usage_date"]
    date_hierarchy = "usage_date"
