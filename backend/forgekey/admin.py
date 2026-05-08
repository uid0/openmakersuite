"""
Admin interface for ForgeKey models.
"""

import logging

from django import forms
from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    AssetAuthorization,
    AssetDevice,
    DeviceCommand,
    DeviceFirmwareUpdate,
    DeviceLockout,
    DeviceType,
    DeviceUsage,
    ESP32Device,
    ESP32DevicePhoto,
    FirmwareSigningKey,
    FirmwareVersion,
    OperationalMode,
    PowerMeterReading,
)
from .services.firmware_signing import (
    FirmwareSigningError,
    derive_public_pem,
    generate_signing_keypair,
)

audit_logger = logging.getLogger("forgekey.audit")


@admin.register(DeviceType)
class DeviceTypeAdmin(admin.ModelAdmin):
    """Admin interface for device types."""

    list_display = ["name", "code", "is_active"]
    list_filter = ["is_active", "code"]
    search_fields = ["name", "code", "description"]


@admin.register(ESP32Device)
class ESP32DeviceAdmin(admin.ModelAdmin):
    """Admin interface for ESP32 devices."""

    list_display = [
        "mac_address",
        "name",
        "device_type",
        "location",
        "firmware_version",
        "is_online",
        "is_active",
        "last_seen",
        "last_photo_thumb",
    ]
    list_filter = ["device_type", "is_online", "is_active", "location"]
    search_fields = ["mac_address", "name", "description"]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "enrollment_photo_preview",
        "last_photo_preview",
    ]
    raw_id_fields = ["device_type", "location"]
    fields = (
        "id",
        "mac_address",
        "name",
        "device_type",
        "description",
        "firmware_version",
        "location",
        "is_online",
        "is_active",
        "last_seen",
        "boot_count",
        "free_heap",
        "ip",
        "enrollment_photo",
        "enrollment_photo_preview",
        "last_photo",
        "last_photo_preview",
        "created_at",
        "updated_at",
    )

    @admin.display(description="Last photo")
    def last_photo_thumb(self, obj):
        if obj.last_photo:
            return format_html('<img src="{}" style="max-height: 48px;"/>', obj.last_photo.url)
        return "—"

    @admin.display(description="Enrollment photo")
    def enrollment_photo_preview(self, obj):
        if obj.enrollment_photo:
            return format_html(
                '<img src="{}" style="max-height: 320px;"/>',
                obj.enrollment_photo.url,
            )
        return "—"

    @admin.display(description="Latest periodic photo")
    def last_photo_preview(self, obj):
        if obj.last_photo:
            return format_html('<img src="{}" style="max-height: 320px;"/>', obj.last_photo.url)
        return "—"


@admin.register(ESP32DevicePhoto)
class ESP32DevicePhotoAdmin(admin.ModelAdmin):
    """Read-only gallery of periodic surveillance photos."""

    list_display = ["device", "received_at", "captured_at", "thumbnail"]
    list_filter = ["device__device_type", "received_at"]
    search_fields = ["device__mac_address", "device__name"]
    readonly_fields = ["id", "device", "image", "captured_at", "received_at", "preview"]
    fields = ("id", "device", "image", "preview", "captured_at", "received_at")
    raw_id_fields: list = []

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    @admin.display(description="Thumbnail")
    def thumbnail(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="max-height: 48px;"/>', obj.image.url)
        return "—"

    @admin.display(description="Preview")
    def preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="max-height: 480px;"/>', obj.image.url)
        return "—"


@admin.register(AssetDevice)
class AssetDeviceAdmin(admin.ModelAdmin):
    """Admin interface for asset-device relationships."""

    list_display = ["asset", "device", "role", "is_primary", "power_off_delay_seconds"]
    list_filter = ["is_primary", "role"]
    search_fields = ["asset__name", "device__mac_address", "device__name"]
    raw_id_fields = ["asset", "device"]


@admin.register(OperationalMode)
class OperationalModeAdmin(admin.ModelAdmin):
    """Admin interface for operational modes."""

    list_display = [
        "asset",
        "mode",
        "classroom_mode_enabled",
        "classroom_mode_enabled_by",
        "updated_at",
    ]
    list_filter = ["mode", "classroom_mode_enabled"]
    search_fields = ["asset__name"]
    raw_id_fields = ["asset", "classroom_mode_enabled_by"]


@admin.register(AssetAuthorization)
class AssetAuthorizationAdmin(admin.ModelAdmin):
    """Admin interface for asset authorizations."""

    list_display = ["asset", "user", "authorized_by", "authorized_at", "is_active"]
    list_filter = ["is_active", "authorized_at"]
    search_fields = ["asset__name", "user__username", "user__email"]
    raw_id_fields = ["asset", "user", "authorized_by"]


@admin.register(DeviceLockout)
class DeviceLockoutAdmin(admin.ModelAdmin):
    """Admin interface for device lockouts."""

    list_display = [
        "asset",
        "locked_by",
        "lockout_level",
        "locked_at",
        "is_active",
        "unlocked_at",
        "unlocked_by",
    ]
    list_filter = ["lockout_level", "is_active", "locked_at"]
    search_fields = ["asset__name", "locked_by__username", "reason"]
    readonly_fields = ["id", "locked_at"]
    raw_id_fields = ["asset", "locked_by", "unlocked_by"]


@admin.register(DeviceUsage)
class DeviceUsageAdmin(admin.ModelAdmin):
    """Admin interface for device usage sessions."""

    list_display = [
        "asset",
        "user",
        "started_at",
        "ended_at",
        "duration_seconds",
        "power_consumption_kwh",
    ]
    list_filter = ["started_at", "ended_at"]
    search_fields = ["asset__name", "user__username"]
    readonly_fields = ["id", "started_at"]
    raw_id_fields = ["asset", "user"]


@admin.register(PowerMeterReading)
class PowerMeterReadingAdmin(admin.ModelAdmin):
    """Admin interface for power meter readings."""

    list_display = [
        "asset",
        "device",
        "timestamp",
        "voltage",
        "current",
        "power",
        "energy",
    ]
    list_filter = ["timestamp"]
    search_fields = ["asset__name", "device__mac_address"]
    readonly_fields = ["id", "timestamp"]
    raw_id_fields = ["asset", "device", "usage_session"]


@admin.register(FirmwareVersion)
class FirmwareVersionAdmin(admin.ModelAdmin):
    """Admin interface for firmware versions."""

    list_display = [
        "version",
        "device_type",
        "mandatory",
        "sha256_short",
        "is_active",
        "created_at",
        "created_by",
    ]
    list_filter = ["device_type", "mandatory", "is_active", "created_at"]
    search_fields = ["version", "device_type__name", "release_notes", "sha256"]
    readonly_fields = ["id", "created_at", "sha256"]
    raw_id_fields = ["device_type", "created_by"]
    actions = ["dispatch_firmware_update", "deploy_ota_to_fleet"]

    def save_model(self, request, obj, form, change):
        # Mirror the legacy ``created_by`` field into the AC-1 "uploaded_by"
        # contract: whoever clicks Save in the admin owns the upload.
        if not change and obj.created_by_id is None and request.user.is_authenticated:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="SHA-256 (short)")
    def sha256_short(self, obj):
        return obj.sha256[:12] if obj.sha256 else "—"

    @admin.action(description="Dispatch firmware update via MQTT (retained advert)")
    def dispatch_firmware_update(self, request, queryset):
        from .services.firmware_dispatch import dispatch_to_device_type

        total = 0
        for firmware in queryset:
            records = dispatch_to_device_type(firmware, requested_by=request.user)
            total += len(records)
        self.message_user(
            request,
            f"Dispatched {queryset.count()} firmware version(s) to {total} device(s).",
            level=messages.SUCCESS,
        )

    @admin.action(description="Deploy OTA to fleet (one-shot trigger per matching device)")
    def deploy_ota_to_fleet(self, request, queryset):
        """Queue a ``trigger_ota`` celery task per active device whose
        ``device_type`` matches the firmware. Each device receives a one-shot
        signed-URL trigger on ``forgekey/<mac>/ota/trigger`` and is expected
        to report back on ``forgekey/<mac>/ota/status``."""
        from .models import ESP32Device
        from .tasks import trigger_ota

        firmware_count = 0
        device_count = 0
        for firmware in queryset:
            firmware_count += 1
            device_ids = list(
                ESP32Device.objects.filter(
                    device_type=firmware.device_type, is_active=True
                ).values_list("id", flat=True)
            )
            for device_id in device_ids:
                trigger_ota.delay(str(device_id), str(firmware.id), request.user.id)
                device_count += 1
        self.message_user(
            request,
            f"Queued OTA triggers for {device_count} device(s) across "
            f"{firmware_count} firmware version(s).",
            level=messages.SUCCESS,
        )


@admin.register(DeviceFirmwareUpdate)
class DeviceFirmwareUpdateAdmin(admin.ModelAdmin):
    """Admin interface for firmware updates."""

    list_display = [
        "device",
        "firmware_version",
        "status",
        "requested_at",
        "requested_by",
        "completed_at",
    ]
    list_filter = ["status", "requested_at"]
    search_fields = ["device__mac_address", "device__name", "firmware_version__version"]
    readonly_fields = ["id", "requested_at"]
    raw_id_fields = ["device", "firmware_version", "requested_by"]


class FirmwareSigningKeyForm(forms.ModelForm):
    """Form for uploading or generating a new firmware signing keypair.

    Two modes are supported, mutually exclusive:
      * ``private_key_pem`` filled in — operator pasted an externally
        generated P-256 PKCS#8 PEM. The form derives the matching public
        key and encrypts the private PEM with the SECRET_KEY-derived KEK.
      * ``generate_new`` checked — server generates a fresh P-256 keypair
        on save (private PEM never crosses the form).
    """

    private_key_pem = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 8, "cols": 80, "style": "font-family: monospace;"}),
        required=False,
        help_text=(
            "Paste a PKCS#8 PEM-encoded ECDSA(P-256) private key. "
            "Leave blank and tick 'Generate new keypair' to have the server create one."
        ),
    )
    generate_new = forms.BooleanField(
        required=False,
        help_text=(
            "Generate a fresh P-256 keypair on the server. "
            "Tick this OR paste a private key — not both."
        ),
    )

    class Meta:
        model = FirmwareSigningKey
        fields = ["label", "description", "is_active"]

    def clean(self):
        cleaned = super().clean()
        private_pem = (cleaned.get("private_key_pem") or "").strip()
        generate_new = bool(cleaned.get("generate_new"))

        if self.instance.pk:
            # Editing an existing row — uploads aren't allowed via this form;
            # operators rotate by creating a new row instead.
            return cleaned

        if private_pem and generate_new:
            raise forms.ValidationError(
                "Provide a private key OR tick 'Generate new keypair', not both."
            )
        if not private_pem and not generate_new:
            raise forms.ValidationError(
                "Either paste a private key PEM or tick 'Generate new keypair'."
            )

        if private_pem:
            try:
                public_pem = derive_public_pem(private_pem)
            except FirmwareSigningError as exc:
                raise forms.ValidationError(f"Invalid signing key: {exc}") from exc
            cleaned["_resolved_private_pem"] = private_pem
            cleaned["_resolved_public_pem"] = public_pem
        else:
            generated_private, generated_public = generate_signing_keypair()
            cleaned["_resolved_private_pem"] = generated_private
            cleaned["_resolved_public_pem"] = generated_public
        return cleaned


@admin.register(FirmwareSigningKey)
class FirmwareSigningKeyAdmin(admin.ModelAdmin):
    """Admin for ECDSA(P-256) firmware signing keypairs.

    Saving a row that is_active=True deactivates every other active row in
    the same transaction; the prior key gets rotated_at / rotated_by set
    so operators can audit when a key was retired and by whom.
    """

    form = FirmwareSigningKeyForm
    list_display = [
        "label",
        "is_active",
        "created_at",
        "created_by",
        "rotated_at",
        "rotated_by",
    ]
    list_filter = ["is_active", "created_at"]
    search_fields = ["label", "description"]
    readonly_fields = [
        "id",
        "public_key_pem",
        "created_at",
        "created_by",
        "rotated_at",
        "rotated_by",
    ]
    fields = (
        "id",
        "label",
        "description",
        "is_active",
        "private_key_pem",
        "generate_new",
        "public_key_pem",
        "created_at",
        "created_by",
        "rotated_at",
        "rotated_by",
    )

    def has_change_permission(self, request, obj=None):
        # Existing rows are intentionally read-mostly; rotation happens by
        # creating a new row. Allow toggling is_active (e.g., emergency
        # disable) but not editing the keypair itself.
        return super().has_change_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            # Once written, the encrypted private PEM is immutable — operators
            # rotate by adding a new row.
            ro.extend(["label", "private_key_pem", "generate_new"])
        return ro

    def save_model(self, request, obj, form, change):
        cleaned = form.cleaned_data
        try:
            private_pem = cleaned.get("_resolved_private_pem")
            public_pem = cleaned.get("_resolved_public_pem")
            if not private_pem or not public_pem:
                raise forms.ValidationError("Internal error: signing key data missing.")
            obj.public_key_pem = public_pem
            # Attempt to encrypt the private PEM using the system's secret key derivation
            obj.private_key_pem_encrypted = FirmwareSigningKey.encrypt_private_pem(private_pem)
        except Exception as e:
            # Catch any exception during encryption or assignment (e.g., missing dependencies, bad KEK)
            audit_logger.error(
                "Failed to process/save signing key data for label %s: %s", obj.label, e
            )
            raise forms.ValidationError(
                f"Could not save the keypair due to a critical processing error: {type(e).__name__}. Check server logs."
            ) from e

        with transaction.atomic():
            super().save_model(request, obj, form, change)
            if obj.is_active:
                # Deactivate every other active row.
                others = FirmwareSigningKey.objects.filter(is_active=True).exclude(pk=obj.pk)
                affected = list(others.values_list("pk", "label"))
                others.update(
                    is_active=False,
                    rotated_at=timezone.now(),
                    rotated_by=request.user if request.user.is_authenticated else None,
                )
                for pk, label in affected:
                    audit_logger.info(
                        "forgekey.firmware_signing_key.rotated retired_id=%s "
                        "retired_label=%s replacement_id=%s replacement_label=%s "
                        "actor_id=%s actor_username=%s",
                        pk,
                        label,
                        obj.pk,
                        obj.label,
                        getattr(request.user, "id", None),
                        getattr(request.user, "username", None),
                    )
                audit_logger.info(
                    "forgekey.firmware_signing_key.activated id=%s label=%s "
                    "actor_id=%s actor_username=%s",
                    obj.pk,
                    obj.label,
                    getattr(request.user, "id", None),
                    getattr(request.user, "username", None),
                )

    def has_delete_permission(self, request, obj=None):
        # Block deletion entirely — keypairs must be retired (is_active=False),
        # not removed, so the audit history stays intact.
        return False


@admin.register(DeviceCommand)
class DeviceCommandAdmin(admin.ModelAdmin):
    """Read-only audit view of commands dispatched to devices."""

    list_display = ["sent_at", "device", "command", "ack_status", "sent_by"]
    list_filter = ["command", "ack_status"]
    search_fields = ["device__mac_address", "command", "sent_by__username"]
    readonly_fields = [
        "id",
        "device",
        "command",
        "payload",
        "sent_by",
        "sent_at",
        "ack_status",
        "ack_at",
        "ack_payload",
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # DeviceCommand is read-only audit history at the row level — staff
        # admins should not scrub individual command rows. But the cascade
        # path that fires when a superuser deletes an ESP32Device asks
        # ``has_delete_permission`` here for each related command before it
        # will let the parent delete proceed; returning False unconditionally
        # blocks ESP32Device deletion entirely (gh forge-1). Allow superusers
        # so the cascade can run; everyone else still gets the audit view.
        return request.user.is_superuser
