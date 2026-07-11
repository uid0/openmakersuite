"""
Electrical and network infrastructure tracking (oms-tt5).

Tracks the physical electrical system (breakers, outlets, light switches) and
network drops (patch panels, APs, cameras, IoT sensors) in the makerspace so
maintenance can trace power and connectivity from any fixture back to its
source.

Power topology models (Power*, oms-wwx) provide a NetBox-grade hierarchy
PowerPanel → PowerBreaker → PowerCircuit → PowerOutlet that supersedes the
flat Breaker/Outlet pair. The legacy models are kept in place for backward
compatibility while consumers migrate.

The Cable / PowerPort / HardwiredConnection abstractions used to model the
cordset and hardwired feeds between a breaker and an asset, but were too
painful to keep current — operators routinely failed to enter (or maintain)
cable data. Assets now point directly at their feeding breaker (and, when
present, an upstream Disconnect for lock-out / tag-out) via FKs on the
``Asset`` model.
"""

from __future__ import annotations

from django.core.validators import MinValueValidator
from django.db import models

from inventory.models import Location

NEMA_PORT_TYPE_CHOICES = [
    # NEMA straight blade
    ("5-15R", "NEMA 5-15R (120V 15A)"),
    ("5-20R", "NEMA 5-20R (120V 20A)"),
    ("6-15R", "NEMA 6-15R (240V 15A)"),
    ("6-20R", "NEMA 6-20R (240V 20A)"),
    # NEMA locking (twist-lock)
    ("L5-15R", "NEMA L5-15R (120V 15A locking)"),
    ("L5-20R", "NEMA L5-20R (120V 20A locking)"),
    ("L5-30R", "NEMA L5-30R (120V 30A locking)"),
    ("L6-20R", "NEMA L6-20R (240V 20A locking)"),
    ("L6-30R", "NEMA L6-30R (240V 30A locking)"),
    # NEMA range/dryer
    ("14-30R", "NEMA 14-30R (240V 30A)"),
    ("14-50R", "NEMA 14-50R (240V 50A)"),
    # IEC 60320 — rack PDU + server / appliance cordsets. Added 2026-05-18
    # after the frontend AssetPowerChainEditor was already offering C13/C19
    # and the backend rejected them.
    ("C13", "IEC C13 (PDU appliance, ≤10A)"),
    ("C14", "IEC C14 (PDU inlet, ≤10A)"),
    ("C19", "IEC C19 (PDU high-current, ≤16A)"),
    ("C20", "IEC C20 (PDU high-current inlet, ≤16A)"),
    # Misc
    ("USB", "USB charging"),
    ("OTHER", "Other"),
    # Lowercase legacy alias — the frontend send "other" in lowercase
    # for a stretch (PR #416). Accept both to avoid breaking existing
    # rows + UI without a migration.
    ("other", "Other (legacy lowercase)"),
]


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


class PowerPanel(models.Model):
    """
    A power distribution panel (load center) at a Location.

    Roughly NetBox's PowerPanel: the upstream container for breakers. Replaces
    the (location, panel) free-text pair on the legacy `Breaker` model.
    """

    PHASE_SINGLE = "single"
    PHASE_SPLIT = "split"
    PHASE_THREE = "three"
    PHASE_CHOICES = [
        (PHASE_SINGLE, "Single phase"),
        (PHASE_SPLIT, "Split phase (120/240V)"),
        (PHASE_THREE, "Three phase"),
    ]

    # Breaker family — informs sourcing replacements + LOTO procedure choice.
    # Listed by prevalence in North American makerspaces; "DIN_RAIL" covers
    # the IEC C60/C120 modular breakers common on imported industrial gear.
    BREAKER_TYPE_CHOICES = [
        ("SQUARE_D_QO", "Square D QO (plug-on, 10kA)"),
        ("SQUARE_D_HOMELINE", "Square D Homeline (plug-on, 10kA)"),
        ("EATON_CH", "Eaton CH / Cutler-Hammer Classic"),
        ("EATON_BR", "Eaton BR (residential)"),
        ("SIEMENS_QP", "Siemens QP / Murray MP"),
        ("GE_Q_LINE", "GE Q-Line / ABB Q-Line"),
        ("FEDERAL_PACIFIC", "Federal Pacific Stab-Lok (legacy — replace)"),
        ("PUSHMATIC", "Pushmatic / ITE-Bulldog (legacy)"),
        ("DIN_RAIL", "IEC DIN-rail (C60 / industrial)"),
        ("OTHER", "Other / unknown"),
    ]

    # Some manufacturers number slots top-to-bottom (most common), others
    # bottom-to-top (notably some industrial / European panels and a few
    # older US load centers). Drives the rendering order in
    # PanelLayoutGrid so the on-screen layout matches what the operator
    # actually sees on the cabinet door.
    NUMBERING_TOP_DOWN = "top_down"
    NUMBERING_BOTTOM_UP = "bottom_up"
    NUMBERING_DIRECTION_CHOICES = [
        (NUMBERING_TOP_DOWN, "Top-down (slot 1 at top)"),
        (NUMBERING_BOTTOM_UP, "Bottom-up (slot 1 at bottom)"),
    ]

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="power_panels",
        help_text="Site/Location where the panel is mounted",
    )
    name = models.CharField(
        max_length=100,
        help_text="Panel name (e.g., 'Sewing Panel A', 'Main Distribution')",
    )
    # Feeder circuit that supplies this panel from upstream. NULL for the
    # service-entrance / main panel. Pointing at a PowerCircuit (not a
    # breaker directly) keeps the chain consistent with how loads are
    # already wired in the model: breaker → circuit → load. Following
    # `fed_by → breaker → panel` gives the parent panel for free.
    fed_by = models.ForeignKey(
        "PowerCircuit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="downstream_panels",
        help_text="Upstream feeder circuit. Leave blank for the main / service-entrance panel.",
    )
    phase_configuration = models.CharField(
        max_length=10,
        choices=PHASE_CHOICES,
        default=PHASE_SPLIT,
    )
    voltage = models.PositiveIntegerField(
        default=240,
        validators=[MinValueValidator(1)],
        help_text="Nominal voltage rating (e.g. 120, 208, 240, 480)",
    )
    main_breaker_amperage = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="Main breaker amperage (null if no main / sub-fed)",
    )
    breaker_type = models.CharField(
        max_length=30,
        choices=BREAKER_TYPE_CHOICES,
        blank=True,
        default="",
        help_text=(
            "Breaker family this panel accepts. Used to filter replacement "
            "sourcing and warn when a mismatched breaker is added."
        ),
    )
    numbering_direction = models.CharField(
        max_length=10,
        choices=NUMBERING_DIRECTION_CHOICES,
        default=NUMBERING_TOP_DOWN,
        help_text=(
            "Slot 1 location on the physical cabinet — top-down for most "
            "North American load centers, bottom-up for some industrial / "
            "European panels."
        ),
    )
    manufacturer = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    install_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    needs_review = models.BooleanField(
        default=False,
        help_text="True for migration-created placeholders that require admin review",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["location", "name"],
                name="electrical_circuits_powerpanel_unique_name",
            ),
        ]
        indexes = [
            models.Index(fields=["location"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} @ {self.location.name}"

    def clean(self) -> None:
        super().clean()
        # Block self-feeding: a panel can't be its own upstream feeder.
        # Direct loops only — deeper cycles (A→B→A) are detected at the
        # service layer where graph traversal makes sense; the common
        # operator slip is picking your own circuit by mistake.
        if self.fed_by_id and self.pk:
            feeder_panel_id = self.fed_by.breaker.panel_id
            if feeder_panel_id == self.pk:
                from django.core.exceptions import ValidationError

                raise ValidationError(
                    {"fed_by": "A panel cannot be fed by one of its own circuits."}
                )


class PowerBreaker(models.Model):
    """
    A breaker installed in a PowerPanel.

    Replaces the legacy `Breaker` model's slot/pole/amperage data with an
    explicit phase assignment so multi-pole and multi-wire-branch circuits can
    be modeled correctly.
    """

    POLE_CHOICES = [(1, "1 pole"), (2, "2 pole"), (3, "3 pole")]

    PHASE_A = "A"
    PHASE_B = "B"
    PHASE_C = "C"
    PHASE_AB = "AB"
    PHASE_BC = "BC"
    PHASE_AC = "AC"
    PHASE_ABC = "ABC"
    PHASE_CHOICES = [
        (PHASE_A, "Phase A"),
        (PHASE_B, "Phase B"),
        (PHASE_C, "Phase C"),
        (PHASE_AB, "Phases A+B"),
        (PHASE_BC, "Phases B+C"),
        (PHASE_AC, "Phases A+C"),
        (PHASE_ABC, "Phases A+B+C"),
    ]

    STATUS_ACTIVE = "active"
    STATUS_SPARE = "spare"
    STATUS_LOCKED_OUT = "locked_out"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_SPARE, "Spare"),
        (STATUS_LOCKED_OUT, "Locked out"),
    ]

    # Orthogonal to `status` — a breaker can be `status=active` AND need
    # review/cleanup of its downstream wiring. Surfaced visually on the
    # panel layout grid so operators can spot stale entries from across
    # the room before they touch anything.
    # Life-safety classification. Critical breakers feed loads that must
    # not be de-energized without protocol — fire alarms, emergency
    # lighting, exit signs, egress door power, refrigerated medicine
    # storage. Surfaced as a red badge on the panel grid, the Location
    # Safety Sign, and the LOTO planning view; consulted by future
    # automation (breaker-trip-impact, scheduled load shedding) to
    # block dangerous actions.
    CRITICAL_CATEGORY_NONE = ""
    CRITICAL_FIRE_ALARM = "fire_alarm"
    CRITICAL_EMERGENCY_LIGHTING = "emergency_lighting"
    CRITICAL_EXIT_SIGN = "exit_sign"
    CRITICAL_EGRESS_DOOR = "egress_door"
    CRITICAL_LIFE_SAFETY_OTHER = "life_safety_other"
    CRITICAL_CATEGORY_CHOICES = [
        (CRITICAL_CATEGORY_NONE, "Not critical"),
        (CRITICAL_FIRE_ALARM, "Fire alarm"),
        (CRITICAL_EMERGENCY_LIGHTING, "Emergency lighting"),
        (CRITICAL_EXIT_SIGN, "Exit sign"),
        (CRITICAL_EGRESS_DOOR, "Egress door power"),
        (CRITICAL_LIFE_SAFETY_OTHER, "Life-safety (other)"),
    ]

    REVIEW_OK = "ok"
    REVIEW_NEEDS_ATTENTION = "needs_attention"
    REVIEW_CIRCUIT_MOVED = "circuit_moved"
    REVIEW_STATUS_CHOICES = [
        (REVIEW_OK, "OK"),
        (
            REVIEW_NEEDS_ATTENTION,
            "Needs attention — active but circuit confirmed gone/wrong",
        ),
        (
            REVIEW_CIRCUIT_MOVED,
            "Circuit moved — awaiting cleanup",
        ),
    ]

    panel = models.ForeignKey(
        PowerPanel,
        on_delete=models.CASCADE,
        related_name="breakers",
    )
    position = models.CharField(
        max_length=20,
        help_text="Slot number on the panel (e.g. '12', '14/16' for tandem)",
    )
    pole_count = models.PositiveSmallIntegerField(
        choices=POLE_CHOICES,
        default=1,
    )
    amperage = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Trip amperage of the breaker",
    )
    phase = models.CharField(
        max_length=3,
        choices=PHASE_CHOICES,
        default=PHASE_A,
    )
    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
    )
    review_status = models.CharField(
        max_length=20,
        choices=REVIEW_STATUS_CHOICES,
        default=REVIEW_OK,
        help_text=(
            "Operator review flag for stale wiring. 'needs_attention' = red "
            "(active but downstream wiring is wrong/missing), 'circuit_moved' "
            "= grey (known to be reassigned, cleanup pending)."
        ),
    )
    review_note = models.TextField(
        blank=True,
        help_text="Free-text context for the review flag (what changed, by whom, when).",
    )
    label = models.CharField(
        max_length=200,
        blank=True,
        help_text="Human-readable label (e.g., 'Wood shop dust collector')",
    )
    notes = models.TextField(blank=True)
    needs_review = models.BooleanField(default=False)
    is_critical = models.BooleanField(
        default=False,
        help_text=(
            "Flags a life-safety circuit (fire alarm, emergency lighting, "
            "exit sign, egress door). Critical breakers render with a red "
            "warning badge and block / warn in LOTO planning."
        ),
    )
    critical_category = models.CharField(
        max_length=32,
        choices=CRITICAL_CATEGORY_CHOICES,
        blank=True,
        default=CRITICAL_CATEGORY_NONE,
        help_text=(
            "Type of life-safety load (e.g., fire_alarm, emergency_lighting). "
            "Required when is_critical=True; ignored otherwise."
        ),
    )
    critical_note = models.TextField(
        blank=True,
        help_text=(
            "Free-text context for the critical flag — what's downstream, "
            "code reference, last inspection, recovery procedure if tripped."
        ),
    )
    required_loto_devices = models.ManyToManyField(
        "loto.LOTODevice",
        blank=True,
        related_name="breakers",
        help_text=(
            "LOTO devices required to safely isolate this breaker. Propagates "
            "to AssetEnergySource rows derived from assets fed by this breaker."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["panel__name", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["panel", "position"],
                name="electrical_circuits_powerbreaker_unique_position",
            ),
        ]
        indexes = [
            models.Index(fields=["panel"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.panel.name}/{self.position} ({self.amperage}A {self.phase})"

    def clean(self) -> None:
        # Critical flag + category are paired: setting one without the other
        # is meaningless and would let a "critical" breaker slip through
        # without telling operators *why* it's critical.
        from django.core.exceptions import ValidationError

        if self.is_critical and not self.critical_category:
            raise ValidationError(
                {
                    "critical_category": (
                        "Pick a critical category when is_critical=True so "
                        "the warning badge and Location Safety Sign can label "
                        "what's protected."
                    ),
                }
            )
        if self.critical_category and not self.is_critical:
            raise ValidationError(
                {
                    "is_critical": (
                        "critical_category is set but is_critical=False — "
                        "either flip the flag or clear the category."
                    ),
                }
            )

    @property
    def assets(self):
        """Assets fed by this breaker.

        Compat shim: the ``Asset → breaker`` FK moved to
        ``facilities.AssetSiteRequirements`` (#880, whose FK uses
        ``related_name="asset_requirements"``). Historical callers used the
        old FK's ``related_name="assets"`` reverse accessor; keep that read
        API working via the profile.
        """
        from inventory.models import Asset

        return Asset.objects.filter(site_requirements__breaker=self)


class PowerCircuit(models.Model):
    """
    A circuit fed by a PowerBreaker.

    Usually 1:1 with its breaker, but multi-wire branch circuits can share a
    single breaker — hence ForeignKey rather than OneToOne. `max_load_amps`
    defaults to 80% of breaker amperage per NEC continuous-load rules.
    """

    breaker = models.ForeignKey(
        PowerBreaker,
        on_delete=models.CASCADE,
        related_name="circuits",
    )
    label = models.CharField(
        max_length=200,
        blank=True,
        help_text="Circuit label (e.g., 'Bench row 1')",
    )
    conductor_size = models.CharField(
        max_length=20,
        blank=True,
        help_text="Conductor gauge (e.g., '12 AWG', '10 AWG')",
    )
    conductor_length_ft = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Approximate conductor run length in feet",
    )
    max_load_amps = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text=(
            "Maximum continuous load in amps. Defaults to breaker amperage × 0.8 "
            "if not set explicitly."
        ),
    )
    notes = models.TextField(blank=True)
    needs_review = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["breaker__panel__name", "breaker__position"]
        indexes = [
            models.Index(fields=["breaker"]),
        ]

    def save(self, *args, **kwargs):
        if self.max_load_amps is None and self.breaker_id is not None:
            self.max_load_amps = int(self.breaker.amperage * 0.8)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.label or f"Circuit on {self.breaker}"


class Disconnect(models.Model):
    """
    A disconnect switch isolating a hardwired (or hard-wired-style) load from
    its upstream circuit.

    Hardwired equipment (RTUs, dust collectors, water heaters, exhaust fans)
    is fed directly from a breaker without a receptacle in between. The
    disconnect switch on the wall next to the equipment is the operator-
    accessible isolation point and the natural LOTO attach point. Assets
    point at a Disconnect directly via ``Asset.disconnect`` so the LOTO chain
    is a single field lookup.

    Some loads have no separate disconnect (the breaker itself serves) — in
    that case operators create a ``Disconnect(disconnect_type='none')`` so
    every hardwired load still resolves through a Disconnect record and the
    LOTO/inspection paths have a single object to walk.
    """

    DISCONNECT_TYPE_FUSED = "fused"
    DISCONNECT_TYPE_UNFUSED = "unfused"
    DISCONNECT_TYPE_TOGGLE = "toggle"
    DISCONNECT_TYPE_INTEGRAL = "integral"
    DISCONNECT_TYPE_NONE = "none"
    DISCONNECT_TYPE_CHOICES = [
        (DISCONNECT_TYPE_FUSED, "Fused safety switch"),
        (DISCONNECT_TYPE_UNFUSED, "Unfused safety switch"),
        (DISCONNECT_TYPE_TOGGLE, "Toggle / snap switch"),
        (DISCONNECT_TYPE_INTEGRAL, "Integral to the equipment"),
        (DISCONNECT_TYPE_NONE, "No separate disconnect (breaker serves)"),
    ]

    circuit = models.ForeignKey(
        PowerCircuit,
        on_delete=models.PROTECT,
        related_name="disconnects",
        help_text="Upstream circuit this disconnect isolates.",
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="disconnects",
        help_text=(
            "Where the disconnect is mounted. Nullable because 'integral' and "
            "'none' types don't have a physical switch separate from the load."
        ),
    )
    label = models.CharField(
        max_length=120,
        help_text="Human label (e.g., 'Dust collector disconnect — east wall').",
    )
    disconnect_type = models.CharField(
        max_length=10,
        choices=DISCONNECT_TYPE_CHOICES,
    )
    amperage = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="Switch rating in amps (informational; not enforced against load).",
    )
    fuse_size = models.CharField(
        max_length=20,
        blank=True,
        help_text="Fuse size string (e.g., '30A class J'). Only meaningful for fused type.",
    )
    is_lockable = models.BooleanField(
        default=True,
        help_text="True if the switch accepts a lockout device for LOTO.",
    )
    photo = models.ImageField(
        upload_to="electrical_circuits/disconnects/",
        null=True,
        blank=True,
    )
    notes = models.TextField(blank=True)
    required_loto_devices = models.ManyToManyField(
        "loto.LOTODevice",
        blank=True,
        related_name="disconnects",
        help_text=(
            "LOTO devices required to safely isolate this disconnect. When set, "
            "LOTO resolution should prefer these over the upstream breaker's list."
        ),
    )
    needs_review = models.BooleanField(
        default=False,
        help_text=(
            "Flagged automatically when clean() detects an inconsistent "
            "combination (fused with no fuse_size, lockable integral/none, etc.)."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["label"]
        indexes = [
            models.Index(fields=["circuit"]),
            models.Index(fields=["location"]),
            models.Index(fields=["disconnect_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.get_disconnect_type_display()})"

    def clean(self) -> None:
        super().clean()
        # Field crews often don't read the fuse size off the cartridge — record
        # the disconnect anyway and flag it for an inspector to chase down,
        # rather than refusing to save and losing the data.
        flag = False
        if self.disconnect_type == self.DISCONNECT_TYPE_FUSED and not self.fuse_size:
            flag = True
        # Integral / none types are part of the equipment (or there is no
        # separate switch); they typically can't accept a lockout device, so a
        # lockable=True is almost always a data-entry slip worth reviewing.
        if (
            self.disconnect_type in (self.DISCONNECT_TYPE_INTEGRAL, self.DISCONNECT_TYPE_NONE)
            and self.is_lockable
        ):
            flag = True
        if flag:
            self.needs_review = True

    @property
    def assets(self):
        """Assets isolated by this disconnect.

        Compat shim for the ``Asset → disconnect`` FK that moved to
        ``facilities.AssetSiteRequirements`` (#880); preserves the old
        ``disconnect.assets`` reverse-accessor read API via the profile.
        """
        from inventory.models import Asset

        return Asset.objects.filter(site_requirements__disconnect=self)


class PowerOutlet(models.Model):
    """
    A power receptacle on a PowerCircuit at a physical Location.

    Supersedes the legacy `Outlet` model. `outlet_type` is a NEMA standard
    code (5-15R, L6-30R, etc.) describing the receptacle shape.
    """

    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUS_CAPPED = "capped"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_INACTIVE, "Inactive"),
        (STATUS_CAPPED, "Capped / decommissioned"),
    ]

    circuit = models.ForeignKey(
        PowerCircuit,
        on_delete=models.PROTECT,
        related_name="outlets",
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="power_outlets",
    )
    disconnect = models.ForeignKey(
        Disconnect,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="powered_outlets",
        help_text=(
            "Optional upstream disconnect — set for receptacle circuits with a "
            "dedicated isolation switch (welder outlets, 240V loads)."
        ),
    )
    outlet_type = models.CharField(
        max_length=20,
        choices=NEMA_PORT_TYPE_CHOICES,
        default="5-15R",
    )
    label = models.CharField(
        max_length=80,
        blank=True,
        help_text="Local identifier (e.g., 'NW-bench-1')",
    )
    location_description = models.CharField(
        max_length=200,
        blank=True,
        help_text="Physical placement (e.g., 'east wall, 3 ft from corner')",
    )
    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
    )
    notes = models.TextField(blank=True)
    needs_review = models.BooleanField(default=False)
    legacy_outlet = models.OneToOneField(
        Outlet,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="power_outlet",
        help_text="Link to the legacy Outlet row this was migrated from",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location__name", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["location", "label"],
                condition=models.Q(label__gt=""),
                name="electrical_circuits_poweroutlet_unique_label",
            ),
        ]
        indexes = [
            models.Index(fields=["circuit"]),
            models.Index(fields=["location"]),
            models.Index(fields=["outlet_type"]),
        ]

    def __str__(self) -> str:
        label = self.label or f"#{self.pk}"
        return f"PowerOutlet {label} @ {self.location.name}"
