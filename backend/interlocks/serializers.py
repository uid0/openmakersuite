"""Serializers for the interlocks API.

The operator serializer is credential-safe: the SSH password is **write-only**
(accepted on create/update, never serialized out); clients read the boolean
``has_credentials`` instead.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import Interlock, InterlockCommand


class InterlockSerializer(serializers.ModelSerializer):
    """Operator CRUD serializer. SSH password in, never out."""

    ssh_password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        style={"input_type": "password"},
        help_text=(
            "SSH password. Write-only: accepted on create/update, stored "
            "encrypted, never returned. Send blank/omit to leave unchanged."
        ),
    )
    has_credentials = serializers.BooleanField(read_only=True)

    class Meta:
        model = Interlock
        fields = [
            "id",
            "label",
            "asset",
            "host",
            "ssh_port",
            "ssh_username",
            "ssh_password",
            "has_credentials",
            "auth_type",
            "service_name",
            "relay_pin",
            "relay_interface",
            "desired_state",
            "last_reported_state",
            "in_use",
            "online",
            "last_seen_at",
            "created_at",
            "updated_at",
        ]
        # ``desired_state`` is driven by the enable/disable actions (which also
        # enqueue a command); the reported-state/telemetry fields are owned by
        # the Pi report endpoint. Keep them out of operator writes.
        read_only_fields = [
            "desired_state",
            "last_reported_state",
            "in_use",
            "online",
            "last_seen_at",
            "created_at",
            "updated_at",
        ]

    def create(self, validated_data):
        password = validated_data.pop("ssh_password", None)
        instance = super().create(validated_data)
        if password:
            instance.set_ssh_password(password)
            instance.save(update_fields=["encrypted_ssh_password", "updated_at"])
        return instance

    def update(self, instance, validated_data):
        password = validated_data.pop("ssh_password", None)
        instance = super().update(instance, validated_data)
        if password:
            instance.set_ssh_password(password)
            instance.save(update_fields=["encrypted_ssh_password", "updated_at"])
        return instance


class InterlockCommandSerializer(serializers.ModelSerializer):
    """Read serializer for queued commands (audit / action responses)."""

    class Meta:
        model = InterlockCommand
        fields = [
            "id",
            "interlock",
            "action",
            "state",
            "requested_by",
            "created_at",
            "claimed_at",
            "completed_at",
            "result_text",
            "success",
        ]
        read_only_fields = fields


class InterlockCommandReportSerializer(serializers.Serializer):
    """Validates the Pi executor's result report body."""

    success = serializers.BooleanField()
    result_text = serializers.CharField(required=False, allow_blank=True, default="")
    state = serializers.ChoiceField(
        choices=Interlock.STATE_CHOICES, required=False, allow_null=True
    )
    in_use = serializers.BooleanField(required=False, allow_null=True)
    online = serializers.BooleanField(required=False)
