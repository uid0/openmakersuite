"""Core inventory models: locations, suppliers, catalog items, stock and serialized components."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.text import slugify

from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFit

if TYPE_CHECKING:
    from inventory.models.asset import Asset


def generate_sku() -> str:
    """Generate a unique SKU using UUID7 (time-ordered UUID)."""
    try:
        # UUID7 available in Python 3.11+
        return str(uuid.uuid7())
    except AttributeError:
        # Fallback to UUID4 for older Python versions
        return str(uuid.uuid4())


class Location(models.Model):
    """
    Physical storage locations in the makerspace.

    Examples: "Main Workshop", "Electronics Lab", "Wood Shop",
              "Storage Room A", "Shelf 3B"
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, help_text="Details about this location")
    is_active = models.BooleanField(
        default=True, help_text="Inactive locations are hidden from selection"
    )
    qr_code = models.ImageField(
        upload_to="inventory/location_qrcodes/",
        blank=True,
        null=True,
        help_text="QR code for location check-ins and feedback",
    )
    access_code = models.CharField(
        max_length=6,
        unique=True,
        blank=True,
        null=True,
        help_text="Unique 6-character code for manual entry (excludes I, 0, O, 1, L)",
    )
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Supplier(models.Model):
    """
    Supplier information for inventory items.

    Supplier types classify vendors by distribution:
    - Local: Local brick-and-mortar stores
    - Online: Online-only retailers
    - National: National chains with physical locations
    """

    # Supplier type choices
    LOCAL = "local"
    ONLINE = "online"
    NATIONAL = "national"

    SUPPLIER_TYPE_CHOICES = [
        (LOCAL, "Local"),
        (ONLINE, "Online"),
        (NATIONAL, "National"),
    ]

    # Ordering adapter — selects the artifact the order-pad export emits for this
    # supplier (op-svpq). ``none``/``generic_csv`` produce the vendor-agnostic
    # part#,qty pad; ``amazon`` produces an add-to-cart URL; ``hdsupply`` produces
    # a Part Number,Quantity CSV for HD Supply's Saved-List / Quick Order pad.
    ADAPTER_NONE = "none"
    ADAPTER_GENERIC_CSV = "generic_csv"
    ADAPTER_AMAZON = "amazon"
    ADAPTER_HDSUPPLY = "hdsupply"

    ORDERING_ADAPTER_CHOICES = [
        (ADAPTER_NONE, "None (generic part#,qty pad)"),
        (ADAPTER_GENERIC_CSV, "Generic CSV (part#,qty pad)"),
        (ADAPTER_AMAZON, "Amazon (add-to-cart URL)"),
        (ADAPTER_HDSUPPLY, "HD Supply (Part#,Qty CSV)"),
    ]

    name = models.CharField(max_length=200)
    supplier_type = models.CharField(
        max_length=20,
        choices=SUPPLIER_TYPE_CHOICES,
        help_text="Classification of supplier by distribution type",
    )
    ordering_adapter = models.CharField(
        max_length=20,
        choices=ORDERING_ADAPTER_CHOICES,
        default=ADAPTER_NONE,
        help_text=(
            "How the order-pad export builds an order for this supplier: a "
            "generic part#,qty pad, an Amazon add-to-cart URL, or an HD Supply "
            "Part#,Qty CSV."
        ),
    )
    website = models.URLField(blank=True)
    account_number = models.CharField(
        max_length=100,
        blank=True,
        help_text="Account number with this supplier (if applicable)",
    )
    tax_free_paperwork_filed = models.BooleanField(
        default=False,
        help_text="Check if tax-free paperwork has been filed with this supplier",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def items(self):
        """Backwards-compatible access to supplied items."""

        return self.supplied_items


class Category(models.Model):
    """
    Categories for organizing inventory items.

    Supports hierarchical categorization through the parent field,
    allowing for nested category structures.
    """

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    description = models.TextField(blank=True)
    color = models.CharField(
        max_length=7,
        blank=True,
        default="",
        help_text="Hex color code for category (e.g., #FF5733) - used for index card styling",
    )
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ["name"]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Auto-generate slug from name if not provided."""
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class InventoryItem(models.Model):
    """
    Core inventory item model.

    Stores information about items in the makerspace including:
    - Product details (name, description, images)
    - Stock levels and reorder thresholds
    - Supplier information and pricing
    - QR code for quick scanning
    - Usage tracking and lead time estimates
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    description = models.TextField()
    sku = models.CharField(
        max_length=100,
        blank=True,
        unique=True,
        help_text="Internal SKU - auto-generated if not provided",
    )

    # Image handling with automatic thumbnailing
    image = models.ImageField(
        upload_to="inventory/images/",
        null=True,
        blank=True,
        help_text="Upload image (supports JPEG, PNG, WebP)",
    )
    image_url = models.URLField(blank=True, help_text="URL to download image from (optional)")

    # Auto-generated thumbnail using ImageSpecField
    thumbnail = ImageSpecField(
        source="image",
        processors=[ResizeToFit(300, 300)],
        format="WEBP",
        options={"quality": 85},
    )

    # Organization
    category = models.ForeignKey(
        "Category", on_delete=models.SET_NULL, null=True, blank=True, related_name="items"
    )
    location = models.ForeignKey(
        "Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="items",
        help_text="Physical storage location",
    )
    shelf_position = models.CharField(
        max_length=20,
        blank=True,
        choices=[("top", "Top Shelf"), ("bottom", "Bottom Shelf")],
        help_text="Shelf position for index card reference",
    )

    # Suppliers - now many-to-many through ItemSupplier
    suppliers = models.ManyToManyField(
        "Supplier", through="ItemSupplier", related_name="supplied_items", blank=True
    )

    # Reordering information
    reorder_quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Quantity to reorder when stock is low",
    )
    current_stock = models.PositiveIntegerField(default=0, help_text="Current quantity in stock")
    minimum_stock = models.PositiveIntegerField(
        default=0, help_text="Minimum quantity before reordering"
    )

    # Case-based reordering (for bulk items like trashbags, toilet paper)
    use_case_based_reorder = models.BooleanField(
        default=False,
        help_text="Enable case/package-based reordering instead of individual unit reordering",
    )
    minimum_cases = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Minimum number of cases/packages before reordering (only used if case-based reordering is enabled)",
    )
    reorder_cases = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Number of cases/packages to reorder when stock is low (only used if case-based reordering is enabled)",
    )
    reorder_instruction = models.TextField(
        blank=True,
        help_text="Custom reorder instruction text for index cards (e.g., 'Reorder when last case is opened'). "
        "If empty, will show default text based on stock levels.",
    )

    # QR code data
    qr_code = models.ImageField(upload_to="inventory/qrcodes/", blank=True, null=True)
    last_scanned_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date and time when this inventory item was last scanned via QR code",
    )
    last_counted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent cycle count",
    )

    # Hazardous Materials Information
    is_hazardous = models.BooleanField(
        default=False,
        help_text="Check if this item is classified as a hazardous material",
    )
    msds_url = models.URLField(
        blank=True,
        verbose_name="Material Safety Data Sheet URL",
        help_text="Link to the Material Safety Data Sheet (MSDS) or Safety Data Sheet (SDS)",
    )
    msds_file = models.FileField(
        upload_to="inventory/msds/",
        blank=True,
        null=True,
        verbose_name="MSDS/SDS File Upload",
        help_text="Upload the Material Safety Data Sheet (MSDS/SDS) PDF or document file",
    )

    # NFPA Fire Diamond (National Fire Protection Association)
    # Scale: 0 = Minimal, 1 = Slight, 2 = Moderate, 3 = High, 4 = Extreme
    nfpa_health_hazard = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(4)],
        verbose_name="NFPA Health Hazard",
        help_text="Health hazard rating (0-4): 0=Minimal, 1=Slight, 2=Moderate, 3=High, 4=Extreme",
    )
    nfpa_fire_hazard = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(4)],
        verbose_name="NFPA Fire Hazard",
        help_text="Fire hazard rating (0-4): 0=Minimal, 1=Slight, 2=Moderate, 3=High, 4=Extreme",
    )
    nfpa_instability_hazard = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(4)],
        verbose_name="NFPA Instability Hazard",
        help_text="Instability/Reactivity hazard rating (0-4): 0=Minimal, 1=Slight, 2=Moderate, 3=High, 4=Extreme",
    )
    nfpa_special_hazards = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="NFPA Special Hazards",
        help_text="Special hazard symbols (e.g., W=Water Reactive, OX=Oxidizer, COR=Corrosive, ALK=Alkali, ACID=Acid, BIO=Biohazard, POI=Poison, RAD=Radioactive)",
    )

    # Ownership - can be owned by User, Group (SIG), or Space (makerspace itself)
    OWNERSHIP_TYPE_USER = "user"
    OWNERSHIP_TYPE_GROUP = "group"
    OWNERSHIP_TYPE_SPACE = "space"

    OWNERSHIP_TYPE_CHOICES = [
        (OWNERSHIP_TYPE_USER, "User"),
        (OWNERSHIP_TYPE_GROUP, "Group"),
        (OWNERSHIP_TYPE_SPACE, "Space"),
    ]

    ownership_type = models.CharField(
        max_length=10,
        choices=OWNERSHIP_TYPE_CHOICES,
        default=OWNERSHIP_TYPE_SPACE,
        help_text="Type of ownership for this inventory item",
    )
    owning_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_inventory_items",
        help_text="User that owns this inventory item (if applicable)",
    )
    owning_group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_inventory_items",
        help_text="Group (SIG) that owns this inventory item (if applicable)",
    )

    # Serialized-component tracking (EufyMake serial/lot tracking).
    # Aggregate stock (current_stock) still applies; when is_serialized is set,
    # individual physical units are additionally tracked as SerializedComponent
    # records that move through a lifecycle branched on serial_tracking_mode.
    SERIAL_TRACKING_CONSUMABLE = "consumable"
    SERIAL_TRACKING_REUSABLE = "reusable"

    SERIAL_TRACKING_MODE_CHOICES = [
        (SERIAL_TRACKING_CONSUMABLE, "Consumable"),
        (SERIAL_TRACKING_REUSABLE, "Reusable"),
    ]

    is_serialized = models.BooleanField(
        default=False,
        help_text="Track individual units of this item by serial number as SerializedComponents",
    )
    serial_tracking_mode = models.CharField(
        max_length=20,
        choices=SERIAL_TRACKING_MODE_CHOICES,
        default=SERIAL_TRACKING_CONSUMABLE,
        help_text=(
            "Consumable components are used up "
            "(received -> in_stock -> installed -> consumed -> disposed); "
            "reusable components can be installed/removed repeatedly and eventually retired"
        ),
    )

    # Metadata
    is_active = models.BooleanField(default=True)
    is_retired = models.BooleanField(
        default=False,
        help_text=(
            "Retired/phased-out: never flagged for reorder; "
            "hidden from the list once stock hits 0."
        ),
    )
    retired_at = models.DateTimeField(null=True, blank=True)
    is_requestable = models.BooleanField(
        default=True,
        help_text="Allow general users to request reorders for this item. "
        "Uncheck to restrict reorder requests to admins only.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["sku"]),
            models.Index(fields=["category"]),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Auto-generate SKU and trigger async image download if needed."""
        # Auto-generate SKU if not provided
        if not self.sku:
            self.sku = generate_sku()

        # Track if we need to download image (before save)
        should_download_image = self.image_url and not self.image

        # Save first to get an ID
        super().save(*args, **kwargs)

        # Trigger async image download after save
        if should_download_image:
            # Import here to avoid circular imports
            from ..tasks import download_image_from_url

            download_image_from_url.delay(str(self.id), self.image_url)

    @property
    def current_cases(self) -> float:
        """Calculate current number of cases/packages in stock."""
        if not self.use_case_based_reorder:
            return 0

        # Get quantity per package from primary supplier
        primary_supplier = self.primary_item_supplier
        if primary_supplier and primary_supplier.quantity_per_package > 0:
            return self.current_stock / primary_supplier.quantity_per_package

        # Fallback to 1 unit per package if no supplier info
        return self.current_stock

    @property
    def needs_reorder(self) -> bool:
        """Check if item stock is below minimum and needs reordering."""
        # Retired items are phased out: never flagged for reorder, regardless of
        # stock level. This is the central chokepoint for every property-based
        # low-stock surface (reorder_status, low_stock action, serializer
        # needs_reorder field, admin, AssetPart, Fixture refill).
        if self.is_retired:
            return False
        if self.use_case_based_reorder:
            # For case-based reordering, calculate current cases and compare to minimum cases
            current_cases = self.current_cases
            return current_cases <= self.minimum_cases
        else:
            # Traditional individual unit reordering
            return self.current_stock <= self.minimum_stock

    def get_active_reorder_request(self):
        """Get the most recent active (pending/approved/ordered) reorder request for this item."""
        return (
            self.reorder_requests.filter(status__in=["pending", "approved", "ordered"])
            .order_by("-requested_at")
            .first()
        )

    def has_pending_reorder(self) -> bool:
        """Check if item has any pending, approved, or ordered reorder requests."""
        return self.reorder_requests.filter(status__in=["pending", "approved", "ordered"]).exists()

    def get_expected_delivery_date(self):
        """Calculate expected delivery date for ordered items."""
        ordered_request = (
            self.reorder_requests.filter(status="ordered").order_by("-ordered_at").first()
        )
        if ordered_request and ordered_request.ordered_at and self.average_lead_time:
            from datetime import timedelta

            return ordered_request.ordered_at.date() + timedelta(days=self.average_lead_time)
        return None

    @property
    def reorder_status(self) -> str:
        """Get current reorder status for this item."""
        if not self.needs_reorder:
            return "well_stocked"

        active_request = self.get_active_reorder_request()
        if not active_request:
            return "needs_order"

        return active_request.status  # 'pending', 'approved', or 'ordered'

    @property
    def lowest_unit_cost(self) -> Optional[Decimal]:
        """Get the lowest unit cost from all suppliers."""
        costs = [
            item_supplier.unit_cost
            for item_supplier in self.item_suppliers.filter(unit_cost__isnull=False)
        ]
        return min(costs) if costs else None

    @property
    def total_value(self) -> Decimal:
        """Calculate total value of current stock using lowest unit cost."""
        cost = self.lowest_unit_cost
        if cost:
            return self.current_stock * cost
        return Decimal("0")

    def average_unit_cost(self) -> Optional[Decimal]:
        """Arithmetic mean of unit_cost across all active, non-null suppliers."""
        from django.db.models import Avg

        agg = self.item_suppliers.filter(is_active=True, unit_cost__isnull=False).aggregate(
            avg=Avg("unit_cost")
        )
        return agg["avg"]

    def average_total_value(self) -> Decimal:
        """Total stock value using the average supplier unit cost."""
        avg = self.average_unit_cost()
        if avg is None:
            return Decimal("0")
        return Decimal(self.current_stock) * avg

    @property
    def primary_supplier(self) -> Optional[Supplier]:
        """Get the primary (preferred) supplier."""
        link = self.primary_item_supplier
        return link.supplier if link else None

    @property
    def primary_item_supplier(self) -> Optional["ItemSupplier"]:
        """Return the preferred ItemSupplier relationship if available."""

        item_supplier = (
            self.item_suppliers.select_related("supplier").filter(is_primary=True).first()
        )
        if item_supplier:
            return item_supplier
        return self.item_suppliers.select_related("supplier").first()

    @property
    def supplier(self) -> Optional[Supplier]:
        """Backwards-compatible access to the primary supplier."""

        return self.primary_supplier

    @property
    def supplier_sku(self) -> Optional[str]:
        """Expose the primary supplier SKU for compatibility with legacy code."""

        link = self.primary_item_supplier
        return link.supplier_sku if link else None

    @property
    def supplier_url(self) -> Optional[str]:
        """Expose the primary supplier URL for compatibility."""

        link = self.primary_item_supplier
        return link.supplier_url if link else None

    def is_user_admin(self, user) -> bool:
        """Check if user is a system admin (staff or superuser)."""
        return user.is_authenticated and (user.is_staff or user.is_superuser)

    def is_user_in_logistics(self, user) -> bool:
        """Check if user is in the Logistics group."""
        if not user.is_authenticated:
            return False
        try:
            logistics_group = Group.objects.get(name="Logistics")
            return logistics_group in user.groups.all()
        except Group.DoesNotExist:
            return False

    def is_user_group_admin(self, user) -> bool:
        """Check if user is a SIG admin for the item's owning group."""
        if not user.is_authenticated or not self.owning_group:
            return False
        # Use the permission utility to check SIG admin status
        from membership.utils import is_sig_admin

        return is_sig_admin(user, self.owning_group)

    def can_user_modify(self, user) -> bool:
        """
        Check if user can modify this inventory item.

        Users can modify an inventory item if:
        - They are a system admin (staff/superuser)
        - They are in the Logistics group
        - They are a SIG admin of the item's owning group

        Args:
            user: The user to check

        Returns:
            bool: True if user can modify the item, False otherwise
        """
        from membership.utils import can_manage_sig_inventory

        return can_manage_sig_inventory(user, self)

    @property
    def unit_cost(self) -> Optional[Decimal]:
        """Provide the primary supplier's unit cost when available."""

        link = self.primary_item_supplier
        return link.unit_cost if link else None

    @property
    def average_lead_time(self) -> Optional[int]:
        """Expose the primary supplier's lead time for compatibility."""

        link = self.primary_item_supplier
        return link.average_lead_time if link else None

    @property
    def package_cost(self) -> Optional[Decimal]:
        """Expose the primary supplier's package cost when available."""

        link = self.primary_item_supplier
        return link.package_cost if link else None

    @property
    def quantity_per_package(self) -> Optional[int]:
        """Expose the primary supplier's quantity per package when available."""

        link = self.primary_item_supplier
        return link.quantity_per_package if link else None

    # Hazardous Materials Helper Methods

    @property
    def nfpa_fire_diamond_display(self) -> str:
        """Return a formatted display of the NFPA Fire Diamond ratings."""
        if not self.is_hazardous:
            return "Not Hazardous"

        parts = []
        if self.nfpa_health_hazard is not None:
            parts.append(f"Health: {self.nfpa_health_hazard}")
        if self.nfpa_fire_hazard is not None:
            parts.append(f"Fire: {self.nfpa_fire_hazard}")
        if self.nfpa_instability_hazard is not None:
            parts.append(f"Instability: {self.nfpa_instability_hazard}")
        if self.nfpa_special_hazards:
            parts.append(f"Special: {self.nfpa_special_hazards}")

        return " | ".join(parts) if parts else "NFPA ratings not specified"

    @property
    def has_complete_nfpa_data(self) -> bool:
        """Check if all required NFPA Fire Diamond data is provided."""
        if not self.is_hazardous:
            return True  # Not hazardous items don't need NFPA data

        return all(
            [
                self.nfpa_health_hazard is not None,
                self.nfpa_fire_hazard is not None,
                self.nfpa_instability_hazard is not None,
            ]
        )

    @property
    def hazmat_compliance_status(self) -> str:
        """Return compliance status for hazardous materials documentation."""
        if not self.is_hazardous:
            return "Not Applicable - Not Hazardous"

        missing_items = []

        # Check if either URL or file is provided
        if not self.msds_url and not self.msds_file:
            missing_items.append("MSDS/SDS (URL or File)")

        if not self.has_complete_nfpa_data:
            missing_nfpa = []
            if self.nfpa_health_hazard is None:
                missing_nfpa.append("Health")
            if self.nfpa_fire_hazard is None:
                missing_nfpa.append("Fire")
            if self.nfpa_instability_hazard is None:
                missing_nfpa.append("Instability")
            missing_items.append(f"NFPA ({', '.join(missing_nfpa)})")

        if missing_items:
            return f"Incomplete - Missing: {', '.join(missing_items)}"

        return "Complete"

    def get_nfpa_hazard_level_display(self, hazard_type: str) -> str:
        """Get human-readable display for NFPA hazard levels."""
        level_map = {0: "Minimal", 1: "Slight", 2: "Moderate", 3: "High", 4: "Extreme"}

        level = None
        if hazard_type == "health":
            level = self.nfpa_health_hazard
        elif hazard_type == "fire":
            level = self.nfpa_fire_hazard
        elif hazard_type == "instability":
            level = self.nfpa_instability_hazard

        if level is not None and level in level_map:
            return f"{level} - {level_map[level]}"

        return "Not specified"


class ItemSupplier(models.Model):
    """
    Through model for Item-Supplier many-to-many relationship.

    Allows each item to have multiple suppliers with different:
    - SKUs
    - Prices
    - Lead times
    - URLs
    - Package dimensions and weights
    - Quantities per package
    """

    item = models.ForeignKey(
        "InventoryItem", on_delete=models.CASCADE, related_name="item_suppliers"
    )
    supplier = models.ForeignKey(
        "Supplier", on_delete=models.CASCADE, related_name="supplier_items"
    )

    # Supplier-specific information
    supplier_sku = models.CharField(max_length=100, help_text="Supplier's product SKU/ID")
    supplier_url = models.URLField(
        blank=True, help_text="Direct link to product on supplier's website"
    )
    package_upc = models.CharField(
        max_length=32,
        blank=True,
        help_text="UPC/EAN printed on the packaged quantity received from this supplier",
    )
    unit_upc = models.CharField(
        max_length=32,
        blank=True,
        help_text="UPC/EAN for individual units when different from the package barcode",
    )

    # Package dimensions and weight (supplier-specific)
    package_height = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Package height in inches",
    )
    package_width = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Package width in inches",
    )
    package_length = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Package length in inches",
    )
    package_weight = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Package weight in pounds",
    )

    quantity_per_package = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Number of individual units included in one package from this supplier",
    )
    unit_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Cost per individual unit from this supplier (auto-calculated from package cost)",
    )
    package_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Total cost for one package from this supplier (what you actually pay)",
    )
    average_lead_time = models.PositiveIntegerField(
        default=7, help_text="Average lead time in days from this supplier"
    )

    # Preferences
    is_primary = models.BooleanField(
        default=False, help_text="Preferred/primary supplier for this item"
    )
    is_active = models.BooleanField(
        default=True, help_text="Whether this supplier option is currently active"
    )
    is_discontinued = models.BooleanField(
        default=False,
        help_text="Whether this item has been discontinued by this supplier (no longer available for purchase)",
    )

    # Metadata
    notes = models.TextField(blank=True, help_text="Notes about this supplier for this item")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "unit_cost"]
        unique_together = [["item", "supplier"]]
        indexes = [
            models.Index(fields=["item", "is_primary"]),
            models.Index(fields=["item", "unit_cost"]),
        ]

    def __str__(self) -> str:
        primary = " (Primary)" if self.is_primary else ""
        return f"{self.item.name} - {self.supplier.name}{primary}"

    @property
    def package_volume(self) -> Optional[Decimal]:
        """Calculate package volume in cubic inches."""
        if all(
            [
                self.package_height is not None,
                self.package_width is not None,
                self.package_length is not None,
            ]
        ):
            return self.package_height * self.package_width * self.package_length
        return None

    @property
    def package_dimensions_display(self) -> str:
        """Return formatted display of package dimensions."""
        dims = []
        if self.package_length:
            dims.append(f'L: {self.package_length}"')
        if self.package_width:
            dims.append(f'W: {self.package_width}"')
        if self.package_height:
            dims.append(f'H: {self.package_height}"')
        if self.package_weight:
            dims.append(f"Weight: {self.package_weight} lbs")

        return " | ".join(dims) if dims else "No dimensions specified"

    @property
    def unit_weight(self) -> Optional[Decimal]:
        """Calculate weight per individual unit in ounces."""
        if self.package_weight and self.quantity_per_package > 0:
            # Convert pounds to ounces (1 lb = 16 oz)
            package_weight_oz = self.package_weight * 16
            return package_weight_oz / self.quantity_per_package
        return None

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        Ensure only one primary supplier per item and auto-calculate unit cost.

        If package_cost is provided, calculate unit_cost automatically.
        If only unit_cost is provided (backward compatibility), calculate package_cost.
        """
        # Auto-calculate unit cost from package cost
        if self.package_cost is not None and self.quantity_per_package > 0:
            self.unit_cost = self.package_cost / self.quantity_per_package
        # Backward compatibility: if only unit_cost is provided, calculate package_cost
        elif (
            self.unit_cost is not None
            and self.package_cost is None
            and self.quantity_per_package > 0
        ):
            self.package_cost = self.unit_cost * self.quantity_per_package

        if self.is_primary:
            # Remove primary flag from other suppliers for this item
            ItemSupplier.objects.filter(item=self.item, is_primary=True).exclude(pk=self.pk).update(
                is_primary=False
            )
        # Check if this is a new record or if pricing has changed
        is_new = self.pk is None
        price_changed = False

        if not is_new:
            # Get the old values from the database
            old_instance = ItemSupplier.objects.get(pk=self.pk)
            price_changed = (
                old_instance.unit_cost != self.unit_cost
                or old_instance.package_cost != self.package_cost
                or old_instance.quantity_per_package != self.quantity_per_package
            )

        super().save(*args, **kwargs)

        # Create price history record if this is new or if pricing changed
        if is_new or price_changed:
            change_type = "created" if is_new else "updated"
            PriceHistory.objects.create(
                item_supplier=self,
                unit_cost=self.unit_cost,
                package_cost=self.package_cost,
                quantity_per_package=self.quantity_per_package,
                change_type=change_type,
            )


class PriceHistory(models.Model):
    """
    Track historical pricing data for item-supplier relationships.

    This model maintains a historical record of all price changes,
    allowing for trend analysis and price tracking over time.
    """

    CHANGE_TYPE_CHOICES = [
        ("created", "Initial Price"),
        ("updated", "Price Update"),
        ("supplier_changed", "Supplier Info Changed"),
    ]

    item_supplier = models.ForeignKey(
        "ItemSupplier", on_delete=models.CASCADE, related_name="price_history"
    )
    unit_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Unit cost at time of this record",
    )
    package_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Package cost at time of this record",
    )
    quantity_per_package = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Quantity per package at time of this record",
    )
    change_type = models.CharField(
        max_length=20,
        choices=CHANGE_TYPE_CHOICES,
        default="updated",
        help_text="Type of change that triggered this history record",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, help_text="Optional notes about this price change")

    class Meta:
        verbose_name_plural = "Price histories"
        ordering = ["-recorded_at"]
        indexes = [
            models.Index(fields=["item_supplier", "-recorded_at"]),
            models.Index(fields=["recorded_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.item_supplier.item.name} - {self.item_supplier.supplier.name} on {self.recorded_at.date()}"

    @property
    def price_change_percentage(self) -> Optional[Decimal]:
        """Calculate percentage change from previous record."""
        previous = PriceHistory.objects.filter(
            item_supplier=self.item_supplier, recorded_at__lt=self.recorded_at
        ).first()

        if previous and previous.unit_cost and self.unit_cost:
            old_cost = previous.unit_cost
            new_cost = self.unit_cost
            change = ((new_cost - old_cost) / old_cost) * 100
            return round(change, 2)
        return None


class UsageLog(models.Model):
    """
    Track usage/consumption of inventory items.

    Usage logs are used to:
    - Calculate reorder predictions based on consumption patterns
    - Estimate lead times for reordering
    - Track item usage history
    """

    item = models.ForeignKey("InventoryItem", on_delete=models.CASCADE, related_name="usage_logs")
    quantity_used = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    usage_date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-usage_date"]
        indexes = [
            models.Index(fields=["item", "-usage_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.item.name} - {self.quantity_used} units on {self.usage_date.date()}"


class StockReconciliation(models.Model):
    """Audit record for a manual stock reconciliation of an inventory item.

    Each row captures a single count: the projected (pre-count) stock, the
    actual physical count, and the delta. History is additive — corrections
    are new rows with reason='miscounted', never edits to existing rows.
    """

    REASON_LOST = "lost"
    REASON_DAMAGED = "damaged"
    REASON_MISCOUNTED = "miscounted"
    REASON_USED_WITHOUT_SCAN = "used_without_scan"
    REASON_FOUND = "found"
    REASON_VISION_SUPPLY_CHECK = "vision_supply_check"
    REASON_OTHER = "other"

    REASON_CHOICES = [
        (REASON_LOST, "Lost"),
        (REASON_DAMAGED, "Damaged"),
        (REASON_MISCOUNTED, "Miscounted"),
        (REASON_USED_WITHOUT_SCAN, "Used without scanning"),
        (REASON_FOUND, "Found (positive delta)"),
        (REASON_VISION_SUPPLY_CHECK, "Vision supply check"),
        (REASON_OTHER, "Other"),
    ]

    item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.CASCADE,
        related_name="reconciliations",
    )
    projected_count = models.IntegerField(help_text="current_stock at the time of reconciliation")
    actual_count = models.IntegerField(help_text="Physically counted quantity")
    delta = models.IntegerField(help_text="actual_count - projected_count (signed)")
    reason = models.CharField(max_length=32, choices=REASON_CHOICES)
    notes = models.TextField(blank=True)
    reconciled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="stock_reconciliations",
    )
    reconciled_at = models.DateTimeField(auto_now_add=True)
    triggered_reorder = models.ForeignKey(
        "reorder_queue.ReorderRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Auto-created reorder when actual_count <= minimum_stock",
    )

    class Meta:
        ordering = ["-reconciled_at"]
        indexes = [
            models.Index(fields=["item", "-reconciled_at"]),
            models.Index(fields=["-reconciled_at"]),
            models.Index(fields=["reason"]),
        ]

    def __str__(self) -> str:
        return (
            f"{self.item.name}: {self.projected_count} -> {self.actual_count} "
            f"({self.delta:+d}) [{self.reason}]"
        )


class SerializedComponent(models.Model):
    """
    An individual, serial-numbered physical unit of a serialized InventoryItem.

    ``InventoryItem`` tracks aggregate stock counts; a ``SerializedComponent``
    tracks one physical unit by serial number as it moves through its lifecycle.
    The set of legal transitions branches on the owning item's
    ``serial_tracking_mode``:

    * consumable: ``received -> in_stock -> installed -> consumed -> disposed``
    * reusable:   adds a repeatable ``installed <-> removed`` cycle plus a
      ``retired`` terminal state before disposal.

    Every transition is recorded as a :class:`ComponentUsageEvent` so the full
    provenance and usage history of a unit is auditable.
    """

    # Lifecycle status choices
    RECEIVED = "received"
    IN_STOCK = "in_stock"
    INSTALLED = "installed"
    REMOVED = "removed"
    CONSUMED = "consumed"
    RETIRED = "retired"
    DISPOSED = "disposed"

    STATUS_CHOICES = [
        (RECEIVED, "Received"),
        (IN_STOCK, "In Stock"),
        (INSTALLED, "Installed"),
        (REMOVED, "Removed"),
        (CONSUMED, "Consumed"),
        (RETIRED, "Retired"),
        (DISPOSED, "Disposed"),
    ]

    # Lifecycle action choices (also used by ComponentUsageEvent.action)
    ACTION_RECEIVE = "receive"
    ACTION_INSTALL = "install"
    ACTION_REMOVE = "remove"
    ACTION_CONSUME = "consume"
    ACTION_RETIRE = "retire"
    ACTION_DISPOSE = "dispose"

    ACTION_CHOICES = [
        (ACTION_RECEIVE, "Receive"),
        (ACTION_INSTALL, "Install"),
        (ACTION_REMOVE, "Remove"),
        (ACTION_CONSUME, "Consume"),
        (ACTION_RETIRE, "Retire"),
        (ACTION_DISPOSE, "Dispose"),
    ]

    # (current_status, action) -> resulting_status, keyed by tracking mode.
    _TRANSITIONS = {
        InventoryItem.SERIAL_TRACKING_CONSUMABLE: {
            (RECEIVED, ACTION_RECEIVE): IN_STOCK,
            (IN_STOCK, ACTION_INSTALL): INSTALLED,
            (INSTALLED, ACTION_CONSUME): CONSUMED,
            (CONSUMED, ACTION_DISPOSE): DISPOSED,
        },
        InventoryItem.SERIAL_TRACKING_REUSABLE: {
            (RECEIVED, ACTION_RECEIVE): IN_STOCK,
            (IN_STOCK, ACTION_INSTALL): INSTALLED,
            (INSTALLED, ACTION_REMOVE): REMOVED,
            (REMOVED, ACTION_INSTALL): INSTALLED,
            (IN_STOCK, ACTION_RETIRE): RETIRED,
            (INSTALLED, ACTION_RETIRE): RETIRED,
            (REMOVED, ACTION_RETIRE): RETIRED,
            (RETIRED, ACTION_DISPOSE): DISPOSED,
        },
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.CASCADE,
        related_name="serialized_components",
        help_text="The serialized inventory item this physical unit is an instance of",
    )
    serial_number = models.CharField(
        max_length=200,
        help_text="Manufacturer or internal serial number uniquely identifying this unit",
    )
    lot = models.CharField(
        max_length=200,
        blank=True,
        help_text="Optional batch/lot number for grouped provenance",
    )
    expiration_date = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "Optional expiry/best-by date for this unit. Recorded and displayed "
            "only — an expired unit still counts normally in available/on-hand "
            "and drives no forecast effect or alert."
        ),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=RECEIVED,
        help_text="Current lifecycle status of this unit",
    )
    installed_in_asset = models.ForeignKey(
        "Asset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="installed_components",
        help_text="Asset this component is currently installed in (if installed)",
    )

    # Lifecycle timestamps
    received_at = models.DateTimeField(
        null=True, blank=True, help_text="When this unit was received into stock"
    )
    installed_at = models.DateTimeField(
        null=True, blank=True, help_text="When this unit was most recently installed"
    )
    disposed_at = models.DateTimeField(
        null=True, blank=True, help_text="When this unit was disposed"
    )

    # Provenance — where this unit came from (a scanned delivery line and/or PO line)
    provenance_delivery_item = models.ForeignKey(
        "reorder_queue.DeliveryItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="serialized_components",
        help_text="Delivery line item this unit was received against (provenance)",
    )
    provenance_purchase_order_item = models.ForeignKey(
        "reorder_queue.PurchaseOrderItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="serialized_components",
        help_text="Purchase-order line item this unit was ordered against (provenance)",
    )

    disposal_reason = models.CharField(
        max_length=500,
        blank=True,
        help_text="Reason recorded when the unit was disposed",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["item", "serial_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "serial_number"],
                name="uniq_serialized_component_item_serial",
            ),
        ]
        indexes = [
            models.Index(fields=["item", "status"]),
            models.Index(fields=["status"]),
            models.Index(fields=["installed_in_asset"]),
        ]

    def __str__(self) -> str:
        return f"{self.serial_number} ({self.get_status_display()})"

    @property
    def tracking_mode(self) -> str:
        """The lifecycle mode inherited from the owning item."""
        return self.item.serial_tracking_mode

    @property
    def available_actions(self) -> list[str]:
        """Actions currently legal from this unit's status, given its mode."""
        table = self._TRANSITIONS.get(self.tracking_mode, {})
        return [action for (status_, action) in table if status_ == self.status]

    def can_apply(self, action: str) -> bool:
        """Whether ``action`` is a legal transition from the current status."""
        table = self._TRANSITIONS.get(self.tracking_mode, {})
        return (self.status, action) in table

    def apply_action(
        self,
        action: str,
        *,
        asset: Optional["Asset"] = None,
        disposal_reason: str = "",
        actor: Any = None,
        notes: str = "",
        at: Any = None,
    ) -> "ComponentUsageEvent":
        """Apply a lifecycle ``action``, mutating state and logging an event.

        Validates the transition against the mode-specific table, applies the
        relevant side effects (asset install/remove, timestamp stamping,
        disposal reason), persists the unit, and records a
        :class:`ComponentUsageEvent`. Raises :class:`ValidationError` if the
        transition is illegal or a required argument is missing.
        """
        table = self._TRANSITIONS.get(self.tracking_mode, {})
        to_status = table.get((self.status, action))
        if to_status is None:
            raise ValidationError(
                f"Cannot '{action}' a {self.tracking_mode} component that is "
                f"{self.get_status_display()} (serial {self.serial_number})."
            )

        now = at or timezone.now()
        event_asset = None

        if action == self.ACTION_RECEIVE:
            if not self.received_at:
                self.received_at = now
        elif action == self.ACTION_INSTALL:
            if asset is None:
                raise ValidationError("An asset is required to install a component.")
            self.installed_in_asset = asset
            self.installed_at = now
            event_asset = asset
        elif action == self.ACTION_REMOVE:
            event_asset = self.installed_in_asset
            self.installed_in_asset = None
        elif action == self.ACTION_CONSUME:
            event_asset = self.installed_in_asset
            self.installed_in_asset = None
        elif action == self.ACTION_RETIRE:
            event_asset = self.installed_in_asset
            self.installed_in_asset = None
        elif action == self.ACTION_DISPOSE:
            if not disposal_reason:
                raise ValidationError("A disposal reason is required to dispose a component.")
            self.disposal_reason = disposal_reason
            self.disposed_at = now
            event_asset = self.installed_in_asset
            self.installed_in_asset = None

        self.status = to_status
        with transaction.atomic():
            self.save()
            return ComponentUsageEvent.objects.create(
                component=self,
                asset=event_asset,
                action=action,
                at=now,
                actor=actor,
                notes=notes,
            )


class ComponentUsageEvent(models.Model):
    """
    Immutable audit-log entry recording one lifecycle action on a component.

    Written automatically by :meth:`SerializedComponent.apply_action` for every
    receive/install/remove/consume/retire/dispose transition, capturing the
    asset involved (when relevant), the actor, and free-text notes.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    component = models.ForeignKey(
        "SerializedComponent",
        on_delete=models.CASCADE,
        related_name="usage_events",
        help_text="The serialized component this event applies to",
    )
    asset = models.ForeignKey(
        "Asset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="component_usage_events",
        help_text="Asset involved in the action (for install/remove and later transitions)",
    )
    action = models.CharField(
        max_length=20,
        choices=SerializedComponent.ACTION_CHOICES,
        help_text="The lifecycle action performed",
    )
    at = models.DateTimeField(
        default=timezone.now,
        help_text="When the action occurred",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="component_usage_events",
        help_text="User who performed the action (if known)",
    )
    notes = models.TextField(blank=True, help_text="Optional free-text notes about the action")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-created_at"]
        indexes = [
            models.Index(fields=["component", "at"]),
            models.Index(fields=["action"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_action_display()} — {self.component.serial_number} @ {self.at:%Y-%m-%d}"
