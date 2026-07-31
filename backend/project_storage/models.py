"""Models for the Project Storage system.

Members can park work-in-progress in a designated project storage area for
up to 30 days at a stretch. After the 30 days expire, the storage warden
sends a violation notice; if the member doesn't remove the items within
7 days, the items move to a "purgatory" location. After a stint is
removed, the member has to wait 3 days before starting a new one.

This module models a single *stint* (one occupancy by one member), an
append-only audit log of events on that stint, and the physical
:class:`StorageSlot` racking those stints eventually get assigned to.
"""

from __future__ import annotations

import re
import secrets
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.functional import cached_property

DEFAULT_STINT_DAYS = 30
DEFAULT_PURGATORY_GRACE_DAYS = 7
DEFAULT_REENTRY_COOLDOWN_DAYS = 3
EXPIRING_SOON_WINDOW_DAYS = 3


def _generate_stint_id() -> str:
    """Short, URL-safe identifier printed inside the QR code on the label.

    8 characters of a Crockford-base32-ish alphabet (no I/L/O/0/1) so a
    warden reading it off the label by eye isn't confused. ~10^11 keyspace
    is plenty for a makerspace's lifetime of stints.
    """
    alphabet = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
    return "PS-" + "".join(secrets.choice(alphabet) for _ in range(8))


class ProjectStorageStint(models.Model):
    """One occupancy of a project storage shelf/area by one member."""

    STATUS_ACTIVE = "active"
    STATUS_EXPIRING_SOON = "expiring_soon"
    STATUS_EXPIRED = "expired"
    STATUS_PURGATORY_WARNED = "purgatory_warned"
    STATUS_PURGATORY = "purgatory"
    STATUS_REMOVED = "removed"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_EXPIRING_SOON, "Expiring soon"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_PURGATORY_WARNED, "Purgatory warned"),
        (STATUS_PURGATORY, "Purgatory"),
        (STATUS_REMOVED, "Removed"),
    ]

    # Stable, short identifier — encoded in the QR + printed in eye-readable
    # form below the QR so a warden can call it out over the radio.
    stint_id = models.CharField(max_length=16, unique=True, default=_generate_stint_id)

    # Member identity. We keep first/last/email denormalized so the label
    # and the violation email stay readable even after the member's WHMCS
    # row changes (renames, email rotates, etc.).
    username = models.CharField(max_length=64, db_index=True)
    first_name = models.CharField(max_length=64, blank=True)
    last_name = models.CharField(max_length=64, blank=True)
    email = models.EmailField(blank=True)

    # Optional human-friendly tag for the project (e.g. "Restoring my
    # 1972 Schwinn") so the warden has more context than a member name.
    project_title = models.CharField(max_length=120, blank=True)

    started_at = models.DateTimeField(default=timezone.now)
    # expires_at is denormalized from started_at + 30 days so the warden
    # scan path is a single SELECT and so that overriding the window per
    # stint (e.g. a one-off 14-day stint for a known clear-out) doesn't
    # require config plumbing.
    expires_at = models.DateTimeField()

    # When the member or warden marks the stint cleared. Drives the
    # 3-day re-entry cooldown.
    removed_at = models.DateTimeField(null=True, blank=True)

    # When the warden sent the violation notice. Once set, the 7-day
    # purgatory_grace clock starts; purgatory_at is computed from this.
    notice_sent_at = models.DateTimeField(null=True, blank=True)

    # When the items physically moved to purgatory. The warden marks this
    # explicitly so we don't auto-purgatory on a clock and then have to
    # back it out when nobody actually moved the items.
    moved_to_purgatory_at = models.DateTimeField(null=True, blank=True)

    # Designated locations come from inventory.Location. We don't FK to
    # avoid creating a hard inventory dependency at migrate time, but the
    # admin should set these from the SiteSettings page once the spots
    # are created. Storing the location *name* keeps it cheap to read.
    #
    # storage_location_name stays for legacy stints and for ad-hoc storage
    # that isn't racked (a pallet on the floor, a corner of the mezzanine).
    # When :attr:`slot` is set it is authoritative and this free text is
    # ignored — see :attr:`location_display`.
    storage_location_name = models.CharField(max_length=120, blank=True)
    purgatory_location_name = models.CharField(max_length=120, blank=True)

    # The physical racking slot this stint occupies, when it's in the rack.
    # Nullable on purpose: pre-racking stints and non-rack storage have no
    # slot, and the free-text field above still describes those.
    slot = models.ForeignKey(
        "project_storage.StorageSlot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stints",
        help_text=(
            "Reservation slot in the racking. Authoritative over "
            "storage_location_name when set; blank for ad-hoc storage."
        ),
    )

    # Print pipeline state. printed_at is set by the Pi daemon after a
    # successful print; print_target picks the layout the daemon should
    # use (defaults to brother_ql when blank). NULL printed_at on a
    # stint is the queue signal.
    printed_at = models.DateTimeField(null=True, blank=True)
    PRINT_TARGET_BROTHER = "brother_ql"
    PRINT_TARGET_EPSON = "epson_tm"
    PRINT_TARGET_CHOICES = [
        (PRINT_TARGET_BROTHER, "Brother QL label printer"),
        (PRINT_TARGET_EPSON, "Epson TM receipt printer"),
    ]
    print_target = models.CharField(max_length=16, blank=True, choices=PRINT_TARGET_CHOICES)

    # Persisted QR PNG for the kiosk preview / admin regenerate UI. The
    # Pi label-print pipeline doesn't read this column — it generates
    # the full label (QR + text strip) at print time via
    # project_storage/services/label_service.py. This field is for
    # surfacing a logo-embedded validated QR via qrcode.react on the
    # warden detail page without going through the Brother layout.
    qr_code = models.ImageField(
        upload_to="project_storage/qrcodes/",
        null=True,
        blank=True,
        help_text=(
            "Generated QR code image for this stint. Encodes "
            "{FRONTEND_URL}/scan/project-storage/<stint_id>; regenerate "
            "from the warden detail page when FRONTEND_URL changes."
        ),
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["username", "-started_at"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["removed_at"]),
            models.Index(fields=["printed_at"]),
        ]
        constraints = [
            # One live occupancy per physical slot. Partial (unique-when-
            # active) like VisionSlot.unique_active_vision_slot_marker and
            # maker_box_bin_id_unique_when_set: history keeps every stint
            # that ever sat in 1A1, but only one of them may be unresolved
            # at a time. "Active" here is the same predicate
            # member_has_active_stint() uses — an expired-but-not-removed
            # stint still holds its slot, which is the point (the shelf is
            # physically still full).
            models.UniqueConstraint(
                fields=["slot"],
                condition=models.Q(
                    slot__isnull=False,
                    removed_at__isnull=True,
                    moved_to_purgatory_at__isnull=True,
                ),
                name="unique_active_stint_per_slot",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.stint_id} · {self.username}"

    def save(self, *args, **kwargs):
        # Backfill expires_at from started_at so the API caller doesn't
        # have to compute it. If a future use case wants a custom window,
        # set expires_at explicitly before save().
        if not self.expires_at:
            self.expires_at = self.started_at + timedelta(days=DEFAULT_STINT_DAYS)
        super().save(*args, **kwargs)

    # ------------------------------------------------------------------
    # Computed status — single source of truth for the warden UI badges
    # ------------------------------------------------------------------

    def compute_status(self, now: Optional[timezone.datetime] = None) -> str:
        """Return one of STATUS_*.

        Order matters: terminal states (removed, purgatory) win, then
        warned → expired → expiring_soon → active.
        """
        now = now or timezone.now()
        if self.removed_at is not None:
            return self.STATUS_REMOVED
        if self.moved_to_purgatory_at is not None:
            return self.STATUS_PURGATORY
        if self.notice_sent_at is not None:
            return self.STATUS_PURGATORY_WARNED
        if now >= self.expires_at:
            return self.STATUS_EXPIRED
        if now >= self.expires_at - timedelta(days=EXPIRING_SOON_WINDOW_DAYS):
            return self.STATUS_EXPIRING_SOON
        return self.STATUS_ACTIVE

    @property
    def purgatory_at(self) -> Optional[timezone.datetime]:
        """When this stint *will* move to purgatory if not removed.

        Only meaningful once a notice has been sent.
        """
        if self.notice_sent_at is None:
            return None
        return self.notice_sent_at + timedelta(days=DEFAULT_PURGATORY_GRACE_DAYS)

    @property
    def display_name(self) -> str:
        name = " ".join(p for p in (self.first_name, self.last_name) if p).strip()
        return name or self.username

    @property
    def slot_code(self) -> str:
        """Canonical code of the racking slot, or "" for non-rack storage."""
        return self.slot.code if self.slot_id else ""

    @property
    def location_display(self) -> str:
        """Where this stint lives, in one string.

        The slot wins when set — it's a surveyed physical place with a code
        printed on the upright, where storage_location_name is whatever the
        member typed at the kiosk. Legacy/ad-hoc stints fall back to that
        free text so nothing that predates the racking reads as location-less.
        """
        return self.slot_code or self.storage_location_name

    @property
    def expiry_week_and_day(self) -> tuple[int, int]:
        """ISO-week and day-of-year of expires_at — printed in big type."""
        local = timezone.localtime(self.expires_at)
        return local.isocalendar().week, local.timetuple().tm_yday

    # ------------------------------------------------------------------
    # Validation helpers used by the API layer
    # ------------------------------------------------------------------

    @classmethod
    def cooldown_blocks_new_stint(
        cls, username: str, now: Optional[timezone.datetime] = None
    ) -> Optional[timezone.datetime]:
        """If the member can't start a new stint yet, return the unblock time.

        The 3-day cooldown begins when their most recent stint was marked
        removed. If they have no prior removed stint, or the last removal
        was more than 3 days ago, returns None (they're clear to start).
        """
        now = now or timezone.now()
        latest_removed = (
            cls.objects.filter(username=username, removed_at__isnull=False)
            .order_by("-removed_at")
            .first()
        )
        if latest_removed is None:
            return None
        unblock = latest_removed.removed_at + timedelta(days=DEFAULT_REENTRY_COOLDOWN_DAYS)
        if now >= unblock:
            return None
        return unblock

    @classmethod
    def active(cls):
        """Stints that are still holding their space.

        "Active" = not removed and not moved to purgatory. One definition,
        used by the per-member guard, the per-slot guard, the occupancy
        readouts, and (as a Q) the partial unique constraint above.
        """
        return cls.objects.filter(
            removed_at__isnull=True,
            moved_to_purgatory_at__isnull=True,
        )

    @classmethod
    def member_has_active_stint(cls, username: str) -> bool:
        """One stint at a time per member.

        "Active" here = not removed and not moved to purgatory. An expired-
        but-not-removed stint still blocks a new one; the warden has to
        resolve the old one (mark removed or move to purgatory) before the
        member starts a new project.
        """
        return cls.active().filter(username=username).exists()

    @classmethod
    def active_stint_in_slot(cls, slot) -> Optional["ProjectStorageStint"]:
        """The stint currently occupying ``slot``, or None if it's free.

        Mirrors :meth:`member_has_active_stint` on the other axis: a slot
        holds at most one live stint (enforced by
        ``unique_active_stint_per_slot``), so the warden console can show
        "1A1 → PS-ABC12345 (Pat Member)" straight off this.
        """
        if slot is None:
            return None
        return cls.active().filter(slot=slot).first()


class StorageSlot(models.Model):
    """One physical reservation slot in the project-storage racking.

    Slots are addressed by a structured location code — ``1A1``, ``1B3``,
    ``2A2`` — decomposed as:

    * leading number → the pallet **rack**,
    * letter → the **level** on that rack. Early letters (a/b/c) are
      ground-reachable; late letters (x/y/z) are up high and need a pallet
      jack, which is what ``requires_pallet_jack`` records per slot.
    * trailing number → the **position** along the rack, numbered from
      South/East toward North/West.

    The three components are authoritative; :attr:`code` is the canonical
    computed string, stored (rather than derived on read) so it can be
    indexed for scan lookups and printed on the slot's QR/AprilTag label.
    :meth:`compose_code` and :meth:`parse_code` round-trip the two forms.

    Lives in ``project_storage`` rather than reusing ``inventory.Location``:
    Location is the generic free-text "places" table for the supply system
    and modelling permanent racking there would pollute it. Keeping the slot
    here also keeps the ``ProjectStorageStint.slot`` FK intra-app, which is
    the reason this app avoids cross-app FKs in the first place.

    Occupancy lives on the other side of that FK: ``slot.stints`` is every
    stint that ever sat here, and at most one of them may be live at a time
    (``ProjectStorageStint.unique_active_stint_per_slot``). Ask
    :meth:`ProjectStorageStint.active_stint_in_slot` for the current one.
    """

    # Shared by the field validator, the parser, and the API layer, so
    # "what is a valid code?" has exactly one definition.
    CODE_PATTERN = r"^(\d+)([A-Za-z])(\d+)$"
    CODE_RE = re.compile(CODE_PATTERN)
    LEVEL_PATTERN = r"^[A-Za-z]$"

    rack = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Pallet rack number — the leading digits of the code (1 in 1A1).",
    )
    level = models.CharField(
        max_length=1,
        validators=[
            RegexValidator(LEVEL_PATTERN, "Level must be a single letter (A-Z)."),
        ],
        help_text=(
            "Level on the rack — the letter in the code (A in 1A1). Stored "
            "upper-case. Early letters are ground-reachable, late letters are high."
        ),
    )
    position = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
        help_text=(
            "Position along the rack — the trailing digits of the code (1 in "
            "1A1), numbered from South/East toward North/West."
        ),
    )

    # unique=True already creates the unique index (Django skips the plain
    # db_index when a field is unique), so this single declaration *is* the
    # unique(code) constraint — a second UniqueConstraint in Meta would only
    # add a duplicate index to maintain.
    code = models.CharField(
        max_length=16,
        unique=True,
        db_index=True,
        help_text="Canonical location code, computed from rack + level + position (e.g. 1A1).",
    )

    requires_pallet_jack = models.BooleanField(
        default=False,
        help_text="This slot is too high to reach without a pallet jack.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive slots stay on file (their AprilTag ID is permanent) but "
        "are not offered for new reservations.",
    )
    owning_group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="storage_slots",
        help_text="Optional SIG this slot is reserved for.",
    )
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["rack", "level", "position"]
        constraints = [
            # The components are the real identity; code is derived from
            # them, so both spellings of "one slot per physical place" are
            # enforced (code's uniqueness comes from the field itself).
            models.UniqueConstraint(
                fields=["rack", "level", "position"],
                name="unique_storage_slot_components",
            ),
        ]

    def __str__(self) -> str:
        return self.code or self.compose_code(self.rack, self.level, self.position)

    # ------------------------------------------------------------------
    # Occupancy
    # ------------------------------------------------------------------

    @classmethod
    def active_stints_prefetch(cls) -> models.Prefetch:
        """Prefetch that feeds :attr:`current_stint` for a whole page at once.

        Kept next to the property it serves so the ``to_attr`` name has one
        definition — every listing that renders occupancy (API, admin) adds
        this and pays one query instead of one per slot.
        """
        return models.Prefetch(
            "stints",
            queryset=ProjectStorageStint.active().order_by("-started_at"),
            to_attr="active_stints",
        )

    @cached_property
    def current_stint(self) -> Optional["ProjectStorageStint"]:
        """The live stint in this slot, or None when it's free.

        Reads :meth:`active_stints_prefetch`'s ``to_attr`` when the caller
        set it up, and falls back to a direct lookup otherwise — a slot
        fetched on its own (a create response, an admin change form) still
        answers correctly, just with a query.
        """
        occupants = getattr(self, "active_stints", None)
        if occupants is None:
            return ProjectStorageStint.active_stint_in_slot(self)
        return occupants[0] if occupants else None

    # ------------------------------------------------------------------
    # code <-> (rack, level, position)
    # ------------------------------------------------------------------

    @staticmethod
    def compose_code(rack, level: str, position) -> str:
        """Build the canonical code string from the three components."""
        return f"{rack}{(level or '').upper()}{position}"

    @classmethod
    def parse_code(cls, code: str) -> tuple[int, str, int]:
        """Split a code back into ``(rack, level, position)``.

        Raises :class:`ValueError` for anything that isn't
        digits + one letter + digits, so callers (scanners, the generator,
        the API) all reject malformed codes the same way.
        """
        match = cls.CODE_RE.match((code or "").strip())
        if match is None:
            raise ValueError(
                f"Invalid storage slot code {code!r}: expected <rack><level><position>, e.g. 1A1."
            )
        rack, level, position = match.groups()
        return int(rack), level.upper(), int(position)

    def save(self, *args, **kwargs):
        # The components are authoritative: normalize the level and recompute
        # the code on every write so the stored string can never drift from
        # them. This is pure derivation — tag allocation and the other
        # side-effects live in services/storage_slots.py.
        self.level = (self.level or "").upper()
        self.code = self.compose_code(self.rack, self.level, self.position)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if update_fields & {"rack", "level", "position"}:
                update_fields.update({"level", "code"})
                kwargs["update_fields"] = update_fields
        super().save(*args, **kwargs)


class ProjectStorageEvent(models.Model):
    """Append-only audit log of state transitions on a stint."""

    EVENT_CREATED = "created"
    EVENT_SCANNED = "scanned"
    EVENT_NOTICE_SENT = "notice_sent"
    EVENT_MOVED_TO_PURGATORY = "moved_to_purgatory"
    EVENT_REMOVED = "removed"
    EVENT_NOTE_ADDED = "note_added"

    EVENT_CHOICES = [
        (EVENT_CREATED, "Created"),
        (EVENT_SCANNED, "Scanned"),
        (EVENT_NOTICE_SENT, "Violation notice sent"),
        (EVENT_MOVED_TO_PURGATORY, "Moved to purgatory"),
        (EVENT_REMOVED, "Removed"),
        (EVENT_NOTE_ADDED, "Note added"),
    ]

    stint = models.ForeignKey(
        ProjectStorageStint,
        related_name="events",
        on_delete=models.CASCADE,
    )
    event_type = models.CharField(max_length=32, choices=EVENT_CHOICES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    actor_label = models.CharField(
        max_length=120,
        blank=True,
        help_text="Free-text actor label for kiosk / self-service events where "
        "no Django user is logged in (e.g. 'kiosk: member self-issue').",
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["stint", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.stint.stint_id} · {self.event_type} @ {self.created_at:%Y-%m-%d}"
