"""
Climate / HVAC models (oms-gzycmj).

Owns the per-room Thermostat record so the per-Location Safety Sign can answer
"which breakers kill the HVAC for this room?". The thermostat's
``controlled_asset`` is the upstream RTU / furnace / mini-split that actually
moves the air — walking from that asset through its hardwired connection (or
fall-back cordset feed) is what produces the kill-breaker list on the sign.
"""

from __future__ import annotations

from django.db import models

from inventory.models import Asset, Location


class Thermostat(models.Model):
    """A wall-mounted thermostat that commands an HVAC asset.

    Thermostats are entered manually — there is no auto-discovery from a
    building-management system. The two FKs to Location are intentional:

    * ``location`` is where the operator finds the device (often a hallway
      or the room itself).
    * ``controls_location`` is the room whose temperature it actually
      regulates. Defaults to ``location`` via ``save`` when blank, since the
      common case is "thermostat in the room it conditions".
    """

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="thermostats",
        help_text="Where the thermostat is mounted.",
    )
    controls_location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="controlling_thermostats",
        help_text=(
            "Which room this thermostat actually conditions — often equal to "
            "``location``. Auto-defaults to ``location`` on save when blank."
        ),
    )
    label = models.CharField(
        max_length=120,
        help_text="Human label (e.g., 'Wood shop north', 'Front office').",
    )
    controlled_asset = models.ForeignKey(
        Asset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="controlling_thermostats",
        help_text=(
            "The HVAC/RTU asset this thermostat commands. Used to derive the "
            "kill-breaker list on the location's safety sign."
        ),
    )
    manufacturer = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    needs_review = models.BooleanField(
        default=False,
        help_text=(
            "Flag for safety-sign rendering. True when the kill-breaker chain "
            "could not be resolved (no controlled_asset, or the asset has no "
            "hardwired/cordset feed)."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["controls_location__name", "label"]
        indexes = [
            models.Index(fields=["location"]),
            models.Index(fields=["controls_location"]),
            models.Index(fields=["controlled_asset"]),
        ]

    def __str__(self) -> str:
        room = self.controls_location.name if self.controls_location_id else self.location.name
        return f"Thermostat {self.label} ({room})"

    def save(self, *args, **kwargs):
        if self.controls_location_id is None and self.location_id is not None:
            self.controls_location_id = self.location_id
        super().save(*args, **kwargs)
