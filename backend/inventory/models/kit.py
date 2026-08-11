"""Kit bill-of-materials (op-8n0).

A *kit* is a purchasable SKU that decomposes: one supplier line ("Eufy Ink
Kit", SKU T3200, $89.99) that physically contains several stock items. It is
**not** a saved shopping list — ordering 2 kits is ONE purchase-order line for
$179.98, and receiving those 2 credits +2 stock to each component item and 0 to
the kit itself.

The kit itself is an :class:`~inventory.models.core.InventoryItem` carrying
``is_kit=True``, not a standalone model. That is what lets a kit inherit
``ItemSupplier``, ``PriceHistory``, ``LeadTimeLog``, the UPC barcode receive
path and — most importantly — ``PurchaseOrderItem.item_supplier``, so the
purchase-order line for a kit needs no schema change at all.

This module holds only the through-model. ``InventoryItem.is_kit`` and the
behaviour it branches (``needs_reorder``, ``save``, ``clean``) live beside their
siblings in :mod:`inventory.models.core`.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q


class KitComponent(models.Model):
    """One line of a kit's bill of materials: "this kit contains N of that item".

    Mirrors :class:`~inventory.models.asset.AssetPart` — a through-model with
    two foreign keys to the same catalog and a quantity — with **one deliberate
    departure: ``component`` is PROTECT, not CASCADE.**

    ``AssetPart`` is descriptive: deleting a part an asset happens to use loses
    a note about that asset. ``KitComponent`` *drives a stock mutation*, so a
    cascaded delete would silently change what a future receipt credits, with no
    error raised anywhere and nothing in the receipt to compare against. The
    delete is refused instead, and the operator has to say what the kit contains
    now.
    """

    kit = models.ForeignKey(
        "inventory.InventoryItem",
        on_delete=models.CASCADE,
        related_name="kit_components",
        limit_choices_to={"is_kit": True},
        help_text="The kit SKU that contains this component",
    )
    component = models.ForeignKey(
        "inventory.InventoryItem",
        on_delete=models.PROTECT,
        related_name="supplied_by_kits",
        limit_choices_to={"is_kit": False},
        help_text="The stock item contained in the kit",
    )
    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="How many of this component one kit contains (e.g. 1 cyan cartridge)",
    )
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kit", "component__name"]
        unique_together = [["kit", "component"]]
        indexes = [
            models.Index(fields=["kit", "component"]),
            models.Index(fields=["component", "kit"]),
        ]
        constraints = [
            # Both are DB-enforced rather than clean()-only on purpose:
            # ``clean()`` is skipped by bulk writes, ``update_or_create`` on the
            # natural key, and the admin's inline formsets. A self-referencing
            # row would make a receipt credit the kit's own stock, and a
            # zero/negative quantity would make it credit nothing or debit.
            models.CheckConstraint(
                condition=~Q(kit=F("component")),
                name="kit_component_no_self_reference",
            ),
            models.CheckConstraint(
                condition=Q(quantity__gte=1),
                name="kit_component_quantity_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kit.name} contains {self.quantity} x {self.component.name}"

    def clean(self) -> None:
        """Reject self-reference, nested kits, a non-kit parent, and serialized parts.

        The two DB constraints above cover self-reference and quantity; the rest
        cannot be expressed as a ``CheckConstraint`` because they read a column
        on the *other* table. Raising here gives the API a field-addressed 400
        instead of an IntegrityError 500.
        """
        super().clean()

        if self.kit_id is not None and self.kit_id == self.component_id:
            raise ValidationError({"component": "A kit cannot contain itself."})

        if self.kit_id is not None and not self.kit.is_kit:
            raise ValidationError({"kit": "Components can only be added to a kit."})

        if self.component_id is not None:
            component = self.component
            # Nested kits are out of scope: exploding a tree would need a
            # recursive receipt and a cycle check, and no supplier sells one.
            if component.is_kit:
                raise ValidationError(
                    {"component": "A kit cannot contain another kit."},
                )
            # Fail loud rather than silently dropping serial tracking: a receipt
            # that credits aggregate stock but mints no SerializedComponent rows
            # is worse than a refusal, because the serials are unrecoverable.
            if component.is_serialized:
                raise ValidationError(
                    {
                        "component": (
                            "Serialized items cannot be kit components — receiving the "
                            "kit would credit stock without recording serial numbers."
                        )
                    },
                )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Validate before writing.

        ``KitComponent`` is only ever written through the kit serializer or the
        admin, both low-volume, so paying ``full_clean`` here keeps the nested-kit
        and serialized-component rules from being bypassed by a direct
        ``objects.create`` in a script or a data migration.

        Uniqueness and the two ``CheckConstraint``s are left to the database —
        validating them here would cost a query per row to re-derive what the
        DB is about to enforce anyway.
        """
        self.full_clean(validate_unique=False, validate_constraints=False)
        super().save(*args, **kwargs)
