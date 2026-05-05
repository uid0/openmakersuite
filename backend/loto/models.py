"""
Lockout / Tagout (LOTO) data model (oms-78j).

Builds the structured LOTO data that ``inventory.services.work_order_context``
already promises in shape — but extended with:

- ``LOTODevice``: physical lockout devices (padlocks, hasps, tags, valve
  covers) tracked as inventory so a maintainer can see what is available,
  who has it, and where it lives.
- ``AssetEnergySource``: an asset can have multiple energy sources
  (electrical / hydraulic / pneumatic / mechanical / thermal / chemical).
  Each source has an isolation point and a set of LOTO devices required to
  isolate it safely.

The free-form ``Asset.lockout_instructions`` text continues to act as the
V1 procedure field. A future ``LOTOProcedure`` model with structured steps
is intentionally out of scope here.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from inventory.models import Asset, Location


class LOTODevice(models.Model):
    """
    A physical lockout / tagout device tracked in the makerspace inventory.

    Devices are checked out to a user when in use, and returned to a known
    location when available. ``status`` reflects the operational state
    rather than location alone — e.g., a device may live in a known cabinet
    but be flagged ``missing`` until the next audit confirms it.
    """

    DEVICE_PADLOCK = "padlock"
    DEVICE_HASP = "hasp"
    DEVICE_TAG = "tag"
    DEVICE_KEY = "key"
    DEVICE_VALVE_COVER = "valve_cover"
    DEVICE_BREAKER_LOCK = "breaker_lock"
    DEVICE_PLUG_LOCK = "plug_lock"
    DEVICE_OTHER = "other"

    DEVICE_TYPE_CHOICES = [
        (DEVICE_PADLOCK, "Padlock"),
        (DEVICE_HASP, "Hasp"),
        (DEVICE_TAG, "Tag"),
        (DEVICE_KEY, "Key"),
        (DEVICE_VALVE_COVER, "Valve cover"),
        (DEVICE_BREAKER_LOCK, "Breaker lock"),
        (DEVICE_PLUG_LOCK, "Plug lock"),
        (DEVICE_OTHER, "Other"),
    ]

    STATUS_AVAILABLE = "available"
    STATUS_IN_USE = "in_use"
    STATUS_MISSING = "missing"
    STATUS_RETIRED = "retired"

    STATUS_CHOICES = [
        (STATUS_AVAILABLE, "Available"),
        (STATUS_IN_USE, "In use"),
        (STATUS_MISSING, "Missing"),
        (STATUS_RETIRED, "Retired"),
    ]

    device_type = models.CharField(
        max_length=20,
        choices=DEVICE_TYPE_CHOICES,
        default=DEVICE_PADLOCK,
        help_text="Kind of lockout device.",
    )
    label = models.CharField(
        max_length=80,
        help_text="Human label or serial (e.g. 'PAD-001', 'red tag #12').",
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loto_devices",
        help_text="Where the device is normally stored.",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loto_devices",
        help_text="User who currently has the device checked out.",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_AVAILABLE,
    )
    secured_breakers = models.ManyToManyField(
        "electrical_circuits.PowerBreaker",
        blank=True,
        related_name="lockout_devices",
        help_text=(
            "PowerBreakers this device is required to lock out. Used by the "
            "LOTO derivation pipeline (oms-qc3) to populate "
            "AssetEnergySource.required_devices for any asset cabled "
            "downstream of the breaker."
        ),
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["device_type", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["device_type", "label"],
                name="loto_device_unique_label_per_type",
            ),
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["location"]),
            models.Index(fields=["assigned_to"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_device_type_display()} {self.label}"


class AssetEnergySource(models.Model):
    """
    One energy source that must be isolated to safely service an asset.

    An asset can have many sources. ``required_devices`` enumerates the
    lockout devices needed to isolate this particular source — e.g., a
    breaker lock + padlock for a 240V electrical feed.
    """

    SOURCE_ELECTRICAL = "electrical"
    SOURCE_HYDRAULIC = "hydraulic"
    SOURCE_PNEUMATIC = "pneumatic"
    SOURCE_MECHANICAL = "mechanical"
    SOURCE_THERMAL = "thermal"
    SOURCE_CHEMICAL = "chemical"
    SOURCE_GRAVITATIONAL = "gravitational"
    SOURCE_OTHER = "other"

    SOURCE_TYPE_CHOICES = [
        (SOURCE_ELECTRICAL, "Electrical"),
        (SOURCE_HYDRAULIC, "Hydraulic"),
        (SOURCE_PNEUMATIC, "Pneumatic"),
        (SOURCE_MECHANICAL, "Mechanical / stored motion"),
        (SOURCE_THERMAL, "Thermal"),
        (SOURCE_CHEMICAL, "Chemical"),
        (SOURCE_GRAVITATIONAL, "Gravitational / suspended load"),
        (SOURCE_OTHER, "Other"),
    ]

    STATUS_ACTIVE = "active"
    STATUS_HISTORICAL = "historical"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_HISTORICAL, "Historical (cable removed/decommissioned)"),
    ]

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="energy_sources",
    )
    source_type = models.CharField(
        max_length=20,
        choices=SOURCE_TYPE_CHOICES,
    )
    magnitude = models.CharField(
        max_length=80,
        blank=True,
        help_text="Human description of the magnitude (e.g. '240V', '80psi').",
    )
    isolation_point = models.CharField(
        max_length=200,
        blank=True,
        help_text="Where to isolate (e.g. 'Panel A breaker 12', 'red valve " "behind machine').",
    )
    required_devices = models.ManyToManyField(
        LOTODevice,
        blank=True,
        related_name="energy_sources",
        help_text="LOTO devices required to isolate this source.",
    )
    derived_from = models.ForeignKey(
        "electrical_circuits.Cable",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="derived_energy_sources",
        help_text=(
            "Power cable this energy source was auto-derived from (oms-qc3). "
            "NULL for manual entries and for derived rows whose cable was "
            "later deleted (those rows are also marked status=historical)."
        ),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        help_text=(
            "Active = the source is current. Historical = the cable backing "
            "this derived source was decommissioned or removed; the row is "
            "preserved for audit instead of being deleted."
        ),
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset_id", "source_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["derived_from"],
                condition=models.Q(derived_from__isnull=False),
                name="loto_energy_source_unique_per_cable",
            ),
        ]
        indexes = [
            models.Index(fields=["asset"]),
            models.Index(fields=["source_type"]),
            models.Index(fields=["status"]),
            models.Index(fields=["derived_from"]),
        ]

    def __str__(self) -> str:
        magnitude = f" ({self.magnitude})" if self.magnitude else ""
        return f"{self.asset.name} - {self.get_source_type_display()}{magnitude}"
