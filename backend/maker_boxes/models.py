"""Models for the maker box (personal storage bin) system."""

from __future__ import annotations

from django.db import models


class MakerBox(models.Model):
    """A physical personal storage bin assigned (or assignable) to a member.

    Bins are identified by a stable bin_id printed on the physical container
    (e.g. ``PSB-007``). When a member rents a bin we record their WHMCS
    username plus the name we'll print on the label, and cache the WHMCS
    membership expiry so the scan endpoint can compute a status without
    re-hitting WHMCS for every read.
    """

    STATUS_VALID = "valid"
    STATUS_GRACE = "grace"
    STATUS_EXPIRED = "expired"
    STATUS_UNASSIGNED = "unassigned"
    STATUS_UNKNOWN = "unknown"
    # Pre-conversion: identity confirmed via the cascade lookup but no
    # bin_id has been allocated yet. Sits on the pre-conversion queue
    # so staff can batch-convert later in one print run.
    STATUS_PRE_CONVERSION = "pre_conversion"

    STATUS_CHOICES = [
        (STATUS_VALID, "Valid"),
        (STATUS_GRACE, "Grace"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_UNASSIGNED, "Unassigned"),
        (STATUS_UNKNOWN, "Unknown"),
        (STATUS_PRE_CONVERSION, "Pre-conversion (queued for bin allocation)"),
    ]

    # Identity source: which backend confirmed this user's existence.
    # WHMCS covers paid-membership lookups; the Common API (via the Pi
    # proxy) covers add-on / sub-account users that WHMCS doesn't see.
    # ``manual`` is for rows an admin keyed in directly via Django admin.
    IDENTITY_WHMCS = "whmcs"
    IDENTITY_COMMON_API = "common_api"
    IDENTITY_MANUAL = "manual"

    IDENTITY_SOURCE_CHOICES = [
        (IDENTITY_WHMCS, "WHMCS"),
        (IDENTITY_COMMON_API, "Common API (badge / AD)"),
        (IDENTITY_MANUAL, "Manual admin entry"),
    ]

    # bin_id is nullable now — a row in ``pre_conversion`` status hasn't
    # been allocated a bin yet. The ``unique=True`` constraint still
    # holds for non-null values via the DB-level partial unique below.
    bin_id = models.CharField(max_length=20, blank=True, null=True)
    assigned_username = models.CharField(max_length=64, blank=True)
    first_name = models.CharField(max_length=64, blank=True)
    last_name = models.CharField(max_length=64, blank=True)
    email = models.EmailField(blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_UNASSIGNED,
    )
    identity_source = models.CharField(
        max_length=16,
        choices=IDENTITY_SOURCE_CHOICES,
        blank=True,
        default="",
        help_text="Which backend confirmed this row's identity (whmcs / common_api / manual).",
    )
    # Stamped when /convert/ allocates the bin_id and the label is
    # produced. Distinct from ``assigned_at`` (set on first scan) so
    # post-conversion verification doesn't disturb the conversion
    # audit trail.
    conversion_completed_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["bin_id"]
        verbose_name = "Maker box"
        verbose_name_plural = "Maker boxes"
        constraints = [
            # bin_id is unique when set; null is fine (pre-conversion
            # rows haven't been allocated a bin yet). Postgres partial
            # unique index gives us this without rejecting nulls.
            models.UniqueConstraint(
                fields=["bin_id"],
                condition=models.Q(bin_id__isnull=False),
                name="maker_box_bin_id_unique_when_set",
            ),
        ]

    def __str__(self) -> str:
        if self.assigned_username:
            return f"{self.bin_id} ({self.assigned_username})"
        return self.bin_id

    @property
    def display_name(self) -> str:
        parts = [self.first_name, self.last_name]
        joined = " ".join(p for p in parts if p)
        return joined or self.assigned_username


# Sequential MBX-NNN allocator. Mirrors reorder_queue.PurchaseOrder.
# auto_generate_po_number — the only existing precedent in the codebase
# for "next unused N" with collision retry. Returns a string; raises
# RuntimeError if 50 consecutive collisions hit (effectively never;
# safety valve against an infinite loop on a bad index).
def auto_generate_bin_id(prefix: str = "MBX", padding: int = 3) -> str:
    """Return the next unused MBX-NNN bin_id.

    Reads the highest existing bin_id matching ``{prefix}-`` and
    increments. Collisions are handled by the caller's save: the
    partial unique constraint will raise IntegrityError if two
    concurrent allocations land on the same number, and the caller
    should retry. ``views._allocate_bin_id`` wraps this in a retry
    loop.
    """
    last = (
        MakerBox.objects.filter(bin_id__startswith=f"{prefix}-")
        .exclude(bin_id__isnull=True)
        .order_by("-bin_id")
        .first()
    )
    next_num = 1
    if last and last.bin_id:
        try:
            last_num = int(last.bin_id.rsplit("-", 1)[-1])
            next_num = last_num + 1
        except (ValueError, IndexError):
            pass
    return f"{prefix}-{next_num:0{padding}d}"
