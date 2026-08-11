"""Core inventory models: locations, suppliers, catalog items, stock and serialized components."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.text import slugify

from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFit

from .ownership import OwnableModel

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
    class SupplierType(models.TextChoices):
        LOCAL = "local", "Local"
        ONLINE = "online", "Online"
        NATIONAL = "national", "National"

    # Ordering adapter — selects the artifact the order-pad export emits for this
    # supplier (op-svpq). ``none``/``generic_csv`` produce the vendor-agnostic
    # part#,qty pad; ``amazon`` produces an add-to-cart URL; ``hdsupply`` produces
    # a Part Number,Quantity CSV for HD Supply's Saved-List / Quick Order pad.
    class OrderingAdapter(models.TextChoices):
        NONE = "none", "None (generic part#,qty pad)"
        GENERIC_CSV = "generic_csv", "Generic CSV (part#,qty pad)"
        AMAZON = "amazon", "Amazon (add-to-cart URL)"
        HDSUPPLY = "hdsupply", "HD Supply (Part#,Qty CSV)"

    name = models.CharField(max_length=200)
    supplier_type = models.CharField(
        max_length=20,
        choices=SupplierType.choices,
        help_text="Classification of supplier by distribution type",
    )
    ordering_adapter = models.CharField(
        max_length=20,
        choices=OrderingAdapter.choices,
        default=OrderingAdapter.NONE,
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


class SupplierAgreement(models.Model):
    """
    A purchase or pricing agreement negotiated with a supplier (op-yoos).

    Some suppliers give the makerspace contract pricing, a standing quote or a
    membership/nonprofit discount. The agreement is recorded here — name, terms
    notes and an optional scan of the paperwork — so a purchase order can point
    at the agreement it was placed under (``PurchaseOrder.supplier_agreement``).
    """

    supplier = models.ForeignKey(
        "Supplier",
        on_delete=models.CASCADE,
        related_name="agreements",
        help_text="Supplier this agreement is with",
    )
    name = models.CharField(
        max_length=200,
        help_text="Short name for the agreement (e.g. '2026 nonprofit pricing')",
    )
    notes = models.TextField(blank=True, help_text="Terms, pricing notes, contacts")
    document = models.FileField(
        upload_to="supplier_agreements/",
        null=True,
        blank=True,
        help_text="Optional scan/PDF of the signed agreement or quote",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive agreements are hidden when picking one on a purchase order",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.supplier.name} — {self.name}"


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


class InventoryItem(OwnableModel):
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

    # Unit of measure / packaging matrix (op-hzji, phase 1).
    #
    # ``current_stock`` above stays THE canonical quantity: it is always counted
    # in BASE units (the smallest countable thing, labelled by ``base_unit``).
    # The fields here only describe how that base count is *expressed* to a
    # human, plus the packaging hierarchy it can be expressed in
    # (:class:`PackagingLevel`). Every existing item keeps today's behaviour by
    # default — ``base_unit="unit"``, ``count_mode=EACH``, no packaging levels —
    # and no quantity flow (reorder, PO, receive, usage) reads them yet.
    class CountMode(models.TextChoices):
        EACH = "each", "Each (count individual base units)"
        BY_LEVEL = "by_level", "By packaging level (count whole packs)"
        OPEN_CLOSED = "open_closed", "Sealed + open (count sealed packs, track open ones)"

    base_unit = models.CharField(
        max_length=40,
        default="unit",
        help_text="Label for the smallest countable thing (e.g. 'sheet', 'bottle', 'unit'). "
        "current_stock is always expressed in these.",
    )
    count_mode = models.CharField(
        max_length=20,
        choices=CountMode.choices,
        default=CountMode.EACH,
        help_text="Granularity this item is counted at. 'Each' counts base units "
        "(today's behaviour); the other modes count whole packs of count_level.",
    )
    count_level = models.ForeignKey(
        "PackagingLevel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Which packaging level is the counting granularity. "
        "Must be one of this item's packaging levels; null for 'Each'.",
    )
    open_container_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of currently-OPEN packs (sealed + open counting mode); typically 0 or 1.",
    )

    # Per-item opt-in for ML demand-forecast reorder alerts. Default OFF; the
    # nightly forecasting task only surfaces flagged items in the
    # ``reorder_alerts`` notify set (see ``inventory.models.DemandForecast``).
    reorder_alerts_enabled = models.BooleanField(
        default=False,
        help_text="Watch this item for ML reorder alerts",
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

    # Hazardous-material / NFPA safety data moved to the 1:1
    # ``InventorySafetyProfile`` in #885. Read/write compat accessors below
    # (``is_hazardous``, ``msds_url``, ``msds_file``, ``nfpa_*``) preserve the
    # historical ``item.<field>`` API, the flat serializer keys, and the admin
    # surface without a column on this (overwhelmingly non-hazardous) table.

    # Ownership (ownership_type / owning_user / owning_group + OwnershipType)
    # is contributed by the shared ``OwnableModel`` abstract base (#881).

    # Serialized-component tracking (EufyMake serial/lot tracking).
    # Aggregate stock (current_stock) still applies; when is_serialized is set,
    # individual physical units are additionally tracked as SerializedComponent
    # records that move through a lifecycle branched on serial_tracking_mode.
    class SerialTrackingMode(models.TextChoices):
        CONSUMABLE = "consumable", "Consumable"
        REUSABLE = "reusable", "Reusable"

    is_serialized = models.BooleanField(
        default=False,
        help_text="Track individual units of this item by serial number as SerializedComponents",
    )
    serial_tracking_mode = models.CharField(
        max_length=20,
        choices=SerialTrackingMode.choices,
        default=SerialTrackingMode.CONSUMABLE,
        help_text=(
            "Consumable components are used up "
            "(received -> in_stock -> installed -> consumed -> disposed); "
            "reusable components can be installed/removed repeatedly and eventually retired"
        ),
    )

    # Kit SKUs (op-8n0). A kit is a purchasable SKU that DECOMPOSES: one
    # supplier line containing several stock items, whose bill of materials
    # lives in ``KitComponent``. Receiving a kit credits its components and
    # never the kit, so a kit deliberately holds no stock of its own — it is a
    # catalog/purchasing construct that happens to reuse this table for its
    # ``ItemSupplier``, price history and purchase-order line.
    #
    # This follows the discriminated-union house style already on this model
    # (is_active / is_retired / is_serialized / is_requestable /
    # use_case_based_reorder / count_mode all branch behaviour here). The cost
    # is that kits must be filtered OUT of the ordinary item surfaces
    # caller-side; see ``InventoryItemViewSet.get_queryset``.
    is_kit = models.BooleanField(
        default=False,
        db_index=True,
        help_text=(
            "This SKU is a kit: buying one line of it delivers the quantities "
            "listed in its components, which is where received stock is credited."
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
        """Persist the item, delegating SKU + image-download side effects to services.

        The workflow lives in :mod:`inventory.services.items` so this override
        stays a thin delegator (gh #887): :func:`assign_sku` keeps the
        synchronous SKU invariant (the SKU is in the create response), and
        :func:`schedule_image_download` enqueues the async fetch on
        ``transaction.on_commit`` — fixing a race where the task could run
        before the row committed (or for a rolled-back row).
        """
        from ..services.items import assign_sku, schedule_image_download, should_download_image

        assign_sku(self)

        # A kit is never individually requestable (op-8n0): a member asks for
        # "cyan ink", and the purchaser decides that the cheapest way to get it
        # is the kit. Forced rather than validated so flipping an existing
        # requestable item into a kit cannot leave a stale True behind.
        if self.is_kit:
            self.is_requestable = False

        # Decide before saving; neither field changes during super().save().
        needs_image_download = should_download_image(self)

        # Save first to get an ID
        super().save(*args, **kwargs)

        # Flush any hazmat writes that were staged while this item was unsaved
        # (e.g. ``InventoryItem.objects.create(is_hazardous=True, ...)`` routes
        # the compat-setter kwargs through ``__init__`` before a PK exists).
        self._flush_pending_hazmat()

        if needs_image_download:
            schedule_image_download(self)

    def clean(self) -> None:
        """Validate the attached/persisted safety profile as part of the item.

        The hazmat fields live on ``InventorySafetyProfile`` now, so
        ``InventoryItem.full_clean()`` would otherwise skip their validators
        (NFPA 0-4 range, MSDS URL format, special-hazards length). Delegating to
        the profile keeps ``item.full_clean()`` enforcing them exactly as when
        the columns lived here.
        """
        super().clean()
        profile = self._working_safety_profile()
        if profile is not None:
            profile.full_clean(exclude=["item"], validate_unique=False, validate_constraints=False)
        self._clean_count_mode()
        self._clean_kit()

    def _clean_kit(self) -> None:
        """A kit is a purchasing construct: it is never serialized and holds no stock (op-8n0).

        Both rules exist because receiving a kit credits its *components*. A
        serialized kit would promise serial numbers for a unit that never enters
        stock, and kit stock would be a number nothing can ever draw down —
        worse, it would read as permanently low if it were not for the
        ``is_kit`` guard in :attr:`needs_reorder`.
        """
        if not self.is_kit:
            return
        if self.is_serialized:
            raise ValidationError(
                {"is_serialized": "A kit cannot be serialized; its components are stocked, not it."}
            )
        if self.current_stock:
            raise ValidationError(
                {
                    "current_stock": (
                        "A kit cannot carry stock — receiving a kit credits its "
                        "component items instead."
                    )
                }
            )

    def _clean_count_mode(self) -> None:
        """Keep ``count_mode`` and ``count_level`` consistent (op-hzji).

        ``EACH`` counts base units and must not name a level; the pack-counting
        modes need a level, and it has to be one of *this* item's levels. Every
        pre-existing item is ``EACH`` with a null level, so this only ever
        rejects newly-invalid combinations.
        """
        if self.count_mode == self.CountMode.EACH:
            if self.count_level_id is not None:
                raise ValidationError(
                    {"count_level": "Count level must be empty when counting each base unit."}
                )
            return

        if self.count_level_id is None:
            raise ValidationError(
                {"count_level": f"Count level is required when count mode is '{self.count_mode}'."}
            )
        if self.count_level.item_id != self.pk:
            raise ValidationError(
                {"count_level": "Count level must be one of this item's packaging levels."}
            )

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
        """Check if item stock is below minimum and needs reordering.

        Compares stock to the reorder point *at the granularity the item is
        counted in* (op-es7c):

        * ``count_mode=each`` — unchanged, and it is what every pre-existing
          item is: case-based items compare ``current_cases`` to
          ``minimum_cases``, everything else ``current_stock`` to
          ``minimum_stock``, both in base units.
        * ``by_level`` / ``open_closed`` — whole packs of ``count_level``
          against ``minimum_stock``, which for these modes is a threshold in the
          item's COUNT unit (cases/reams/sealed packs). ``count_mode`` is the
          source of truth here: the legacy ``use_case_based_reorder`` /
          ``minimum_cases`` pair is deliberately not consulted.

        Pure stock math — despite the name it never touches ``reorder_requests``,
        so it is prefetch-safe and is intentionally NOT part of the
        ``reorder_requests`` N+1 fix (issue #890). Do not add a reorder-request
        query here. The database-side twin lives in
        ``inventory.services.packaging.low_stock_q``.
        """
        from inventory.services.packaging import count_at_level, counts_in_packs

        # Retired items are phased out: never flagged for reorder, regardless of
        # stock level. This is the central chokepoint for every property-based
        # low-stock surface (reorder_status, low_stock action, serializer
        # needs_reorder field, admin, AssetPart, Fixture refill).
        if self.is_retired:
            return False
        # A kit holds no stock of its own — receiving one credits its components
        # — so its stock/minimum pair is meaningless and would otherwise read as
        # permanently low (0 <= 0). Kits are bought as the *answer* to a low
        # component, never as a low item themselves. Same chokepoint reasoning
        # as ``is_retired`` above; the database-side twin is the caller-side
        # ``is_kit=False`` filter beside each ``low_stock_q()`` use, deliberately
        # NOT folded into ``low_stock_q`` itself (see
        # ``test_reorder_at_level.py`` — it asserts parity between this property
        # and that query, and that test documents the caller-side convention).
        if self.is_kit:
            return False
        if counts_in_packs(self):
            # Counted in whole packs: minimum_stock is the threshold in those packs.
            return count_at_level(self) <= self.minimum_stock
        if self.use_case_based_reorder:
            # For case-based reordering, calculate current cases and compare to minimum cases
            current_cases = self.current_cases
            return current_cases <= self.minimum_cases
        else:
            # Traditional individual unit reordering
            return self.current_stock <= self.minimum_stock

    def get_active_reorder_request(self):
        """Get the most recent active (pending/approved/ordered) reorder request for this item.

        When the list view has prefetched ``_active_reorder_requests`` (a
        ``Prefetch(to_attr=...)`` mirroring this exact filter + ``-requested_at``
        order — see ``InventoryItemViewSet.get_queryset``), read the first
        element of that cached list to avoid a per-row query (the
        ``reorder_requests`` N+1, issue #890). Falls back to the live ORM query
        when not prefetched (detail retrieve, admin, tasks) so the returned row
        is identical either way.
        """
        if hasattr(self, "_active_reorder_requests"):
            return self._active_reorder_requests[0] if self._active_reorder_requests else None
        return (
            self.reorder_requests.filter(status__in=["pending", "approved", "ordered"])
            .order_by("-requested_at")
            .first()
        )

    def has_pending_reorder(self) -> bool:
        """Check if item has any pending, approved, or ordered reorder requests.

        Reads the prefetched ``_active_reorder_requests`` list when present (see
        :meth:`get_active_reorder_request`) to avoid the ``reorder_requests``
        N+1; otherwise falls back to a live ``.exists()`` query.
        """
        if hasattr(self, "_active_reorder_requests"):
            return bool(self._active_reorder_requests)
        return self.reorder_requests.filter(status__in=["pending", "approved", "ordered"]).exists()

    def get_expected_delivery_date(self):
        """Calculate expected delivery date for ordered items.

        ⚠️ Orders by ``-ordered_at`` (NOT the ``-requested_at`` used by the
        active-request accessors), so the list view prefetches a SEPARATE
        ``_ordered_reorder_requests`` list with that exact filter + order. Read
        it when present, else fall back to the live query. Sharing a single
        prefetch with :meth:`get_active_reorder_request` would silently return
        the wrong row (issue #890 ordering trap).
        """
        if hasattr(self, "_ordered_reorder_requests"):
            ordered_request = (
                self._ordered_reorder_requests[0] if self._ordered_reorder_requests else None
            )
        else:
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
        """Get the lowest unit cost from all suppliers.

        Reads from ``item_suppliers.all()`` and filters the nulls out in Python
        so a caller that prefetched ``item_suppliers`` (e.g. the list endpoint,
        which serialises ``total_value``) hits the prefetch cache instead of
        firing a per-row ``filter(unit_cost__isnull=False)`` query — the same
        N+1 fix applied to :meth:`primary_item_supplier` (issue #882). The value
        is unchanged: the minimum non-null unit cost across all suppliers.
        """
        costs = [
            item_supplier.unit_cost
            for item_supplier in self.item_suppliers.all()
            if item_supplier.unit_cost is not None
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

    @cached_property
    def primary_item_supplier(self) -> Optional["ItemSupplier"]:
        """Preferred supplier relationship for this item — prefetch-friendly.

        Delegates to the named :mod:`inventory.services.supplier_selection`
        service (issue #882) so the selection lives in one place rather than a
        hidden model query. The chosen row is byte-for-byte the one the previous
        ``filter(is_primary=True).first() or first()`` returned — the supplier
        flagged primary with the lowest unit cost, or the cheapest supplier when
        none is primary — because the service resolves it from
        ``item_suppliers``' ``Meta.ordering`` (``["-is_primary", "unit_cost"]``).

        The result rides an ``item_suppliers`` prefetch when the caller set one
        up (the list/detail/reorder read paths all do), so serialising the seven
        flat compat fields across a page costs ZERO extra queries instead of an
        N+1. It is memoised per instance via ``cached_property`` so reading all
        seven flats touches the database at most once.
        """
        from inventory.services.supplier_selection import primary_item_supplier

        return primary_item_supplier(self)

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

    # ``is_user_admin`` / ``is_user_in_logistics`` / ``is_user_group_admin`` are
    # inherited from ``OwnableModel`` (they delegate to ``membership.services``).

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

    # ── InventorySafetyProfile compatibility layer (#885) ────────────────────
    #
    # The hazmat/NFPA fields moved to the 1:1 ``InventorySafetyProfile``. These
    # accessors preserve the historical ``item.<field>`` read/write API so
    # existing callers, the serializer, and the admin keep working unchanged.
    #
    # * Getters tolerate a missing profile row (return the field default).
    # * Setters stage the write in-memory (exactly as assigning a model field
    #   was in-memory until ``save()``); ``save()`` flushes the staged writes to
    #   the profile. Staging — rather than writing through immediately — means
    #   ``item.field = value; item.full_clean()`` still validates *before* any
    #   DB write (e.g. an over-length value raises ``ValidationError`` from
    #   ``full_clean`` rather than a ``DataError`` from a premature save), and
    #   ``InventoryItem.objects.create(is_hazardous=True, ...)`` — which Django
    #   routes through ``__init__`` before a PK exists — works unchanged. A row
    #   is only materialised when the staged data is non-default (lazy profile).
    # ─────────────────────────────────────────────────────────────────────────
    def _get_safety_profile(self) -> Optional["InventorySafetyProfile"]:
        """Return the 1:1 safety-profile row, or ``None`` if absent."""
        try:
            return self.safety_profile
        except ObjectDoesNotExist:
            return None

    def _read_safety_field(self, field: str, default: Any) -> Any:
        pending = self.__dict__.get("_pending_hazmat")
        if pending is not None and field in pending:
            return pending[field]
        profile = self._get_safety_profile()
        return getattr(profile, field) if profile is not None else default

    def _write_safety_field(self, field: str, value: Any) -> None:
        # Stage in-memory; flushed to the profile by ``save()``.
        self.__dict__.setdefault("_pending_hazmat", {})[field] = value

    def _working_safety_profile(self) -> Optional["InventorySafetyProfile"]:
        """The profile to validate in ``clean()`` — pending writes + any row."""
        profile = self._get_safety_profile()
        pending = self.__dict__.get("_pending_hazmat")
        if not pending:
            return profile
        if profile is None:
            profile = InventorySafetyProfile()
        for field, value in pending.items():
            setattr(profile, field, value)
        return profile

    def _flush_pending_hazmat(self) -> None:
        """Persist writes staged before the item had a PK (see ``save()``)."""
        pending = self.__dict__.pop("_pending_hazmat", None)
        if not pending:
            return
        profile = self._get_safety_profile()
        if profile is None:
            profile = InventorySafetyProfile(item=self)
        for field, value in pending.items():
            setattr(profile, field, value)
        # Stay lazy: only materialise a brand-new row when it carries real data.
        if profile.pk or profile.has_hazmat_data():
            profile.item = self
            profile.save()
            self.safety_profile = profile

    @property
    def is_hazardous(self) -> bool:
        return self._read_safety_field("is_hazardous", False)

    @is_hazardous.setter
    def is_hazardous(self, value: bool) -> None:
        self._write_safety_field("is_hazardous", value)

    @property
    def msds_url(self) -> str:
        return self._read_safety_field("msds_url", "")

    @msds_url.setter
    def msds_url(self, value: str) -> None:
        self._write_safety_field("msds_url", value)

    @property
    def msds_file(self) -> Any:
        # Read-only compat: the MSDS file is edited on the profile via the admin
        # inline (it is not part of the writable serializer surface).
        return self._read_safety_field("msds_file", None)

    @property
    def nfpa_health_hazard(self) -> Optional[int]:
        return self._read_safety_field("nfpa_health_hazard", None)

    @nfpa_health_hazard.setter
    def nfpa_health_hazard(self, value: Optional[int]) -> None:
        self._write_safety_field("nfpa_health_hazard", value)

    @property
    def nfpa_fire_hazard(self) -> Optional[int]:
        return self._read_safety_field("nfpa_fire_hazard", None)

    @nfpa_fire_hazard.setter
    def nfpa_fire_hazard(self, value: Optional[int]) -> None:
        self._write_safety_field("nfpa_fire_hazard", value)

    @property
    def nfpa_instability_hazard(self) -> Optional[int]:
        return self._read_safety_field("nfpa_instability_hazard", None)

    @nfpa_instability_hazard.setter
    def nfpa_instability_hazard(self, value: Optional[int]) -> None:
        self._write_safety_field("nfpa_instability_hazard", value)

    @property
    def nfpa_special_hazards(self) -> str:
        return self._read_safety_field("nfpa_special_hazards", "")

    @nfpa_special_hazards.setter
    def nfpa_special_hazards(self, value: str) -> None:
        self._write_safety_field("nfpa_special_hazards", value)

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


class PackagingLevel(models.Model):
    """One rung of an item's packaging hierarchy (op-hzji, phase 1).

    A chain describes how an item is packed, outermost first — e.g. paper is
    bought by the ``case`` (500 sheets), stored by the ``ream`` (100 sheets) and
    used by the ``sheet`` (the base). ``sort_order`` 0 is the outermost/largest
    rung and increases toward the base; ``base_units`` is how many of the item's
    BASE units one of this rung contains, so the base rung is always 1.

    This is descriptive only in phase 1: it never changes
    :attr:`InventoryItem.current_stock`, which stays the canonical base-unit
    count every reorder / purchase / usage flow reads. Items with no packaging
    levels behave exactly as they always have.

    ``ItemSupplier.quantity_per_package`` is a different thing and stays put: it
    is the *supplier's* case size for ordering and costing, which can differ per
    supplier, while this chain is the item's own physical packaging.
    """

    item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.CASCADE,
        related_name="packaging_levels",
    )
    name = models.CharField(
        max_length=40,
        help_text="What this rung is called: 'case', 'ream', 'bottle', 'bag', 'sheet', 'roll'…",
    )
    sort_order = models.PositiveIntegerField(
        help_text="0 = outermost/largest rung; increases toward the base unit.",
    )
    base_units = models.PositiveIntegerField(
        help_text="How many of the item's base units ONE of this rung holds. The base rung is 1.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["item", "sort_order"]
        unique_together = [("item", "sort_order")]

    def __str__(self) -> str:
        return f"{self.item} {self.name} (={self.base_units} {self.item.base_unit})"

    def clean(self) -> None:
        """Validate this rung against the rest of its item's chain.

        A rung is only meaningful in context, so ``full_clean()`` re-checks the
        whole prospective chain: the persisted siblings with this instance
        substituted in. ``clean()`` is not called on bulk writes, so the same
        rules are enforced independently in
        ``InventoryItemSerializer.validate`` — see
        :func:`inventory.services.packaging.validate_packaging_chain`.

        Writers that submit a whole chain at once set
        ``_chain_validated_as_a_set`` to opt out: row-by-row validation would
        reject every rung of a brand-new chain (the first row it sees has no
        base rung yet) even though the finished set is valid. The admin inline
        formset does this and validates the assembled set instead.
        """
        super().clean()
        if self.item_id is None or getattr(self, "_chain_validated_as_a_set", False):
            return
        from ..services.packaging import validate_packaging_chain

        siblings = [
            level
            for level in PackagingLevel.objects.filter(item_id=self.item_id)
            if level.pk != self.pk and level.sort_order != self.sort_order
        ]
        validate_packaging_chain(siblings + [self])


class InventorySafetyProfile(models.Model):
    """Hazardous-material / NFPA safety data for a single inventory item (1:1).

    Split off :class:`InventoryItem` in issue #885. The hazmat/NFPA columns used
    to hang directly on the item; the vast majority of catalog items are not
    hazardous, so keeping this data on a dedicated 1:1 profile lets ordinary
    catalog reads ignore it entirely. Only items with non-default hazmat data
    get a row — :class:`InventoryItem` exposes read/write compat accessors
    (``item.is_hazardous`` etc.) that fall back to the field defaults when no
    profile row exists, so the public model + API surface is unchanged.

    The fields below are moved verbatim from ``InventoryItem`` (same names,
    types, defaults, validators) so the historical values migrate 1:1.
    """

    item = models.OneToOneField(
        "InventoryItem",
        on_delete=models.CASCADE,
        related_name="safety_profile",
        help_text="Inventory item these safety details belong to.",
    )

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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Inventory safety profile"
        verbose_name_plural = "Inventory safety profiles"

    def __str__(self) -> str:
        return f"Safety profile for {self.item}"

    def has_hazmat_data(self) -> bool:
        """Whether this profile carries any non-default hazmat data.

        Used to keep the profile *lazy*: a row is only worth persisting when at
        least one field differs from its default. ``nfpa_* == 0`` is real data
        (minimal-hazard rating), so it is checked with ``is not None`` rather
        than truthiness.
        """
        return bool(
            self.is_hazardous
            or self.msds_url
            or self.msds_file
            or self.nfpa_health_hazard is not None
            or self.nfpa_fire_hazard is not None
            or self.nfpa_instability_hazard is not None
            or self.nfpa_special_hazards
        )


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
        """Derive cost fields, then delegate the primary-flag + price-history side effects.

        Cost derivation (``unit_cost``/``package_cost``) is a pure local
        invariant and stays here. The single-primary enforcement and
        :class:`PriceHistory` write are grouped into one ``transaction.atomic``
        block and delegated to :mod:`inventory.services.suppliers` (gh #887,
        AC-2) so a failure can't leave a demoted sibling without the save, or a
        saved row without its history entry. Order is preserved exactly:
        demote siblings, snapshot the pre-save pricing, save, then record.

        If package_cost is provided, calculate unit_cost automatically.
        If only unit_cost is provided (backward compatibility), calculate package_cost.
        """
        # Auto-calculate unit cost from package cost (local invariant)
        if self.package_cost is not None and self.quantity_per_package > 0:
            self.unit_cost = self.package_cost / self.quantity_per_package
        # Backward compatibility: if only unit_cost is provided, calculate package_cost
        elif (
            self.unit_cost is not None
            and self.package_cost is None
            and self.quantity_per_package > 0
        ):
            self.package_cost = self.unit_cost * self.quantity_per_package

        from ..services.suppliers import (
            enforce_single_primary,
            pricing_changed,
            record_price_history,
        )

        is_new = self.pk is None
        with transaction.atomic():
            enforce_single_primary(self)
            price_changed = pricing_changed(self)
            super().save(*args, **kwargs)
            record_price_history(self, is_new=is_new, price_changed=price_changed)


class PriceHistory(models.Model):
    """
    Track historical pricing data for item-supplier relationships.

    This model maintains a historical record of all price changes,
    allowing for trend analysis and price tracking over time.
    """

    class ChangeType(models.TextChoices):
        CREATED = "created", "Initial Price"
        UPDATED = "updated", "Price Update"
        SUPPLIER_CHANGED = "supplier_changed", "Supplier Info Changed"

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
        choices=ChangeType.choices,
        default=ChangeType.UPDATED,
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
        """Calculate percentage change from previous record.

        Single-instance-preferred and NOT prefetch-safe: it runs a
        self-referential ``recorded_at__lt`` prior-row lookup, so rendering it
        across a list (``PriceHistorySerializer`` list / ``PriceHistoryAdmin``)
        is one query per row. A ``Window(Lag("unit_cost"), partition_by=
        item_supplier, order_by=recorded_at)`` annotation would move it DB-side,
        but is intentionally NOT applied (issue #890): ``Lag`` ordered by
        ``recorded_at`` treats an equal-``recorded_at`` neighbour as the prior
        row, whereas the strict ``recorded_at__lt`` filter here excludes it — a
        non-byte-identical tie edge. Prefer small/paginated result sets, or add
        the annotation deliberately if a tie-behaviour change is acceptable.
        """
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

    Committee chargeback (accounting Phase 2): when supplies are consumed *for a
    committee* the log also records who was charged (``charged_group``), a
    snapshot of the item's cost at consume time (``unit_cost`` / ``total_cost`` —
    later price changes must not rewrite history), the acting user
    (``charged_by``), and a link to the posted ``SIG_CHARGE`` journal entry
    (``ledger_transaction``). The cost/actor snapshot is taken on every consume;
    ``charged_group`` and ``ledger_transaction`` are populated only when a
    committee is charged. See ``accounting.adapters.post_supply_consumption`` and
    ``docs/accounting.md``.
    """

    item = models.ForeignKey("InventoryItem", on_delete=models.CASCADE, related_name="usage_logs")
    quantity_used = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    usage_date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    # Committee chargeback (accounting Phase 2). PROTECT on ``charged_group``:
    # a committee with charge history must not be silently deletable (mirrors
    # ``accounting.LegDimension.sig``). ``unit_cost``/``total_cost`` are snapshots
    # taken at consume time; ``total_cost`` is null when the item has no cost on
    # file. ``ledger_transaction`` is null when nothing was posted.
    charged_group = models.ForeignKey(
        "auth.Group",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="charged_usage_logs",
        help_text="The committee (SIG) charged for this consumption, if any.",
    )
    unit_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Snapshot of the item's unit cost at consume time (may be unknown).",
    )
    total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Snapshot of unit_cost x quantity_used at consume time (null when cost unknown).",
    )
    charged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The user who recorded this consumption.",
    )
    ledger_transaction = models.ForeignKey(
        "hordak.Transaction",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The posted SIG_CHARGE journal entry, if a committee was charged.",
    )

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

    class ReasonCode(models.TextChoices):
        LOST = "lost", "Lost"
        DAMAGED = "damaged", "Damaged"
        MISCOUNTED = "miscounted", "Miscounted"
        USED_WITHOUT_SCAN = "used_without_scan", "Used without scanning"
        FOUND = "found", "Found (positive delta)"
        VISION_SUPPLY_CHECK = "vision_supply_check", "Vision supply check"
        OTHER = "other", "Other"

    item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.CASCADE,
        related_name="reconciliations",
    )
    projected_count = models.IntegerField(help_text="current_stock at the time of reconciliation")
    actual_count = models.IntegerField(help_text="Physically counted quantity")
    delta = models.IntegerField(help_text="actual_count - projected_count (signed)")
    reason = models.CharField(max_length=32, choices=ReasonCode.choices)
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


class StockLevelSnapshot(models.Model):
    """A weekly point-in-time snapshot of an item's ``current_stock``.

    Written by the ``inventory.tasks.snapshot_stock_levels`` beat task (weekly,
    no backfill) so the Inventory Stock-History chart has a real time series to
    plot even for items whose usage is never scanned. One row per
    ``(item, snapshot_date)``; the task ``update_or_create``s on that pair so a
    re-run within the same week is idempotent (it refreshes the count rather
    than appending a duplicate point).

    Behaviour-free storage — read by the ``stock_history`` action on
    :class:`inventory.views.InventoryItemViewSet`.
    """

    item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.CASCADE,
        related_name="stock_snapshots",
    )
    count = models.PositiveIntegerField(
        help_text="The item's current_stock at the moment the snapshot was taken.",
    )
    snapshot_date = models.DateField(
        db_index=True,
        help_text="Week the snapshot represents (the run's week-start date).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["snapshot_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "snapshot_date"],
                name="uniq_stock_snapshot_per_item_week",
            ),
        ]
        indexes = [
            models.Index(fields=["item", "snapshot_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.item_id} @ {self.snapshot_date}: {self.count}"


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
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        IN_STOCK = "in_stock", "In Stock"
        INSTALLED = "installed", "Installed"
        REMOVED = "removed", "Removed"
        CONSUMED = "consumed", "Consumed"
        RETIRED = "retired", "Retired"
        DISPOSED = "disposed", "Disposed"

    # Lifecycle action choices (also used by ComponentUsageEvent.action)
    class Action(models.TextChoices):
        RECEIVE = "receive", "Receive"
        INSTALL = "install", "Install"
        REMOVE = "remove", "Remove"
        CONSUME = "consume", "Consume"
        RETIRE = "retire", "Retire"
        DISPOSE = "dispose", "Dispose"

    # (current_status, action) -> resulting_status, keyed by tracking mode.
    _TRANSITIONS = {
        InventoryItem.SerialTrackingMode.CONSUMABLE: {
            (Status.RECEIVED, Action.RECEIVE): Status.IN_STOCK,
            (Status.IN_STOCK, Action.INSTALL): Status.INSTALLED,
            (Status.INSTALLED, Action.CONSUME): Status.CONSUMED,
            (Status.CONSUMED, Action.DISPOSE): Status.DISPOSED,
        },
        InventoryItem.SerialTrackingMode.REUSABLE: {
            (Status.RECEIVED, Action.RECEIVE): Status.IN_STOCK,
            (Status.IN_STOCK, Action.INSTALL): Status.INSTALLED,
            (Status.INSTALLED, Action.REMOVE): Status.REMOVED,
            (Status.REMOVED, Action.INSTALL): Status.INSTALLED,
            (Status.IN_STOCK, Action.RETIRE): Status.RETIRED,
            (Status.INSTALLED, Action.RETIRE): Status.RETIRED,
            (Status.REMOVED, Action.RETIRE): Status.RETIRED,
            (Status.RETIRED, Action.DISPOSE): Status.DISPOSED,
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
        choices=Status.choices,
        default=Status.RECEIVED,
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

        if action == self.Action.RECEIVE:
            if not self.received_at:
                self.received_at = now
        elif action == self.Action.INSTALL:
            if asset is None:
                raise ValidationError("An asset is required to install a component.")
            self.installed_in_asset = asset
            self.installed_at = now
            event_asset = asset
        elif action == self.Action.REMOVE:
            event_asset = self.installed_in_asset
            self.installed_in_asset = None
        elif action == self.Action.CONSUME:
            event_asset = self.installed_in_asset
            self.installed_in_asset = None
        elif action == self.Action.RETIRE:
            event_asset = self.installed_in_asset
            self.installed_in_asset = None
        elif action == self.Action.DISPOSE:
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
        choices=SerializedComponent.Action.choices,
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
