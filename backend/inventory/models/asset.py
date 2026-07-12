"""Asset models: assets, parts, problems, documents, meters and asset state."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFit


class AssetTagSequence(models.Model):
    """Atomic per-year counter backing the ``DMS-YYANNNSS`` asset tag.

    One row per calendar year. ``number`` runs 1..999 and resets each year;
    when it would exceed 999 the ``alpha`` section advances (``A`` -> ``B`` ->
    ...). :meth:`allocate_core` hands out the next ``YYANNN`` core inside a
    ``select_for_update`` lock so concurrent asset creation can never mint the
    same sequence value twice.
    """

    year = models.PositiveIntegerField(
        unique=True,
        help_text="Calendar year this counter tracks (e.g. 2026).",
    )
    alpha = models.CharField(
        max_length=1,
        default="A",
        help_text="Current alpha section, advances when the numeric run passes 999.",
    )
    number = models.PositiveIntegerField(
        default=0,
        help_text="Last allocated number for the year (0 means none allocated yet).",
    )

    class Meta:
        ordering = ["year"]
        verbose_name = "asset tag sequence"
        verbose_name_plural = "asset tag sequences"

    def __str__(self) -> str:
        return f"{self.year}: {self.alpha}{self.number:03d}"

    @staticmethod
    def _next_alpha(alpha: str) -> str:
        """Return the alpha section that follows ``alpha`` (``A`` -> ``B`` ...)."""
        nxt = chr(ord(alpha.upper()) + 1)
        if nxt > "Z":
            raise ValueError("Asset tag sequence exhausted: alpha section passed 'Z'")
        return nxt

    @classmethod
    def allocate_core(cls, year: int) -> str:
        """Atomically allocate and return the next ``YYANNN`` core for ``year``.

        Advances the per-year counter under ``select_for_update`` and returns
        the six significant characters (two-digit year, alpha, zero-padded
        three-digit number). Callers add the ``DMS-`` prefix and checksum.
        """
        with transaction.atomic():
            # get_or_create absorbs the create race on the unique ``year``;
            # the subsequent select_for_update serializes the increment.
            cls.objects.get_or_create(year=year)
            seq = cls.objects.select_for_update().get(year=year)
            seq.number += 1
            if seq.number > 999:
                seq.number = 1
                seq.alpha = cls._next_alpha(seq.alpha)
            seq.save(update_fields=["alpha", "number"])
            return f"{year % 100:02d}{seq.alpha}{seq.number:03d}"


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
    class Status(models.TextChoices):
        IMPLEMENTING = "implementing", "Implementing"
        TESTING = "testing", "Testing"
        ACTIVE = "active", "Active"
        MAINTENANCE = "maintenance", "Under Maintenance"
        RETIRED = "retired", "Retired"
        LOST = "lost", "Lost"
        DONATED_OUT = "donated_out", "Donated Out"

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
        "InventoryItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assets",
        help_text="Link to inventory item type (optional)",
    )

    # Organization
    category = models.ForeignKey(
        "Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assets",
        help_text="Category for organizing assets",
    )
    location = models.ForeignKey(
        "Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assets",
        help_text="Current physical location of the asset",
    )

    # Manufacturer and supplier information
    manufacturer = models.ForeignKey(
        "Supplier",
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
    # NOTE: ``circuit``, ``needs_compressed_air`` and ``needs_ventilation``
    # moved to facilities.AssetSiteRequirements in #880. Read/write compat
    # accessors live at the bottom of this class so existing callers keep
    # working (``circuit`` is now a read-only breaker-derived label).
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

    # Power & electrical tracking
    class WiringType(models.TextChoices):
        PLUG_120V = "120v_plug", "120V Plug"
        PLUG_240V = "240v_plug", "240V Plug"
        HARDWIRED = "hardwired", "Hardwired"
        BATTERY = "battery", "Battery"
        PNEUMATIC = "pneumatic", "Pneumatic / non-electric"
        NONE = "none", "No power required"
        UNKNOWN = "unknown", "Unknown"

    power_draw_watts = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Rated power draw in watts (nameplate rating, when known).",
    )
    wiring_type = models.CharField(
        max_length=20,
        choices=WiringType.choices,
        default=WiringType.UNKNOWN,
        blank=True,
        help_text="How this asset is wired or supplied with power.",
    )
    suite = models.CharField(
        max_length=100,
        blank=True,
        help_text="Suite or area of the building where this asset's power drop originates.",
    )
    electrical_box = models.CharField(
        max_length=100,
        blank=True,
        help_text="Electrical box / panel enclosure that supplies this asset.",
    )
    breaker_location = models.CharField(
        max_length=100,
        blank=True,
        help_text="Specific breaker label / number serving this asset (e.g., 'Panel A, Breaker 12').",
    )
    # NOTE: the ``breaker`` and ``disconnect`` FKs moved to
    # facilities.AssetSiteRequirements in #880. Read/write compat accessors
    # (plus ``breaker_id`` / ``disconnect_id``) live at the bottom of this
    # class so existing callers keep working.
    is_critical = models.BooleanField(
        default=False,
        help_text=(
            "Marks the asset as a critical load. Critical loads are surfaced "
            "separately in trip-impact reports so operators can plan around "
            "outages of fire alarms, network APs, freezers, etc."
        ),
    )
    training_required = models.BooleanField(
        default=False,
        help_text=(
            "Operators must complete training / hold a certification before "
            "using this asset. Surfaced as a TRAINING REQUIRED banner on the "
            "e-paper PM panel. Independent of the formal Certification model "
            "in membership.* — this is the lightweight 'has the operator been "
            "shown how to use this safely' flag."
        ),
    )
    required_certifications = models.ManyToManyField(
        "membership.Certification",
        blank=True,
        related_name="required_for_assets",
        help_text=(
            "Specific certifications an operator must hold before this asset "
            "will unlock. When set, the e-paper panel renders the cert name(s) "
            "instead of the generic TRAINING REQUIRED badge. Independent of "
            "training_required — set either, both, or neither."
        ),
    )

    # Utilization & lifecycle (powers analytics dashboards + maintenance forecast)
    hours_used = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Cumulative operating hours. Updated manually via the asset hours endpoint; "
            "feeds utilization metrics and the hours-based maintenance forecast."
        ),
    )
    maintenance_interval_hours = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Service every N operating hours. The forecast flags the asset when "
            "(hours_used since last completed work order) crosses this value."
        ),
    )
    maintenance_interval_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Service every N calendar days. The forecast flags the asset when "
            "(days since last completed work order) crosses this value. Combined "
            "with maintenance_interval_hours: whichever fires first."
        ),
    )

    # Interlock tracking
    class InterlockType(models.TextChoices):
        NONE = "none", "None"
        FORGEKEY = "forgekey", "ForgeKey-controlled"
        KEY_SWITCH = "key_switch", "Key switch"
        E_STOP = "e_stop", "E-Stop"
        CONTACTOR = "contactor", "Contactor / relay"
        OTHER = "other", "Other"
        UNKNOWN = "unknown", "Unknown"

    has_interlock = models.BooleanField(
        default=False,
        help_text="Whether this asset has a hardware interlock that prevents operation.",
    )
    interlock_type = models.CharField(
        max_length=20,
        choices=InterlockType.choices,
        default=InterlockType.UNKNOWN,
        blank=True,
        help_text="How the interlock is implemented.",
    )
    interlock_responsible = models.CharField(
        max_length=200,
        blank=True,
        help_text="Person or group responsible for the interlock (when not managed by ForgeKey).",
    )

    # Lockout / Tagout tracking
    class LockoutType(models.TextChoices):
        LOTO = "loto", "LOTO (Lockout / Tagout)"
        PLUG = "plug_lockout", "Plug lockout"
        BREAKER = "breaker_lockout", "Breaker lockout"
        KEY = "key_lockout", "Key lockout"
        VALVE = "valve_lockout", "Valve lockout"
        FORGEKEY = "forgekey", "ForgeKey-managed"
        NONE = "none", "None"
        UNKNOWN = "unknown", "Unknown"

    lockout_type = models.CharField(
        max_length=20,
        choices=LockoutType.choices,
        default=LockoutType.UNKNOWN,
        blank=True,
        help_text="How the asset is locked out for service.",
    )
    lockout_instructions = models.TextField(
        blank=True,
        help_text="Step-by-step instructions for safely locking out this asset before service.",
    )
    lockout_responsible = models.CharField(
        max_length=200,
        blank=True,
        help_text="Person or group responsible for performing/verifying lockout.",
    )

    # Network drop tracking
    has_network_drop = models.BooleanField(
        default=False,
        help_text="Whether this asset has a wired network drop nearby.",
    )
    network_drop_location = models.CharField(
        max_length=200,
        blank=True,
        help_text="Where the network drop is located (jack label, switch port, etc.).",
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

    # Status and condition
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        help_text="Current status of the asset",
    )
    condition_notes = models.TextField(
        blank=True,
        help_text="Notes about the asset's condition, maintenance history, etc.",
    )

    # Ownership - can be owned by User, Group, or Space (makerspace itself)
    class OwnershipType(models.TextChoices):
        USER = "user", "User"
        GROUP = "group", "Group"
        SPACE = "space", "Space"

    ownership_type = models.CharField(
        max_length=10,
        choices=OwnershipType.choices,
        default=OwnershipType.SPACE,
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
        "InventoryItem",
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
        """Auto-generate a ``DMS-YYANNNSS`` asset tag on first save.

        The tag is a human-meaningful identifier printed under the QR code:
        ``DMS-`` + two-digit received year + alpha section + per-year counter
        + a two-character checksum (see
        :mod:`inventory.services.asset_tag_id`). It is assigned once, only
        when ``asset_tag`` is empty, so an existing tag is never regenerated.

        The QR payload is unaffected — scanning still resolves the asset by
        its UUID (see ``qr_code_service``); the tag exists for humans reading
        the physical label.

        Year comes from ``date_received`` (the asset is usually entered when
        received) and falls back to the current year when unset. The per-year
        counter guarantees uniqueness, but we still retry on the off chance a
        backfilled tag collides with the freshly allocated one.
        """
        if not self.asset_tag:
            from ..services.asset_tag_id import generate_asset_tag

            year = self.date_received.year if self.date_received else timezone.now().year
            for _ in range(10):
                candidate = generate_asset_tag(year)
                if not Asset.objects.filter(asset_tag=candidate).exists():
                    self.asset_tag = candidate
                    break
            else:  # pragma: no cover - only if 10 consecutive allocations collide
                raise IntegrityError("Could not allocate a unique asset tag")

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
        if self.status == self.Status.ACTIVE:
            return True

        # Implementing and Testing status assets have restricted access
        if self.status in [self.Status.IMPLEMENTING, self.Status.TESTING]:
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

    @property
    def is_forgekey_managed(self) -> bool:
        """
        Return True if this asset has at least one ForgeKey AssetDevice
        configured to control its power, or if the asset is explicitly marked
        as ForgeKey-controlled via interlock_type / lockout_type.
        """
        if self.interlock_type == self.InterlockType.FORGEKEY:
            return True
        if self.lockout_type == self.LockoutType.FORGEKEY:
            return True
        try:
            return self.forgekey_devices.exists()
        except Exception:
            # forgekey app may not be installed in some test contexts
            return False

    # ─────────────────────────────────────────────────────────────────────
    # facilities.AssetSiteRequirements compatibility layer (#880)
    #
    # The operational/site-requirements fields moved to a 1:1 profile in the
    # ``facilities`` app. These accessors preserve the historical
    # ``asset.<field>`` read/write API so existing callers, the serializer,
    # and the admin keep working unchanged.
    #
    # * Getters tolerate a missing profile row (return the field default).
    # * Setters get-or-create the profile and write through immediately — the
    #   asset must already be saved (it needs a PK for the profile FK). The
    #   reverse-relation cache is refreshed so a subsequent read sees the new
    #   value and the loto post_save derivation reads the fresh breaker.
    # ─────────────────────────────────────────────────────────────────────
    def _get_site_requirements(self):
        """Return the 1:1 site-requirements row, or ``None`` if absent."""
        try:
            return self.site_requirements
        except ObjectDoesNotExist:
            return None

    def _read_site_requirement(self, field: str, default: Any) -> Any:
        profile = self._get_site_requirements()
        return getattr(profile, field) if profile is not None else default

    def _write_site_requirement(self, field: str, value: Any) -> None:
        from facilities.models import AssetSiteRequirements

        profile = self._get_site_requirements()
        if profile is None:
            profile = AssetSiteRequirements(asset=self)
        setattr(profile, field, value)
        profile.save()
        # Keep the reverse-relation cache coherent with what we just wrote so
        # a follow-up read (and the loto post_save signal) sees it.
        self.site_requirements = profile

    @property
    def breaker(self):
        return self._read_site_requirement("breaker", None)

    @breaker.setter
    def breaker(self, value) -> None:
        self._write_site_requirement("breaker", value)

    @property
    def breaker_id(self):
        return self._read_site_requirement("breaker_id", None)

    @property
    def disconnect(self):
        return self._read_site_requirement("disconnect", None)

    @disconnect.setter
    def disconnect(self, value) -> None:
        self._write_site_requirement("disconnect", value)

    @property
    def disconnect_id(self):
        return self._read_site_requirement("disconnect_id", None)

    @property
    def needs_compressed_air(self) -> bool:
        return self._read_site_requirement("needs_compressed_air", False)

    @needs_compressed_air.setter
    def needs_compressed_air(self, value: bool) -> None:
        self._write_site_requirement("needs_compressed_air", value)

    @property
    def needs_ventilation(self) -> bool:
        return self._read_site_requirement("needs_ventilation", False)

    @needs_ventilation.setter
    def needs_ventilation(self, value: bool) -> None:
        self._write_site_requirement("needs_ventilation", value)

    @property
    def generates_heat_or_flame(self) -> bool:
        return self._read_site_requirement("generates_heat_or_flame", False)

    @generates_heat_or_flame.setter
    def generates_heat_or_flame(self, value: bool) -> None:
        self._write_site_requirement("generates_heat_or_flame", value)

    @property
    def needs_chilling(self) -> bool:
        return self._read_site_requirement("needs_chilling", False)

    @needs_chilling.setter
    def needs_chilling(self, value: bool) -> None:
        self._write_site_requirement("needs_chilling", value)

    @property
    def special_requirements(self) -> str:
        return self._read_site_requirement("special_requirements", "")

    @special_requirements.setter
    def special_requirements(self, value: str) -> None:
        self._write_site_requirement("special_requirements", value)

    @property
    def work_safety_notes(self) -> str:
        return self._read_site_requirement("work_safety_notes", "")

    @work_safety_notes.setter
    def work_safety_notes(self, value: str) -> None:
        self._write_site_requirement("work_safety_notes", value)

    @property
    def circuit(self) -> str:
        """Read-only compat shim for the removed free-text ``circuit`` field.

        The breaker FK (now on the site-requirements profile) plus
        ``breaker_location`` supersede the old free-text circuit string. Old
        readers still reference ``asset.circuit``; return the best label
        available: the explicit breaker location, else a breaker-derived
        label, else empty.
        """
        if self.breaker_location:
            return self.breaker_location
        breaker = self.breaker
        return str(breaker) if breaker is not None else ""


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
        "Asset",
        on_delete=models.CASCADE,
        related_name="asset_parts",
        help_text="The asset that uses this part",
    )
    part = models.ForeignKey(
        "InventoryItem",
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
    replacement_serial_number = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text=(
            "Serial number of the replacement unit installed at the last "
            "replacement (captured only for serialized parts)"
        ),
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
    class Status(models.TextChoices):
        REPORTED = "reported", "Reported"
        IN_PROGRESS = "in_progress", "In Progress"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        "Asset",
        on_delete=models.CASCADE,
        related_name="problems",
        help_text="The asset with the problem",
    )
    part = models.ForeignKey(
        "AssetPart",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="part_problems",
        help_text="Optional: The specific part associated with this problem (if applicable)",
    )
    affected_parts = models.ManyToManyField(
        "AssetPart",
        blank=True,
        related_name="problems",
        help_text=(
            "Which of the asset's parts/components the reporter flagged as "
            "needing replacement or repair, so maintenance knows what to fix."
        ),
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
        choices=Status.choices,
        default=Status.REPORTED,
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


class AssetProblemPhoto(models.Model):
    """A photo attached to an asset problem report by the reporter."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    problem = models.ForeignKey(
        "AssetProblem",
        on_delete=models.CASCADE,
        related_name="photos",
        help_text="The asset problem this photo documents",
    )
    image = models.ImageField(
        upload_to="asset_problems/%Y/%m/",
        help_text="Photo illustrating the reported problem",
    )
    caption = models.CharField(max_length=500, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="asset_problem_photos",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self) -> str:
        return f"Photo for problem {self.problem_id} ({self.uploaded_at.date()})"


class AssetDocument(models.Model):
    """A document in an asset's per-asset document library.

    Beyond the single ``Asset.manual_pdf``/``image``, maker spaces need a real
    library of manuals, CAD sources, wiring diagrams, cut-sheets, and the
    maker-native twist: cut-ready reference files (DXF/SVG/G-code/STL) that
    live WITH the machine. Extensions are deliberately unrestricted so CAD/DXF/
    STL uploads are first-class.

    Lightweight versioning keeps people off stale manuals (Maximo's weakness):
    uploading a new version of a document links ``supersedes`` back to the
    prior one, bumps ``version``, and flips the prior one's ``is_current`` to
    False so it drops out of the "current" view.
    """

    class Category(models.TextChoices):
        MANUAL = "manual", "Manual / Documentation"
        CAD_SOURCE = "cad_source", "CAD Source"
        WIRING_DIAGRAM = "wiring_diagram", "Wiring Diagram"
        CUT_SHEET_SPEC = "cut_sheet_spec", "Cut Sheet / Spec"
        CUT_READY_TEMPLATE = (
            "cut_ready_template",
            "Cut-Ready Template (DXF/SVG/G-code/STL)",
        )
        PHOTO = "photo", "Photo"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        "Asset",
        on_delete=models.CASCADE,
        related_name="documents",
        help_text="The asset this document belongs to",
    )
    file = models.FileField(
        upload_to="assets/documents/%Y/%m/",
        help_text=(
            "The document file (manual, CAD source, wiring diagram, cut-sheet, "
            "cut-ready template, etc.). Any file type is accepted."
        ),
    )
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.OTHER,
        help_text="What kind of document this is",
    )
    title = models.CharField(
        max_length=255,
        help_text="Human-readable title for the document",
    )
    description = models.TextField(
        blank=True,
        help_text="Optional notes about this document",
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Version number; incremented when this document supersedes an earlier one",
    )
    is_current = models.BooleanField(
        default=True,
        help_text="False once a newer version supersedes this document",
    )
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="superseded_by",
        help_text="The prior document version this one replaces (if any)",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_asset_documents",
        help_text="User who uploaded this document",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["asset", "category", "-version", "-uploaded_at"]
        indexes = [
            models.Index(fields=["asset", "category"]),
            models.Index(fields=["asset", "is_current"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} (v{self.version}) — {self.get_category_display()}"


class AssetMeter(models.Model):
    """A meter tracking a cumulative usage quantity on an asset (EAM bead-1).

    Beyond ``Asset.hours_used`` (a single manually-logged integer), maker spaces
    run assets whose maintenance is driven by *measured* usage: runtime hours off
    ForgeKey usage sessions, gallons through a water fountain, cycles on a press,
    kWh on a kiln. A meter is a named counter on an asset with a ``source`` that
    says HOW it advances:

    * ``auto_session`` — pulled from ended ``forgekey.DeviceUsage`` sessions on a
      15-minute rollup (the framework's real work; see
      :mod:`inventory.services.meter_sources`).
    * ``auto_telemetry`` — pushed from an MQTT flow/counter sensor. Registered as
      a stub in this bead; the MQTT ingestion is a noted follow-up.
    * ``manual`` — a human enters the reading (e.g. the fountain's gallon
      counter). Manual entry is first-class and usable now.

    ``current_value`` caches the latest reading's ``value_after`` so clients get
    the number without walking the ledger. Readings themselves are the immutable
    source of truth (:class:`AssetMeterReading`).
    """

    class MeterType(models.TextChoices):
        RUNTIME_HOURS = "runtime_hours", "Runtime hours"
        VOLUME_GALLONS = "volume_gallons", "Volume (gallons)"
        CYCLES = "cycles", "Cycles"
        KWH = "kwh", "Energy (kWh)"
        GENERIC_COUNT = "generic_count", "Generic count"

    class Source(models.TextChoices):
        AUTO_SESSION = "auto_session", "Auto — usage sessions"
        AUTO_TELEMETRY = "auto_telemetry", "Auto — telemetry"
        MANUAL = "manual", "Manual entry"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        "Asset",
        on_delete=models.CASCADE,
        related_name="meters",
        help_text="The asset this meter measures",
    )
    name = models.CharField(
        max_length=100,
        help_text="Meter name (e.g. 'Spindle runtime', 'Water dispensed')",
    )
    meter_type = models.CharField(
        max_length=20,
        choices=MeterType.choices,
        default=MeterType.GENERIC_COUNT,
        help_text="What this meter measures",
    )
    unit = models.CharField(
        max_length=20,
        blank=True,
        help_text="Unit label for the value (e.g. hours, gallons, cycles, kWh, count)",
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.MANUAL,
        help_text="How this meter advances — auto rollup or manual entry",
    )
    current_value = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal("0"),
        help_text="Cached latest reading value (value_after of the newest reading)",
    )
    current_is_estimated = models.BooleanField(
        default=False,
        help_text="True when the current value came from an estimated (eyeballed) reading",
    )
    rollup_watermark_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Idempotency watermark for auto_session meters — the ended_at of the "
            "last usage session already folded into a reading. The rollup only "
            "consumes sessions ended AFTER this, so re-runs are exactly-once."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive meters are skipped by the rollup and hidden by default",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset", "name"]
        unique_together = [("asset", "name")]
        indexes = [
            models.Index(fields=["asset", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.asset.name} — {self.name} ({self.current_value} {self.unit})".rstrip()


class AssetMeterReading(models.Model):
    """An append-only ledger entry for an :class:`AssetMeter` (EAM bead-1).

    Every change to a meter — an auto_session rollup, a telemetry push, a manual
    reading, or a manual correction — writes ONE immutable row here. ``delta`` is
    the signed change and ``value_after`` snapshots the meter total right after
    this reading, so the full history reconstructs without recomputation and a
    bad reading can be audited (never edited).

    Absolute vs delta reads collapse into the same row via a shared helper:
    an absolute reading of ``V`` becomes ``delta = V - current_value`` /
    ``value_after = V``; a delta reading of ``d`` becomes
    ``value_after = current_value + d``.
    """

    class Source(models.TextChoices):
        AUTO_SESSION = "auto_session", "Auto — usage sessions"
        AUTO_TELEMETRY = "auto_telemetry", "Auto — telemetry"
        MANUAL = "manual", "Manual entry"
        MANUAL_ADJUST = "manual_adjust", "Manual correction"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meter = models.ForeignKey(
        "AssetMeter",
        on_delete=models.CASCADE,
        related_name="readings",
        help_text="The meter this reading belongs to",
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        help_text="What produced this reading",
    )
    delta = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        help_text="Signed change to the meter total from this reading",
    )
    value_after = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        help_text="Meter total immediately after applying this reading",
    )
    is_estimated = models.BooleanField(
        default=False,
        help_text="False for measured readings; True for a human eyeball estimate",
    )
    observed_at = models.DateTimeField(
        help_text="When the reading was observed / the usage it covers ended",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_meter_readings",
        help_text="User who recorded a manual reading (null for automatic rollups)",
    )
    source_ref = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Soft provenance string (e.g. 'device_usage x12 ≤<ts>'). Deliberately "
            "NOT a foreign key so inventory need not migrate against forgekey."
        ),
    )
    notes = models.TextField(
        blank=True,
        help_text="Optional notes (e.g. the reason for a manual correction)",
    )

    class Meta:
        ordering = ["-recorded_at"]
        indexes = [
            models.Index(fields=["meter", "recorded_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.meter.name}: {self.delta:+} → {self.value_after} @ {self.observed_at:%Y-%m-%d %H:%M}"


class AssetReservation(models.Model):
    """A class / training / event reservation against an asset.

    Reservations show up as a rotating "RESERVED" face on the asset's
    bound e-paper panel (alongside the usual PM card). The e-paper
    render service decides on each fetch which face to show using the
    panel's per-display rotation ratio; see
    forgekey.services.epaper_render._pick_face.

    Overlap policy: a new reservation cannot start before any existing
    one for the same asset ends. clean() enforces this; the
    create/update endpoints surface the ValidationError to the caller
    as a stable error envelope (code=reservation_overlap).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        "Asset",
        on_delete=models.CASCADE,
        related_name="reservations",
        help_text="Asset being reserved",
    )
    title = models.CharField(
        max_length=200,
        help_text='Short human-readable label, e.g. "Welding Class — John Smith"',
    )
    reserved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="asset_reservations",
        help_text="Member who owns the reservation (staff or SIG admin)",
    )
    starts_at = models.DateTimeField(help_text="When the reservation begins")
    ends_at = models.DateTimeField(help_text="When the reservation ends")
    notes = models.TextField(
        blank=True,
        help_text="Optional context (room, prerequisites, etc.)",
    )
    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the reservation was cancelled (soft-delete so the audit "
            "history sticks). Cancelled rows do not block new overlapping "
            "reservations and never drive the panel face."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-starts_at"]
        indexes = [
            models.Index(fields=["asset", "starts_at"]),
            models.Index(fields=["asset", "ends_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.starts_at:%Y-%m-%d %H:%M})"

    @property
    def is_active(self) -> bool:
        """True when not cancelled and not past its end."""
        if self.cancelled_at is not None:
            return False
        return self.ends_at > timezone.now()

    @property
    def is_current(self) -> bool:
        """True when the reservation is happening right now (drives the panel face)."""
        if self.cancelled_at is not None:
            return False
        now = timezone.now()
        return self.starts_at <= now < self.ends_at

    def clean(self) -> None:
        super().clean()
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValidationError({"ends_at": "ends_at must be after starts_at."})
        if self.asset_id is None or self.starts_at is None or self.ends_at is None:
            return
        # Block overlaps against any other non-cancelled reservation for
        # the same asset. Bound the lookup by [starts_at, ends_at) so a
        # back-to-back reservation that begins at the prior one's
        # ends_at is allowed.
        overlap = AssetReservation.objects.filter(
            asset_id=self.asset_id,
            cancelled_at__isnull=True,
            starts_at__lt=self.ends_at,
            ends_at__gt=self.starts_at,
        )
        if self.pk is not None:
            overlap = overlap.exclude(pk=self.pk)
        if overlap.exists():
            raise ValidationError(
                {"starts_at": "Reservation overlaps an existing reservation for this asset."}
            )


class AssetOutOfService(models.Model):
    """An out-of-service event against an asset.

    Each row is a single OOS window. Opening a new OOS while the asset
    has an unrestored one in flight is rejected by clean(). Restoring
    sets ``restored_at``; the row stays for the audit log. The e-paper
    panel renders the OOS face for any asset with a currently-open
    row (no rotation — OOS preempts both PM and reservation faces).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        "Asset",
        on_delete=models.CASCADE,
        related_name="out_of_service_events",
        help_text="Asset that's out of service",
    )
    placed_out_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the OOS was opened",
    )
    placed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="asset_out_of_service_events",
        help_text="Member who placed the asset OOS",
    )
    expected_return_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Best-guess return-to-service date; renders as 'expected back: …' on the panel",
    )
    reason = models.TextField(help_text="Why it's out of service")
    restored_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the asset was brought back online. Null means the row "
            "is the current active OOS; only one such row per asset."
        ),
    )
    restored_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="asset_restorations",
        help_text="Member who cleared the OOS",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-placed_out_at"]
        indexes = [
            models.Index(fields=["asset", "restored_at"]),
        ]

    def __str__(self) -> str:
        suffix = "open" if self.restored_at is None else f"restored {self.restored_at:%Y-%m-%d}"
        return f"OOS on asset {self.asset_id} ({suffix})"

    @property
    def is_open(self) -> bool:
        return self.restored_at is None

    def clean(self) -> None:
        super().clean()
        if self.asset_id is None or self.restored_at is not None:
            return
        # Block a second open OOS row on the same asset — closing the
        # first one is the operator's intended path.
        existing = AssetOutOfService.objects.filter(
            asset_id=self.asset_id, restored_at__isnull=True
        )
        if self.pk is not None:
            existing = existing.exclude(pk=self.pk)
        if existing.exists():
            raise ValidationError(
                "Asset already has an open out-of-service event; restore it before opening another."
            )
