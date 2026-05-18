"""
Electrical and network infrastructure tracking (oms-tt5).

Tracks the physical electrical system (breakers, outlets, light switches) and
network drops (patch panels, APs, cameras, IoT sensors) in the makerspace so
maintenance can trace power and connectivity from any fixture back to its
source.

Power topology models (Power*, oms-wwx) provide a NetBox-grade hierarchy
PowerPanel → PowerBreaker → PowerCircuit → PowerOutlet ↔ PowerPort that
supersedes the flat Breaker/Outlet pair. The legacy models are kept in place
for backward compatibility while consumers migrate.
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from inventory.models import Asset, Location

NEMA_PORT_TYPE_CHOICES = [
    ("5-15R", "NEMA 5-15R (120V 15A)"),
    ("5-20R", "NEMA 5-20R (120V 20A)"),
    ("6-15R", "NEMA 6-15R (240V 15A)"),
    ("6-20R", "NEMA 6-20R (240V 20A)"),
    ("L5-15R", "NEMA L5-15R (120V 15A locking)"),
    ("L5-20R", "NEMA L5-20R (120V 20A locking)"),
    ("L6-20R", "NEMA L6-20R (240V 20A locking)"),
    ("L6-30R", "NEMA L6-30R (240V 30A locking)"),
    ("14-30R", "NEMA 14-30R (240V 30A)"),
    ("14-50R", "NEMA 14-50R (240V 50A)"),
    ("USB", "USB charging"),
    ("OTHER", "Other"),
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
    label = models.CharField(
        max_length=200,
        blank=True,
        help_text="Human-readable label (e.g., 'Wood shop dust collector')",
    )
    notes = models.TextField(blank=True)
    needs_review = models.BooleanField(default=False)
    required_loto_devices = models.ManyToManyField(
        "loto.LOTODevice",
        blank=True,
        related_name="breakers",
        help_text=(
            "LOTO devices required to safely isolate this breaker. Propagates "
            "to AssetEnergySource rows derived from cables fed by this breaker."
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


class PowerOutlet(models.Model):
    """
    A power receptacle on a PowerCircuit at a physical Location.

    Supersedes the legacy `Outlet` model. `outlet_type` is a NEMA standard
    code (5-15R, L6-30R, etc.) so the cable model in [3/7] can validate
    PowerPort ↔ PowerOutlet compatibility.
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


class PowerPort(models.Model):
    """
    A power input port on an Asset.

    Assets with redundant PSUs (servers, network switches) have multiple
    PowerPort rows. `port_type` matches the NEMA codes on PowerOutlet so the
    cable model in [3/7] can pair compatible plugs and receptacles.
    """

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="power_ports",
    )
    label = models.CharField(
        max_length=80,
        help_text="Port label (e.g., 'Main', 'Auxiliary', 'PSU 1')",
    )
    port_type = models.CharField(
        max_length=20,
        choices=NEMA_PORT_TYPE_CHOICES,
        default="5-15R",
    )
    max_draw_amps = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Nameplate max current draw in amps",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset__name", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "label"],
                name="electrical_circuits_powerport_unique_label",
            ),
        ]
        indexes = [
            models.Index(fields=["asset"]),
            models.Index(fields=["port_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.asset.name} / {self.label} ({self.port_type})"


class Cable(models.Model):
    """
    A physical cable connecting two endpoints in the makerspace (oms-i6g).

    Generic on both ends so the same model captures power runs (PowerOutlet
    ↔ PowerPort) and the volunteer-controlled segments of the network plant
    (devices.Interface ↔ devices.NetworkDrop). Endpoints upstream of the
    devices.NetworkDrop (the IT-managed switch fabric, IDF patch panels) are
    intentionally not modeled.

    Validation in :meth:`clean` enforces that the two endpoints form a
    compatible pair for ``cable_type``: power cables must connect a
    PowerOutlet to a PowerPort, network cables must connect an Interface to
    a NetworkDrop. Order does not matter — either side may be ``endpoint_a``.
    """

    CABLE_TYPE_POWER = "power"
    CABLE_TYPE_NETWORK = "network"
    CABLE_TYPE_CHOICES = [
        (CABLE_TYPE_POWER, "Power"),
        (CABLE_TYPE_NETWORK, "Network"),
    ]

    STATUS_CONNECTED = "connected"
    STATUS_PLANNED = "planned"
    STATUS_DECOMMISSIONED = "decommissioned"
    STATUS_CHOICES = [
        (STATUS_CONNECTED, "Connected"),
        (STATUS_PLANNED, "Planned"),
        (STATUS_DECOMMISSIONED, "Decommissioned"),
    ]

    # Power side endpoints: (electrical_circuits, poweroutlet),
    #                      (electrical_circuits, powerport)
    # Network side endpoints: (devices, interface), (devices, networkdrop)
    _POWER_ENDPOINT_PAIR = frozenset(
        {
            ("electrical_circuits", "poweroutlet"),
            ("electrical_circuits", "powerport"),
        }
    )
    _NETWORK_ENDPOINT_PAIR = frozenset(
        {
            ("devices", "interface"),
            ("devices", "networkdrop"),
        }
    )

    cable_type = models.CharField(
        max_length=10,
        choices=CABLE_TYPE_CHOICES,
        help_text="Determines which endpoint pairing is valid (power vs network).",
    )

    endpoint_a_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.PROTECT,
        related_name="+",
    )
    endpoint_a_object_id = models.CharField(max_length=64)
    endpoint_a = GenericForeignKey("endpoint_a_content_type", "endpoint_a_object_id")

    endpoint_b_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.PROTECT,
        related_name="+",
    )
    endpoint_b_object_id = models.CharField(max_length=64)
    endpoint_b = GenericForeignKey("endpoint_b_content_type", "endpoint_b_object_id")

    color = models.CharField(
        max_length=30,
        blank=True,
        help_text="Optional cable color (useful in dense racks).",
    )
    length_ft = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Approximate cable length in feet.",
    )
    label = models.CharField(
        max_length=80,
        blank=True,
        help_text="Optional cable label (e.g., printed sleeve text).",
    )
    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default=STATUS_CONNECTED,
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["cable_type", "label"]
        indexes = [
            models.Index(
                fields=["endpoint_a_content_type", "endpoint_a_object_id"],
                name="ec_cable_endpoint_a_idx",
            ),
            models.Index(
                fields=["endpoint_b_content_type", "endpoint_b_object_id"],
                name="ec_cable_endpoint_b_idx",
            ),
            models.Index(fields=["cable_type"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_cable_type_display()} cable {self.label or f'#{self.pk}'}"

    def _endpoint_key(self, content_type: ContentType | None) -> tuple[str, str] | None:
        if content_type is None:
            return None
        return (content_type.app_label, content_type.model)

    def clean(self) -> None:
        super().clean()
        a_key = self._endpoint_key(self.endpoint_a_content_type)
        b_key = self._endpoint_key(self.endpoint_b_content_type)
        if a_key is None or b_key is None:
            raise ValidationError("Both endpoints are required.")

        pair = frozenset({a_key, b_key})
        if self.cable_type == self.CABLE_TYPE_POWER:
            if pair != self._POWER_ENDPOINT_PAIR:
                raise ValidationError("Power cables must connect a PowerOutlet to a PowerPort.")
        elif self.cable_type == self.CABLE_TYPE_NETWORK:
            if pair != self._NETWORK_ENDPOINT_PAIR:
                raise ValidationError(
                    "Network cables must connect a devices.Interface to a " "devices.NetworkDrop."
                )
        else:
            raise ValidationError(
                f"Unknown cable_type {self.cable_type!r}.",
            )

        if self.endpoint_a_content_type_id == self.endpoint_b_content_type_id and str(
            self.endpoint_a_object_id
        ) == str(self.endpoint_b_object_id):
            raise ValidationError("A cable cannot connect an endpoint to itself.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
