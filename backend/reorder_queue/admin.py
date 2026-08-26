"""
* @file            backend/reorder_queue/admin.py
* @description     Admin configuration for reorder queue app.
* @author          Ian Wilson <me@ianwilson.org>
* @createTime      2025-10-25 06:04:12
* @lastModified    2025-11-06 04:46:27
*
* Copyright ©Ian Wilson All rights reserved.
*
*  This program is free software: you can redistribute it and/or modify
*  it under the terms of the GNU Affero General Public License as
*  published by the Free Software Foundation, either version 3 of the
*  License, or (at your option) any later version.
*
*  This program is distributed in the hope that it will be useful,
*  but WITHOUT ANY WARRANTY; without even the implied warranty of
*  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
*  GNU Affero General Public License for more details.
*
*  You should have received a copy of the GNU Affero General Public License
*  along with this program.  If not, see <https://www.gnu.org/licenses/>.
*
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .audit import record_line_reprice
from .models import (
    DeliveryItem,
    LeadTimeLog,
    OrderDelivery,
    PurchaseOrder,
    PurchaseOrderAttachment,
    PurchaseOrderItem,
    PurchaseOrderItemQuerySet,
    ReorderRequest,
    WebHook,
)
from .settlement_signals import settlement_batch


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
    """Filter the line changelist by a line's receipt state.

    Both halves come off :class:`PurchaseOrderItem.ReceiptState` — the options
    from its choices, the rows from the queryset's own twin of ``receipt_state``
    — so this filter and the "Pending" column beside it always describe a line
    the same way, and a state added to the enum turns up here on its own.

    It used to spell three of the states out in SQL by hand and knew nothing of
    the other three: a struck-off line was filed under "Pending Receipt" and a
    line closed short under "Partially Received", each directly contradicting
    what its own row said.
    """

    title = "receipt status"
    parameter_name = "receipt_status"

    def lookups(self, request, model_admin):
        return PurchaseOrderItem.ReceiptState.choices

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        alias = PurchaseOrderItemQuerySet.RECEIPT_STATE_ALIAS
        return queryset.with_receipt_state().filter(**{alias: value})


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
    readonly_fields = [
        "requested_at",
        "updated_at",
        "estimated_cost_display",
        "days_pending_display",
    ]
    date_hierarchy = "requested_at"

    fieldsets = (
        ("Request Information", {"fields": ("item", "quantity", "status", "priority")}),
        (
            "Requester Details",
            {"fields": ("requested_by", "request_notes", "requested_at")},
        ),
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
                    "estimated_cost_display",
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
        (
            "Metadata",
            {
                "fields": ("days_pending_display", "updated_at"),
                "classes": ("collapse",),
            },
        ),
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
        updated = queryset.filter(status=ReorderRequest.Status.PENDING).update(
            status=ReorderRequest.Status.APPROVED, reviewed_by=request.user
        )
        self.message_user(request, f"{updated} requests approved.")

    @admin.action(description="Cancel selected requests")
    def cancel_requests(self, request, queryset):
        """Bulk cancel selected requests."""
        updated = queryset.update(status=ReorderRequest.Status.CANCELLED, reviewed_by=request.user)
        self.message_user(request, f"{updated} requests cancelled.")


# Purchase Order Admin


class PurchaseOrderItemInline(admin.TabularInline):
    """Inline for purchase order items."""

    model = PurchaseOrderItem
    extra = 0
    readonly_fields = [
        "estimated_cost_display",
        "actual_cost_display",
        "quantity_pending",
        "is_fully_received",
    ]

    @admin.display(description="Est. Cost")
    def estimated_cost_display(self, obj):
        if obj and obj.estimated_cost:
            return f"${obj.estimated_cost:.2f}"
        return "-"

    @admin.display(description="Actual Cost")
    def actual_cost_display(self, obj):
        if obj and obj.actual_cost:
            return f"${obj.actual_cost:.2f}"
        return "-"


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = [
        "po_number",
        "supplier",
        "status",
        "priority",
        "order_date",
        "expected_delivery_date",
        "estimated_total_display",
        "total_items",
        "days_since_ordered_display",
    ]
    list_filter = ["status", "priority", "payment_terms", "freight_terms", "order_date", "supplier"]
    search_fields = ["po_number", "supplier__name", "notes"]
    # ``order_date`` is editable (op-bwo9) so an order entered after the fact can
    # be backdated to when it was actually placed.
    readonly_fields = [
        "po_number",
        "updated_at",
        "total_items",
        "total_quantity",
        "total_received_quantity",
        "is_fully_received",
        "days_since_ordered",
        "payment_schedule_display",
    ]
    date_hierarchy = "order_date"

    def get_queryset(self, request):
        # Prefetch line items so the changelist's total_items column (and any
        # other line-item aggregate) reads from cache instead of one query per
        # row (#883).
        return super().get_queryset(request).prefetch_related("items")

    inlines = [PurchaseOrderItemInline]

    def save_formset(self, request, form, formset, change):
        """Save the line-item inline, recording any price change as one.

        The inline edits ``unit_cost_ordered`` on existing lines just as the
        line-item change form does, so it owes the same trace. Inline saves go
        through here rather than ``save_model``, so closing one without the
        other would leave the price-trace invariant with a hole beside it.

        It edits the settlement columns too, and used to owe the status
        re-derivation from here as well. It no longer does, and no admin hook
        does: that obligation moved onto the line's own save/delete signals
        (:mod:`reorder_queue.settlement_signals`), which is what finally stopped
        each new admin door — inline, row delete, bulk delete, reparent —
        needing its own entry in a hand-maintained list of method names.

        A welcome consequence: ``save_related`` runs this for every inline on
        every save whether or not a row moved, and the old refresh here was
        gated by hand so it would not overwrite the ``status`` an operator had
        just chosen on the same form. The signal derives that gate instead —
        an unchanged inline row is never saved, so nothing re-derives.
        """
        previous_unit_costs = {}
        if formset.model is PurchaseOrderItem:
            previous_unit_costs = dict(
                PurchaseOrderItem.objects.filter(
                    pk__in=[f.instance.pk for f in formset.forms if f.instance.pk]
                ).values_list("pk", "unit_cost_ordered")
            )

        with settlement_batch():
            super().save_formset(request, form, formset, change)

        if not previous_unit_costs:
            return
        for line_item in PurchaseOrderItem.objects.filter(pk__in=previous_unit_costs):
            previous_unit_cost = previous_unit_costs[line_item.pk]
            if previous_unit_cost != line_item.unit_cost_ordered:
                record_line_reprice(
                    line_item=line_item,
                    previous_unit_cost=previous_unit_cost,
                    actor=request.user,
                )

    fieldsets = (
        (
            "Order Information",
            {
                "fields": (
                    "po_number",
                    "supplier",
                    "status",
                    "priority",
                    "order_date",
                    "expected_delivery_date",
                )
            },
        ),
        (
            "Terms",
            {
                "fields": ("payment_terms", "freight_terms", "payment_schedule_display"),
                "description": (
                    "Descriptive terms agreed with the supplier — neither moves "
                    "stock nor posts to the ledger. The payment schedule is "
                    "derived from the payment terms, the order date and the "
                    "live estimated total."
                ),
            },
        ),
        (
            "Associations",
            {
                "fields": ("work_order", "owning_group"),
                "description": (
                    "Who this order was placed for. Attribution only — the "
                    "material bridge and the receiving ledger read the line "
                    "items, not these."
                ),
            },
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
        (
            "Metadata",
            {"fields": ("days_since_ordered", "updated_at"), "classes": ("collapse",)},
        ),
    )

    @admin.display(description="Est. Total")
    def estimated_total_display(self, obj):
        """Display estimated total with currency formatting."""
        if obj.estimated_total:
            return f"${obj.estimated_total:,.2f}"
        return "-"

    @admin.display(description="Payment Schedule")
    def payment_schedule_display(self, obj):
        """Show the payment this order's terms imply (op-bwo9)."""
        if obj is None or obj.pk is None:
            return "-"
        schedule = obj.payment_schedule
        when = schedule["due_date"].isoformat() if schedule["due_date"] else "no date"
        return f"${schedule['amount']:,.2f} due {when} ({schedule['basis']})"

    @admin.display(description="Days Since Ordered")
    def days_since_ordered_display(self, obj):
        """Display days since ordered with color coding."""
        days = obj.days_since_ordered
        if days == 0:
            return "Today"
        elif obj.status in [PurchaseOrder.Status.SENT, PurchaseOrder.Status.CONFIRMED]:
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
        updated = queryset.filter(status=PurchaseOrder.Status.DRAFT).update(
            status=PurchaseOrder.Status.SENT, sent_by=request.user
        )
        self.message_user(request, f"{updated} orders marked as sent.")

    @admin.action(description="Mark selected orders as confirmed")
    def mark_as_confirmed(self, request, queryset):
        """Mark orders as confirmed by supplier."""
        updated = queryset.filter(status=PurchaseOrder.Status.SENT).update(
            status=PurchaseOrder.Status.CONFIRMED
        )
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
        # Who the line was bought for (op-bu80 / op-shb9). Related columns, so
        # the changelist select_related()s them — no query per row.
        "work_order",
        "owning_group",
    ]
    list_filter = [
        "purchase_order__status",
        "purchase_order__supplier",
        "owning_group",
        ReceiptStatusFilter,  # Custom filter for receipt status
    ]
    search_fields = [
        "purchase_order__po_number",
        "item_supplier__item__name",
        "item_supplier__supplier__name",
    ]
    readonly_fields = [
        "estimated_cost_display",
        "actual_cost_display",
        "quantity_pending",
        "is_fully_received",
        "created_at",
        "updated_at",
    ]

    def save_model(self, request, obj, form, change):
        """Save the line, and record a price change as a price change.

        ``unit_cost_ordered`` stays editable here on purpose — admin exists for
        the exceptional correction the API's draft-only reprice cannot serve.
        The right answer is that such a correction leaves a trace, not that it
        becomes impossible, so it emits the same event every other route that
        rewrites that field emits.

        The settlement columns stay editable for the same reason, and this hook
        no longer answers for them. ``purchase_order`` is editable here too, so
        this form can MOVE a line to another order — a settlement transition
        for the order it left as much as for the one it joined, and one no
        amount of refreshing ``obj.purchase_order_id`` after the save could
        have seen. :mod:`reorder_queue.settlement_signals` re-derives both.
        """
        previous_unit_cost = None
        if change and obj.pk:
            previous_unit_cost = (
                PurchaseOrderItem.objects.filter(pk=obj.pk)
                .values_list("unit_cost_ordered", flat=True)
                .first()
            )

        with settlement_batch():
            super().save_model(request, obj, form, change)

        if previous_unit_cost is not None and previous_unit_cost != obj.unit_cost_ordered:
            record_line_reprice(
                line_item=obj,
                previous_unit_cost=previous_unit_cost,
                actor=request.user,
            )

    @admin.display(description="Item")
    def item_name(self, obj):
        # ``.item`` is None on asset-only and freeform lines — label through the
        # typed-target accessor instead of dereferencing it (BACKEND-13).
        return obj.target_label

    @admin.display(description="Supplier")
    def supplier_name(self, obj):
        # ``.supplier`` is None on freeform lines and manufacturer-less assets;
        # None renders as the admin's empty value.
        supplier = obj.supplier
        return supplier.name if supplier is not None else None

    @admin.display(description="Pending")
    def quantity_pending_display(self, obj):
        """How much this line is still waiting on, colour-coded.

        Reads the line's SETTLEMENT, not its raw pending quantity: a line closed
        short or struck off keeps a non-zero ``quantity_pending`` while nothing
        more is coming for it, and "N pending" in orange is then a claim about
        goods in transit that nobody is waiting for. Settled lines report how
        they ended instead.
        """
        if obj.is_settled:
            if obj.is_fully_received:
                return mark_safe('<span style="color: green;">✓ Complete</span>')
            return format_html('<span style="color: gray;">{}</span>', obj.receipt_state_label)
        return format_html('<span style="color: orange;">{} pending</span>', obj.quantity_pending)

    @admin.display(description="Est. Cost")
    def estimated_cost_display(self, obj):
        if obj.estimated_cost:
            return f"${obj.estimated_cost:.2f}"
        return "-"

    @admin.display(description="Actual Cost")
    def actual_cost_display(self, obj):
        if obj.actual_cost:
            return f"${obj.actual_cost:.2f}"
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

    def get_queryset(self, request):
        # Prefetch delivery items so the changelist's total_quantity_received
        # column reads from cache instead of one query per row (#883).
        return super().get_queryset(request).prefetch_related("items")

    inlines = [DeliveryItemInline]

    fieldsets = (
        (
            "Delivery Information",
            {
                "fields": (
                    "purchase_order",
                    "delivery_date",
                    "tracking_number",
                    "carrier",
                )
            },
        ),
        (
            "Receipt Details",
            {"fields": ("received_by", "receipt_notes", "is_complete")},
        ),
        (
            "Summary",
            {
                "fields": ("total_items_received", "total_quantity_received"),
                "classes": ("collapse",),
            },
        ),
        (
            "Metadata",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
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
        # Same nullable target as the line itself (BACKEND-13).
        return obj.purchase_order_item.target_label

    @admin.display(description="Supplier")
    def supplier_name(self, obj):
        supplier = obj.supplier
        return supplier.name if supplier is not None else None

    @admin.display(description="Condition")
    def condition_status(self, obj):
        """Display condition status with visual indicators."""
        if obj.is_damaged and obj.is_expired:
            return mark_safe('<span style="color: red;">⚠️ Damaged & Expired</span>')
        elif obj.is_damaged:
            return mark_safe('<span style="color: orange;">⚠️ Damaged</span>')
        elif obj.is_expired:
            return mark_safe('<span style="color: red;">⚠️ Expired</span>')
        else:
            return mark_safe('<span style="color: green;">✓ Good</span>')


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
            return mark_safe('<span style="color: green;">✓ On Time</span>')
        elif variance < 0:
            return format_html('<span style="color: blue;">⚡ {} days early</span>', abs(variance))
        else:
            return format_html('<span style="color: red;">⚠️ {} days late</span>', variance)


# WebHook Admin


@admin.register(WebHook)
class WebHookAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "event_type",
        "url",
        "is_active",
        "success_rate_display",
        "total_triggers_display",
        "last_triggered_at",
    ]
    list_filter = ["event_type", "is_active", "last_triggered_at"]
    search_fields = ["name", "url", "description"]
    readonly_fields = [
        "success_count",
        "failure_count",
        "success_rate_display",
        "total_triggers_display",
        "last_triggered_at",
        "last_error",
        "created_at",
        "updated_at",
    ]

    fieldsets = (
        (
            "Basic Information",
            {"fields": ("name", "description", "event_type", "is_active")},
        ),
        (
            "Endpoint Configuration",
            {
                "fields": ("url", "secret", "headers"),
                "description": "Configure the webhook endpoint. Secret is used for HMAC signature verification.",
            },
        ),
        (
            "Statistics",
            {
                "fields": (
                    "success_count",
                    "failure_count",
                    "success_rate_display",
                    "total_triggers_display",
                    "last_triggered_at",
                    "last_error",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Metadata",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    actions = [
        "test_selected_webhooks",
        "test_selected_webhooks_sync",
        "reset_statistics",
    ]

    @admin.display(description="Success Rate")
    def success_rate_display(self, obj):
        """Display success rate with color coding."""
        total = obj.success_count + obj.failure_count
        if total == 0:
            return mark_safe('<span style="color: gray;">No triggers yet</span>')

        rate = (obj.success_count / total) * 100
        if rate >= 95:
            color = "green"
        elif rate >= 80:
            color = "orange"
        else:
            color = "red"

        return format_html(
            '<span style="color: {};">{:.1f}% ({}/{})</span>',
            color,
            rate,
            obj.success_count,
            total,
        )

    @admin.display(description="Total Triggers")
    def total_triggers_display(self, obj):
        """Display total trigger count."""
        total = obj.success_count + obj.failure_count
        return format_html("{} total", total)

    @admin.action(description="Test selected webhooks (async)")
    def test_selected_webhooks(self, request, queryset):
        """Test selected webhook configurations asynchronously via Celery."""
        from .tasks import run_webhook_test

        count = 0
        for webhook in queryset:
            run_webhook_test.delay(webhook.id)
            count += 1

        self.message_user(
            request,
            f"{count} webhook test(s) queued. Check the webhook statistics for results.",
        )

    @admin.action(description="Test selected webhooks (sync)")
    def test_selected_webhooks_sync(self, request, queryset):
        """Test selected webhook configurations synchronously and show immediate results."""
        import hashlib
        import hmac
        import json
        import time

        from django.utils import timezone

        import requests

        results = []
        for webhook in queryset:
            # Prepare test payload
            test_payload = {
                "event": webhook.event_type,
                "test": True,
                "timestamp": timezone.now().isoformat(),
                "data": {
                    "message": "This is a test webhook notification",
                    "webhook_id": webhook.id,
                    "webhook_name": webhook.name,
                },
            }

            start_time = time.time()

            try:
                # Prepare request
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "DMS-Inventory-Webhook/1.0",
                }

                # Add custom headers
                if webhook.headers:
                    headers.update(webhook.headers)

                # Prepare payload
                json_payload = json.dumps(test_payload)

                # Add HMAC signature if configured
                if webhook.secret:
                    signature = hmac.new(
                        webhook.secret.encode("utf-8"),
                        json_payload.encode("utf-8"),
                        hashlib.sha256,
                    ).hexdigest()
                    headers["X-Webhook-Signature"] = f"sha256={signature}"

                # Send test webhook
                response = requests.post(
                    webhook.url, data=json_payload, headers=headers, timeout=30
                )

                response_time_ms = (time.time() - start_time) * 1000

                response.raise_for_status()

                results.append(
                    format_html(
                        '<span style="color: green;">✓ {}: Success ({}ms, status {})</span>',
                        webhook.name,
                        round(response_time_ms, 2),
                        response.status_code,
                    )
                )

            except requests.exceptions.RequestException as e:
                response_time_ms = (time.time() - start_time) * 1000
                results.append(
                    format_html(
                        '<span style="color: red;">✗ {}: Failed ({}ms) - {}</span>',
                        webhook.name,
                        round(response_time_ms, 2),
                        str(e)[:100],
                    )
                )
            except Exception as e:
                response_time_ms = (time.time() - start_time) * 1000
                results.append(
                    format_html(
                        '<span style="color: red;">✗ {}: Error ({}ms) - {}</span>',
                        webhook.name,
                        round(response_time_ms, 2),
                        str(e)[:100],
                    )
                )

        if results:
            message = format_html("<br>".join(results))
            self.message_user(request, message)
        else:
            self.message_user(request, "No webhooks selected.")

    @admin.action(description="Reset statistics for selected webhooks")
    def reset_statistics(self, request, queryset):
        """Reset success/failure statistics for selected webhooks."""
        updated = queryset.update(
            success_count=0,
            failure_count=0,
            last_error="",
            last_triggered_at=None,
        )
        self.message_user(request, f"Statistics reset for {updated} webhook(s).")


@admin.register(PurchaseOrderAttachment)
class PurchaseOrderAttachmentAdmin(admin.ModelAdmin):
    """Admin for purchase order attachments."""

    list_display = ("purchase_order", "description", "uploaded_by", "uploaded_at")
    list_filter = ("uploaded_at",)
    search_fields = (
        "purchase_order__po_number",
        "description",
        "uploaded_by__username",
    )
    readonly_fields = ("uploaded_at",)
    autocomplete_fields = ("purchase_order", "uploaded_by")
