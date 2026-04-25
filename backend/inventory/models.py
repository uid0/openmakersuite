"""
Models for inventory management.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils.text import slugify

from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFit


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

    name = models.CharField(max_length=200)
    supplier_type = models.CharField(
        max_length=20,
        choices=SUPPLIER_TYPE_CHOICES,
        help_text="Classification of supplier by distribution type",
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
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="items"
    )
    location = models.ForeignKey(
        Location,
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
        Supplier, through="ItemSupplier", related_name="supplied_items", blank=True
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
    access_code = models.CharField(
        max_length=6,
        unique=True,
        blank=True,
        null=True,
        help_text="Unique 6-character code for manual entry (excludes I, 0, O, 1, L)",
    )
    last_scanned_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date and time when this inventory item was last scanned via QR code",
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

    # Metadata
    is_active = models.BooleanField(default=True)
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
            from .tasks import download_image_from_url

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

    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="item_suppliers")
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="supplier_items")

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
        ItemSupplier, on_delete=models.CASCADE, related_name="price_history"
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

    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="usage_logs")
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


class Asset(models.Model):
    """
    Track individual hard assets in the makerspace.

    Assets represent physical items with unique identifiers like:
    - Equipment (3D printers, CNC machines, hand tools)
    - Electronics (oscilloscopes, multimeters, computers)
    - Furniture (workbenches, chairs, storage cabinets)
    - Donated items with specific serial numbers

    Each asset can optionally be linked to an InventoryItem to indicate
    what type of item it is (e.g., this laptop is a Dell XPS 15).
    """

    # Status choices
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    RETIRED = "retired"
    LOST = "lost"
    DONATED_OUT = "donated_out"

    STATUS_CHOICES = [
        (IMPLEMENTING, "Implementing"),
        (TESTING, "Testing"),
        (ACTIVE, "Active"),
        (MAINTENANCE, "Under Maintenance"),
        (RETIRED, "Retired"),
        (LOST, "Lost"),
        (DONATED_OUT, "Donated Out"),
    ]

    # Note: Operational status, lockout, and authorization fields have been moved to the forgekey app

    # Identification
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, help_text="Asset name or model")
    description = models.TextField(blank=True, help_text="Detailed description of the asset")
    serial_number = models.CharField(
        max_length=100,
        blank=True,
        help_text="Serial number or unique identifier (if available)",
    )
    asset_tag = models.CharField(
        max_length=50,
        blank=True,
        unique=True,
        help_text="Internal asset tag number",
    )

    # Relationship to inventory item type
    inventory_item = models.ForeignKey(
        InventoryItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assets",
        help_text="Link to inventory item type (optional)",
    )

    # Organization
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assets",
        help_text="Category for organizing assets",
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assets",
        help_text="Current physical location of the asset",
    )

    # Manufacturer and supplier information
    manufacturer = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manufactured_assets",
        help_text="Manufacturer or supplier of the asset",
    )
    manufacturer_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Manufacturer name (use if not in supplier list)",
    )

    # Acquisition information
    date_received = models.DateField(
        null=True,
        blank=True,
        help_text="Date the asset was received or acquired",
    )
    amount_paid = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Amount paid for the asset (0.00 for donations)",
    )
    is_donation = models.BooleanField(
        default=False,
        help_text="Check if this asset was donated",
    )
    donor_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Name of donor (if applicable)",
    )

    # Product information
    product_url = models.URLField(
        blank=True,
        help_text="Link to product page or documentation",
    )
    wiki_page_url = models.URLField(
        blank=True,
        help_text="Link to wiki page for this asset (shown to all users when scanning QR code)",
    )

    # Maintenance
    maintenance_plan = models.TextField(
        blank=True,
        help_text="Maintenance schedule and procedures (visible to authenticated users only)",
    )

    # Operational requirements
    circuit = models.CharField(
        max_length=100,
        blank=True,
        help_text="Circuit identification where this device is plugged in",
    )
    needs_compressed_air = models.BooleanField(
        default=False,
        help_text="Device requires compressed air to operate",
    )
    needs_ventilation = models.BooleanField(
        default=False,
        help_text="Device requires ventilation when running",
    )
    is_chargeable = models.BooleanField(
        default=False,
        help_text="Device is chargeable to members (for billing purposes)",
    )
    mac_address = models.CharField(
        max_length=17,
        blank=True,
        validators=[
            RegexValidator(
                regex=r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$",
                message="Enter a valid MAC address (e.g., AA:BB:CC:11:22:33).",
            )
        ],
        help_text="MAC address for electronic/networked assets (e.g., AA:BB:CC:11:22:33).",
    )

    # Scanning tracking
    last_scanned_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date and time when this asset was last scanned via QR code",
    )

    # Media files
    image = models.ImageField(
        upload_to="assets/images/",
        null=True,
        blank=True,
        help_text="Photo of the asset",
    )
    thumbnail = ImageSpecField(
        source="image",
        processors=[ResizeToFit(300, 300)],
        format="WEBP",
        options={"quality": 85},
    )
    manual_pdf = models.FileField(
        upload_to="assets/manuals/",
        null=True,
        blank=True,
        help_text="User manual or documentation PDF",
    )

    # QR code for asset tracking
    qr_code = models.ImageField(
        upload_to="assets/qrcodes/",
        blank=True,
        null=True,
        help_text="QR code for quick asset identification",
    )
    access_code = models.CharField(
        max_length=6,
        unique=True,
        blank=True,
        null=True,
        help_text="Unique 6-character code for manual entry (excludes I, 0, O, 1, L)",
    )

    # Status and condition
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=ACTIVE,
        help_text="Current status of the asset",
    )
    condition_notes = models.TextField(
        blank=True,
        help_text="Notes about the asset's condition, maintenance history, etc.",
    )

    # Ownership - can be owned by User, Group, or Space (makerspace itself)
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
        help_text="Type of ownership for this asset",
    )
    owning_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_assets",
        help_text="User that owns this asset (if applicable)",
    )
    owning_group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_assets",
        help_text="Group that owns this asset (if applicable)",
    )
    groups_can_enable = models.ManyToManyField(
        Group,
        blank=True,
        related_name="assets_can_enable",
        help_text="Groups that are allowed to enable this asset",
    )

    # Note: Operational status, lockout, and authorization fields have been moved to the forgekey app
    # Use forgekey.models.OperationalMode, DeviceLockout, and AssetAuthorization instead

    # Parts and consumables - many-to-many through AssetPart
    parts = models.ManyToManyField(
        InventoryItem,
        through="AssetPart",
        related_name="assets_using_part",
        blank=True,
        help_text="Consumable parts and supplies used by this asset (e.g., saw blades, nozzles, filters)",
    )

    # Metadata
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive assets are hidden from most views",
    )
    report_only = models.BooleanField(
        default=False,
        help_text="If True, this asset only supports problem reporting (no enable/disable functionality). Examples: soap dispensers, fixtures.",
    )
    notes = models.TextField(blank=True, help_text="General notes about the asset")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["serial_number"]),
            models.Index(fields=["asset_tag"]),
            models.Index(fields=["status"]),
            models.Index(fields=["manufacturer"]),
        ]

    def __str__(self) -> str:
        if self.asset_tag:
            return f"{self.name} ({self.asset_tag})"
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Auto-generate asset tag if not provided."""
        if not self.asset_tag:
            # Generate asset tag using UUID
            self.asset_tag = f"DMS-{generate_sku()[:8].upper()}"

        super().save(*args, **kwargs)

    @property
    def display_manufacturer(self) -> str:
        """Return manufacturer name from either FK or text field."""
        if self.manufacturer:
            return self.manufacturer.name
        elif self.manufacturer_name:
            return self.manufacturer_name
        return "Unknown"

    @property
    def acquisition_display(self) -> str:
        """Return formatted acquisition information."""
        if self.is_donation:
            donor = f" from {self.donor_name}" if self.donor_name else ""
            return f"Donated{donor}"
        elif self.amount_paid > 0:
            return f"Purchased for ${self.amount_paid}"
        return "No acquisition info"

    @property
    def age_in_days(self) -> Optional[int]:
        """Calculate how many days since the asset was received."""
        if self.date_received:
            from datetime import date

            today = date.today()
            delta = today - self.date_received
            return delta.days
        return None

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
        """Check if user is a SIG admin for the asset's owning group."""
        if not user.is_authenticated or not self.owning_group:
            return False
        # Use the permission utility to check SIG admin status
        from membership.utils import is_sig_admin

        return is_sig_admin(user, self.owning_group)

    def can_user_operate(self, user) -> bool:
        """
        Check if user can operate this asset.

        Assets in Implementing or Testing status can be operated by:
        - Maintainers
        - Group Admins (for assets owned by their group)
        - Logistics team members
        - COO

        Active assets can be operated by anyone (subject to other permissions).
        """
        if not user.is_authenticated:
            return False

        # Active assets can be operated by anyone (subject to other permissions)
        if self.status == self.ACTIVE:
            return True

        # Implementing and Testing status assets have restricted access
        if self.status in [self.IMPLEMENTING, self.TESTING]:
            # Check for COO
            try:
                coo_group = Group.objects.get(name="COO")
                if coo_group in user.groups.all() or user.is_superuser:
                    return True
            except Group.DoesNotExist:
                if user.is_superuser:
                    return True

            # Check for Logistics
            if self.is_user_in_logistics(user):
                return True

            # Check for Group Admin
            if self.is_user_group_admin(user):
                return True

            # Check for Maintainer
            try:
                maintainer_group = Group.objects.get(name="Maintainer")
                if maintainer_group in user.groups.all():
                    return True
            except Group.DoesNotExist:
                pass

            return False

        # For other statuses (maintenance, retired, etc.), default to False
        return False

    # Note: Lock/unlock methods have been moved to forgekey.models.DeviceLockout


class AssetPart(models.Model):
    """
    Through model for Asset-InventoryItem many-to-many relationship.

    Tracks consumable parts and supplies that are used by assets.
    Examples: saw blades for table saws, nozzles for 3D printers,
    soap for soap dispensers, filters for air compressors.

    This allows:
    - An asset to have multiple parts/consumables
    - A part to be used by multiple assets
    - Tracking quantities needed, maintenance schedules, and replacement history
    """

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="asset_parts",
        help_text="The asset that uses this part",
    )
    part = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name="used_by_assets",
        help_text="The inventory item (part/consumable) used by this asset",
    )

    # Part-specific information
    quantity_needed = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="How many of this part are needed for the asset (e.g., 2 saw blades)",
    )
    is_required = models.BooleanField(
        default=True,
        help_text="If True, this part is required for the asset to function",
    )
    maintenance_interval_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Recommended days between replacements (e.g., 90 for filters)",
    )
    last_replaced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this part was last replaced",
    )
    notes = models.TextField(
        blank=True,
        help_text="Additional notes about this part (installation instructions, compatibility, etc.)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Asset Part"
        verbose_name_plural = "Asset Parts"
        unique_together = [["asset", "part"]]
        ordering = ["asset", "part__name"]
        indexes = [
            models.Index(fields=["asset", "part"]),
            models.Index(fields=["part", "asset"]),
        ]

    def __str__(self) -> str:
        return f"{self.asset.name} - {self.part.name}"

    @property
    def days_since_replacement(self) -> Optional[int]:
        """Calculate days since last replacement."""
        if self.last_replaced_at:
            from django.utils import timezone

            delta = timezone.now() - self.last_replaced_at
            return delta.days
        return None

    @property
    def needs_replacement(self) -> bool:
        """Check if part needs replacement based on maintenance interval."""
        if not self.maintenance_interval_days or not self.last_replaced_at:
            return False
        days_since = self.days_since_replacement
        return days_since is not None and days_since >= self.maintenance_interval_days


class AssetProblem(models.Model):
    """
    Track problems reported with assets by users.

    When a user scans an asset QR code and reports a problem,
    this model stores the issue for admin review.
    """

    # Problem status choices
    REPORTED = "reported"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"

    STATUS_CHOICES = [
        (REPORTED, "Reported"),
        (IN_PROGRESS, "In Progress"),
        (RESOLVED, "Resolved"),
        (CLOSED, "Closed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="problems",
        help_text="The asset with the problem",
    )
    part = models.ForeignKey(
        AssetPart,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="part_problems",
        help_text="Optional: The specific part associated with this problem (if applicable)",
    )
    reported_by = models.CharField(
        max_length=200,
        blank=True,
        help_text="Username or identifier of person reporting the problem",
    )
    description = models.TextField(
        help_text="Description of the problem or issue",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=REPORTED,
        help_text="Current status of the problem report",
    )
    resolution_notes = models.TextField(
        blank=True,
        help_text="Notes about how the problem was resolved",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the problem was resolved",
    )
    resolved_by = models.CharField(
        max_length=200,
        blank=True,
        help_text="Username or identifier of person who resolved the problem",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["asset", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.asset.name} - {self.get_status_display()} ({self.created_at.date()})"


class MaintenanceItem(models.Model):
    """
    A recurring preventive maintenance (PM) task for a physical asset.

    Each item defines what needs to be done, how often, and what materials are needed.
    When a user scans an asset QR code, outstanding PM tasks are shown with
    step-by-step instructions.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="maintenance_items",
        help_text="The asset this maintenance task belongs to",
    )
    title = models.CharField(max_length=200, help_text="Brief title for this maintenance task")
    description = models.TextField(
        blank=True,
        help_text="Detailed description of why this maintenance is needed",
    )
    instructions = models.TextField(
        blank=True,
        help_text="Step-by-step instructions for performing the maintenance",
    )
    estimated_time_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Estimated time to complete in minutes",
    )
    estimated_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Estimated total cost for materials and labor",
    )
    interval_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="How often this task should be performed (in days). Null means one-time or as-needed.",
    )
    last_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this task was last completed",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive tasks are hidden from the scan page",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset", "title"]
        indexes = [
            models.Index(fields=["asset", "is_active"], name="mi_asset_active_idx"),
            models.Index(fields=["last_completed_at"], name="mi_last_completed_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.asset.name} — {self.title}"

    @property
    def next_due_at(self) -> Optional[Any]:
        """Calculate when this task is next due based on interval and last completion."""
        if not self.interval_days:
            return None
        if self.last_completed_at:
            from datetime import timedelta

            return self.last_completed_at + timedelta(days=self.interval_days)
        return None

    @property
    def is_overdue(self) -> bool:
        """Return True if the task is past its due date or has never been completed.

        A task with interval_days set but no last_completed_at is considered overdue
        immediately — it needs to be done at least once before a schedule can begin.
        Tasks without interval_days (one-time or as-needed) are never considered overdue.
        """
        from django.utils import timezone

        if not self.interval_days:
            return False
        next_due = self.next_due_at
        if next_due is None:
            return True
        return timezone.now() >= next_due

    @property
    def days_overdue(self) -> Optional[int]:
        """Return how many days overdue the task is, or None if not overdue."""
        from django.utils import timezone

        if not self.is_overdue:
            return None
        next_due = self.next_due_at
        if next_due is None:
            return None
        delta = timezone.now() - next_due
        return max(0, delta.days)


class MaintenanceMaterial(models.Model):
    """
    A material or supply needed to complete a MaintenanceItem.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    maintenance_item = models.ForeignKey(
        MaintenanceItem,
        on_delete=models.CASCADE,
        related_name="materials",
        help_text="The maintenance task that requires this material",
    )
    inventory_item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_materials",
        help_text="Optional link to an inventory item for stock checking",
    )
    name = models.CharField(max_length=200, help_text="Name of the material or supply")
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.00"),
        help_text="Quantity needed",
    )
    unit = models.CharField(
        max_length=50,
        blank=True,
        help_text="Unit of measurement (e.g., pieces, oz, ml, feet)",
    )
    estimated_cost_per_unit = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Estimated cost per unit",
    )
    notes = models.TextField(blank=True, help_text="Notes about sourcing or usage")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.quantity} {self.unit})"

    @property
    def total_estimated_cost(self) -> Decimal:
        """Calculate total estimated cost for this material."""
        return self.quantity * self.estimated_cost_per_unit


class MaintenanceLog(models.Model):
    """
    A record of a completed maintenance task.

    Created when a user marks a MaintenanceItem as completed.
    Updates the MaintenanceItem's last_completed_at timestamp.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    maintenance_item = models.ForeignKey(
        MaintenanceItem,
        on_delete=models.CASCADE,
        related_name="logs",
        help_text="The maintenance task that was completed",
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_logs",
        help_text="The user who completed the task",
    )
    completed_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the task was completed",
    )
    time_spent_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Actual time spent in minutes",
    )
    cost_incurred = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Actual cost incurred for materials and labor",
    )
    notes = models.TextField(blank=True, help_text="Notes about what was done or observed")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-completed_at"]
        indexes = [
            models.Index(
                fields=["maintenance_item", "completed_at"],
                name="ml_item_completed_idx",
            ),
        ]

    def __str__(self) -> str:
        completed_by = self.completed_by.get_full_name() if self.completed_by else "Unknown"
        return f"{self.maintenance_item.title} — completed by {completed_by} at {self.completed_at}"


class MaintenanceTask(models.Model):
    """
    An ordered sub-task (line item) within a MaintenanceItem.

    Allows a single maintenance item to have a numbered checklist of steps,
    e.g., Task 1: "Disconnect Power", Task 2: "Lock Out Equipment".
    These appear on both printed work order forms and the digital interface.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    maintenance_item = models.ForeignKey(
        MaintenanceItem,
        on_delete=models.CASCADE,
        related_name="tasks",
        help_text="The maintenance task this step belongs to",
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Display order (lower numbers appear first)",
    )
    title = models.CharField(max_length=200, help_text="Short title for this step")
    description = models.TextField(blank=True, help_text="Detailed instructions for this step")
    is_required = models.BooleanField(
        default=True,
        help_text="Required steps must be completed before the work order can close",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "title"]
        indexes = [
            models.Index(fields=["maintenance_item", "order"], name="mt_item_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.maintenance_item.title} — Step {self.order}: {self.title}"


class WorkOrder(models.Model):
    """
    A scheduled or generated work order for a preventive maintenance item.

    Work orders can be printed as PDF forms for non-technical users or
    completed digitally by technical users. Both paths feed back into the
    maintenance history.
    """

    STATUS_OPEN = "open"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_BLOCKED = "blocked"
    STATUS_COMPLETED = "completed"

    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_BLOCKED, "Blocked"),
        (STATUS_COMPLETED, "Completed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    maintenance_item = models.ForeignKey(
        MaintenanceItem,
        on_delete=models.CASCADE,
        related_name="work_orders",
        help_text="The maintenance task this work order is for",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
        help_text="Current status of this work order",
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date by which this work order should be completed",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_work_orders",
        help_text="User assigned to complete this work order",
    )
    completed_by_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Name of person who completed the work (for paper form tracking)",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this work order was marked complete",
    )
    notes = models.TextField(blank=True, help_text="Notes about this work order")
    completed_scan = models.FileField(
        upload_to="work_orders/scans/%Y/%m/",
        null=True,
        blank=True,
        help_text=(
            "Completed work order PDF (attached when a paper form is scanned/emailed in "
            "via the Postmark inbound webhook)."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["maintenance_item", "status"], name="wo_item_status_idx"),
            models.Index(fields=["due_date", "status"], name="wo_due_status_idx"),
            models.Index(fields=["status", "-created_at"], name="wo_status_created_idx"),
        ]

    def __str__(self) -> str:
        return f"WO-{str(self.id)[:8].upper()} — {self.maintenance_item.title} ({self.get_status_display()})"

    @property
    def is_overdue(self) -> bool:
        """Return True if this open work order is past its due date."""
        from django.utils import timezone

        if self.status == self.STATUS_COMPLETED:
            return False
        if not self.due_date:
            return False
        return timezone.now().date() > self.due_date

    @property
    def short_id(self) -> str:
        """Return a short human-readable identifier for this work order."""
        return f"WO-{str(self.id)[:8].upper()}"


class WorkOrderTaskCompletion(models.Model):
    """
    Tracks completion of an individual task step within a work order.

    Created for each MaintenanceTask when a WorkOrder is generated,
    allowing granular tracking of which steps have been completed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="task_completions",
        help_text="The work order this task completion belongs to",
    )
    task = models.ForeignKey(
        MaintenanceTask,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completions",
        help_text="The task step (null if task was deleted after WO creation)",
    )
    task_title = models.CharField(
        max_length=200,
        help_text="Denormalized task title (preserved if task is later deleted)",
    )
    task_order = models.PositiveIntegerField(
        default=0,
        help_text="Display order (denormalized from task at creation time)",
    )
    is_required = models.BooleanField(
        default=True,
        help_text="Whether this step is required",
    )
    is_completed = models.BooleanField(default=False)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_work_order_tasks",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["task_order", "task_title"]
        indexes = [
            models.Index(fields=["work_order", "is_completed"], name="wotc_wo_completed_idx"),
        ]

    def __str__(self) -> str:
        status = "✓" if self.is_completed else "○"
        return f"{status} {self.task_title} (WO {self.work_order.short_id})"


class WorkOrderMaterialUsage(models.Model):
    """
    Tracks which materials were actually used when completing a work order.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="material_usage",
        help_text="The work order this material usage belongs to",
    )
    material = models.ForeignKey(
        MaintenanceMaterial,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usage_records",
        help_text="The material specification (null if deleted after WO creation)",
    )
    material_name = models.CharField(
        max_length=200,
        help_text="Denormalized material name",
    )
    quantity_planned = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.00"),
        help_text="Planned quantity from the maintenance item spec",
    )
    unit = models.CharField(max_length=50, blank=True)
    was_used = models.BooleanField(
        default=False,
        help_text="Whether this material was actually used",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["material_name"]

    def __str__(self) -> str:
        status = "used" if self.was_used else "not used"
        return f"{self.material_name} ({status}) — WO {self.work_order.short_id}"


class WorkOrderPhoto(models.Model):
    """
    A photo attached to a work order by a technician.

    Used for documenting wear, damage, or completed work.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="photos",
        help_text="The work order this photo belongs to",
    )
    image = models.ImageField(
        upload_to="work_order_photos/%Y/%m/",
        help_text="Photo of the asset or work performed",
    )
    caption = models.CharField(max_length=500, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_order_photos",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return f"Photo for WO {self.work_order.short_id} ({self.uploaded_at.date()})"


class WorkOrderSubmission(models.Model):
    """
    An inbound, emailed copy of a completed work order PDF.

    Created when Postmark delivers an email with a PDF attachment to our inbound
    webhook. The PDF is parsed for its embedded Work Order ID and AcroForm
    checkbox values; on success, the corresponding WorkOrderTaskCompletion rows
    are marked complete and the PDF is attached to the WorkOrder's maintenance
    history.
    """

    STATUS_RECEIVED = "received"
    STATUS_APPLIED = "applied"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_RECEIVED, "Received"),
        (STATUS_APPLIED, "Applied"),
        (STATUS_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
        help_text="Resolved work order (null until the PDF has been parsed)",
    )
    received_at = models.DateTimeField(auto_now_add=True)
    from_email = models.EmailField(blank=True)
    subject = models.CharField(max_length=500, blank=True)
    attachment = models.FileField(
        upload_to="work_orders/submissions/%Y/%m/",
        help_text="The raw PDF attachment as received from the email",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_RECEIVED,
    )
    parse_error = models.TextField(blank=True)
    parsed_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text="Snapshot of checkbox values extracted from the PDF",
    )
    postmark_message_id = models.CharField(
        max_length=200,
        blank=True,
        db_index=True,
        help_text="Postmark MessageID header; used for idempotency",
    )

    class Meta:
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["status", "-received_at"], name="wos_status_received_idx"),
        ]

    def __str__(self) -> str:
        wo = self.work_order.short_id if self.work_order else "unresolved"
        return f"WorkOrderSubmission({wo}) from {self.from_email or 'unknown'} [{self.status}]"


class Fixture(models.Model):
    """
    Fixed assets that require periodic refilling (e.g., soap dispensers, paper towel holders).

    Fixtures are installed at specific locations and use inventory items as refills.
    Users can scan QR codes to request refills.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=200,
        help_text="Descriptive name for this fixture (e.g., 'Bathroom 1 Soap Dispenser')",
    )
    description = models.TextField(blank=True, help_text="Additional details about this fixture")
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="fixtures",
        help_text="Where this fixture is installed",
    )
    refill_item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.PROTECT,
        related_name="used_in_fixtures",
        help_text="The inventory item used to refill this fixture",
    )
    asset_tag = models.CharField(
        max_length=100,
        blank=True,
        unique=True,
        null=True,
        help_text="Unique asset tag or identifier",
    )
    is_active = models.BooleanField(
        default=True, help_text="Inactive fixtures are hidden from scanning"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location__name", "name"]
        indexes = [
            models.Index(fields=["location", "is_active"]),
            models.Index(fields=["refill_item"]),
            models.Index(fields=["asset_tag"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.location.name})"

    @property
    def pending_requests_count(self) -> int:
        """Count of pending refill requests for this fixture."""
        return self.refill_requests.filter(status="pending").count()


class FixtureRefillRequest(models.Model):
    """
    A request to refill a fixture.

    Created when someone scans a fixture's QR code to report it needs refilling.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fixture = models.ForeignKey(
        Fixture,
        on_delete=models.CASCADE,
        related_name="refill_requests",
        help_text="The fixture that needs refilling",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        help_text="Current status of this refill request",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    requested_by = models.CharField(
        max_length=200,
        blank=True,
        help_text="Username or identifier of person who reported this (optional)",
    )
    resolved_at = models.DateTimeField(
        null=True, blank=True, help_text="When this request was resolved"
    )
    resolved_by = models.CharField(
        max_length=200,
        blank=True,
        help_text="Username of person who resolved this request",
    )
    notes = models.TextField(blank=True, help_text="Additional notes about this request")

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["fixture", "status"]),
            models.Index(fields=["status", "-requested_at"]),
        ]

    def __str__(self) -> str:
        return f"Refill request for {self.fixture.name} ({self.get_status_display()})"

    @property
    def time_to_resolve(self) -> Optional[int]:
        """Calculate time in minutes from request to resolution."""
        if self.resolved_at:
            delta = self.resolved_at - self.requested_at
            return int(delta.total_seconds() / 60)
        return None


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
    REASON_OTHER = "other"

    REASON_CHOICES = [
        (REASON_LOST, "Lost"),
        (REASON_DAMAGED, "Damaged"),
        (REASON_MISCOUNTED, "Miscounted"),
        (REASON_USED_WITHOUT_SCAN, "Used without scanning"),
        (REASON_FOUND, "Found (positive delta)"),
        (REASON_OTHER, "Other"),
    ]

    item = models.ForeignKey(
        InventoryItem,
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
