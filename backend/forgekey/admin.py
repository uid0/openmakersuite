"""
Admin interface for ForgeKey models.
"""

import logging

from django import forms
from django.contrib import admin, messages
from django.db import transaction
from django.http import Http404, HttpResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from cryptography.hazmat.primitives import serialization

from .models import (
    AssetAuthorization,
    AssetDevice,
    CertificateAuthority,
    DeviceCertificate,
    DeviceCommand,
    DeviceEnrollment,
    DeviceFirmwareUpdate,
    DeviceIdentity,
    DeviceLockout,
    DeviceType,
    DeviceUsage,
    EPaperDisplay,
    ESP32Device,
    ESP32DevicePhoto,
    FirmwareBuild,
    FirmwareSigningKey,
    FirmwareVersion,
    OperationalMode,
    PowerMeterReading,
)
from .services.ca_key_storage import CaKeyStorageError, encrypt_ca_key
from .services.csr_signing import (
    CsrSigningError,
    CsrValidationError,
    generate_ca_keypair,
    sign_csr,
    sign_firmware_signing_csr,
    validate_csr,
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


@admin.register(FirmwareBuild)
class FirmwareBuildAdmin(admin.ModelAdmin):
    """Track + recover self-hosted firmware-builder jobs.

    The build worker (Dockerfile.firmware-builder) consumes the `builds`
    Celery queue. When the worker is down (or Redis is restarted)
    queued rows pile up with no in-flight message to claim them; the
    `Re-dispatch to worker` action re-publishes the Celery task for the
    selected rows so the worker can pick them up once it's back. Same
    DB row, same parameters — audit chain intact.
    """

    list_display = [
        "id_short",
        "device_type",
        "pio_env",
        "version",
        "source_ref",
        "status",
        "requested_at",
        "requested_by",
    ]
    list_filter = ["status", "device_type", "pio_env"]
    search_fields = ["version", "source_ref", "pio_env", "commit_sha"]
    readonly_fields = [
        "id",
        "ca_fingerprint",
        "commit_sha",
        "log",
        "error_message",
        "firmware_version",
        "requested_at",
        "started_at",
        "completed_at",
    ]
    raw_id_fields = ["device_type", "requested_by", "firmware_version"]
    actions = ["redispatch_selected"]

    @admin.display(description="id", ordering="id")
    def id_short(self, obj):
        return str(obj.id)[:8]

    @admin.action(description="Re-dispatch selected queued / failed builds to the worker")
    def redispatch_selected(self, request, queryset):
        from .models import FirmwareBuild as _FB
        from .tasks import build_firmware

        eligible = queryset.filter(status__in=[_FB.STATUS_QUEUED, _FB.STATUS_FAILED])
        skipped = queryset.exclude(pk__in=eligible.values_list("pk", flat=True))

        sent = 0
        for build in eligible:
            build.status = _FB.STATUS_QUEUED
            build.started_at = None
            build.completed_at = None
            build.error_message = ""
            build.log = ""
            build.save(
                update_fields=[
                    "status",
                    "started_at",
                    "completed_at",
                    "error_message",
                    "log",
                ]
            )
            build_firmware.delay(str(build.pk))
            sent += 1
            audit_logger.info(
                "firmware_build.redispatched id=%s actor=%s",
                build.pk,
                getattr(request.user, "username", "<anonymous>"),
            )

        if sent:
            self.message_user(
                request, f"Re-dispatched {sent} build(s) to the worker.", messages.SUCCESS
            )
        if skipped.exists():
            self.message_user(
                request,
                f"Skipped {skipped.count()} build(s) that were not queued/failed.",
                messages.WARNING,
            )


class FirmwareSigningKeyForm(forms.ModelForm):
    """Form for uploading or generating a new firmware signing keypair.

    Two modes are supported, mutually exclusive:
      * ``private_key_pem`` filled in — operator pasted an externally
        generated P-256 PKCS#8 PEM. The form derives the matching public
        key and encrypts the private PEM with the SECRET_KEY-derived KEK.
      * ``generate_new`` checked — server generates a fresh P-256 keypair
        on save (private PEM never crosses the form).

    For either mode, ``sign_with_ca`` (default on when the active CA + KEK
    are configured) issues a CODE_SIGNING leaf cert over the keypair's
    public key, signed by the internal CA. The leaf PEM is stored on the
    row and shipped with firmware-dispatch payloads so devices can verify
    binaries against the CA chain instead of a single burned-in pubkey.
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
    sign_with_ca = forms.BooleanField(
        required=False,
        initial=True,
        label="Sign with internal CA",
        help_text=(
            "Issue a CA-signed leaf cert over this keypair. Recommended — "
            "lets devices verify firmware signatures against the CA chain "
            "they already trust from /enroll/, so rotating this key doesn't "
            "require re-flashing the embedded public key. Requires an active "
            "CA and FORGEKEY_CA_KEY_ENCRYPTION_KEY set."
        ),
    )

    class Meta:
        model = FirmwareSigningKey
        fields = ["label", "description", "is_active"]

    def clean(self):
        cleaned = super().clean()
        private_pem = (cleaned.get("private_key_pem") or "").strip()
        generate_new = bool(cleaned.get("generate_new"))
        sign_with_ca = bool(cleaned.get("sign_with_ca"))

        # `self.instance.pk` is set by the UUID default on construction; the
        # reliable check for "is this a fresh row" is `_state.adding`.
        if not self.instance._state.adding:
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

        if sign_with_ca:
            label = (cleaned.get("label") or "").strip()
            if not label:
                raise forms.ValidationError(
                    "Sign with internal CA requires a non-empty label "
                    "(used in the leaf certificate's subject + SAN URI)."
                )
            active_ca = CertificateAuthority.get_active()
            if active_ca is None:
                raise forms.ValidationError(
                    "Sign with internal CA is checked but no active CA "
                    "exists. Bootstrap one in /admin/forgekey/"
                    "certificateauthority/add/ first."
                )
            public_key = serialization.load_pem_public_key(
                cleaned["_resolved_public_pem"].encode("ascii")
            )
            try:
                signed = sign_firmware_signing_csr(public_key, label=label)
            except CsrSigningError as exc:
                # KEK unset, decrypt failure, etc. — keep this in form-error
                # land so it doesn't propagate as a 500 / Sentry capture.
                raise forms.ValidationError(f"Cannot CA-sign keypair: {exc}") from exc
            cleaned["_resolved_cert_pem"] = signed.cert_pem
            cleaned["_resolved_ca"] = active_ca
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
        "cert_pem",
        "signed_by_ca",
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
        "sign_with_ca",
        "public_key_pem",
        "cert_pem",
        "signed_by_ca",
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
            ro.extend(["label", "private_key_pem", "generate_new", "sign_with_ca"])
        return ro

    def save_model(self, request, obj, form, change):
        cleaned = form.cleaned_data
        try:
            private_pem = cleaned.get("_resolved_private_pem")
            public_pem = cleaned.get("_resolved_public_pem")
            if not private_pem or not public_pem:
                raise forms.ValidationError("Internal error: signing key data missing.")
            obj.public_key_pem = public_pem
            obj.cert_pem = cleaned.get("_resolved_cert_pem") or ""
            obj.signed_by_ca = cleaned.get("_resolved_ca")
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


# ---------------------------------------------------------------------------
# Device-identity trust foundation (oms-d2axqu / forgekey-trust-refactor)
# ---------------------------------------------------------------------------


@admin.register(DeviceIdentity)
class DeviceIdentityAdmin(admin.ModelAdmin):
    """Per-chip security anchor populated by /enroll/."""

    list_display = ["device_id", "status", "created_at", "updated_at"]
    list_filter = ["status"]
    search_fields = ["device_id", "notes"]
    readonly_fields = ["id", "created_at", "updated_at"]
    fields = ["id", "device_id", "status", "notes", "created_at", "updated_at"]


class DeviceCertificateForm(forms.ModelForm):
    """Add-form for a device certificate: paste a CSR, server signs it.

    The full enrollment flow normally runs over `/api/forgekey/devices/enroll/`
    — the device generates its own keypair, POSTs the CSR with the
    provisioning token, gets a signed cert in the response. This admin form
    is the manual escape-hatch for the cases that flow doesn't cover:
    factory pre-provisioning, devices on flaky networks, support-driven
    re-cert, etc. The operator obtains a CSR from the device out-of-band
    and pastes it here.

    Form-only fields:
      * ``csr_pem`` — PEM-encoded CSR (parsed + validated on clean()).
      * ``validity_days`` — optional cert lifetime (defaults to the
        ``FORGEKEY_CLIENT_CERT_VALIDITY_DAYS`` setting).
    """

    csr_pem = forms.CharField(
        label="CSR (PEM)",
        widget=forms.Textarea(attrs={"rows": 10, "cols": 80, "style": "font-family: monospace;"}),
        help_text=(
            "Paste the device's PEM-encoded certificate signing request. "
            "Must be EC P-256 with SHA-256, matching the /enroll/ contract."
        ),
    )
    validity_days = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=825,  # CA/Browser Forum cap; keeps us off the rails.
        help_text=(
            "Days before the cert expires. Leave blank to use the server "
            "default (FORGEKEY_CLIENT_CERT_VALIDITY_DAYS)."
        ),
    )

    class Meta:
        model = DeviceCertificate
        fields = ["device"]

    def clean(self):
        cleaned = super().clean()
        if not self.instance._state.adding:
            # Edit path — only `revoked_at` is editable; nothing to validate.
            return cleaned

        csr_pem = (cleaned.get("csr_pem") or "").strip()
        if csr_pem:
            try:
                validate_csr(csr_pem)
            except CsrValidationError as exc:
                raise forms.ValidationError(f"Invalid CSR: {exc}") from exc

        device = cleaned.get("device")
        if device is not None and device.status == DeviceIdentity.STATUS_DECOMMISSIONED:
            raise forms.ValidationError(
                f"Device {device.device_id!r} is decommissioned; cannot issue."
            )
        return cleaned


@admin.register(DeviceCertificate)
class DeviceCertificateAdmin(admin.ModelAdmin):
    """mTLS client certificates issued by the internal CA.

    Add: paste a CSR for a known DeviceIdentity, server signs it via the
    same `sign_csr` path the /enroll/ endpoint uses, prior active cert for
    the same device is revoked atomically, the resulting PEM is returned to
    the browser as a `cert-<serial>.pem` download (the server doesn't keep
    the PEM long-term — mirrors the /enroll/ HTTP-only delivery model).
    Change: revoke only.
    """

    form = DeviceCertificateForm
    list_display = [
        "serial",
        "device",
        "fingerprint_sha256_short",
        "not_after",
        "revoked_at",
        "status_label",
    ]
    list_filter = ["revoked_at", "issued_by"]
    search_fields = ["serial", "fingerprint_sha256", "device__device_id", "subject"]
    readonly_fields = [
        "id",
        "serial",
        "subject",
        "fingerprint_sha256",
        "not_before",
        "not_after",
        "issued_by",
        "created_at",
    ]

    def has_add_permission(self, request):
        # Signing a device cert mints something the device authenticates
        # with on every mTLS handshake; superuser-only on purpose.
        return request.user.is_active and request.user.is_superuser

    def get_fields(self, request, obj=None):
        if obj is None:
            return ["device", "csr_pem", "validity_days"]
        return [
            "id",
            "device",
            "serial",
            "subject",
            "fingerprint_sha256",
            "not_before",
            "not_after",
            "issued_by",
            "created_at",
            "revoked_at",
        ]

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        # `device` is editable only at signing time — never change which
        # device a cert is bound to.
        return ["device"] + list(self.readonly_fields)

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return

        device = form.cleaned_data["device"]
        csr_pem = form.cleaned_data["csr_pem"].strip()
        validity_days = form.cleaned_data.get("validity_days")

        try:
            signed = sign_csr(
                csr_pem,
                device_id=device.device_id,
                validity_days=validity_days,
            )
        except CsrSigningError as exc:
            # Most common: no active CA configured. Surface clearly.
            messages.error(request, f"CA unavailable: {exc}")
            raise
        except CsrValidationError as exc:
            # clean() should have caught this, but signers re-validate.
            messages.error(request, f"Invalid CSR: {exc}")
            raise

        now = timezone.now()
        active_ca = CertificateAuthority.get_active()
        ca_name = active_ca.name if active_ca else ""

        with transaction.atomic():
            revoked_count = DeviceCertificate.objects.filter(
                device=device, revoked_at__isnull=True
            ).update(revoked_at=now)

            obj.device = device
            obj.serial = signed.serial
            obj.subject = signed.subject
            obj.fingerprint_sha256 = signed.fingerprint_sha256
            obj.not_before = signed.not_before
            obj.not_after = signed.not_after
            obj.issued_by = ca_name
            obj.save()

            audit_logger.info(
                "forgekey.device_certificate.issued_via_admin id=%s device_id=%s "
                "serial=%s revoked_prior=%d actor_id=%s actor_username=%s",
                obj.pk,
                device.device_id,
                obj.serial,
                revoked_count,
                getattr(request.user, "id", None),
                getattr(request.user, "username", None),
            )

        if revoked_count:
            messages.warning(
                request,
                f"Revoked {revoked_count} prior active cert(s) for {device.device_id}.",
            )
        # Stash the PEM for response_add to serve as a download. We don't
        # persist the PEM — the /enroll/ contract is also delivery-only,
        # and storing it would mean an extra ~1.5 KB per cert with no
        # current consumer.
        request._signed_cert_pem = signed.cert_pem
        request._signed_cert_serial = signed.serial

    def response_add(self, request, obj, post_url_continue=None):
        pem = getattr(request, "_signed_cert_pem", None)
        serial = getattr(request, "_signed_cert_serial", None)
        if pem and serial:
            response = HttpResponse(pem, content_type="application/x-pem-file")
            response["Content-Disposition"] = f'attachment; filename="cert-{serial}.pem"'
            return response
        return super().response_add(request, obj, post_url_continue)

    @admin.display(description="fingerprint")
    def fingerprint_sha256_short(self, obj):
        return (obj.fingerprint_sha256 or "")[:16]

    @admin.display(description="status")
    def status_label(self, obj):
        return obj.status


@admin.register(DeviceEnrollment)
class DeviceEnrollmentAdmin(admin.ModelAdmin):
    """CSR / bootstrap sessions submitted to /enroll/."""

    list_display = [
        "unique_chip_id",
        "mac_address",
        "sensor_kind",
        "status",
        "requested_at",
        "approved_at",
    ]
    list_filter = ["status", "sensor_kind"]
    search_fields = ["unique_chip_id", "mac_address", "device__device_id"]
    readonly_fields = [
        "id",
        "device",
        "csr_pem",
        "nonce",
        "unique_chip_id",
        "mac_address",
        "sensor_kind",
        "firmware_version",
        "chip_info",
        "boot_count",
        "free_heap",
        "ip_address",
        "flash_memory_id",
        "token_fingerprint",
        "requested_at",
        "approved_at",
        "approved_by",
        "certificate",
        "expires_at",
        "enrollment_photo",
    ]
    fields = readonly_fields + ["status"]

    def has_add_permission(self, request):
        # Enrollments come from devices POSTing CSRs to /enroll/, never from
        # the admin. Same read-only-form NOT-NULL trap as DeviceCertificate.
        return False


class CertificateAuthorityForm(forms.ModelForm):
    """Add-form for a CA: collects metadata, mints the keypair on save.

    Only ``name`` is a real model field on this form. ``cn`` and
    ``validity_years`` parameterize ``generate_ca_keypair`` at save time;
    ``force_replace`` gates rotation when an active CA already exists.
    The private key is generated server-side and never enters the form.
    """

    cn = forms.CharField(
        label="Common name",
        max_length=200,
        initial="ForgeKey Internal Root CA",
        help_text="CN baked into the CA certificate's subject. Cosmetic; "
        "devices identify the CA by full subject + fingerprint.",
    )
    validity_years = forms.IntegerField(
        min_value=1,
        max_value=20,
        initial=10,
        help_text="Years until the CA expires. Rotate before expiry — every "
        "device certificate it signed stops verifying when it lapses.",
    )
    force_replace = forms.BooleanField(
        required=False,
        label="Replace the active CA",
        help_text="Required when an active CA already exists. The prior CA is "
        "deactivated atomically; device certs it issued only continue to "
        "verify as long as relying parties still trust the old CA cert.",
    )

    class Meta:
        model = CertificateAuthority
        fields = ["name"]

    def clean(self):
        cleaned = super().clean()
        # `self.instance.pk` is unreliable here — the model's UUID pk default
        # fires on construction so a fresh instance still has a non-None pk.
        # `_state.adding` flips to False once the row hits the DB.
        if not self.instance._state.adding:
            return cleaned
        active = CertificateAuthority.get_active()
        if active is not None and not cleaned.get("force_replace"):
            raise forms.ValidationError(
                f"An active CA already exists (name={active.name!r}). Tick "
                "'Replace the active CA' to deactivate it and bootstrap a "
                "replacement."
            )
        return cleaned


@admin.register(CertificateAuthority)
class CertificateAuthorityAdmin(admin.ModelAdmin):
    """Internal CA admin. Add generates a fresh CA server-side; change view is read-only.

    The CLI command ``manage.py forgekey_ca init`` remains for scripted /
    headless bootstrap; this admin is the operator-facing equivalent.
    """

    form = CertificateAuthorityForm
    list_display = ["name", "is_active", "not_before", "not_after", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "key_kid"]
    readonly_fields = [
        "id",
        "cert_pem",
        "key_kid",
        "not_before",
        "not_after",
        "is_active",
        "created_at",
        "updated_at",
    ]
    # The encrypted private-key blob never appears in the admin.

    def has_add_permission(self, request):
        # Generating a CA mints the root private key for every device cert in
        # the system — superuser-only on purpose.
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def get_fields(self, request, obj=None):
        if obj is None:
            # Add form: name + generation parameters.
            return ["name", "cn", "validity_years", "force_replace"]
        # Change form: existing readonly view (plus the name field).
        return ["id", "name"] + self.readonly_fields[1:]

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return ["name"] + list(self.readonly_fields)

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return

        cn = form.cleaned_data["cn"]
        validity_years = form.cleaned_data["validity_years"]
        try:
            private_pem, ca_cert = generate_ca_keypair(cn=cn, validity_days=validity_years * 365)
            ciphertext, kid = encrypt_ca_key(private_pem)
        except CaKeyStorageError as exc:
            # Most common cause: FORGEKEY_CA_KEY_ENCRYPTION_KEY unset. Surface
            # the configuration problem to the operator instead of 500ing.
            messages.error(request, f"Cannot encrypt CA private key: {exc}")
            raise

        with transaction.atomic():
            prior = CertificateAuthority.get_active()
            if prior is not None:
                CertificateAuthority.objects.filter(pk=prior.pk).update(is_active=False)
                audit_logger.info(
                    "forgekey.certificate_authority.rotated retired_id=%s "
                    "retired_name=%s actor_id=%s actor_username=%s",
                    prior.pk,
                    prior.name,
                    getattr(request.user, "id", None),
                    getattr(request.user, "username", None),
                )
            obj.cert_pem = ca_cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
            obj.encrypted_private_key = ciphertext
            obj.key_kid = kid
            obj.not_before = ca_cert.not_valid_before_utc
            obj.not_after = ca_cert.not_valid_after_utc
            obj.is_active = True
            obj.save()
            audit_logger.info(
                "forgekey.certificate_authority.created id=%s name=%s "
                "validity_years=%d actor_id=%s actor_username=%s",
                obj.pk,
                obj.name,
                validity_years,
                getattr(request.user, "id", None),
                getattr(request.user, "username", None),
            )

        if prior is not None:
            messages.warning(request, f"Deactivated prior active CA: {prior.name!r}")
        messages.success(
            request,
            f"Generated CA {obj.name!r} (valid until {obj.not_after.isoformat()}).",
        )


@admin.register(EPaperDisplay)
class EPaperDisplayAdmin(admin.ModelAdmin):
    """Operate-and-bind view for XIAO 7.5" ePaper PM panels."""

    list_display = [
        "device",
        "asset",
        "battery_percent",
        "last_battery_at",
        "firmware_version",
        "last_image_at",
        "is_active",
        "preview_link",
    ]
    list_filter = ["is_active"]
    search_fields = [
        "device__mac_address",
        "device__name",
        "asset__name",
        "asset__asset_tag",
    ]
    readonly_fields = [
        "id",
        "panel_preview",
        "battery_percent",
        "last_battery_at",
        "firmware_version",
        "last_image_etag",
        "last_image_at",
        "created_at",
        "updated_at",
    ]

    # --- Live panel preview -------------------------------------------------
    # Renders the exact PNG a panel would flash, so the layout can be
    # iterated on from the admin without a device in hand.

    def get_urls(self):
        custom = [
            path(
                "<uuid:pk>/preview.png",
                self.admin_site.admin_view(self.preview_png),
                name="forgekey_epaperdisplay_preview",
            ),
        ]
        return custom + super().get_urls()

    def preview_png(self, request, pk):
        from .services.epaper_render import render_pm_image
        from .views import epaper_service_url

        display = self.get_object(request, pk)
        if display is None:
            raise Http404("No such ePaper display.")
        if display.asset_id is None:
            return HttpResponse(
                "Display is unbound — bind it to an asset to preview the panel.",
                content_type="text/plain",
                status=409,
            )
        png = render_pm_image(display.asset, service_url=epaper_service_url(request, display.pk))
        return HttpResponse(png, content_type="image/png")

    @admin.display(description="Preview")
    def preview_link(self, obj):
        if obj.asset_id is None:
            return "—"
        url = reverse("admin:forgekey_epaperdisplay_preview", args=[obj.pk])
        return format_html('<a href="{}" target="_blank">Preview Image</a>', url)

    @admin.display(description="Panel preview")
    def panel_preview(self, obj):
        if obj is None or obj.pk is None:
            return "(save the display first)"
        if obj.asset_id is None:
            return "Bind an asset to preview the panel."
        url = reverse("admin:forgekey_epaperdisplay_preview", args=[obj.pk])
        return format_html(
            '<div><a href="{0}" target="_blank">Open full size (800×480)</a></div>'
            '<img src="{0}" alt="panel preview" style="margin-top:8px;width:480px;'
            'max-width:100%;border:1px solid #ccc;image-rendering:pixelated"/>',
            url,
        )
