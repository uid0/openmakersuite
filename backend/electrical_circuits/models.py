"""
Electrical and network infrastructure tracking (oms-tt5).

Tracks the physical electrical system (breakers, outlets, light switches) and
network drops (patch panels, APs, cameras, IoT sensors) in the makerspace so
maintenance can trace power and connectivity from any fixture back to its
source.
"""

from __future__ import annotations

from django.core.validators import MinValueValidator
from django.db import models

from inventory.models import Location


class Breaker(models.Model):
    """
    A circuit breaker on an electrical panel.

    Outlets and light switches are fed from a Breaker. The breaker itself
    lives at a Location (the electrical room / panel cabinet).
    """

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="breakers",
        help_text="Location of the panel that holds this breaker",
    )
    panel = models.CharField(
        max_length=50,
        help_text="Panel name or designation (e.g. 'Panel A', 'Main')",
    )
    breaker_number = models.CharField(
        max_length=20,
        help_text="Slot or number on the panel (e.g. '12', '14/16')",
    )
    amperage = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Trip amperage of the breaker",
    )
    voltage = models.PositiveIntegerField(
        default=120,
        validators=[MinValueValidator(1)],
        help_text="Nominal voltage (e.g. 120, 240)",
    )
    poles = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Number of poles (1=single, 2=double, 3=three-phase)",
    )
    description = models.CharField(
        max_length=200,
        blank=True,
        help_text="Optional human description (e.g. 'Wood shop dust collector')",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["panel", "breaker_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["location", "panel", "breaker_number"],
                name="electrical_circuits_breaker_unique_slot",
            ),
        ]
        indexes = [
            models.Index(fields=["location"]),
            models.Index(fields=["panel"]),
        ]

    def __str__(self) -> str:
        return f"{self.panel} / {self.breaker_number} ({self.amperage}A) @ {self.location.name}"


class Outlet(models.Model):
    """
    An electrical outlet (receptacle) at a Location, fed from a Breaker.

    `plugged_in_notes` captures an approximation of what is currently plugged
    in — free-form because equipment changes frequently.
    """

    OUTLET_TYPE_CHOICES = [
        ("standard", "Standard 120V"),
        ("240v", "240V"),
        ("nema_5_15", "NEMA 5-15 (120V 15A)"),
        ("nema_5_20", "NEMA 5-20 (120V 20A)"),
        ("nema_6_15", "NEMA 6-15 (240V 15A)"),
        ("nema_6_20", "NEMA 6-20 (240V 20A)"),
        ("nema_l6_30", "NEMA L6-30 (240V 30A locking)"),
        ("nema_14_30", "NEMA 14-30 (240V 30A)"),
        ("nema_14_50", "NEMA 14-50 (240V 50A)"),
        ("usb", "USB charging"),
        ("other", "Other"),
    ]

    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="outlets",
        help_text="Location of the outlet",
    )
    identifier = models.CharField(
        max_length=80,
        help_text="Local identifier within the location (e.g. 'NW-bench-1')",
    )
    breaker = models.ForeignKey(
        Breaker,
        on_delete=models.PROTECT,
        related_name="outlets",
        null=True,
        blank=True,
        help_text="Breaker that feeds this outlet (null if unknown)",
    )
    outlet_type = models.CharField(
        max_length=20,
        choices=OUTLET_TYPE_CHOICES,
        default="standard",
    )
    description = models.CharField(max_length=200, blank=True)
    plugged_in_notes = models.TextField(
        blank=True,
        help_text="Approximate description of what is plugged in",
    )
    photo = models.ImageField(
        upload_to="electrical_circuits/outlets/",
        blank=True,
        null=True,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location__name", "identifier"]
        constraints = [
            models.UniqueConstraint(
                fields=["location", "identifier"],
                name="electrical_circuits_outlet_unique_identifier",
            ),
        ]
        indexes = [
            models.Index(fields=["location"]),
            models.Index(fields=["breaker"]),
        ]

    def __str__(self) -> str:
        return f"Outlet {self.identifier} @ {self.location.name}"


class LightSwitch(models.Model):
    """
    A light switch at a Location.

    `controls_location` records the area whose lights it operates (often
    different from the location of the switch itself — a switch in the
    hallway controls the wood shop lights, etc.).
    """

    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="light_switches",
        help_text="Location where the switch is mounted",
    )
    identifier = models.CharField(
        max_length=80,
        help_text="Local identifier within the location (e.g. 'door-east')",
    )
    controls_location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        related_name="controlling_switches",
        null=True,
        blank=True,
        help_text="Location whose lights this switch controls (if different)",
    )
    breaker = models.ForeignKey(
        Breaker,
        on_delete=models.PROTECT,
        related_name="light_switches",
        null=True,
        blank=True,
        help_text="Breaker that feeds this lighting circuit",
    )
    description = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location__name", "identifier"]
        constraints = [
            models.UniqueConstraint(
                fields=["location", "identifier"],
                name="electrical_circuits_lightswitch_unique_identifier",
            ),
        ]
        indexes = [
            models.Index(fields=["location"]),
            models.Index(fields=["breaker"]),
            models.Index(fields=["controls_location"]),
        ]

    def __str__(self) -> str:
        return f"Switch {self.identifier} @ {self.location.name}"


class NetworkDrop(models.Model):
    """
    A network jack / endpoint at a Location.

    Covers raw data jacks, voice drops, patch panel terminations, APs,
    cameras and IoT sensors. The patch panel + port fields capture the
    upstream termination so technicians can trace from a wall jack to the
    technology closet.
    """

    DROP_TYPE_CHOICES = [
        ("data", "Data jack"),
        ("voice", "Voice / phone"),
        ("patch_panel", "Patch panel termination"),
        ("ap", "Wireless access point"),
        ("camera", "Camera"),
        ("iot", "IoT sensor"),
        ("other", "Other"),
    ]

    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="network_drops",
        help_text="Physical location of the drop",
    )
    identifier = models.CharField(
        max_length=80,
        help_text="Jack label or local identifier (e.g. 'wallplate-3A')",
    )
    drop_type = models.CharField(
        max_length=20,
        choices=DROP_TYPE_CHOICES,
        default="data",
    )
    patch_panel = models.CharField(
        max_length=80,
        blank=True,
        help_text="Upstream patch panel name (e.g. 'IDF-1 patch panel B')",
    )
    patch_port = models.CharField(
        max_length=20,
        blank=True,
        help_text="Port number on the upstream patch panel",
    )
    mac_address = models.CharField(
        max_length=17,
        blank=True,
        help_text="MAC address of the connected device, if known",
    )
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    description = models.CharField(max_length=200, blank=True)
    notes = models.TextField(
        blank=True,
        help_text="Approximate description of the connected device",
    )
    photo = models.ImageField(
        upload_to="electrical_circuits/network_drops/",
        blank=True,
        null=True,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location__name", "identifier"]
        constraints = [
            models.UniqueConstraint(
                fields=["location", "identifier"],
                name="electrical_circuits_networkdrop_unique_identifier",
            ),
        ]
        indexes = [
            models.Index(fields=["location"]),
            models.Index(fields=["drop_type"]),
            models.Index(fields=["patch_panel"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_drop_type_display()} {self.identifier} @ {self.location.name}"
