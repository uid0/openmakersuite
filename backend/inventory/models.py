"""
Models for inventory management.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from django.core.validators import MinValueValidator
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
        source="image", processors=[ResizeToFit(300, 300)], format="WEBP", options={"quality": 85}
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

    # Suppliers - now many-to-many through ItemSupplier
    suppliers = models.ManyToManyField(
        Supplier, through="ItemSupplier", related_name="supplied_items", blank=True
    )

    # Reordering information
    reorder_quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)], help_text="Quantity to reorder when stock is low"
    )
    current_stock = models.PositiveIntegerField(default=0, help_text="Current quantity in stock")
    minimum_stock = models.PositiveIntegerField(
        default=0, help_text="Minimum quantity before reordering"
    )

    # QR code data
    qr_code = models.ImageField(upload_to="inventory/qrcodes/", blank=True, null=True)

    # Metadata
    is_active = models.BooleanField(default=True)
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
    def needs_reorder(self) -> bool:
        """Check if item stock is below minimum and needs reordering."""
        return self.current_stock <= self.minimum_stock

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


class ItemSupplier(models.Model):
    """
    Through model for Item-Supplier many-to-many relationship.

    Allows each item to have multiple suppliers with different:
    - SKUs
    - Prices
    - Lead times
    - URLs
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
        super().save(*args, **kwargs)


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
