"""DRF serializers for electrical circuits and network drops."""

from django.contrib.contenttypes.models import ContentType

from rest_framework import serializers

from .models import (
    Breaker,
    Cable,
    LightSwitch,
    NetworkDrop,
    Outlet,
    PowerBreaker,
    PowerCircuit,
    PowerOutlet,
    PowerPanel,
    PowerPort,
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


class PowerPortSerializer(serializers.ModelSerializer):
    """Full read/write serializer for PowerPort."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)

    class Meta:
        model = PowerPort
        fields = [
            "id",
            "asset",
            "asset_name",
            "label",
            "port_type",
            "max_draw_amps",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class PowerCableSerializer(serializers.ModelSerializer):
    """Write a power Cable using simple ``outlet`` + ``port`` fields.

    Hides the generic-foreign-key plumbing from API consumers: callers POST
    ``{"outlet": <PowerOutlet id>, "port": <PowerPort id>, ...}`` and the
    serializer translates that into endpoint_a / endpoint_b on save. Reads
    expose the same flat shape plus the resolved names so the frontend can
    render a chain row without follow-up queries.
    """

    outlet = serializers.IntegerField(write_only=True)
    port = serializers.IntegerField(write_only=True)
    outlet_id = serializers.SerializerMethodField()
    outlet_label = serializers.SerializerMethodField()
    port_id = serializers.SerializerMethodField()
    port_label = serializers.SerializerMethodField()
    asset_id = serializers.SerializerMethodField()
    asset_name = serializers.SerializerMethodField()

    class Meta:
        model = Cable
        fields = [
            "id",
            "outlet",
            "port",
            "outlet_id",
            "outlet_label",
            "port_id",
            "port_label",
            "asset_id",
            "asset_name",
            "color",
            "length_ft",
            "label",
            "status",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def _outlet_obj(self, obj: Cable) -> PowerOutlet | None:
        for endpoint in (obj.endpoint_a, obj.endpoint_b):
            if isinstance(endpoint, PowerOutlet):
                return endpoint
        return None

    def _port_obj(self, obj: Cable) -> PowerPort | None:
        for endpoint in (obj.endpoint_a, obj.endpoint_b):
            if isinstance(endpoint, PowerPort):
                return endpoint
        return None

    def get_outlet_id(self, obj: Cable) -> int | None:
        o = self._outlet_obj(obj)
        return o.pk if o else None

    def get_outlet_label(self, obj: Cable) -> str | None:
        o = self._outlet_obj(obj)
        return str(o) if o else None

    def get_port_id(self, obj: Cable) -> int | None:
        p = self._port_obj(obj)
        return p.pk if p else None

    def get_port_label(self, obj: Cable) -> str | None:
        p = self._port_obj(obj)
        return p.label if p else None

    def get_asset_id(self, obj: Cable):
        p = self._port_obj(obj)
        return str(p.asset_id) if p else None

    def get_asset_name(self, obj: Cable) -> str | None:
        p = self._port_obj(obj)
        return p.asset.name if p else None

    def _resolve_endpoints(self, validated):
        outlet_pk = validated.pop("outlet")
        port_pk = validated.pop("port")
        try:
            outlet = PowerOutlet.objects.get(pk=outlet_pk)
        except PowerOutlet.DoesNotExist as exc:
            raise serializers.ValidationError({"outlet": "PowerOutlet not found."}) from exc
        try:
            port = PowerPort.objects.get(pk=port_pk)
        except PowerPort.DoesNotExist as exc:
            raise serializers.ValidationError({"port": "PowerPort not found."}) from exc
        outlet_ct = ContentType.objects.get_for_model(PowerOutlet)
        port_ct = ContentType.objects.get_for_model(PowerPort)
        validated.update(
            {
                "cable_type": Cable.CABLE_TYPE_POWER,
                "endpoint_a_content_type": outlet_ct,
                "endpoint_a_object_id": str(outlet.pk),
                "endpoint_b_content_type": port_ct,
                "endpoint_b_object_id": str(port.pk),
            }
        )
        return validated

    def create(self, validated_data):
        validated_data = self._resolve_endpoints(validated_data)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "outlet" in validated_data or "port" in validated_data:
            validated_data.setdefault("outlet", self.get_outlet_id(instance))
            validated_data.setdefault("port", self.get_port_id(instance))
            validated_data = self._resolve_endpoints(validated_data)
        return super().update(instance, validated_data)
