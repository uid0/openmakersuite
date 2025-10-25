"""
Admin configuration for reorder queue app.
"""
from django.contrib import admin
from django.utils.html import format_html
from .models import ReorderRequest


@admin.register(ReorderRequest)
class ReorderRequestAdmin(admin.ModelAdmin):
    list_display = [
        'item', 'quantity', 'status', 'priority',
        'requested_by', 'requested_at', 'days_pending_display',
        'estimated_cost_display'
    ]
    list_filter = ['status', 'priority', 'requested_at']
    search_fields = ['item__name', 'requested_by', 'order_number']
    readonly_fields = ['requested_at', 'updated_at', 'estimated_cost', 'days_pending']
    date_hierarchy = 'requested_at'

    fieldsets = (
        ('Request Information', {
            'fields': ('item', 'quantity', 'status', 'priority')
        }),
        ('Requester Details', {
            'fields': ('requested_by', 'request_notes', 'requested_at')
        }),
        ('Admin Review', {
            'fields': ('reviewed_by', 'reviewed_at', 'admin_notes')
        }),
        ('Order Details', {
            'fields': (
                'ordered_at', 'estimated_delivery', 'actual_delivery',
                'order_number', 'actual_cost', 'estimated_cost'
            )
        }),
        ('Metadata', {
            'fields': ('days_pending', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def estimated_cost_display(self, obj):
        """Display estimated cost with currency formatting."""
        if obj.estimated_cost:
            return f"${obj.estimated_cost:.2f}"
        return "-"
    estimated_cost_display.short_description = "Est. Cost"

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
    days_pending_display.short_description = "Days Pending"

    actions = ['approve_requests', 'cancel_requests']

    def approve_requests(self, request, queryset):
        """Bulk approve selected requests."""
        updated = queryset.filter(status='pending').update(
            status='approved',
            reviewed_by=request.user
        )
        self.message_user(request, f"{updated} requests approved.")
    approve_requests.short_description = "Approve selected requests"

    def cancel_requests(self, request, queryset):
        """Bulk cancel selected requests."""
        updated = queryset.update(
            status='cancelled',
            reviewed_by=request.user
        )
        self.message_user(request, f"{updated} requests cancelled.")
    cancel_requests.short_description = "Cancel selected requests"
