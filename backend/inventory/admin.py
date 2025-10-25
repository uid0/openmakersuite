"""
Admin configuration for inventory app.
"""
from django.contrib import admin
from .models import Supplier, Category, InventoryItem, UsageLog


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ['name', 'supplier_type', 'website']
    list_filter = ['supplier_type']
    search_fields = ['name']


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'parent', 'slug']
    list_filter = ['parent']
    search_fields = ['name']
    prepopulated_fields = {'slug': ('name',)}


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'sku', 'category', 'current_stock',
        'minimum_stock', 'needs_reorder', 'supplier', 'is_active'
    ]
    list_filter = ['category', 'supplier', 'is_active']
    search_fields = ['name', 'sku', 'description']
    readonly_fields = ['id', 'created_at', 'updated_at', 'qr_code']
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'description', 'sku', 'category', 'location', 'is_active')
        }),
        ('Images', {
            'fields': ('image', 'thumbnail', 'qr_code')
        }),
        ('Stock Information', {
            'fields': ('current_stock', 'minimum_stock', 'reorder_quantity')
        }),
        ('Supplier Information', {
            'fields': ('supplier', 'supplier_sku', 'supplier_url', 'unit_cost', 'average_lead_time')
        }),
        ('Additional Information', {
            'fields': ('notes', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(UsageLog)
class UsageLogAdmin(admin.ModelAdmin):
    list_display = ['item', 'quantity_used', 'usage_date']
    list_filter = ['usage_date', 'item']
    search_fields = ['item__name', 'notes']
    readonly_fields = ['usage_date']
    date_hierarchy = 'usage_date'
