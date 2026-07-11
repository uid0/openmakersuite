"""Facilities models — what an asset needs from the building it lives in.

Split out of :mod:`inventory` in issue #880. The operational/site-requirements
fields that used to hang directly off :class:`inventory.Asset` now live on a
dedicated 1:1 profile here, so the asset record stays focused on
identity/acquisition while the building-interface details (electrical feed,
mechanical services, safety guidance) are modelled explicitly.

This is deliberately *not* ForgeKey: ForgeKey is the many:1 smart-device
control layer. These requirements are intrinsic to the asset even when it has
no smart devices attached.
"""

from django.db import models


class AssetSiteRequirements(models.Model):
    """The building-interface requirements for a single asset (1:1).

    Captures the electrical feed the asset is wired to, the mechanical
    services it depends on (compressed air, ventilation, chilled water, heat/
    flame), and free-text safety guidance for anyone working on it.

    Electrical *topology* stays in :mod:`electrical_circuits`; only the
    asset↔plant LINK (which breaker/disconnect isolate this asset) lives here.
    """

    asset = models.OneToOneField(
        "inventory.Asset",
        on_delete=models.CASCADE,
        related_name="site_requirements",
        help_text="Asset these site requirements belong to.",
    )

    # ── Electrical feed (relocated from inventory.Asset in #880) ──────────
    breaker = models.ForeignKey(
        "electrical_circuits.PowerBreaker",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="asset_requirements",
        help_text="Breaker that feeds this asset.",
    )
    disconnect = models.ForeignKey(
        "electrical_circuits.Disconnect",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="asset_requirements",
        help_text="Disconnect switch used to isolate this asset for lock-out / tag-out.",
    )

    # ── Mechanical services ──────────────────────────────────────────────
    needs_compressed_air = models.BooleanField(
        default=False,
        help_text="Device requires compressed air to operate.",
    )
    generates_heat_or_flame = models.BooleanField(
        default=False,
        help_text="Device has a flame or gives off a lot of heat.",
    )
    needs_ventilation = models.BooleanField(
        default=False,
        help_text="Device requires ventilation when running.",
    )
    needs_chilling = models.BooleanField(
        default=False,
        help_text="Device requires chilled water / active cooling.",
    )

    # ── Free-text guidance ───────────────────────────────────────────────
    special_requirements = models.TextField(
        blank=True,
        help_text="Anything else the asset needs from the building.",
    )
    work_safety_notes = models.TextField(
        blank=True,
        help_text="What the crew should know before working on the asset.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Asset site requirements"
        verbose_name_plural = "Asset site requirements"

    def __str__(self) -> str:
        return f"Site requirements for {self.asset}"
