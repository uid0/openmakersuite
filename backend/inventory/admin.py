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
    Category,
    InventoryItem,
    ItemSupplier,
    Location,
    PriceHistory,
    Supplier,
    UsageLog,
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
        ("Product Details", {"fields": ("package_upc", "unit_upc", "quantity_per_package")}),
        (
            "Package Dimensions",
            {"fields": ("package_height", "package_width", "package_length", "package_weight")},
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
                api_url,
            )
        return "—"

    api_link.short_description = "API Link"

    def price_history_link(self, obj):
        """Create a link to the price history for this item-supplier relationship."""
        if obj.pk:
            api_url = f"/api/inventory/item-suppliers/{obj.pk}/price_history/"
            return format_html(
                '<a href="{}" target="_blank" style="background: #417690; color: white; padding: 4px 8px; text-decoration: none; border-radius: 3px;">📊 Price History</a>',
                api_url,
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
        ("Stock Information", {"fields": ("current_stock", "minimum_stock", "reorder_quantity")}),
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

    def api_link(self, obj):
        """Create a link to the DRF API endpoint for this InventoryItem."""
        if obj.pk:
            api_url = f"/api/inventory/items/{obj.pk}/"
            return format_html(
                '<a href="{}" target="_blank" style="background: #007cba; color: white; padding: 4px 8px; text-decoration: none; border-radius: 3px;">📡 See API Object</a>',
                api_url,
            )
        return "—"

    api_link.short_description = "API Link"

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

    reorder_link.short_description = "Reorder Request"

    def index_card_preview(self, obj):
        """Create a preview window for the index card."""
        if not obj.pk:
            return "—"

        preview_url = "/api/index-cards/preview/"

        # Create a button that opens a modal with the preview
        preview_html = format_html(
            """
            <button type="button"
                    onclick="showIndexCardPreview('{}', '{}')"
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
                    <button onclick="closeIndexCardPreview()"
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
                function showIndexCardPreview(itemId, previewUrl) {{
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

                function closeIndexCardPreview() {{
                    document.getElementById('indexCardPreviewModal').style.display = 'none';
                }}

                // Close modal when clicking outside
                window.onclick = function(event) {{
                    const modal = document.getElementById('indexCardPreviewModal');
                    if (event.target == modal) {{
                        modal.style.display = 'none';
                    }}
                }}
            </script>
            """,
            str(obj.pk),
            preview_url,
        )
        return preview_html

    index_card_preview.short_description = "Index Card Preview"

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

    hazmat_status_icon.short_description = "Hazmat"

    def nfpa_fire_diamond_display(self, obj):
        """Display NFPA Fire Diamond ratings in admin."""
        return obj.nfpa_fire_diamond_display

    nfpa_fire_diamond_display.short_description = "NFPA Fire Diamond"

    def hazmat_compliance_status(self, obj):
        """Display hazmat compliance status in admin."""
        status = obj.hazmat_compliance_status
        if "Complete" in status:
            return format_html('<span style="color: #28a745; font-weight: bold;">{}</span>', status)
        elif "Incomplete" in status:
            return format_html('<span style="color: #dc3545; font-weight: bold;">{}</span>', status)
        else:
            return format_html('<span style="color: #6c757d;">{}</span>', status)

    hazmat_compliance_status.short_description = "Compliance Status"


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
        "status_badge",
        "category",
        "location",
        "display_manufacturer",
        "acquisition_display",
        "is_active",
        "api_link",
    ]
    list_filter = ["status", "category", "location", "is_donation", "is_active", "manufacturer"]
    search_fields = [
        "name",
        "description",
        "serial_number",
        "asset_tag",
        "manufacturer_name",
        "donor_name",
    ]
    readonly_fields = [
        "id",
        "asset_tag",
        "qr_code",
        "thumbnail",
        "display_manufacturer",
        "acquisition_display",
        "age_in_days",
        "created_at",
        "updated_at",
        "api_link",
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

    status_badge.short_description = "Status"

    def api_link(self, obj):
        """Create a link to the DRF API endpoint for this Asset."""
        if obj.pk:
            api_url = f"/api/inventory/assets/{obj.pk}/"
            return format_html(
                '<a href="{}" target="_blank" style="background: #007cba; color: white; padding: 4px 8px; text-decoration: none; border-radius: 3px;">📡 See API Object</a>',
                api_url,
            )
        return "—"

    api_link.short_description = "API Link"

    actions = ["duplicate_asset"]

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

    duplicate_asset.short_description = "Duplicate selected asset (enter new serial number)"

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
