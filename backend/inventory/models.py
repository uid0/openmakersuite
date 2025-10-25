"""
Models for inventory management.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import models
from django.core.validators import MinValueValidator
from django.utils.text import slugify
from imagekit.models import ProcessedImageField
from imagekit.processors import ResizeToFit
import uuid


class Supplier(models.Model):
    """
    Supplier information for inventory items.

    Supplier types classify vendors by distribution:
    - Local: Local brick-and-mortar stores
    - Online: Online-only retailers
    - National: National chains with physical locations
    """

    # Supplier type choices
    LOCAL = 'local'
    ONLINE = 'online'
    NATIONAL = 'national'

    SUPPLIER_TYPE_CHOICES = [
        (LOCAL, 'Local'),
        (ONLINE, 'Online'),
        (NATIONAL, 'National'),
    ]

    name = models.CharField(max_length=200)
    supplier_type = models.CharField(
        max_length=20,
        choices=SUPPLIER_TYPE_CHOICES,
        help_text="Classification of supplier by distribution type"
    )
    website = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class Category(models.Model):
    """
    Categories for organizing inventory items.

    Supports hierarchical categorization through the parent field,
    allowing for nested category structures.
    """

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    description = models.TextField(blank=True)
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children'
    )

    class Meta:
        verbose_name_plural = 'Categories'
        ordering = ['name']

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
    sku = models.CharField(max_length=100, blank=True, help_text="Internal SKU")

    # Image handling with automatic thumbnailing
    image = ProcessedImageField(
        upload_to='inventory/images/',
        processors=[ResizeToFit(800, 800)],
        format='JPEG',
        options={'quality': 90},
        null=True,
        blank=True
    )
    thumbnail = ProcessedImageField(
        upload_to='inventory/thumbnails/',
        processors=[ResizeToFit(300, 300)],
        format='JPEG',
        options={'quality': 80},
        null=True,
        blank=True
    )

    # Organization
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='items'
    )
    location = models.CharField(max_length=200, help_text="Physical location in makerspace")

    # Reordering information
    reorder_quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Quantity to reorder when stock is low"
    )
    current_stock = models.PositiveIntegerField(
        default=0,
        help_text="Current quantity in stock"
    )
    minimum_stock = models.PositiveIntegerField(
        default=0,
        help_text="Minimum quantity before reordering"
    )

    # Supplier information
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='items'
    )
    supplier_sku = models.CharField(
        max_length=100,
        blank=True,
        help_text="Supplier's product SKU/ID"
    )
    supplier_url = models.URLField(
        blank=True,
        help_text="Direct link to product on supplier's website"
    )
    unit_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Cost per unit"
    )

    # Lead time tracking (in days)
    average_lead_time = models.PositiveIntegerField(
        default=7,
        help_text="Average lead time in days"
    )

    # QR code data
    qr_code = models.ImageField(
        upload_to='inventory/qrcodes/',
        blank=True,
        null=True
    )

    # Metadata
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['sku']),
            models.Index(fields=['category']),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def needs_reorder(self) -> bool:
        """Check if item stock is below minimum and needs reordering."""
        return self.current_stock <= self.minimum_stock

    @property
    def total_value(self) -> Decimal:
        """Calculate total value of current stock."""
        if self.unit_cost:
            return self.current_stock * self.unit_cost
        return Decimal('0')


class UsageLog(models.Model):
    """
    Track usage/consumption of inventory items.

    Usage logs are used to:
    - Calculate reorder predictions based on consumption patterns
    - Estimate lead times for reordering
    - Track item usage history
    """

    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name='usage_logs'
    )
    quantity_used = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    usage_date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-usage_date']
        indexes = [
            models.Index(fields=['item', '-usage_date']),
        ]

    def __str__(self) -> str:
        return f"{self.item.name} - {self.quantity_used} units on {self.usage_date.date()}"
