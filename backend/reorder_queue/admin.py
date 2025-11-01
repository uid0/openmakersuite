"""
Admin configuration for reorder queue app.
"""

from django.contrib import admin
from django.utils.html import format_html

from .models import (
    DeliveryItem,
    LeadTimeLog,
    OrderDelivery,
    PurchaseOrder,
    PurchaseOrderItem,
    ReorderRequest,
)


class DeliveryPerformanceFilter(admin.SimpleListFilter):
    """Custom filter for delivery performance based on variance_days."""

    title = "delivery performance"
    parameter_name = "performance"

    def lookups(self, request, model_admin):
        return (
            ("early", "Early Delivery"),
            ("on_time", "On Time"),
            ("late", "Late Delivery"),
        )

    def queryset(self, request, queryset):
        if self.value() == "early":
            return queryset.filter(variance_days__lt=0)
        elif self.value() == "on_time":
            return queryset.filter(variance_days=0)
        elif self.value() == "late":
            return queryset.filter(variance_days__gt=0)


class ReceiptStatusFilter(admin.SimpleListFilter):
    """Custom filter for order item receipt status."""

    title = "receipt status"
    parameter_name = "receipt_status"

    def lookups(self, request, model_admin):
        return (
            ("fully_received", "Fully Received"),
            ("partially_received", "Partially Received"),
            ("pending", "Pending Receipt"),
        )

    def queryset(self, request, queryset):
        from django.db.models import F

        if self.value() == "fully_received":
            return queryset.filter(quantity_received__gte=F("quantity_ordered"))
        elif self.value() == "partially_received":
            return queryset.filter(
                quantity_received__gt=0, quantity_received__lt=F("quantity_ordered")
            )
        elif self.value() == "pending":
            return queryset.filter(quantity_received=0)


@admin.register(ReorderRequest)
class ReorderRequestAdmin(admin.ModelAdmin):
    list_display = [
        "item",
        "quantity",
        "status",
        "priority",
        "requested_by",
        "requested_at",
        "days_pending_display",
        "estimated_cost_display",
    ]
    list_filter = ["status", "priority", "requested_at"]
    search_fields = ["item__name", "requested_by", "order_number"]
    readonly_fields = ["requested_at", "updated_at", "estimated_cost", "days_pending"]
    date_hierarchy = "requested_at"

    fieldsets = (
        ("Request Information", {"fields": ("item", "quantity", "status", "priority")}),
        ("Requester Details", {"fields": ("requested_by", "request_notes", "requested_at")}),
        ("Admin Review", {"fields": ("reviewed_by", "reviewed_at", "admin_notes")}),
        (
            "Order Details",
            {
                "fields": (
                    "ordered_at",
                    "estimated_delivery",
                    "actual_delivery",
                    "order_number",
                    "actual_cost",
                    "estimated_cost",
                )
            },
        ),
        (
            "Transparency Information",
            {
                "fields": (
                    "invoice_number",
                    "invoice_url",
                    "purchase_order_url",
                    "delivery_tracking_url",
                    "supplier_url",
                    "public_notes",
                ),
                "description": "Public transparency information - visible to all makerspace members",
            },
        ),
        ("Metadata", {"fields": ("days_pending", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Est. Cost")
    def estimated_cost_display(self, obj):
        """Display estimated cost with currency formatting."""
        if obj.estimated_cost:
            return f"${obj.estimated_cost:.2f}"
        return "-"

    @admin.display(description="Days Pending")
    def days_pending_display(self, obj):
        """Display days pending with color coding."""
        days = obj.days_pending
        if days == 0:
            return "-"
        elif days > 7:
            return format_html('<span style="color: red;">{} days</span>', days)
        elif days > 3:
            return format_html('<span style="color: orange;">{} days</span>', days)
        else:
            return f"{days} days"

    actions = ["approve_requests", "cancel_requests"]

    @admin.action(description="Approve selected requests")
    def approve_requests(self, request, queryset):
        """Bulk approve selected requests."""
        updated = queryset.filter(status="pending").update(
            status="approved", reviewed_by=request.user
        )
        self.message_user(request, f"{updated} requests approved.")

    @admin.action(description="Cancel selected requests")
    def cancel_requests(self, request, queryset):
        """Bulk cancel selected requests."""
        updated = queryset.update(status="cancelled", reviewed_by=request.user)
        self.message_user(request, f"{updated} requests cancelled.")


# Purchase Order Admin


class PurchaseOrderItemInline(admin.TabularInline):
    """Inline for purchase order items."""

    model = PurchaseOrderItem
    extra = 0
    readonly_fields = ["estimated_cost", "actual_cost", "quantity_pending", "is_fully_received"]

    @admin.display(description="Est. Cost")
    def estimated_cost(self, obj):
        if obj and obj.estimated_cost:
            return f"${obj.estimated_cost:.2f}"
        return "-"

    @admin.display(description="Actual Cost")
    def actual_cost(self, obj):
        if obj and obj.actual_cost:
            return f"${obj.actual_cost:.2f}"
        return "-"


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = [
        "po_number",
        "supplier",
        "status",
        "order_date",
        "expected_delivery_date",
        "estimated_total_display",
        "total_items",
        "days_since_ordered_display",
    ]
    list_filter = ["status", "order_date", "supplier"]
    search_fields = ["po_number", "supplier__name", "notes"]
    readonly_fields = [
        "po_number",
        "order_date",
        "updated_at",
        "total_items",
        "total_quantity",
        "total_received_quantity",
        "is_fully_received",
        "days_since_ordered",
    ]
    date_hierarchy = "order_date"

    inlines = [PurchaseOrderItemInline]

    fieldsets = (
        (
            "Order Information",
            {"fields": ("po_number", "supplier", "status", "order_date", "expected_delivery_date")},
        ),
        ("Financial Details", {"fields": ("estimated_total", "actual_total")}),
        ("User Tracking", {"fields": ("created_by", "sent_by", "sent_at")}),
        (
            "Order Summary",
            {
                "fields": (
                    "total_items",
                    "total_quantity",
                    "total_received_quantity",
                    "is_fully_received",
                ),
                "classes": ("collapse",),
            },
        ),
        ("Notes", {"fields": ("notes",)}),
        ("Metadata", {"fields": ("days_since_ordered", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Est. Total")
    def estimated_total_display(self, obj):
        """Display estimated total with currency formatting."""
        if obj.estimated_total:
            return f"${obj.estimated_total:,.2f}"
        return "-"

    @admin.display(description="Days Since Ordered")
    def days_since_ordered_display(self, obj):
        """Display days since ordered with color coding."""
        days = obj.days_since_ordered
        if days == 0:
            return "Today"
        elif obj.status in [PurchaseOrder.SENT, PurchaseOrder.CONFIRMED]:
            if days > 30:
                return format_html(
                    '<span style="color: red; font-weight: bold;">{} days</span>', days
                )
            elif days > 14:
                return format_html('<span style="color: orange;">{} days</span>', days)
            else:
                return f"{days} days"
        else:
            return f"{days} days"

    actions = ["mark_as_sent", "mark_as_confirmed"]

    @admin.action(description="Mark selected orders as sent")
    def mark_as_sent(self, request, queryset):
        """Mark orders as sent to supplier."""
        updated = queryset.filter(status=PurchaseOrder.DRAFT).update(
            status=PurchaseOrder.SENT, sent_by=request.user
        )
        self.message_user(request, f"{updated} orders marked as sent.")

    @admin.action(description="Mark selected orders as confirmed")
    def mark_as_confirmed(self, request, queryset):
        """Mark orders as confirmed by supplier."""
        updated = queryset.filter(status=PurchaseOrder.SENT).update(status=PurchaseOrder.CONFIRMED)
        self.message_user(request, f"{updated} orders marked as confirmed.")


@admin.register(PurchaseOrderItem)
class PurchaseOrderItemAdmin(admin.ModelAdmin):
    list_display = [
        "purchase_order",
        "item_name",
        "supplier_name",
        "quantity_ordered",
        "quantity_received",
        "quantity_pending_display",
        "unit_cost_ordered",
        "estimated_cost_display",
    ]
    list_filter = [
        "purchase_order__status",
        "purchase_order__supplier",
        ReceiptStatusFilter,  # Custom filter for receipt status
    ]
    search_fields = [
        "purchase_order__po_number",
        "item_supplier__item__name",
        "item_supplier__supplier__name",
    ]
    readonly_fields = [
        "estimated_cost",
        "actual_cost",
        "quantity_pending",
        "is_fully_received",
        "created_at",
        "updated_at",
    ]

    @admin.display(description="Item")
    def item_name(self, obj):
        return obj.item.name

    @admin.display(description="Supplier")
    def supplier_name(self, obj):
        return obj.supplier.name

    @admin.display(description="Pending")
    def quantity_pending_display(self, obj):
        """Display quantity pending with color coding."""
        pending = obj.quantity_pending
        if pending == 0:
            return format_html('<span style="color: green;">✓ Complete</span>')
        else:
            return format_html('<span style="color: orange;">{} pending</span>', pending)

    @admin.display(description="Est. Cost")
    def estimated_cost_display(self, obj):
        if obj.estimated_cost:
            return f"${obj.estimated_cost:.2f}"
        return "-"


# Order Receipt Admin


class DeliveryItemInline(admin.TabularInline):
    """Inline for delivery items."""

    model = DeliveryItem
    extra = 0
    readonly_fields = ["scanned_at", "scanned_by", "created_at"]


@admin.register(OrderDelivery)
class OrderDeliveryAdmin(admin.ModelAdmin):
    list_display = [
        "purchase_order",
        "delivery_date",
        "received_by",
        "total_items_received",
        "total_quantity_received",
        "is_complete",
        "tracking_number",
    ]
    list_filter = ["delivery_date", "received_by", "is_complete"]
    search_fields = ["purchase_order__po_number", "tracking_number", "carrier"]
    readonly_fields = [
        "total_items_received",
        "total_quantity_received",
        "created_at",
        "updated_at",
    ]
    date_hierarchy = "delivery_date"

    inlines = [DeliveryItemInline]

    fieldsets = (
        (
            "Delivery Information",
            {"fields": ("purchase_order", "delivery_date", "tracking_number", "carrier")},
        ),
        ("Receipt Details", {"fields": ("received_by", "receipt_notes", "is_complete")}),
        (
            "Summary",
            {
                "fields": ("total_items_received", "total_quantity_received"),
                "classes": ("collapse",),
            },
        ),
        ("Metadata", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(DeliveryItem)
class DeliveryItemAdmin(admin.ModelAdmin):
    list_display = [
        "delivery",
        "item_name",
        "quantity_received",
        "condition_status",
        "scanned_upc",
        "scanned_at",
        "scanned_by",
    ]
    list_filter = ["is_damaged", "is_expired", "scanned_at", "delivery__delivery_date"]
    search_fields = [
        "delivery__purchase_order__po_number",
        "purchase_order_item__item_supplier__item__name",
        "scanned_upc",
    ]
    readonly_fields = ["item_name", "supplier_name", "created_at"]
    date_hierarchy = "scanned_at"

    @admin.display(description="Item")
    def item_name(self, obj):
        return obj.item.name

    @admin.display(description="Supplier")
    def supplier_name(self, obj):
        return obj.supplier.name

    @admin.display(description="Condition")
    def condition_status(self, obj):
        """Display condition status with visual indicators."""
        if obj.is_damaged and obj.is_expired:
            return format_html('<span style="color: red;">⚠️ Damaged & Expired</span>')
        elif obj.is_damaged:
            return format_html('<span style="color: orange;">⚠️ Damaged</span>')
        elif obj.is_expired:
            return format_html('<span style="color: red;">⚠️ Expired</span>')
        else:
            return format_html('<span style="color: green;">✓ Good</span>')


# Analytics Admin


@admin.register(LeadTimeLog)
class LeadTimeLogAdmin(admin.ModelAdmin):
    list_display = [
        "item_name",
        "supplier_name",
        "purchase_order",
        "order_date",
        "actual_delivery_date",
        "estimated_lead_time_days",
        "actual_lead_time_days",
        "variance_display",
        "quantity_received",
    ]
    list_filter = [
        "actual_delivery_date",
        "item_supplier__supplier",
        DeliveryPerformanceFilter,  # Custom filter for early/on-time/late delivery
    ]
    search_fields = [
        "item_supplier__item__name",
        "item_supplier__supplier__name",
        "purchase_order__po_number",
    ]
    readonly_fields = [
        "item_name",
        "supplier_name",
        "was_late",
        "was_early",
        "variance_days",
        "recorded_at",
    ]
    date_hierarchy = "actual_delivery_date"

    @admin.display(description="Item")
    def item_name(self, obj):
        return obj.item.name

    @admin.display(description="Supplier")
    def supplier_name(self, obj):
        return obj.supplier.name

    @admin.display(description="Variance")
    def variance_display(self, obj):
        """Display variance with color coding."""
        variance = obj.variance_days
        if variance == 0:
            return format_html('<span style="color: green;">✓ On Time</span>')
        elif variance < 0:
            return format_html('<span style="color: blue;">⚡ {} days early</span>', abs(variance))
        else:
            return format_html('<span style="color: red;">⚠️ {} days late</span>', variance)
