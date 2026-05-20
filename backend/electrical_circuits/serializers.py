"""DRF serializers for electrical circuits and network drops."""

from rest_framework import serializers

from loto.models import LOTODevice
from loto.serializers import LOTODeviceSerializer

from .models import (
    Breaker,
    Disconnect,
    LightSwitch,
    NetworkDrop,
    Outlet,
    PowerBreaker,
    PowerCircuit,
    PowerOutlet,
    PowerPanel,
)


class BreakerSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = Breaker
        fields = [
            "id",
            "location",
            "location_name",
            "panel",
            "breaker_number",
            "amperage",
            "voltage",
            "poles",
            "description",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class OutletSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)
    breaker_label = serializers.SerializerMethodField()

    class Meta:
        model = Outlet
        fields = [
            "id",
            "location",
            "location_name",
            "identifier",
            "breaker",
            "breaker_label",
            "outlet_type",
            "description",
            "plugged_in_notes",
            "photo",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_breaker_label(self, obj):
        if obj.breaker_id is None:
            return None
        b = obj.breaker
        return f"{b.panel} / {b.breaker_number}"


class LightSwitchSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)
    controls_location_name = serializers.CharField(
        source="controls_location.name", read_only=True, allow_null=True
    )
    breaker_label = serializers.SerializerMethodField()

    class Meta:
        model = LightSwitch
        fields = [
            "id",
            "location",
            "location_name",
            "identifier",
            "controls_location",
            "controls_location_name",
            "breaker",
            "breaker_label",
            "description",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_breaker_label(self, obj):
        if obj.breaker_id is None:
            return None
        b = obj.breaker
        return f"{b.panel} / {b.breaker_number}"


# ---------------------------------------------------------------------
# Power topology CRUD (oms-b25 + oms-wwx). Distinct from the legacy
# Breaker / Outlet flat-pair serializers above — these write to the
# NetBox-grade PowerPanel → PowerBreaker → PowerCircuit → PowerOutlet
# hierarchy that the safety API + frontend visualization read from.
# ---------------------------------------------------------------------


class PowerPanelSerializer(serializers.ModelSerializer):
    """Full read/write serializer for PowerPanel."""

    location_name = serializers.CharField(source="location.name", read_only=True)
    breaker_count = serializers.SerializerMethodField()
    fed_by_summary = serializers.SerializerMethodField()
    downstream_panel_count = serializers.SerializerMethodField()

    class Meta:
        model = PowerPanel
        fields = [
            "id",
            "location",
            "location_name",
            "name",
            "phase_configuration",
            "voltage",
            "main_breaker_amperage",
            "breaker_type",
            "numbering_direction",
            "manufacturer",
            "model",
            "install_date",
            "notes",
            "needs_review",
            "fed_by",
            "fed_by_summary",
            "downstream_panel_count",
            "breaker_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "breaker_count",
            "fed_by_summary",
            "downstream_panel_count",
            "created_at",
            "updated_at",
        ]

    def get_breaker_count(self, obj) -> int:
        # Use a cached annotation when the view supplied one (avoids N+1
        # on the list endpoint); otherwise fall back to a fresh count.
        cached = getattr(obj, "annotated_breaker_count", None)
        if cached is not None:
            return cached
        return obj.breakers.count()

    def get_fed_by_summary(self, obj):
        """Denormalized lineage for the UI hero: parent panel + feeding
        breaker so the frontend can render 'Sub-panel of: LV2A · 60A
        3-pole at slots 14/16/18' without a second round-trip.
        """
        circuit = obj.fed_by
        if circuit is None:
            return None
        breaker = circuit.breaker
        panel = breaker.panel
        return {
            "circuit_id": circuit.id,
            "circuit_label": circuit.label or "",
            "breaker_id": breaker.id,
            "breaker_position": breaker.position,
            "breaker_amperage": breaker.amperage,
            "breaker_pole_count": breaker.pole_count,
            "panel_id": panel.id,
            "panel_name": panel.name,
        }

    def get_downstream_panel_count(self, obj) -> int:
        # No SerializerMethod for the reverse: simple count over the
        # related_name. Negligible cost on the detail endpoint; the list
        # endpoint can annotate if it ever needs this hot.
        return PowerPanel.objects.filter(fed_by__breaker__panel=obj).count()

    def validate(self, attrs):
        """Block self-feeding at the API boundary in addition to model.clean(),
        since DRF skips model.clean() by default."""
        fed_by = attrs.get("fed_by") or getattr(self.instance, "fed_by", None)
        if fed_by is not None and self.instance is not None:
            if fed_by.breaker.panel_id == self.instance.pk:
                raise serializers.ValidationError(
                    {"fed_by": "A panel cannot be fed by one of its own circuits."}
                )
        return attrs


class PowerBreakerSerializer(serializers.ModelSerializer):
    """Full read/write serializer for PowerBreaker."""

    panel_name = serializers.CharField(source="panel.name", read_only=True)
    circuit_count = serializers.SerializerMethodField()

    class Meta:
        model = PowerBreaker
        fields = [
            "id",
            "panel",
            "panel_name",
            "position",
            "pole_count",
            "amperage",
            "phase",
            "status",
            "review_status",
            "review_note",
            "label",
            "notes",
            "needs_review",
            "circuit_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "circuit_count", "created_at", "updated_at"]

    def get_circuit_count(self, obj) -> int:
        return obj.circuits.count()


class PowerCircuitSerializer(serializers.ModelSerializer):
    """Full read/write serializer for PowerCircuit."""

    breaker_label = serializers.SerializerMethodField()
    panel_id = serializers.IntegerField(source="breaker.panel_id", read_only=True)
    panel_name = serializers.CharField(source="breaker.panel.name", read_only=True)
    outlet_count = serializers.SerializerMethodField()

    class Meta:
        model = PowerCircuit
        fields = [
            "id",
            "breaker",
            "breaker_label",
            "panel_id",
            "panel_name",
            "label",
            "conductor_size",
            "conductor_length_ft",
            "max_load_amps",
            "notes",
            "needs_review",
            "outlet_count",
            "created_at",
            "updated_at",
        ]
        # ``max_load_amps`` stays writable but the model's save() defaults
        # it to 80% of the breaker amperage when null — so callers can
        # omit it and get the NEC-derate behavior automatically.
        read_only_fields = ["id", "outlet_count", "created_at", "updated_at"]

    def get_breaker_label(self, obj) -> str:
        b = obj.breaker
        return f"{b.panel.name} / pos {b.position}"

    def get_outlet_count(self, obj) -> int:
        return obj.outlets.count()


class PowerOutletSerializer(serializers.ModelSerializer):
    """Full read/write serializer for PowerOutlet."""

    location_name = serializers.CharField(source="location.name", read_only=True)
    circuit_label = serializers.SerializerMethodField()
    disconnect_label = serializers.CharField(
        source="disconnect.label", read_only=True, allow_null=True
    )

    class Meta:
        model = PowerOutlet
        fields = [
            "id",
            "circuit",
            "circuit_label",
            "location",
            "location_name",
            "disconnect",
            "disconnect_label",
            "outlet_type",
            "label",
            "location_description",
            "status",
            "notes",
            "needs_review",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_circuit_label(self, obj) -> str:
        return obj.circuit.label or f"Circuit #{obj.circuit_id}"


class DisconnectSerializer(serializers.ModelSerializer):
    """Read/write serializer for Disconnect rows.

    Reads expose denormalized panel / breaker / circuit context so the
    frontend can render a disconnect row without a follow-up tree walk.
    Writes accept a list of LOTODevice ids via the dedicated
    ``required_loto_device_ids`` field; the read shape carries the full
    LOTODevice payloads under ``required_loto_devices``.
    """

    location_name = serializers.CharField(source="location.name", read_only=True, allow_null=True)
    circuit_label = serializers.CharField(source="circuit.label", read_only=True)
    breaker_position = serializers.CharField(source="circuit.breaker.position", read_only=True)
    panel_name = serializers.CharField(source="circuit.breaker.panel.name", read_only=True)
    required_loto_devices = LOTODeviceSerializer(many=True, read_only=True)
    required_loto_device_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        write_only=True,
        queryset=LOTODevice.objects.all(),
        source="required_loto_devices",
        required=False,
    )

    class Meta:
        model = Disconnect
        fields = [
            "id",
            "circuit",
            "circuit_label",
            "panel_name",
            "breaker_position",
            "location",
            "location_name",
            "label",
            "disconnect_type",
            "amperage",
            "fuse_size",
            "is_lockable",
            "photo",
            "notes",
            "required_loto_devices",
            "required_loto_device_ids",
            "needs_review",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class NetworkDropSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = NetworkDrop
        fields = [
            "id",
            "location",
            "location_name",
            "identifier",
            "drop_type",
            "patch_panel",
            "patch_port",
            "mac_address",
            "ip_address",
            "description",
            "notes",
            "photo",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
