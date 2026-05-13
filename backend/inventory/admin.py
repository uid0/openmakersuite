"""
Admin configuration for inventory app.
"""

import os

from django.contrib import admin, messages
from django.core.files.base import ContentFile
from django.forms import CharField, Form
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html

from .models import (
    Asset,
    AssetPart,
    AssetProblem,
    Category,
    InventoryItem,
    ItemSupplier,
    Location,
    LocationProblem,
    MaintenanceItem,
    MaintenanceLog,
    MaintenanceMaterial,
    MaintenanceRecord,
    MaintenanceTask,
    PriceHistory,
    StockReconciliation,
    Supplier,
    UsageLog,
    WorkOrder,
    WorkOrderMaterialUsage,
    WorkOrderPhoto,
    WorkOrderSubmission,
    WorkOrderTaskCompletion,
)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "supplier_type",
        "account_number",
        "tax_free_paperwork_filed",
        "website",
    ]
    list_filter = ["supplier_type", "tax_free_paperwork_filed"]
    search_fields = ["name", "account_number"]
    fieldsets = (
        (
            "Basic Information",
            {"fields": ("name", "supplier_type", "website")},
        ),
        (
            "Account Information",
            {
                "fields": ("account_number", "tax_free_paperwork_filed"),
                "description": "Account details for ordering and tax purposes",
            },
        ),
        (
            "Additional Information",
            {
                "fields": ("notes",),
            },
        ),
    )


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
    actions = ["regenerate_qr_codes"]

    @admin.action(description="Regenerate QR codes for selected locations")
    def regenerate_qr_codes(self, request, queryset):
        """Admin action to regenerate QR codes for selected locations."""
        from .services.qr_code_service import QRCodeService

        count = 0
        for location in queryset:
            try:
                service = QRCodeService(include_logo=True)
                service.generate_for_location(location)
                count += 1
            except Exception as e:
                self.message_user(
                    request,
                    f"Error regenerating QR code for {location.name}: {str(e)}",
                    level=messages.ERROR,
                )

        if count > 0:
            self.message_user(
                request,
                f"Successfully regenerated QR codes for {count} location(s).",
                level=messages.SUCCESS,
            )


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
        # Editable dimensional fields
        "package_height",
        "package_width",
        "package_length",
        "package_weight",
        # Calculated fields (readonly)
        "package_dimensions_display",
        "package_volume_display",
        "unit_weight_display",
        "package_cost",
        "unit_cost_display",
        "average_lead_time",
        "is_primary",
        "is_active",
    ]
    readonly_fields = [
        "unit_cost_display",
        "package_dimensions_display",
        "package_volume_display",
        "unit_weight_display",
    ]

    @admin.display(description="Package Dimensions")
    def package_dimensions_display(self, obj):
        """Display package dimensions in a compact format."""
        if obj:
            return obj.package_dimensions_display
        return "—"

    @admin.display(description="Package Volume")
    def package_volume_display(self, obj):
        """Display calculated package volume."""
        if obj and obj.package_volume:
            return f"{obj.package_volume:,.2f} in³"
        return "—"

    @admin.display(description="Unit Weight")
    def unit_weight_display(self, obj):
        """Display calculated weight per unit."""
        if obj and obj.unit_weight:
            return f"{obj.unit_weight:.3f} oz"
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
        "api_link",
    ]
    list_filter = ["supplier", "is_primary", "is_active", "item__category"]
    search_fields = ["item__name", "item__sku", "supplier__name", "supplier_sku"]
    readonly_fields = [
        "unit_cost",
        "created_at",
        "updated_at",
        "api_link",
        "price_history_link",
        "package_dimensions_display",
        "package_volume_display",
        "unit_weight_display",
    ]

    fieldsets = (
        (
            "Basic Information",
            {
                "fields": (
                    "item",
                    "supplier",
                    "supplier_sku",
                    "supplier_url",
                    "is_primary",
                    "is_active",
                )
            },
        ),
        (
            "Product Details",
            {"fields": ("package_upc", "unit_upc", "quantity_per_package")},
        ),
        (
            "Package Dimensions",
            {
                "fields": (
                    "package_height",
                    "package_width",
                    "package_length",
                    "package_weight",
                )
            },
        ),
        (
            "Calculated Dimensions",
            {
                "fields": (
                    "package_dimensions_display",
                    "package_volume_display",
                    "unit_weight_display",
                ),
                "classes": ("collapse",),
            },
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

    @admin.display(description="Item")
    def item_link(self, obj):
        """Create a clickable link to the item admin page."""
        if obj.item:
            url = reverse("admin:inventory_inventoryitem_change", args=[obj.item.pk])
            return format_html('<a href="{}">{}</a>', url, obj.item.name)
        return "—"

    @admin.display(description="API Link")
    def api_link(self, obj):
        """Create a link to the DRF API endpoint for this ItemSupplier."""
        if obj.pk:
            api_url = f"/api/inventory/item-suppliers/{obj.pk}/"
            return format_html(
                '<a href="{}" target="_blank" style="background: #007cba; color: white; padding: 4px 8px; text-decoration: none; border-radius: 3px;">📡 See API Object</a>',
                api_url,
            )
        return "—"

    @admin.display(description="Price History")
    def price_history_link(self, obj):
        """Create a link to the price history for this item-supplier relationship."""
        if obj.pk:
            api_url = f"/api/inventory/item-suppliers/{obj.pk}/price_history/"
            return format_html(
                '<a href="{}" target="_blank" style="background: #417690; color: white; padding: 4px 8px; text-decoration: none; border-radius: 3px;">📊 Price History</a>',
                api_url,
            )
        return "—"

    @admin.display(description="Package Dimensions")
    def package_dimensions_display(self, obj):
        """Display package dimensions in a readable format."""
        if obj:
            return obj.package_dimensions_display
        return "—"

    @admin.display(description="Package Volume")
    def package_volume_display(self, obj):
        """Display calculated package volume."""
        if obj and obj.package_volume:
            return f"{obj.package_volume:,.2f} in³"
        return "—"

    @admin.display(description="Unit Weight")
    def unit_weight_display(self, obj):
        """Display calculated weight per unit."""
        if obj and obj.unit_weight:
            return f"{obj.unit_weight:.3f} oz"
        return "—"


class AssetPartInline(admin.TabularInline):
    """Inline admin for managing asset parts/consumables."""

    model = AssetPart
    extra = 1
    fields = [
        "part",
        "quantity_needed",
        "is_required",
        "maintenance_interval_days",
        "last_replaced_at",
        "needs_replacement_display",
        "days_since_replacement_display",
        "notes",
    ]
    readonly_fields = ["needs_replacement_display", "days_since_replacement_display"]
    autocomplete_fields = ["part"]

    @admin.display(description="Needs Replacement")
    def needs_replacement_display(self, obj):
        """Display if part needs replacement."""
        if obj and obj.needs_replacement:
            return format_html('<span style="color: #dc3545; font-weight: bold;">⚠️ Yes</span>')
        return format_html('<span style="color: #28a745;">✓ No</span>')

    @admin.display(description="Days Since Replacement")
    def days_since_replacement_display(self, obj):
        """Display days since last replacement."""
        if obj:
            days = obj.days_since_replacement
            if days is not None:
                return f"{days} days"
        return "—"


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
        "use_case_based_reorder",
        "is_active",
        "is_requestable",
        "hazmat_status_icon",
        "api_link",
        "reorder_link",
    ]
    list_filter = [
        "category",
        "location",
        "is_active",
        "is_requestable",
        "is_hazardous",
        "use_case_based_reorder",
    ]
    search_fields = ["name", "sku", "description"]
    readonly_fields = [
        "id",
        "sku",
        "created_at",
        "updated_at",
        "qr_code",
        "thumbnail",
        "api_link",
        "reorder_link",
        "nfpa_fire_diamond_display",
        "hazmat_compliance_status",
        "index_card_preview",
    ]
    inlines = [ItemSupplierInline]
    fieldsets = (
        (
            "Basic Information",
            {
                "fields": (
                    "name",
                    "description",
                    "sku",
                    "category",
                    "location",
                    "shelf_position",
                    "is_active",
                    "is_requestable",
                )
            },
        ),
        ("Images", {"fields": ("image", "image_url", "thumbnail", "qr_code")}),
        (
            "Stock Information",
            {"fields": ("current_stock", "minimum_stock", "reorder_quantity")},
        ),
        (
            "Case-Based Reordering",
            {
                "fields": (
                    "use_case_based_reorder",
                    "minimum_cases",
                    "reorder_cases",
                    "reorder_instruction",
                ),
                "description": "Enable case/package-based reordering for bulk items like trashbags, toilet paper, etc. "
                "Use 'reorder_instruction' to customize the text shown on index cards (e.g., 'Reorder when last case is opened').",
            },
        ),
        (
            "Hazardous Materials",
            {
                "fields": (
                    "is_hazardous",
                    "msds_url",
                    "msds_file",
                    "nfpa_health_hazard",
                    "nfpa_fire_hazard",
                    "nfpa_instability_hazard",
                    "nfpa_special_hazards",
                    "nfpa_fire_diamond_display",
                    "hazmat_compliance_status",
                ),
                "description": "Safety information for hazardous materials. Provide either an MSDS URL or upload the file directly. NFPA ratings: 0=Minimal, 1=Slight, 2=Moderate, 3=High, 4=Extreme",
            },
        ),
        (
            "Frontend Links",
            {"fields": ("api_link", "reorder_link", "index_card_preview")},
        ),
        (
            "Additional Information",
            {"fields": ("notes", "created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    @admin.display(description="API Link")
    def api_link(self, obj):
        """Create a link to the DRF API endpoint for this InventoryItem."""
        if obj.pk:
            api_url = f"/api/inventory/items/{obj.pk}/"
            return format_html(
                '<a href="{}" target="_blank" style="background: #007cba; color: white; padding: 4px 8px; text-decoration: none; border-radius: 3px;">📡 See API Object</a>',
                api_url,
            )
        return "—"

    @admin.display(description="Reorder Request")
    def reorder_link(self, obj):
        """Create a link to request reorder on the frontend application."""
        if obj.pk:
            from django.conf import settings

            frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
            # Use the existing scan page which has reorder functionality
            reorder_url = f"{frontend_url}/scan/{obj.pk}"
            return format_html(
                '<a href="{}" target="_blank" style="background: #28a745; color: white; padding: 4px 8px; text-decoration: none; border-radius: 3px;">🔄 Request Reorder</a>',
                reorder_url,
            )
        return "—"

    @admin.display(description="Index Card Preview")
    def index_card_preview(self, obj):
        """Create a preview window for the index card."""
        if not obj.pk:
            return "—"

        preview_url = "/api/index-cards/preview/"

        # Create a button that opens a modal with the preview
        preview_html = format_html(
            """
            <button type="button"
                    onclick="showIndexCardPreview('{}', '{}', event)"
                    style="background: #007cba; color: white; padding: 8px 16px;
                           border: none; border-radius: 4px; cursor: pointer;
                           font-weight: bold;">
                🖼️ Preview Index Card
            </button>
            <div id="indexCardPreviewModal" style="display: none; position: fixed;
                 top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8);
                 z-index: 10000; overflow: auto;">
                <div style="position: relative; width: 90%; max-width: 800px;
                    margin: 50px auto; background: white; padding: 20px; border-radius: 8px;">
                    <button type="button" onclick="closeIndexCardPreview('{}', event)"
                            style="position: absolute; top: 10px; right: 10px;
                                   background: #dc3545; color: white; border: none;
                                   border-radius: 50%; width: 30px; height: 30px;
                                   cursor: pointer; font-size: 18px;">×</button>
                    <h2 style="margin-top: 0;">Index Card Preview</h2>
                    <div id="indexCardPreviewContent" style="text-align: center;">
                        <p>Loading preview...</p>
                    </div>
                </div>
            </div>
            <script>
                function showIndexCardPreview(itemId, previewUrl, event) {{
                    if (event) {{
                        event.preventDefault();
                        event.stopPropagation();
                    }}
                    const modal = document.getElementById('indexCardPreviewModal');
                    const content = document.getElementById('indexCardPreviewContent');
                    modal.style.display = 'block';
                    content.innerHTML = '<p>Loading preview...</p>';

                    fetch(previewUrl, {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                            'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value
                        }},
                        body: JSON.stringify({{item_id: itemId}})
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.preview) {{
                            content.innerHTML = '<iframe src="data:application/pdf;base64,' +
                                data.preview + '" style="width: 100%; height: 600px; border: none;"></iframe>';
                        }} else {{
                            content.innerHTML = '<p style="color: red;">Error loading preview</p>';
                        }}
                    }})
                    .catch(error => {{
                        content.innerHTML = '<p style="color: red;">Error: ' + error + '</p>';
                    }});
                }}

                function closeIndexCardPreview(itemId, event) {{
                    if (event) {{
                        event.preventDefault();
                        event.stopPropagation();
                    }}
                    document.getElementById('indexCardPreviewModal').style.display = 'none';
                    // Ensure we stay on the current item page - don't trigger any form submission
                    // The form should remain unchanged when closing the modal
                }}

                // Close modal when clicking outside
                window.addEventListener('click', function(event) {{
                    const modal = document.getElementById('indexCardPreviewModal');
                    if (event.target == modal) {{
                        event.preventDefault();
                        event.stopPropagation();
                        modal.style.display = 'none';
                    }}
                }});
            </script>
            """,
            str(obj.pk),
            preview_url,
            str(obj.pk),
        )
        return preview_html

    @admin.display(description="Hazmat")
    def hazmat_status_icon(self, obj):
        """Display hazmat status with visual icon."""
        if obj.is_hazardous:
            if obj.hazmat_compliance_status == "Complete":
                return format_html(
                    '<span style="color: #d63384; font-weight: bold;" title="{} - {}">⚠️ HAZMAT</span>',
                    obj.hazmat_compliance_status,
                    obj.nfpa_fire_diamond_display,
                )
            else:
                return format_html(
                    '<span style="color: #dc3545; font-weight: bold;" title="{}">❌ INCOMPLETE</span>',
                    obj.hazmat_compliance_status,
                )
        return format_html('<span style="color: #28a745;" title="Not Hazardous">✅</span>')

    @admin.display(description="NFPA Fire Diamond")
    def nfpa_fire_diamond_display(self, obj):
        """Display NFPA Fire Diamond ratings in admin."""
        return obj.nfpa_fire_diamond_display

    @admin.display(description="Compliance Status")
    def hazmat_compliance_status(self, obj):
        """Display hazmat compliance status in admin."""
        status = obj.hazmat_compliance_status
        if "Complete" in status:
            return format_html('<span style="color: #28a745; font-weight: bold;">{}</span>', status)
        elif "Incomplete" in status:
            return format_html('<span style="color: #dc3545; font-weight: bold;">{}</span>', status)
        else:
            return format_html('<span style="color: #6c757d;">{}</span>', status)

    def response_change(self, request, obj):
        """Override to redirect back to the change page instead of the list after saving."""
        # Handle special save buttons normally
        if "_continue" in request.POST:
            # "Save and continue editing" - stay on change page (default behavior)
            return super().response_change(request, obj)
        if "_addanother" in request.POST:
            # "Save and add another" - go to add page (default behavior)
            return super().response_change(request, obj)
        # For regular "Save" button, redirect back to change page instead of list
        return HttpResponseRedirect(reverse("admin:inventory_inventoryitem_change", args=[obj.pk]))

    actions = ["print_selected_items", "regenerate_qr_codes"]

    @admin.action(description="Print these items (Avery card templates)")
    def print_selected_items(self, request, queryset):
        """Admin action to generate and download PDF for selected items."""
        from django.http import HttpResponse

        from index_cards.services import IndexCardRenderer

        if not queryset.exists():
            self.message_user(request, "No items selected.", level=messages.ERROR)
            return

        items = list(queryset.order_by("name"))
        renderer = IndexCardRenderer(blank_cards=False)
        pdf_bytes = renderer.render_to_bytes(items, blank_cards=False)

        # Generate filename with timestamp
        from django.utils import timezone

        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        filename = f"index_cards_{timestamp}.pdf"

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        self.message_user(
            request,
            f"Generated PDF for {queryset.count()} item(s). Download should start automatically.",
            level=messages.SUCCESS,
        )

        return response

    @admin.action(description="Regenerate QR codes for selected items")
    def regenerate_qr_codes(self, request, queryset):
        """Admin action to regenerate QR codes for selected items."""
        from .services.qr_code_service import QRCodeService

        count = 0
        for item in queryset:
            try:
                service = QRCodeService(include_logo=True)
                service.generate_for_item(item)
                count += 1
            except Exception as e:
                self.message_user(
                    request,
                    f"Error regenerating QR code for {item.name}: {str(e)}",
                    level=messages.ERROR,
                )

        if count > 0:
            self.message_user(
                request,
                f"Successfully regenerated QR codes for {count} item(s).",
                level=messages.SUCCESS,
            )


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
        "price_change_percentage",
    ]
    list_filter = ["change_type", "recorded_at", "item_supplier__supplier"]
    search_fields = ["item_supplier__item__name", "item_supplier__supplier__name"]
    readonly_fields = [
        "item_supplier",
        "unit_cost",
        "package_cost",
        "quantity_per_package",
        "change_type",
        "recorded_at",
        "price_change_percentage",
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


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    """Admin interface for managing hard assets."""

    list_display = [
        "name",
        "asset_tag",
        "serial_number",
        "mac_address",
        "status_badge",
        "operational_mode_display",
        "category",
        "location",
        "display_manufacturer",
        "acquisition_display",
        "is_active",
        "lock_status_display",
        "api_link",
        "scan_qr_link",
    ]
    list_filter = [
        "status",
        "category",
        "location",
        "is_donation",
        "is_active",
        "report_only",
        "ownership_type",
        "owning_user",
        "owning_group",
        "manufacturer",
    ]
    search_fields = [
        "name",
        "description",
        "serial_number",
        "asset_tag",
        "mac_address",
        "manufacturer_name",
        "donor_name",
    ]
    inlines = [AssetPartInline]
    readonly_fields = [
        "id",
        "asset_tag",
        "qr_code",
        "thumbnail",
        "display_manufacturer",
        "acquisition_display",
        "age_in_days",
        "last_scanned_at",
        "lock_status_display",
        "created_at",
        "updated_at",
        "api_link",
        "scan_qr_link",
    ]

    fieldsets = (
        (
            "Basic Information",
            {
                "fields": (
                    "name",
                    "description",
                    "asset_tag",
                    "serial_number",
                    "is_active",
                    "report_only",
                )
            },
        ),
        (
            "Classification",
            {
                "fields": (
                    "inventory_item",
                    "category",
                    "location",
                )
            },
        ),
        (
            "Manufacturer Information",
            {
                "fields": (
                    "manufacturer",
                    "manufacturer_name",
                    "display_manufacturer",
                )
            },
        ),
        (
            "Acquisition Details",
            {
                "fields": (
                    "date_received",
                    "amount_paid",
                    "is_donation",
                    "donor_name",
                    "acquisition_display",
                    "age_in_days",
                )
            },
        ),
        (
            "Product Information & Wiki",
            {
                "fields": (
                    "product_url",
                    "wiki_page_url",
                )
            },
        ),
        (
            "Maintenance",
            {
                "fields": ("maintenance_plan",),
                "description": "Maintenance plan is only visible to authenticated users when scanning QR codes.",
            },
        ),
        (
            "Parts & Consumables",
            {
                "fields": (),
                "description": "Parts and consumables used by this asset (e.g., saw blades, nozzles, filters, soap). "
                "Manage these in the inline table below.",
                "classes": ("collapse",),
            },
        ),
        (
            "Operational Requirements",
            {
                "fields": (
                    "circuit",
                    "needs_compressed_air",
                    "needs_ventilation",
                    "is_chargeable",
                    "mac_address",
                ),
                "description": "Operational requirements and billing information for the asset.",
            },
        ),
        (
            "Power & Electrical",
            {
                "fields": (
                    "power_draw_watts",
                    "wiring_type",
                    "suite",
                    "electrical_box",
                    "breaker_location",
                    "has_interlock",
                    "interlock_type",
                    "interlock_responsible",
                    "lockout_type",
                    "lockout_instructions",
                    "lockout_responsible",
                    "has_network_drop",
                    "network_drop_location",
                ),
                "description": (
                    "How this asset is powered, where its breaker lives, and how to "
                    "lock it out for service. Surfaced on printed work orders so the "
                    "tech can lock out and verify before working on the device."
                ),
            },
        ),
        (
            "Ownership",
            {
                "fields": (
                    "ownership_type",
                    "owning_user",
                    "owning_group",
                    "groups_can_enable",
                ),
                "description": "Asset ownership (User, Group, or Space). Groups that can enable this asset. For operational status and lockouts, see the ForgeKey app.",
            },
        ),
        (
            "Scanning & Tracking",
            {
                "fields": ("scan_qr_link", "last_scanned_at"),
                "description": "Scan the QR code to test the asset scanning workflow. Last time this asset was scanned via QR code.",
            },
        ),
        (
            "Media Files",
            {
                "fields": (
                    "image",
                    "thumbnail",
                    "manual_pdf",
                    "qr_code",
                )
            },
        ),
        (
            "Status & Condition",
            {
                "fields": (
                    "status",
                    "condition_notes",
                )
            },
        ),
        (
            "API Link",
            {"fields": ("api_link",)},
        ),
        (
            "Additional Information",
            {
                "fields": ("notes", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        """Display status with color-coded badge."""
        status_colors = {
            "active": "#28a745",
            "maintenance": "#ffc107",
            "retired": "#6c757d",
            "lost": "#dc3545",
            "donated_out": "#17a2b8",
        }
        color = status_colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background: {}; color: white; padding: 4px 8px; border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    @admin.display(description="API Link")
    def api_link(self, obj):
        """Create a link to the DRF API endpoint for this Asset."""
        if obj.pk:
            api_url = f"/api/inventory/assets/{obj.pk}/"
            return format_html(
                '<a href="{}" target="_blank" style="background: #007cba; color: white; padding: 4px 8px; text-decoration: none; border-radius: 3px;">📡 See API Object</a>',
                api_url,
            )
        return "—"

    @admin.display(description="Scan QR Code")
    def scan_qr_link(self, obj):
        """Create a link to scan the QR code for this asset on the frontend."""
        if obj.pk:
            from django.conf import settings

            frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
            scan_url = f"{frontend_url}/scan/asset/{obj.id}"
            return format_html(
                '<a href="{}" target="_blank" style="background: #17a2b8; color: white; padding: 4px 8px; text-decoration: none; border-radius: 3px;">📱 Scan QR Code</a>',
                scan_url,
            )
        return "—"

    @admin.display(description="Operational Mode")
    def operational_mode_display(self, obj):
        """Display operational mode from ForgeKey."""
        try:
            from forgekey.models import OperationalMode

            mode = OperationalMode.objects.get(asset=obj)
            mode_display = mode.get_mode_display()
            if mode.classroom_mode_enabled:
                mode_display += " (Classroom)"
            return format_html("<span>{}</span>", mode_display)
        except OperationalMode.DoesNotExist:
            return format_html('<span style="color: #6c757d;">Available</span>')

    @admin.display(description="Lock Status")
    def lock_status_display(self, obj):
        """Display lock status from ForgeKey."""
        try:
            from forgekey.models import DeviceLockout

            active_lockouts = DeviceLockout.objects.filter(asset=obj, is_active=True)
            if active_lockouts.exists():
                lockout = active_lockouts.first()
                lock_info = f"🔒 Locked ({lockout.get_lockout_level_display()})"
                if lockout.locked_by:
                    lock_info += f" by {lockout.locked_by.username}"
                if lockout.locked_at:
                    lock_info += f" on {lockout.locked_at.strftime('%Y-%m-%d %H:%M')}"
                if active_lockouts.count() > 1:
                    lock_info += f" (+{active_lockouts.count() - 1} more)"
                return format_html('<span style="color: #dc3545;">{}</span>', lock_info)
            return format_html('<span style="color: #28a745;">✓ Unlocked</span>')
        except Exception:
            return format_html('<span style="color: #28a745;">✓ Unlocked</span>')

    actions = ["duplicate_asset", "regenerate_qr_codes"]

    @admin.action(description="Duplicate selected asset (enter new serial number)")
    def duplicate_asset(self, request, queryset):
        """Admin action to duplicate selected assets."""
        if queryset.count() != 1:
            self.message_user(
                request,
                "Please select exactly one asset to duplicate.",
                level=messages.ERROR,
            )
            return

        asset = queryset.first()
        # Redirect to the duplicate view with the asset ID
        return HttpResponseRedirect(reverse("admin:inventory_asset_duplicate", args=[asset.pk]))

    @admin.action(description="Regenerate QR codes for selected assets")
    def regenerate_qr_codes(self, request, queryset):
        """Admin action to regenerate QR codes for selected assets."""
        from .services.qr_code_service import QRCodeService

        count = 0
        for asset in queryset:
            try:
                service = QRCodeService(include_logo=True)
                service.generate_for_asset(asset)
                count += 1
            except Exception as e:
                self.message_user(
                    request,
                    f"Error regenerating QR code for {asset.name}: {str(e)}",
                    level=messages.ERROR,
                )

        if count > 0:
            self.message_user(
                request,
                f"Successfully regenerated QR codes for {count} asset(s).",
                level=messages.SUCCESS,
            )

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        """Add duplicate button to change form."""
        extra_context = extra_context or {}
        if object_id:
            duplicate_url = reverse("admin:inventory_asset_duplicate", args=[object_id])
            extra_context["duplicate_url"] = duplicate_url
        return super().changeform_view(request, object_id, form_url, extra_context)

    def get_urls(self):
        """Add custom URL for duplicate asset view."""
        urls = super().get_urls()
        custom_urls = [
            path(
                "<uuid:asset_id>/duplicate/",
                self.admin_site.admin_view(self.duplicate_asset_view),
                name="inventory_asset_duplicate",
            ),
        ]
        return custom_urls + urls

    def duplicate_asset_view(self, request, asset_id):
        """View to duplicate an asset with a new serial number."""
        original_asset = get_object_or_404(Asset, pk=asset_id)

        class DuplicateAssetForm(Form):
            serial_number = CharField(
                max_length=100,
                required=True,
                label="New Serial Number",
                help_text="Enter the serial number for the new asset. All other information will be copied from the original asset.",
            )

        if request.method == "POST":
            form = DuplicateAssetForm(request.POST)
            if form.is_valid():
                serial_number = form.cleaned_data["serial_number"]

                # Create a new asset with all fields from the original except serial_number and asset_tag
                new_asset = Asset(
                    name=original_asset.name,
                    description=original_asset.description,
                    serial_number=serial_number,
                    # asset_tag will be auto-generated on save
                    inventory_item=original_asset.inventory_item,
                    category=original_asset.category,
                    location=original_asset.location,
                    manufacturer=original_asset.manufacturer,
                    manufacturer_name=original_asset.manufacturer_name,
                    date_received=original_asset.date_received,
                    amount_paid=original_asset.amount_paid,
                    is_donation=original_asset.is_donation,
                    donor_name=original_asset.donor_name,
                    product_url=original_asset.product_url,
                    wiki_page_url=original_asset.wiki_page_url,
                    maintenance_plan=original_asset.maintenance_plan,
                    status=original_asset.status,
                    condition_notes=original_asset.condition_notes,
                    is_active=original_asset.is_active,
                    notes=original_asset.notes,
                )
                new_asset.save()

                # Copy image and manual if they exist
                if original_asset.image:
                    # Read the original file and save it to the new asset
                    original_asset.image.open()
                    file_content = original_asset.image.read()
                    original_asset.image.close()
                    # Generate a new filename to avoid conflicts
                    filename = os.path.basename(original_asset.image.name)
                    name, ext = os.path.splitext(filename)
                    new_filename = f"{name}_copy{ext}"
                    new_asset.image.save(new_filename, ContentFile(file_content), save=False)

                if original_asset.manual_pdf:
                    # Read the original file and save it to the new asset
                    original_asset.manual_pdf.open()
                    file_content = original_asset.manual_pdf.read()
                    original_asset.manual_pdf.close()
                    # Generate a new filename to avoid conflicts
                    filename = os.path.basename(original_asset.manual_pdf.name)
                    name, ext = os.path.splitext(filename)
                    new_filename = f"{name}_copy{ext}"
                    new_asset.manual_pdf.save(new_filename, ContentFile(file_content), save=False)

                new_asset.save()

                messages.success(
                    request,
                    f'Asset "{new_asset.name}" has been duplicated successfully. '
                    f'You may edit it <a href="{reverse("admin:inventory_asset_change", args=[new_asset.pk])}">here</a>.',
                )
                return HttpResponseRedirect(
                    reverse("admin:inventory_asset_change", args=[new_asset.pk])
                )
        else:
            form = DuplicateAssetForm(initial={"serial_number": original_asset.serial_number})

        context = {
            **self.admin_site.each_context(request),
            "title": f"Duplicate Asset: {original_asset.name}",
            "form": form,
            "original_asset": original_asset,
            "opts": self.model._meta,
            "has_view_permission": self.has_view_permission(request, original_asset),
            "has_change_permission": self.has_change_permission(request, original_asset),
        }

        return render(request, "admin/inventory/asset/duplicate.html", context)


@admin.register(AssetPart)
class AssetPartAdmin(admin.ModelAdmin):
    """Admin interface for managing asset parts/consumables."""

    list_display = [
        "asset_link",
        "part_link",
        "quantity_needed",
        "is_required",
        "maintenance_interval_days",
        "last_replaced_at",
        "needs_replacement_display",
        "days_since_replacement_display",
    ]
    list_filter = [
        "is_required",
        "asset__category",
        "part__category",
        "last_replaced_at",
    ]
    search_fields = [
        "asset__name",
        "asset__asset_tag",
        "part__name",
        "part__sku",
        "notes",
    ]
    readonly_fields = [
        "needs_replacement_display",
        "days_since_replacement_display",
        "created_at",
        "updated_at",
    ]
    autocomplete_fields = ["asset", "part"]
    date_hierarchy = "last_replaced_at"

    fieldsets = (
        (
            "Part Information",
            {
                "fields": (
                    "asset",
                    "part",
                    "quantity_needed",
                    "is_required",
                )
            },
        ),
        (
            "Maintenance",
            {
                "fields": (
                    "maintenance_interval_days",
                    "last_replaced_at",
                    "needs_replacement_display",
                    "days_since_replacement_display",
                )
            },
        ),
        (
            "Additional Information",
            {
                "fields": ("notes", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Asset")
    def asset_link(self, obj):
        """Create a clickable link to the asset admin page."""
        if obj.asset:
            url = reverse("admin:inventory_asset_change", args=[obj.asset.pk])
            return format_html('<a href="{}">{}</a>', url, obj.asset.name)
        return "—"

    @admin.display(description="Part")
    def part_link(self, obj):
        """Create a clickable link to the inventory item admin page."""
        if obj.part:
            url = reverse("admin:inventory_inventoryitem_change", args=[obj.part.pk])
            return format_html('<a href="{}">{}</a>', url, obj.part.name)
        return "—"

    @admin.display(description="Needs Replacement")
    def needs_replacement_display(self, obj):
        """Display if part needs replacement."""
        if obj and obj.needs_replacement:
            return format_html('<span style="color: #dc3545; font-weight: bold;">⚠️ Yes</span>')
        return format_html('<span style="color: #28a745;">✓ No</span>')

    @admin.display(description="Days Since Replacement")
    def days_since_replacement_display(self, obj):
        """Display days since last replacement."""
        if obj:
            days = obj.days_since_replacement
            if days is not None:
                return f"{days} days"
        return "—"


@admin.register(AssetProblem)
class AssetProblemAdmin(admin.ModelAdmin):
    """Admin interface for asset problem reports."""

    list_display = [
        "asset",
        "asset_tag_display",
        "reported_by",
        "status_badge",
        "created_at",
        "resolved_at",
    ]
    list_filter = ["status", "created_at", "resolved_at"]
    search_fields = [
        "asset__name",
        "asset__asset_tag",
        "description",
        "reported_by",
    ]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["part"]
    date_hierarchy = "created_at"

    fieldsets = (
        (
            "Problem Information",
            {
                "fields": (
                    "asset",
                    "part",
                    "reported_by",
                    "description",
                    "status",
                )
            },
        ),
        (
            "Resolution",
            {
                "fields": (
                    "resolution_notes",
                    "resolved_at",
                )
            },
        ),
        (
            "Metadata",
            {
                "fields": ("id", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Asset Tag")
    def asset_tag_display(self, obj):
        """Display asset tag."""
        return obj.asset.asset_tag if obj.asset.asset_tag else "—"

    @admin.display(description="Status")
    def status_badge(self, obj):
        """Display status with color-coded badge."""
        status_colors = {
            "reported": "#dc3545",
            "in_progress": "#ffc107",
            "resolved": "#28a745",
            "closed": "#6c757d",
        }
        color = status_colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background: {}; color: white; padding: 4px 8px; border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    actions = ["mark_resolved", "mark_closed"]

    @admin.action(description="Mark selected as resolved")
    def mark_resolved(self, request, queryset):
        """Mark selected problems as resolved."""
        from django.utils import timezone

        count = 0
        for problem in queryset.filter(status=AssetProblem.REPORTED):
            problem.status = AssetProblem.RESOLVED
            problem.resolved_at = timezone.now()
            problem.save()
            count += 1

        self.message_user(
            request,
            f"{count} problem(s) marked as resolved.",
            level=messages.SUCCESS,
        )

    @admin.action(description="Mark selected as closed")
    def mark_closed(self, request, queryset):
        """Mark selected problems as closed."""
        count = queryset.update(status=AssetProblem.CLOSED)
        self.message_user(
            request,
            f"{count} problem(s) marked as closed.",
            level=messages.SUCCESS,
        )


@admin.register(LocationProblem)
class LocationProblemAdmin(admin.ModelAdmin):
    """Admin interface for location problem reports."""

    list_display = [
        "location",
        "severity_badge",
        "status_badge",
        "reported_by",
        "reported_at",
        "resolved_at",
    ]
    list_filter = ["status", "severity", "location", "reported_at"]
    search_fields = [
        "location__name",
        "description",
        "reported_by",
    ]
    readonly_fields = ["id", "reported_at", "updated_at"]
    date_hierarchy = "reported_at"

    fieldsets = (
        (
            "Problem Information",
            {
                "fields": (
                    "location",
                    "reported_by",
                    "description",
                    "severity",
                    "status",
                    "photo",
                    "paper_form_attachment",
                )
            },
        ),
        (
            "Promotion",
            {
                "fields": (
                    "work_order",
                    "third_party_work_order",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Resolution",
            {
                "fields": (
                    "resolution_notes",
                    "resolved_at",
                    "resolved_by",
                )
            },
        ),
        (
            "Metadata",
            {
                "fields": ("id", "reported_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            "reported": "#dc3545",
            "in_progress": "#ffc107",
            "resolved": "#28a745",
            "closed": "#6c757d",
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background: {}; color: white; padding: 4px 8px; '
            'border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    @admin.display(description="Severity")
    def severity_badge(self, obj):
        colors = {
            "low": "#6c757d",
            "medium": "#17a2b8",
            "high": "#fd7e14",
            "urgent": "#dc3545",
        }
        color = colors.get(obj.severity, "#6c757d")
        return format_html(
            '<span style="background: {}; color: white; padding: 4px 8px; '
            'border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_severity_display(),
        )


class MaintenanceMaterialInline(admin.TabularInline):
    model = MaintenanceMaterial
    extra = 1
    fields = ["name", "quantity", "unit", "estimated_cost_per_unit", "notes"]


class MaintenanceTaskInline(admin.TabularInline):
    model = MaintenanceTask
    extra = 1
    fields = ["order", "title", "description", "is_required"]
    ordering = ["order"]


@admin.register(MaintenanceItem)
class MaintenanceItemAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "asset",
        "interval_days",
        "last_completed_at",
        "is_overdue",
        "is_active",
    ]
    list_filter = ["is_active", "asset__category"]
    search_fields = ["title", "description", "asset__name", "asset__asset_tag"]
    autocomplete_fields = ["asset"]
    inlines = [MaintenanceMaterialInline, MaintenanceTaskInline]
    readonly_fields = ["is_overdue", "days_overdue", "next_due_at", "created_at", "updated_at"]
    fieldsets = [
        (None, {"fields": ["asset", "title", "description", "is_active"]}),
        ("Instructions", {"fields": ["instructions"]}),
        (
            "Schedule & Cost",
            {
                "fields": [
                    "interval_days",
                    "estimated_time_minutes",
                    "estimated_cost",
                    "last_completed_at",
                ]
            },
        ),
        ("Computed", {"fields": ["is_overdue", "days_overdue", "next_due_at"]}),
        ("Timestamps", {"fields": ["created_at", "updated_at"]}),
    ]


@admin.register(MaintenanceLog)
class MaintenanceLogAdmin(admin.ModelAdmin):
    list_display = [
        "maintenance_item",
        "completed_by",
        "completed_at",
        "time_spent_minutes",
        "cost_incurred",
    ]
    list_filter = ["completed_at"]
    search_fields = ["maintenance_item__title", "maintenance_item__asset__name", "notes"]
    readonly_fields = ["completed_at", "created_at"]


@admin.register(MaintenanceTask)
class MaintenanceTaskAdmin(admin.ModelAdmin):
    list_display = ["maintenance_item", "order", "title", "is_required", "created_at"]
    list_filter = ["is_required"]
    search_fields = ["title", "maintenance_item__title", "maintenance_item__asset__name"]
    ordering = ["maintenance_item", "order"]


class WorkOrderTaskCompletionInline(admin.TabularInline):
    model = WorkOrderTaskCompletion
    extra = 0
    fields = [
        "task_order",
        "task_title",
        "is_required",
        "is_completed",
        "completed_by",
        "completed_at",
        "notes",
    ]
    readonly_fields = ["task_order", "task_title", "is_required", "completed_at"]


class WorkOrderMaterialUsageInline(admin.TabularInline):
    model = WorkOrderMaterialUsage
    extra = 0
    fields = ["material_name", "quantity_planned", "unit", "was_used"]
    readonly_fields = ["material_name", "quantity_planned", "unit"]


class WorkOrderPhotoInline(admin.TabularInline):
    model = WorkOrderPhoto
    extra = 0
    fields = ["image", "caption", "uploaded_by", "uploaded_at"]
    readonly_fields = ["uploaded_at"]


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = [
        "short_id",
        "maintenance_item",
        "status",
        "due_date",
        "assigned_to",
        "completed_by_name",
        "is_overdue",
        "created_at",
    ]
    list_filter = ["status", "due_date"]
    search_fields = [
        "maintenance_item__title",
        "maintenance_item__asset__name",
        "completed_by_name",
        "notes",
    ]
    readonly_fields = ["short_id", "is_overdue", "created_at", "updated_at"]
    inlines = [WorkOrderTaskCompletionInline, WorkOrderMaterialUsageInline, WorkOrderPhotoInline]


@admin.register(WorkOrderSubmission)
class WorkOrderSubmissionAdmin(admin.ModelAdmin):
    list_display = ["id", "work_order", "status", "from_email", "received_at"]
    list_filter = ["status"]
    search_fields = ["from_email", "subject", "postmark_message_id", "work_order__id"]
    readonly_fields = [
        "id",
        "received_at",
        "parsed_fields",
        "parse_error",
        "postmark_message_id",
    ]


@admin.register(StockReconciliation)
class StockReconciliationAdmin(admin.ModelAdmin):
    list_display = [
        "reconciled_at",
        "item",
        "projected_count",
        "actual_count",
        "delta",
        "reason",
        "reconciled_by",
        "triggered_reorder",
    ]
    list_filter = ["reason", "reconciled_at", "reconciled_by"]
    search_fields = ["item__name", "item__sku", "notes"]
    readonly_fields = [
        "item",
        "projected_count",
        "actual_count",
        "delta",
        "reason",
        "notes",
        "reconciled_by",
        "reconciled_at",
        "triggered_reorder",
    ]
    date_hierarchy = "reconciled_at"


@admin.register(MaintenanceRecord)
class MaintenanceRecordAdmin(admin.ModelAdmin):
    list_display = [
        "completed_on",
        "asset",
        "title",
        "vendor",
        "performed_by_internal",
        "cost",
        "recorded_by",
        "recorded_at",
    ]
    list_filter = ["completed_on", "vendor", "recorded_at"]
    search_fields = [
        "title",
        "description",
        "invoice_number",
        "asset__name",
        "asset__asset_tag",
        "vendor__name",
        "notes",
    ]
    date_hierarchy = "completed_on"
    autocomplete_fields = ["asset", "vendor", "performed_by_internal", "recorded_by"]
