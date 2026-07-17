"""OMS-native side-tables that annotate hordak's ledger with provenance.

hordak 2.0 has no swappable models — you cannot add columns to ``hordak.Leg``
or ``hordak.Transaction``. So the OMS layer attaches its own data alongside:

- :class:`EntryMeta` (1:1 ``hordak.Transaction``) — why a journal entry exists,
  who posted it, what it reverses, and an idempotency key for source adapters.
- :class:`LegDimension` (1:1 ``hordak.Leg``) — the committee (SIG) and/or asset
  a single balanced line is attributed to.

Phase 1 defines these tables plus the service layer only; nothing writes them
from the domain yet — that wiring is Phase 2.
"""

from django.conf import settings
from django.db import models


class SourceType(models.TextChoices):
    """What produced a ledger entry (stored on :attr:`EntryMeta.source_type`)."""

    SIG_CHARGE = "SIG_CHARGE", "SIG charge"
    PO_RECEIPT = "PO_RECEIPT", "PO receipt"
    VENDOR_INVOICE = "VENDOR_INVOICE", "Vendor invoice"
    DONATION = "DONATION", "Donation"
    ASSET_PURCHASE = "ASSET_PURCHASE", "Asset purchase"
    COST_RECOVERY = "COST_RECOVERY", "Cost recovery"
    SETTLEMENT = "SETTLEMENT", "Settlement"
    OPENING_BALANCE = "OPENING_BALANCE", "Opening balance"
    REVERSAL = "REVERSAL", "Reversal"
    MANUAL = "MANUAL", "Manual"


class EntryMeta(models.Model):
    """OMS provenance/audit for a single hordak ``Transaction`` (journal entry).

    The optional ``(source_type, source_ref)`` pair is the idempotency key a
    source adapter uses so re-processing the same domain event does not double
    post. It is enforced by :attr:`Meta.constraints` (partial unique, ignoring
    blank ``source_ref``).
    """

    transaction = models.OneToOneField(
        "hordak.Transaction",
        on_delete=models.CASCADE,
        related_name="meta",
    )
    source_type = models.CharField(max_length=32, choices=SourceType.choices)
    source_ref = models.CharField(max_length=128, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reverses = models.ForeignKey(
        "hordak.Transaction",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reversed_by",
    )
    posted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "entry metadata"
        verbose_name_plural = "entry metadata"
        constraints = [
            models.UniqueConstraint(
                fields=["source_type", "source_ref"],
                condition=~models.Q(source_ref=""),
                name="uniq_entry_source",
            )
        ]

    def __str__(self) -> str:
        if self.source_ref:
            return f"{self.source_type}:{self.source_ref}"
        return f"{self.source_type} (txn {self.transaction_id})"


class LegDimension(models.Model):
    """Committee (SIG) and/or asset attribution for a single hordak ``Leg``.

    ``sig`` and ``asset`` are ``PROTECT`` on purpose: a Group or Asset that has
    ledger history must not be silently deletable — the books are permanent.
    """

    leg = models.OneToOneField(
        "hordak.Leg",
        on_delete=models.CASCADE,
        related_name="dimension",
    )
    # ForeignKey columns are indexed by default, satisfying the "index sig and
    # asset" requirement for dimension-filtered committee/asset reporting.
    sig = models.ForeignKey(
        "auth.Group",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ledger_lines",
    )
    asset = models.ForeignKey(
        "inventory.Asset",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ledger_lines",
    )

    class Meta:
        verbose_name = "leg dimension"

    def __str__(self) -> str:
        bits = []
        if self.sig_id:
            bits.append(f"sig={self.sig_id}")
        if self.asset_id:
            bits.append(f"asset={self.asset_id}")
        return f"LegDimension(leg={self.leg_id}, {', '.join(bits) or 'unattributed'})"
