from django.contrib.auth.models import Group

from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from fiducials.services.allocator import get_active_tag_id
from membership.actor import actor_display

from .models import ProjectStorageEvent, ProjectStorageStint, StorageAssignment, StorageSlot

# ---------------------------------------------------------------------------
# Naming a slot in a request payload
# ---------------------------------------------------------------------------
#
# Two endpoints take "which slot?" from a caller — the kiosk claim and the
# staff assign — and they have to agree on what they accept, because the same
# card in the same hand feeds both: the QR encodes a CODE and the console
# holds a PK. These live at module level rather than on either serializer so
# neither can drift from the other.


def slot_from_code(code, field: str = "slot_code") -> StorageSlot | None:
    """Look up a slot by its printed code. Blank/None → None."""
    code = (code or "").strip()
    if not code:
        return None
    try:
        rack, level, position = StorageSlot.parse_code(code)
    except ValueError as exc:
        raise serializers.ValidationError({field: str(exc)}) from exc
    canonical = StorageSlot.compose_code(rack, level, position)
    slot = StorageSlot.objects.filter(code=canonical).first()
    if slot is None:
        raise serializers.ValidationError({field: f"No storage slot with code {canonical}."})
    return slot


def resolve_slot_ref(value, field: str = "slot") -> StorageSlot | None:
    """Look a slot up by whichever identifier ``value`` turns out to be.

    A code always contains a letter and a pk never does, so ``1A1`` and ``7``
    can't be confused for each other.
    """
    value = (value or "").strip()
    if not value:
        return None

    if StorageSlot.CODE_RE.match(value):
        return slot_from_code(value, field=field)
    if value.isdigit():
        slot = StorageSlot.objects.filter(pk=int(value)).first()
        if slot is None:
            raise serializers.ValidationError({field: f"No storage slot with id {value}."})
        return slot
    raise serializers.ValidationError(
        {field: f"Expected a storage slot id or a code like 1A1; got {value!r}."}
    )


def resolve_slot_fields(attrs: dict) -> StorageSlot | None:
    """Pop ``slot``/``slot_code`` off ``attrs`` and return the one slot meant.

    Both spellings may be sent as long as they name the same slot; sending
    two different slots is the caller contradicting themselves, not a
    precedence question, so it's a 400.
    """
    slot = resolve_slot_ref(attrs.pop("slot", None))
    by_code = slot_from_code(attrs.pop("slot_code", None))
    if slot is not None and by_code is not None and slot.pk != by_code.pk:
        raise serializers.ValidationError(
            {
                "slot_code": f"slot and slot_code name different slots ({slot.code} vs {by_code.code})."
            }
        )
    return slot or by_code


class ProjectStorageEventSerializer(serializers.ModelSerializer):
    actor_username = serializers.SerializerMethodField()

    class Meta:
        model = ProjectStorageEvent
        fields = (
            "id",
            "event_type",
            "actor_username",
            "actor_label",
            "note",
            "created_at",
        )

    def get_actor_username(self, obj: ProjectStorageEvent) -> str:
        return obj.actor.username if obj.actor else ""


class ProjectStorageStintSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()
    display_name = serializers.ReadOnlyField()
    purgatory_at = serializers.ReadOnlyField()
    # Model properties rather than ``source="slot.code"``: a dotted source
    # into a null FK makes DRF drop the key entirely from the payload for
    # slot-less stints (same trap as StorageSlotSerializer.owning_group_name
    # below). The viewset select_related()s slot, so reading them is free.
    slot_code = serializers.ReadOnlyField()
    location_display = serializers.ReadOnlyField()
    expiry_week = serializers.SerializerMethodField()
    expiry_day_of_year = serializers.SerializerMethodField()
    events = ProjectStorageEventSerializer(many=True, read_only=True)
    qr_code_url = serializers.SerializerMethodField()
    april_tag_id = serializers.SerializerMethodField()

    class Meta:
        model = ProjectStorageStint
        fields = (
            "id",
            "stint_id",
            "username",
            "first_name",
            "last_name",
            "email",
            "display_name",
            "project_title",
            "started_at",
            "expires_at",
            "removed_at",
            "notice_sent_at",
            "moved_to_purgatory_at",
            "storage_location_name",
            "purgatory_location_name",
            "slot",
            "slot_code",
            "location_display",
            "notes",
            "status",
            "purgatory_at",
            "expiry_week",
            "expiry_day_of_year",
            "events",
            "qr_code_url",
            "april_tag_id",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "stint_id",
            "expires_at",
            "removed_at",
            "notice_sent_at",
            "moved_to_purgatory_at",
            "slot",
            "slot_code",
            "location_display",
            "status",
            "purgatory_at",
            "expiry_week",
            "expiry_day_of_year",
            "events",
            "qr_code_url",
            "april_tag_id",
            "created_at",
            "updated_at",
        )

    def get_status(self, obj: ProjectStorageStint) -> str:
        return obj.compute_status()

    def get_qr_code_url(self, obj: ProjectStorageStint) -> str | None:
        if not obj.qr_code:
            return None
        request = self.context.get("request") if hasattr(self, "context") else None
        url = obj.qr_code.url
        if request is not None:
            return request.build_absolute_uri(url)
        return url

    def get_april_tag_id(self, obj: ProjectStorageStint) -> int | None:
        # Prefer the annotation (set by the viewset to avoid an N+1 on
        # list endpoints); fall back to a direct lookup for single objects
        # returned from write actions.
        if hasattr(obj, "active_april_tag_id"):
            return obj.active_april_tag_id
        return get_active_tag_id(obj)

    def get_expiry_week(self, obj: ProjectStorageStint) -> int:
        return obj.expiry_week_and_day[0]

    def get_expiry_day_of_year(self, obj: ProjectStorageStint) -> int:
        return obj.expiry_week_and_day[1]


class StartStintSerializer(serializers.Serializer):
    """Self-service kiosk payload for starting a new stint.

    A slot may be named two ways and both land in ``validated_data["slot"]``
    as a :class:`~project_storage.models.StorageSlot` (or None):

    * ``slot`` — either the primary key (what the warden console holds) or
      the code (what the slot's own QR puts in the kiosk URL,
      ``…/kiosk?slot=1A1``). The two can't be confused: a code always
      contains a letter and a pk never does;
    * ``slot_code`` — the code, explicitly, for callers that would rather
      not rely on that. Parsed through :meth:`StorageSlot.parse_code`, so
      ``1a1`` finds ``1A1``.

    Neither is required: ad-hoc, non-rack storage still starts a stint with
    just ``storage_location_name`` (or nothing at all).
    """

    username = serializers.CharField(max_length=64)
    first_name = serializers.CharField(max_length=64, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=64, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    project_title = serializers.CharField(max_length=120, required=False, allow_blank=True)
    storage_location_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    slot = serializers.CharField(max_length=16, required=False, allow_blank=True, allow_null=True)
    slot_code = serializers.CharField(max_length=16, required=False, allow_blank=True)

    def validate(self, attrs: dict) -> dict:
        # Resolve to one canonical key so the view never has to ask which
        # spelling the caller used.
        slot = resolve_slot_fields(attrs)

        # Out-of-service slots stay on file (their marker is permanent) but
        # are explicitly "not offered for new reservations" — a claim is
        # exactly that offer.
        if slot is not None and not slot.is_active:
            raise serializers.ValidationError(
                {"slot": f"Slot {slot.code} is out of service and can't be reserved."}
            )

        attrs["slot"] = slot
        return attrs


# ---------------------------------------------------------------------------
# Storage slots (the physical racking)
# ---------------------------------------------------------------------------


class SlotOccupantSerializer(serializers.ModelSerializer):
    """The live stint in a slot, trimmed to what a warden needs at a glance.

    Deliberately not :class:`ProjectStorageStintSerializer` — that one drags
    the whole event log along, which would make a rack listing enormous.
    """

    display_name = serializers.ReadOnlyField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = ProjectStorageStint
        fields = (
            "id",
            "stint_id",
            "username",
            "display_name",
            "project_title",
            "started_at",
            "expires_at",
            "status",
        )
        read_only_fields = fields

    def get_status(self, obj: ProjectStorageStint) -> str:
        return obj.compute_status()


class SlotAssignmentSummarySerializer(serializers.ModelSerializer):
    """The live C/L/E holding of a slot, trimmed for a rack listing.

    The :class:`SlotOccupantSerializer` of the other occupancy kind: enough
    to say who has the slot and since when, without the notes and audit
    fields the full :class:`StorageAssignmentSerializer` carries.
    """

    type_letter = serializers.ReadOnlyField()
    occupant_display = serializers.ReadOnlyField()

    class Meta:
        model = StorageAssignment
        fields = (
            "id",
            "storage_type",
            "type_letter",
            "occupant_display",
            "assigned_at",
        )
        read_only_fields = fields


class StorageSlotSerializer(serializers.ModelSerializer):
    """Read/write shape for one slot.

    ``code`` is read-only: the components are authoritative and the model
    recomputes the string on save. The explicit UniqueTogetherValidator
    turns a duplicate slot into a 400 instead of letting the DB constraint
    surface as a 500.

    The occupancy readout is the warden-console "who is in this slot right
    now?", and it has two halves because there are two kinds of occupant:
    ``current_stint`` for a member's project (P) and ``current_assignment``
    for a committee/logistics/class holding (C/L/E). ``occupancy_type`` is
    the letter of whichever is live and ``is_occupied`` covers both — a slot
    the welding SIG holds is not free to hand out either.
    """

    april_tag_id = serializers.SerializerMethodField()
    current_stint = serializers.SerializerMethodField()
    current_assignment = serializers.SerializerMethodField()
    is_occupied = serializers.ReadOnlyField()
    occupancy_type = serializers.ReadOnlyField()
    # ``default=""`` (the convention used elsewhere in the codebase) keeps the
    # key present for an unowned slot — without it DRF hits an AttributeError
    # walking into the null FK and silently drops the field from the payload.
    owning_group_name = serializers.CharField(
        source="owning_group.name", read_only=True, default=""
    )

    class Meta:
        model = StorageSlot
        fields = (
            "id",
            "code",
            "rack",
            "level",
            "position",
            "requires_pallet_jack",
            "is_active",
            "owning_group",
            "owning_group_name",
            "notes",
            "april_tag_id",
            "current_stint",
            "current_assignment",
            "occupancy_type",
            "is_occupied",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "code",
            "owning_group_name",
            "april_tag_id",
            "current_stint",
            "current_assignment",
            "occupancy_type",
            "is_occupied",
            "created_at",
            "updated_at",
        )
        validators = [
            UniqueTogetherValidator(
                queryset=StorageSlot.objects.all(),
                fields=("rack", "level", "position"),
                message="A storage slot already exists at that rack/level/position.",
            )
        ]

    def validate_level(self, value: str) -> str:
        # Normalize before the unique-together check runs, so posting "1a1"
        # collides with an existing "1A1" at the serializer instead of at
        # the DB.
        return (value or "").upper()

    def get_april_tag_id(self, obj: StorageSlot) -> int | None:
        # Prefer the viewset's annotation (no N+1 on list); fall back to a
        # direct lookup for objects returned from write actions.
        if hasattr(obj, "active_april_tag_id"):
            return obj.active_april_tag_id
        return get_active_tag_id(obj)

    # StorageSlot.current_stint / current_assignment read the viewset's
    # to_attr prefetches when there are any and fall back to a lookup for the
    # single instance a create/update returns; both are cached per instance,
    # so is_occupied and occupancy_type ride along for free.
    def get_current_stint(self, obj: StorageSlot) -> dict | None:
        occupant = obj.current_stint
        return None if occupant is None else SlotOccupantSerializer(occupant).data

    def get_current_assignment(self, obj: StorageSlot) -> dict | None:
        holding = obj.current_assignment
        return None if holding is None else SlotAssignmentSummarySerializer(holding).data


class RackLevelSpecSerializer(serializers.Serializer):
    """One level of a rack in a bulk-generate request."""

    level = serializers.RegexField(
        StorageSlot.LEVEL_PATTERN,
        error_messages={"invalid": "Level must be a single letter (A-Z)."},
    )
    # Capped so a fat-fingered "1000" can't spawn a rack that swallows the
    # whole tag family in one request.
    positions = serializers.IntegerField(min_value=1, max_value=100)
    requires_pallet_jack = serializers.BooleanField(required=False, default=False)

    def validate_level(self, value: str) -> str:
        return value.upper()


class GenerateRackSerializer(serializers.Serializer):
    """Bulk-generate payload: one rack, a spec per level."""

    rack = serializers.IntegerField(min_value=1)
    levels = serializers.ListField(
        child=RackLevelSpecSerializer(),
        allow_empty=False,
        max_length=26,
    )
    owning_group = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(),
        required=False,
        allow_null=True,
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_levels(self, value: list[dict]) -> list[dict]:
        seen = [spec["level"] for spec in value]
        duplicates = sorted({level for level in seen if seen.count(level) > 1})
        if duplicates:
            raise serializers.ValidationError(
                f"Each level may appear at most once: {', '.join(duplicates)} repeated."
            )
        return value


# ---------------------------------------------------------------------------
# Staff-assigned storage (committee / logistics / class)
# ---------------------------------------------------------------------------


class StorageAssignmentSerializer(serializers.ModelSerializer):
    """Full read shape for one C/L/E holding.

    Read-only end to end: an assignment is created by the ``assign`` action
    and ended by ``release``, both of which are staff-gated and audited
    (``assigned_by``). Letting PATCH move ``released_at`` around would give
    the same state change a second, unaudited door.
    """

    slot_code = serializers.CharField(source="slot.code", read_only=True)
    storage_type_display = serializers.CharField(source="get_storage_type_display", read_only=True)
    type_letter = serializers.ReadOnlyField()
    occupant_display = serializers.ReadOnlyField()
    is_active = serializers.ReadOnlyField()
    # ``default=""`` on both: a dotted source into a null FK makes DRF drop
    # the key from the payload entirely (see StorageSlotSerializer's
    # owning_group_name), and both of these are null for a logistics
    # assignment / an imported row with no recorded staff member.
    owning_group_name = serializers.CharField(
        source="owning_group.name", read_only=True, default=""
    )
    assigned_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StorageAssignment
        fields = (
            "id",
            "slot",
            "slot_code",
            "storage_type",
            "storage_type_display",
            "type_letter",
            "owning_group",
            "owning_group_name",
            "occupant_label",
            "occupant_display",
            "assigned_by",
            "assigned_by_name",
            "assigned_at",
            "released_at",
            "is_active",
            "notes",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_assigned_by_name(self, obj: StorageAssignment) -> str:
        return actor_display(obj.assigned_by)


class AssignSlotSerializer(serializers.Serializer):
    """Payload for ``POST assignments/assign/``.

    The slot is named the same two ways the kiosk accepts (pk or code, under
    ``slot`` or ``slot_code``) — a warden assigning from the console has the
    pk, a warden standing at the rack with a scanner has the code.
    """

    slot = serializers.CharField(max_length=16, required=False, allow_blank=True, allow_null=True)
    slot_code = serializers.CharField(max_length=16, required=False, allow_blank=True)
    storage_type = serializers.ChoiceField(choices=StorageAssignment.STORAGE_TYPE_CHOICES)
    owning_group = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(),
        required=False,
        allow_null=True,
    )
    occupant_label = serializers.CharField(max_length=120, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs: dict) -> dict:
        slot = resolve_slot_fields(attrs)
        if slot is None:
            raise serializers.ValidationError(
                {"slot": "Name the slot to assign, by id or by code (e.g. 1A1)."}
            )
        # Same rule as a kiosk claim: a retired slot keeps its marker but is
        # "not offered for new reservations", and an assignment is a
        # reservation that outlasts every stint.
        if not slot.is_active:
            raise serializers.ValidationError(
                {"slot": f"Slot {slot.code} is out of service and can't be assigned."}
            )
        attrs["slot"] = slot

        # A committee assignment that doesn't say which committee is just a
        # blocked slot — the group IS the occupant identity for type C, and
        # the overview has nothing to show without it. Logistics and class
        # carry their occupant as free text, which may legitimately be blank
        # (a logistics slot is self-describing).
        if (
            attrs["storage_type"] == StorageAssignment.TYPE_COMMITTEE
            and not attrs.get("owning_group")
            and not attrs.get("occupant_label")
        ):
            raise serializers.ValidationError(
                {
                    "owning_group": (
                        "A committee assignment must name the committee "
                        "(owning_group), or describe it in occupant_label."
                    )
                }
            )
        return attrs


class SlotCardBatchSerializer(serializers.Serializer):
    """Which slots to print cards for: an explicit list, or a whole rack.

    Two selection modes because the two real jobs are different. Racking a
    new aisle prints every slot of a rack in code order (the sheets come off
    the printer in the order they get stuck on the uprights); replacing a
    handful of scuffed cards prints exactly those slots, in the order the
    caller listed them. Mixing the two would only make "what did I just
    print?" ambiguous, so exactly one mode is required.
    """

    slot_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=False,
        # 300 cards = 100 sheets. Past that the request is a rack print that
        # should say so, and the renderer would be holding a lot of PNGs.
        max_length=300,
    )
    rack = serializers.IntegerField(min_value=1, required=False)
    level = serializers.RegexField(
        StorageSlot.LEVEL_PATTERN,
        required=False,
        error_messages={"invalid": "Level must be a single letter (A-Z)."},
    )
    # A rack print covers the slots in service. Retired slots keep their
    # marker (see StorageSlot.is_active) and can still be reprinted on
    # purpose, but they shouldn't ride along with a rack's sheets.
    include_inactive = serializers.BooleanField(required=False, default=False)

    def validate_level(self, value: str) -> str:
        return (value or "").upper()

    def validate(self, attrs: dict) -> dict:
        has_ids = bool(attrs.get("slot_ids"))
        has_rack = attrs.get("rack") is not None
        if has_ids and has_rack:
            raise serializers.ValidationError("Provide either slot_ids or a rack filter, not both.")
        if not has_ids and not has_rack:
            raise serializers.ValidationError("Provide slot_ids or a rack to print.")
        if attrs.get("level") and not has_rack:
            raise serializers.ValidationError({"level": "level narrows a rack — send rack too."})
        return attrs


# ---------------------------------------------------------------------------
# The overview grid
# ---------------------------------------------------------------------------
#
# Output-only shapes over the dicts built by services/storage_overview.py.
# They exist so the grid's contract is declared in one readable place (and
# lands in the OpenAPI schema) instead of being implied by whatever the
# builder happened to emit — PR7/PR8 render this payload twice, in a web grid
# and an ASCII table, and both need to know what a cell is.


class StorageOverviewCellSerializer(serializers.Serializer):
    """One slot in the grid. See ``services/storage_overview.py`` for the
    type/status/colour rules — notably that only Project storage is
    coloured."""

    code = serializers.CharField()
    slot_id = serializers.IntegerField()
    position = serializers.IntegerField()
    # P / C / L / E, or null for an empty slot.
    type = serializers.CharField(allow_null=True)
    status = serializers.CharField()
    color = serializers.CharField(allow_null=True)
    occupant = serializers.CharField(allow_blank=True)
    is_active = serializers.BooleanField()


class StorageOverviewRowSerializer(serializers.Serializer):
    """One level of one rack: a dense, 1-indexed run of positions.

    ``cells`` is ``max_position`` long and holds ``null`` where the racking
    has no slot at that position, so every row of a rack lines up.
    """

    level = serializers.CharField()
    cells = serializers.ListField(child=StorageOverviewCellSerializer(allow_null=True))


class StorageOverviewRackSerializer(serializers.Serializer):
    """One rack. ``levels`` is descending — high shelves first, ground last."""

    rack = serializers.IntegerField()
    levels = serializers.ListField(child=serializers.CharField())
    max_position = serializers.IntegerField()
    rows = StorageOverviewRowSerializer(many=True)


class StorageOverviewSerializer(serializers.Serializer):
    """The whole grid, rack by rack in numeric order."""

    racks = StorageOverviewRackSerializer(many=True)
    generated_at = serializers.DateTimeField()
