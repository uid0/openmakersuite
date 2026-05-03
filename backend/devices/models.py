"""
Device interface + network drop modeling (oms-06x).

Provides NetBox-style network modeling on top of ``inventory.Asset``:

* :class:`Interface` — a network interface (wifi, ethernet, etc.) on an Asset,
  uniquely identified by MAC address. Replaces the various ad-hoc MAC fields
  scattered across ``Asset.mac_address``, ``forgekey.ESP32Device.mac_address``,
  and the connected-device MAC on legacy ``electrical_circuits.NetworkDrop``.

* :class:`NetworkDrop` — a wall jack / patch panel position at a Location,
  intended as the cable connection target for Interfaces in the [3/7] cable
  modeling work. Distinct from the legacy ``electrical_circuits.NetworkDrop``
  (which mixes power-area and IT-area concerns); the legacy model stays in
  place for back-compat until the frontend migrates to this one.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from inventory.models import Asset, Location

# Optional separators so input forms accept colon, dash, or no separator. All
# representations normalize to lowercase no-separator on save.
MAC_REGEX = r"^[0-9A-Fa-f]{2}([:-]?[0-9A-Fa-f]{2}){5}$"

mac_validator = RegexValidator(
    regex=MAC_REGEX,
    message=(
        "MAC address must be 12 hex digits, optionally separated by ':' or '-' "
        "(e.g., aabbccddeeff or aa:bb:cc:dd:ee:ff)."
    ),
)


def normalize_mac(value: str) -> str:
    """Return ``value`` stripped of separators and lowercased.

    Raises :class:`~django.core.exceptions.ValidationError` if the input does
    not match the canonical MAC pattern.
    """

    if value is None:
        return value
    cleaned = re.sub(r"[:-]", "", value).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", cleaned):
        raise ValidationError(
            "MAC address must be 12 hex digits " "(got %(value)r)." % {"value": value},
        )
    return cleaned


class Interface(models.Model):
    """A network interface on an Asset.

    The MAC address is stored in canonical lowercase no-separator form
    (e.g., ``aabbccddeeff``). Validation accepts the more user-friendly
    colon- or dash-separated forms; :meth:`save` normalizes on the way in.
    """

    TYPE_ETHERNET = "ethernet"
    TYPE_WIFI = "wifi"
    TYPE_USB_ETHERNET = "usb-ethernet"
    TYPE_BLUETOOTH = "bluetooth"
    TYPE_RS485 = "rs485"
    TYPE_OTHER = "other"

    INTERFACE_TYPE_CHOICES = [
        (TYPE_ETHERNET, "Ethernet"),
        (TYPE_WIFI, "Wi-Fi"),
        (TYPE_USB_ETHERNET, "USB Ethernet"),
        (TYPE_BLUETOOTH, "Bluetooth"),
        (TYPE_RS485, "RS-485"),
        (TYPE_OTHER, "Other"),
    ]

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="interfaces",
        help_text="Asset this interface belongs to.",
    )
    name = models.CharField(
        max_length=50,
        help_text="Interface name (e.g., 'wlan0', 'eth0', 'lan0').",
    )
    interface_type = models.CharField(
        max_length=20,
        choices=INTERFACE_TYPE_CHOICES,
        default=TYPE_ETHERNET,
    )
    mac_address = models.CharField(
        max_length=17,
        validators=[mac_validator],
        help_text=(
            "MAC address. Accepted in any common form on input; normalized to "
            "lowercase no-separator on save (e.g., 'aabbccddeeff')."
        ),
    )
    mtu = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum transmission unit, in bytes (optional).",
    )
    enabled = models.BooleanField(default=True)
    description = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "mac_address"],
                name="devices_interface_unique_mac_per_asset",
            ),
            models.UniqueConstraint(
                fields=["asset", "name"],
                name="devices_interface_unique_name_per_asset",
            ),
        ]
        indexes = [
            models.Index(fields=["mac_address"]),
            models.Index(fields=["asset"]),
        ]

    def __str__(self) -> str:
        return f"{self.asset.name} / {self.name} ({self.mac_address})"

    def clean(self) -> None:
        super().clean()
        if self.mac_address:
            self.mac_address = normalize_mac(self.mac_address)

    def save(self, *args, **kwargs):
        if self.mac_address:
            self.mac_address = normalize_mac(self.mac_address)
        super().save(*args, **kwargs)


class NetworkDrop(models.Model):
    """A wall jack / patch panel termination at a Location.

    Intended as the cable connection target for :class:`Interface` once the
    cable model lands in [3/7]. Because we don't control IT racks, the
    upstream patch panel is recorded as free text rather than a modeled FK.
    """

    WIRE_CAT5E = "cat5e"
    WIRE_CAT6 = "cat6"
    WIRE_CAT6A = "cat6a"
    WIRE_FIBER_MM = "fiber-mm"
    WIRE_FIBER_SM = "fiber-sm"

    WIRE_TYPE_CHOICES = [
        (WIRE_CAT5E, "Cat5e"),
        (WIRE_CAT6, "Cat6"),
        (WIRE_CAT6A, "Cat6a"),
        (WIRE_FIBER_MM, "Fiber, multi-mode"),
        (WIRE_FIBER_SM, "Fiber, single-mode"),
    ]

    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="device_network_drops",
        help_text="Location of the drop.",
    )
    drop_label = models.CharField(
        max_length=80,
        help_text="Drop label (e.g., 'A12', 'Sewing-NW-1').",
    )
    wire_type = models.CharField(
        max_length=20,
        choices=WIRE_TYPE_CHOICES,
        default=WIRE_CAT6,
    )
    patch_panel_position = models.CharField(
        max_length=120,
        blank=True,
        help_text=(
            "Free-text upstream patch panel + port (e.g., 'IDF-1 / 24'). "
            "Free-text since the user does not control the IT rack."
        ),
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location__name", "drop_label"]
        constraints = [
            models.UniqueConstraint(
                fields=["location", "drop_label"],
                name="devices_networkdrop_unique_label",
            ),
        ]
        indexes = [
            models.Index(fields=["location"]),
            models.Index(fields=["wire_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.drop_label} @ {self.location.name}"
