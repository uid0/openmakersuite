"""DRF serializers for electrical circuits and network drops."""

from rest_framework import serializers

from .models import (
    Breaker,
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
            "manufacturer",
            "model",
            "install_date",
            "notes",
            "needs_review",
            "breaker_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "breaker_count", "created_at", "updated_at"]

    def get_breaker_count(self, obj) -> int:
        # Use a cached annotation when the view supplied one (avoids N+1
        # on the list endpoint); otherwise fall back to a fresh count.
        cached = getattr(obj, "annotated_breaker_count", None)
        if cached is not None:
            return cached
        return obj.breakers.count()


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

    class Meta:
        model = PowerOutlet
        fields = [
            "id",
            "circuit",
            "circuit_label",
            "location",
            "location_name",
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
