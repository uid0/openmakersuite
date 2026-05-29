"""
Models for ForgeKey - ESP32 device management and asset authorization system.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Optional

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from inventory.models import Asset, Location

logger = logging.getLogger(__name__)


class DeviceType(models.Model):
    """
    Types of ESP32 devices that can be managed.
    """

    TYPE_INDICATOR = "indicator"
    TYPE_BADGE_READER = "badge_reader"
    TYPE_EPAPER_SCREEN = "epaper_screen"
    TYPE_OLED_SCREEN = "oled_screen"
    TYPE_TEMPERATURE_SENSOR = "temperature_sensor"
    TYPE_GENERIC_INPUT = "generic_input"
    TYPE_GENERIC_OUTPUT = "generic_output"
    TYPE_AC_RELAY = "ac_relay"
    TYPE_POWER_MEASUREMENT = "power_measurement"
    TYPE_PEOPLE_COUNTER = "people_counter"
    TYPE_ENV_SENSOR = "env_sensor"
    TYPE_DOOR_COUNTER = "door_counter"
    # Locker / door / electronic-device controllers (gh ForgeKey expansion).
    TYPE_LOCKER_LATCH = "locker_latch"
    TYPE_DOOR_LATCH = "door_latch"
    TYPE_OTP_KEYPAD = "otp_keypad"
    TYPE_LED_STRIP = "led_strip"
    TYPE_REED_SWITCH = "reed_switch"
    TYPE_IR_BREAK = "ir_break"
    TYPE_MORTISE_KEY = "mortise_key"

    TYPE_CHOICES = [
        (TYPE_INDICATOR, "Indicator/Status Light"),
        (TYPE_BADGE_READER, "Badge Reader"),
        (TYPE_EPAPER_SCREEN, "E-Paper Screen"),
        (TYPE_OLED_SCREEN, "OLED Screen"),
        (TYPE_TEMPERATURE_SENSOR, "Temperature Sensor"),
        (TYPE_GENERIC_INPUT, "Generic Input"),
        (TYPE_GENERIC_OUTPUT, "Generic Output"),
        (TYPE_AC_RELAY, "AC Relay"),
        (TYPE_POWER_MEASUREMENT, "Power Measurement"),
        (TYPE_PEOPLE_COUNTER, "People Counter"),
        (TYPE_ENV_SENSOR, "Environmental Sensor"),
        (TYPE_DOOR_COUNTER, "Door Counter"),
        (TYPE_LOCKER_LATCH, "Locker latch controller"),
        (TYPE_DOOR_LATCH, "Door latch controller"),
        (TYPE_OTP_KEYPAD, "OTP keypad"),
        (TYPE_LED_STRIP, "WS2818 LED strip controller"),
        (TYPE_REED_SWITCH, "Door reed switch"),
        (TYPE_IR_BREAK, "Inventory IR-break sensor"),
        (TYPE_MORTISE_KEY, "Mortise key (admin override) sensor"),
    ]

    name = models.CharField(max_length=50, unique=True, help_text="Device type name")
    code = models.CharField(
        max_length=30,
        unique=True,
        choices=TYPE_CHOICES,
        help_text="Device type code",
    )
    description = models.TextField(blank=True, help_text="Description of this device type")
    is_active = models.BooleanField(default=True, help_text="Is this device type active?")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ESP32Device(models.Model):
    """
    Represents an ESP32 device managed by ForgeKey.
    """

    # MAC address validation (format: XX:XX:XX:XX:XX:XX)
    mac_validator = RegexValidator(
        regex=r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$",
        message="MAC address must be in format XX:XX:XX:XX:XX:XX",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mac_address = models.CharField(
        max_length=17,
        unique=True,
        validators=[mac_validator],
        help_text="MAC address of the ESP32 device (format: XX:XX:XX:XX:XX:XX)",
    )
    device_type = models.ForeignKey(
        DeviceType,
        on_delete=models.PROTECT,
        related_name="devices",
        help_text="Type of device",
    )
    name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Human-readable name for this device",
    )
    description = models.TextField(blank=True, help_text="Description of this device")
    firmware_version = models.CharField(
        max_length=50,
        blank=True,
        help_text="Current firmware version on the device",
    )
    last_seen = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time the device was seen online",
    )
    is_online = models.BooleanField(default=False, help_text="Is the device currently online?")
    is_active = models.BooleanField(default=True, help_text="Is this device active?")
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forgekey_devices",
        help_text="Default location assignment (for sensor devices unbound to a specific asset)",
    )
    enrollment_photo = models.ImageField(
        upload_to="forgekey/enrollment_photos/",
        null=True,
        blank=True,
        help_text="Photo captured at device enrollment for staff identification",
    )
    last_photo = models.ImageField(
        upload_to="forgekey/device_photos/last/",
        null=True,
        blank=True,
        help_text="Most-recent periodic surveillance photo from the device",
    )
    boot_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Last-reported boot counter from the device",
    )
    free_heap = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Last-reported free heap (bytes) from the device",
    )
    ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Last-reported IP address of the device",
    )
    capabilities = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "List of capability identifiers announced by firmware over the "
            "<prefix>/<mac>/capabilities MQTT topic (e.g. ['people_counter', "
            "'status_led']). Refreshed on every retained announcement."
        ),
    )
    capabilities_announced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the device last published its capability set.",
    )
    identity = models.OneToOneField(
        "DeviceIdentity",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="esp32_device",
        help_text=(
            "Per-chip device identity (set by /enroll/). MAC drops to inventory metadata "
            "once an identity is bound."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["mac_address"]
        indexes = [
            models.Index(fields=["mac_address"]),
            models.Index(fields=["device_type"]),
            models.Index(fields=["is_online"]),
        ]

    def __str__(self) -> str:
        if self.name:
            return f"{self.name} ({self.mac_address})"
        return self.mac_address

    def generate_jwt_secret(self, shared_secret: str) -> str:
        """
        Generate a JWT secret for this device based on MAC address and shared secret.
        """
        message = f"{self.mac_address}:{shared_secret}"
        return hashlib.sha256(message.encode()).hexdigest()

    def normalize_mac_address(self) -> str:
        """Normalize MAC address to uppercase with colons."""
        mac = self.mac_address.replace("-", ":").upper()
        return mac


class AssetDevice(models.Model):
    """
    Links assets to ESP32 devices.
    An asset can have multiple devices (e.g., power relay + power meter).
    """

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="forgekey_devices",
        help_text="Asset this device is associated with",
    )
    device = models.ForeignKey(
        ESP32Device,
        on_delete=models.CASCADE,
        related_name="asset_assignments",
        help_text="ESP32 device",
    )
    role = models.CharField(
        max_length=100,
        blank=True,
        help_text="Role of this device for the asset (e.g., 'power_control', 'metering')",
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="Is this the primary device for controlling this asset?",
    )
    power_off_delay_seconds = models.PositiveIntegerField(
        default=0,
        help_text="Delay in seconds before removing power after device is disabled",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["asset", "device"]]
        ordering = ["asset", "-is_primary", "role"]

    def __str__(self) -> str:
        return f"{self.asset.name} - {self.device.name or self.device.mac_address}"


class OperationalMode(models.Model):
    """
    Operational modes for assets.
    """

    MODE_AVAILABLE = "available"
    MODE_CLASSROOM = "classroom"
    MODE_MAINTENANCE = "maintenance"
    MODE_LOCKED_OUT = "locked_out"

    MODE_CHOICES = [
        (MODE_AVAILABLE, "Available"),
        (MODE_CLASSROOM, "Classroom Mode"),
        (MODE_MAINTENANCE, "Maintenance Mode"),
        (MODE_LOCKED_OUT, "Locked Out"),
    ]

    asset = models.OneToOneField(
        Asset,
        on_delete=models.CASCADE,
        related_name="operational_mode",
        help_text="Asset this mode applies to",
    )
    mode = models.CharField(
        max_length=20,
        choices=MODE_CHOICES,
        default=MODE_AVAILABLE,
        help_text="Current operational mode",
    )
    classroom_mode_enabled = models.BooleanField(
        default=False,
        help_text="Is classroom mode currently enabled?",
    )
    classroom_mode_enabled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="classroom_modes_enabled",
        help_text="User who enabled classroom mode",
    )
    classroom_mode_enabled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When classroom mode was enabled",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset"]

    def __str__(self) -> str:
        return f"{self.asset.name} - {self.get_mode_display()}"


class AssetAuthorization(models.Model):
    """
    Tracks which users are authorized to use an asset.
    """

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="authorizations",
        help_text="Asset this authorization is for",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="asset_authorizations",
        help_text="User authorized to use this asset",
    )
    authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="authorizations_granted",
        help_text="User who granted this authorization",
    )
    authorized_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, help_text="Is this authorization active?")
    notes = models.TextField(blank=True, help_text="Notes about this authorization")

    class Meta:
        unique_together = [["asset", "user"]]
        ordering = ["asset", "user"]
        indexes = [
            models.Index(fields=["asset", "user", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.asset.name} - {self.user.username}"


class LockoutLevel(models.TextChoices):
    """Lockout permission levels in hierarchical order."""

    USER = "user", "User"
    MAINTAINER = "maintainer", "Maintainer"
    GROUP_ADMIN = "group_admin", "Group Admin"
    LOGISTICS_TEAM = "logistics_team", "Logistics Team"
    LOGISTICS_LEAD = "logistics_lead", "Logistics Lead"
    COO = "coo", "COO"


class DeviceLockout(models.Model):
    """
    Tracks lockouts for assets with hierarchical unlock permissions.
    Lockouts can be stacked - higher level lockouts can only be unlocked by higher level users.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="lockouts",
        help_text="Asset that is locked out",
    )
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lockouts_created",
        help_text="User who created this lockout",
    )
    lockout_level = models.CharField(
        max_length=20,
        choices=LockoutLevel.choices,
        help_text="Permission level of the user who locked this out",
    )
    reason = models.TextField(help_text="Reason for the lockout")
    locked_at = models.DateTimeField(auto_now_add=True)
    unlocked_at = models.DateTimeField(
        null=True, blank=True, help_text="When this lockout was cleared"
    )
    unlocked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lockouts_unlocked",
        help_text="User who unlocked this",
    )
    is_active = models.BooleanField(default=True, help_text="Is this lockout currently active?")

    class Meta:
        ordering = ["-locked_at"]
        indexes = [
            models.Index(fields=["asset", "is_active"]),
            models.Index(fields=["locked_by"]),
        ]

    def __str__(self) -> str:
        status = "Active" if self.is_active else "Cleared"
        return f"{self.asset.name} - {status} ({self.get_lockout_level_display()})"

    def can_be_unlocked_by(self, user) -> bool:
        """
        Check if this lockout can be unlocked by the given user.
        Uses hierarchical permission system.
        """
        if not user.is_authenticated:
            return False

        # COO can unlock anything
        if self._is_coo(user):
            return True

        # Original locker can always unlock their own lockout
        if self.locked_by == user:
            return True

        # Check hierarchical permissions
        lockout_levels = [
            LockoutLevel.USER,
            LockoutLevel.MAINTAINER,
            LockoutLevel.GROUP_ADMIN,
            LockoutLevel.LOGISTICS_TEAM,
            LockoutLevel.LOGISTICS_LEAD,
            LockoutLevel.COO,
        ]

        current_level_index = lockout_levels.index(self.lockout_level)
        user_level = self._get_user_lockout_level(user)

        if user_level not in lockout_levels:
            return False

        user_level_index = lockout_levels.index(user_level)

        # User can unlock if their level is higher than the lockout level
        return user_level_index > current_level_index

    def _is_coo(self, user) -> bool:
        """Check if user is COO."""
        # COO would be determined by a specific group or flag
        # For now, check for a "COO" group or is_superuser
        if user.is_superuser:
            return True
        try:
            coo_group = Group.objects.get(name="COO")
            return coo_group in user.groups.all()
        except Group.DoesNotExist:
            return False

    def _get_user_lockout_level(self, user) -> Optional[str]:
        """Determine the lockout level for a user."""
        if self._is_coo(user):
            return LockoutLevel.COO

        # Check for Logistics Lead
        try:
            logistics_lead_group = Group.objects.get(name="Logistics Lead")
            if logistics_lead_group in user.groups.all():
                return LockoutLevel.LOGISTICS_LEAD
        except Group.DoesNotExist:
            pass

        # Check for Logistics Team
        try:
            logistics_group = Group.objects.get(name="Logistics")
            if logistics_group in user.groups.all():
                return LockoutLevel.LOGISTICS_TEAM
        except Group.DoesNotExist:
            pass

        # Check for Group Admin
        if self.asset.owning_group and self.asset.owning_group in user.groups.all():
            # Check if user has group admin permissions
            if (
                user.has_perm("inventory.group_admin")
                or user.groups.filter(name__endswith="_admin").exists()
            ):
                return LockoutLevel.GROUP_ADMIN

        # Check for Maintainer
        try:
            maintainer_group = Group.objects.get(name="Maintainer")
            if maintainer_group in user.groups.all():
                return LockoutLevel.MAINTAINER
        except Group.DoesNotExist:
            pass

        # Default to user level
        return LockoutLevel.USER


class DeviceUsage(models.Model):
    """
    Tracks usage sessions for assets.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="usage_sessions",
        help_text="Asset being used",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="device_usage_sessions",
        help_text="User using the asset",
    )
    started_at = models.DateTimeField(auto_now_add=True, help_text="When usage started")
    ended_at = models.DateTimeField(null=True, blank=True, help_text="When usage ended")
    duration_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Duration of usage in seconds",
    )
    power_consumption_kwh = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Power consumption during this session (kWh)",
    )
    notes = models.TextField(blank=True, help_text="Notes about this usage session")

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["asset", "user", "started_at"]),
            models.Index(fields=["started_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.asset.name} - {self.user.username if self.user else 'Unknown'} - {self.started_at}"

    def end_session(self) -> None:
        """End the usage session and calculate duration."""
        if self.ended_at:
            return  # Already ended

        self.ended_at = timezone.now()
        if self.started_at:
            delta = self.ended_at - self.started_at
            self.duration_seconds = int(delta.total_seconds())
        self.save()


class PowerMeterReading(models.Model):
    """
    Stores power measurement readings from ESP32 power measurement devices.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey(
        ESP32Device,
        on_delete=models.CASCADE,
        related_name="power_readings",
        help_text="Power measurement device",
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="power_readings",
        help_text="Asset this reading is for",
    )
    usage_session = models.ForeignKey(
        DeviceUsage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="power_readings",
        help_text="Usage session this reading belongs to",
    )
    voltage = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Voltage reading (V)",
    )
    current = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Current reading (A)",
    )
    power = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Power reading (W)",
    )
    energy = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Energy reading (kWh)",
    )
    timestamp = models.DateTimeField(auto_now_add=True, help_text="When this reading was taken")

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["device", "timestamp"]),
            models.Index(fields=["asset", "timestamp"]),
            models.Index(fields=["usage_session"]),
        ]

    def __str__(self) -> str:
        return f"{self.asset.name} - {self.timestamp} - {self.power or 0}W"


class FirmwareVersion(models.Model):
    """
    Tracks firmware versions available for ESP32 devices.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.CharField(max_length=50, unique=True, help_text="Firmware version string")
    device_type = models.ForeignKey(
        DeviceType,
        on_delete=models.CASCADE,
        related_name="firmware_versions",
        help_text="Device type this firmware is for",
    )
    firmware_file = models.FileField(
        upload_to="forgekey/firmware/",
        help_text="Firmware binary file",
    )
    signature = models.TextField(
        blank=True,
        help_text=(
            "base64(DER) ECDSA(P-256) signature over the firmware binary, "
            "computed on save when FORGEKEY_FIRMWARE_SIGNING_KEY is configured."
        ),
    )
    sha256 = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA-256 hex digest of the firmware binary (auto-computed on save)",
    )
    binary_url = models.URLField(
        blank=True,
        help_text="Override URL for the firmware binary; if blank, the firmware_file URL is used",
    )
    mandatory = models.BooleanField(
        default=False,
        help_text="If true, devices must apply this firmware before continuing normal operation",
    )
    release_notes = models.TextField(blank=True, help_text="Release notes for this version")
    is_active = models.BooleanField(default=True, help_text="Is this firmware version active?")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="firmware_versions_created",
        help_text="User who uploaded this firmware",
    )

    class Meta:
        ordering = ["-created_at"]
        unique_together = [["version", "device_type"]]

    def __str__(self) -> str:
        return f"{self.device_type.name} - {self.version}"

    def save(self, *args, **kwargs):
        if self.firmware_file and (not self.sha256 or not self.signature):
            data = self._read_firmware_bytes()
            if data is not None:
                if not self.sha256:
                    self.sha256 = hashlib.sha256(data).hexdigest()
                if not self.signature:
                    self.signature = self._sign_or_log(data)
        super().save(*args, **kwargs)

    def _read_firmware_bytes(self) -> Optional[bytes]:
        try:
            self.firmware_file.seek(0)
            data = self.firmware_file.read()
            self.firmware_file.seek(0)
            return data
        except (ValueError, AttributeError):
            if not self.firmware_file.name:
                return None
            with self.firmware_file.storage.open(self.firmware_file.name, "rb") as fh:
                return fh.read()

    @staticmethod
    def _sign_or_log(data: bytes) -> str:
        # Imported lazily so the model module stays import-safe even if the
        # cryptography stack is unavailable in some test environments.
        from .services.firmware_signing import (
            FirmwareSigningError,
            is_signing_configured,
            sign_firmware_bytes,
        )

        if not is_signing_configured():
            return ""
        try:
            return sign_firmware_bytes(data)
        except FirmwareSigningError as exc:
            logger.warning("Skipping firmware signing: %s", exc)
            return ""

    @property
    def effective_binary_url(self) -> str:
        if self.binary_url:
            return self.binary_url
        if self.firmware_file:
            return self.firmware_file.url
        return ""


class ESP32DevicePhoto(models.Model):
    """
    Periodic surveillance photo uploaded by an ESP32 sensor device.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey(
        ESP32Device,
        on_delete=models.CASCADE,
        related_name="photos",
        help_text="Device that uploaded this photo",
    )
    image = models.ImageField(
        upload_to="forgekey/device_photos/",
        help_text="JPEG photo from the device",
    )
    captured_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the device captured the photo (device-supplied)",
    )
    received_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the server received the photo",
    )

    class Meta:
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["device", "-received_at"]),
            models.Index(fields=["received_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.device.mac_address} photo @ {self.received_at:%Y-%m-%d %H:%M:%S}"


class OccupancyEvent(models.Model):
    """
    A single occupancy event published over MQTT by a ForgeKey people-counter
    device. The firmware contract is one event per crossing, with ``count_in``
    and ``count_out`` carrying the per-event delta (typically one of them is 1
    and the other 0). ``raw_payload`` stores the full original message body so
    we can recover from forward-compatible firmware changes without a schema
    migration.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey(
        ESP32Device,
        on_delete=models.CASCADE,
        related_name="occupancy_events",
        help_text="Device that published this event",
    )
    sensor_kind = models.CharField(
        max_length=50,
        help_text="Sensor kind extracted from the MQTT topic (e.g. 'people_counter')",
    )
    count_in = models.PositiveSmallIntegerField(
        default=0,
        help_text="People entering during this event",
    )
    count_out = models.PositiveSmallIntegerField(
        default=0,
        help_text="People leaving during this event",
    )
    event_timestamp_utc = models.DateTimeField(
        help_text="Device-supplied event time (UTC). Falls back to server time if absent.",
    )
    ingested_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the backend persisted this event",
    )
    raw_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Original MQTT message body, kept for forward compatibility",
    )

    class Meta:
        ordering = ["-event_timestamp_utc"]
        indexes = [
            models.Index(fields=["device", "event_timestamp_utc"]),
            models.Index(fields=["event_timestamp_utc"]),
        ]

    def __str__(self) -> str:
        return (
            f"{self.device.mac_address} {self.sensor_kind} "
            f"+{self.count_in}/-{self.count_out} @ {self.event_timestamp_utc:%Y-%m-%d %H:%M:%S}"
        )

    @property
    def occupancy_delta(self) -> int:
        """Signed change in occupancy contributed by this event."""
        return int(self.count_in) - int(self.count_out)

    @classmethod
    def current_occupancy_for(cls, device: "ESP32Device") -> int:
        """Sum of (count_in - count_out) across all events for a device.

        Returns the running occupancy assuming the table is the source of
        truth from the device's installation onward. Negative values are
        clamped to zero — those indicate missed enter events, not negative
        people.
        """
        agg = cls.objects.filter(device=device).aggregate(
            total_in=models.Sum("count_in"),
            total_out=models.Sum("count_out"),
        )
        delta = (agg["total_in"] or 0) - (agg["total_out"] or 0)
        return max(delta, 0)


class FirmwareSigningKey(models.Model):
    """
    Database-managed ECDSA(P-256) firmware signing keypair.

    The private key PEM is encrypted at rest with Fernet using a KEK
    derived from ``settings.SECRET_KEY`` (see
    ``forgekey.services.firmware_signing._fernet``); the public key is
    stored in cleartext so build pipelines and the public-key endpoint
    can serve it without unwrapping the secret. Only one row may be
    active at a time — rotation deactivates the prior active key in the
    same transaction. The legacy ``FORGEKEY_FIRMWARE_SIGNING_KEY``
    environment variable is consulted as a fallback when no active row
    exists, so existing deployments keep working without a data
    migration.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    label = models.CharField(
        max_length=120,
        help_text="Human-readable label for this keypair (e.g., 'prod-2026-q2').",
    )
    public_key_pem = models.TextField(
        help_text="PEM-encoded SubjectPublicKeyInfo. Embedded into firmware builds.",
    )
    private_key_pem_encrypted = models.BinaryField(
        help_text="Fernet ciphertext of the PEM-encoded private key.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this keypair is the one used to sign new firmware.",
    )
    description = models.TextField(blank=True, help_text="Free-form notes for operators.")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="firmware_signing_keys_created",
        help_text="User who uploaded / generated this keypair",
    )
    rotated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this key was retired (set automatically on rotation).",
    )
    rotated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="firmware_signing_keys_rotated",
        help_text="User who retired this key",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_active", "-created_at"]),
        ]

    def __str__(self) -> str:
        flag = "active" if self.is_active else "retired"
        return f"{self.label} [{flag}]"

    def decrypt_private_pem(self) -> str:
        from .services.firmware_signing import _fernet

        ciphertext = bytes(self.private_key_pem_encrypted)
        return _fernet().decrypt(ciphertext).decode("utf-8")

    @classmethod
    def encrypt_private_pem(cls, pem: str) -> bytes:
        from .services.firmware_signing import _fernet

        return _fernet().encrypt(pem.encode("utf-8"))

    @classmethod
    def get_active(cls) -> Optional["FirmwareSigningKey"]:
        return cls.objects.filter(is_active=True).order_by("-created_at").first()


class DeviceFirmwareUpdate(models.Model):
    """
    Tracks firmware update requests and status for ESP32 devices.
    """

    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey(
        ESP32Device,
        on_delete=models.CASCADE,
        related_name="firmware_updates",
        help_text="Device to update",
    )
    firmware_version = models.ForeignKey(
        FirmwareVersion,
        on_delete=models.PROTECT,
        related_name="device_updates",
        help_text="Firmware version to install",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        help_text="Update status",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="firmware_updates_requested",
        help_text="User who requested this update",
    )
    started_at = models.DateTimeField(null=True, blank=True, help_text="When update started")
    completed_at = models.DateTimeField(null=True, blank=True, help_text="When update completed")
    error_message = models.TextField(blank=True, help_text="Error message if update failed")

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["device", "status"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.device.mac_address} - {self.firmware_version.version} - {self.get_status_display()}"


class DeviceCommand(models.Model):
    """Audit row for a single command dispatched to a device.

    Each row represents one MQTT publish on the device's ``command`` topic,
    plus the eventual ack the firmware echoes back over its ``status`` topic.
    The UI polls these rows to render live ack feedback (oms-zta) and the
    recent-commands history table.
    """

    ACK_PENDING = "pending"
    ACK_OK = "acked"
    ACK_ERROR = "error"
    ACK_TIMEOUT = "timeout"

    ACK_CHOICES = [
        (ACK_PENDING, "Pending"),
        (ACK_OK, "Acknowledged"),
        (ACK_ERROR, "Error"),
        (ACK_TIMEOUT, "Timed out"),
    ]

    # How long a pending ack is considered "live" before it's reported as
    # timed out by the recent-commands endpoint. Mirrors the frontend's 10s
    # ack window so the UI and DB agree on what 'no ack' looks like.
    ACK_TIMEOUT_SECONDS = 10

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey(
        ESP32Device,
        on_delete=models.CASCADE,
        related_name="commands",
    )
    command = models.CharField(
        max_length=64,
        help_text="Logical command name, e.g. 'restart', 'blink', 'identify'.",
    )
    payload = models.JSONField(
        default=dict,
        help_text="Full MQTT payload published to the device.",
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="device_commands_sent",
    )
    sent_at = models.DateTimeField(auto_now_add=True)
    ack_status = models.CharField(
        max_length=16,
        choices=ACK_CHOICES,
        default=ACK_PENDING,
    )
    ack_at = models.DateTimeField(null=True, blank=True)
    ack_payload = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-sent_at"]
        indexes = [
            models.Index(fields=["device", "-sent_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.device.mac_address} - {self.command} ({self.ack_status})"

    @property
    def effective_ack_status(self) -> str:
        """Return ``timeout`` for old pending rows; otherwise the stored status.

        Avoids requiring a sweeper task to flip stale pending rows — the
        recent-commands endpoint and admin both call through this property.
        """
        if self.ack_status != self.ACK_PENDING:
            return self.ack_status
        age = (timezone.now() - self.sent_at).total_seconds()
        if age > self.ACK_TIMEOUT_SECONDS:
            return self.ACK_TIMEOUT
        return self.ACK_PENDING


class ForgeKeyAuditEvent(models.Model):
    """Append-only audit log for safety-critical ForgeKey device actions.

    Captures actor, timestamp, affected entities, and free-form notes for
    each safety- or access-relevant mutation: authorization grants and
    revocations, device lockouts and unlocks, firmware update requests.
    Rows are written by ``forgekey.audit.record_event`` and never updated
    or deleted by application code (admin removal requires raw SQL).

    Per gh #352 / #334. Pattern mirrors
    ``maintenance_orders.ThirdPartyWorkOrderAuditLog`` so future per-domain
    audit logs land with the same shape and the eventual unified review
    surface (gh #359) can join across them cleanly.
    """

    ACTION_AUTHORIZATION_GRANT = "authorization_grant"
    ACTION_AUTHORIZATION_REVOKE = "authorization_revoke"
    ACTION_LOCKOUT_CREATE = "lockout_create"
    ACTION_LOCKOUT_UNLOCK = "lockout_unlock"
    ACTION_FIRMWARE_REQUEST = "firmware_request"

    ACTION_CHOICES = [
        (ACTION_AUTHORIZATION_GRANT, "Authorization granted"),
        (ACTION_AUTHORIZATION_REVOKE, "Authorization revoked"),
        (ACTION_LOCKOUT_CREATE, "Device locked out"),
        (ACTION_LOCKOUT_UNLOCK, "Device unlocked"),
        (ACTION_FIRMWARE_REQUEST, "Firmware update requested"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forgekey_audit_actions",
        help_text="User who performed the action; null for system-initiated events.",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    # Optional FKs to the entities involved. At least one is set per row;
    # SET_NULL on delete so the audit trail survives entity teardown.
    asset = models.ForeignKey(
        Asset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forgekey_audit_events",
    )
    device = models.ForeignKey(
        "ESP32Device",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    authorization = models.ForeignKey(
        "AssetAuthorization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    lockout = models.ForeignKey(
        "DeviceLockout",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    firmware_update = models.ForeignKey(
        "DeviceFirmwareUpdate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    notes = models.TextField(blank=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Action-specific payload (lockout level, firmware version, etc).",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["asset", "-created_at"], name="fk_audit_asset_idx"),
            models.Index(fields=["device", "-created_at"], name="fk_audit_device_idx"),
            models.Index(fields=["actor", "-created_at"], name="fk_audit_actor_idx"),
            models.Index(fields=["action", "-created_at"], name="fk_audit_action_idx"),
        ]

    def __str__(self) -> str:
        target = self.asset_id or self.device_id or self.authorization_id or self.lockout_id
        return f"{self.action} ({target}) @ {self.created_at:%Y-%m-%d %H:%M}"


# ---------------------------------------------------------------------------
# Device-identity trust foundation (oms-d2axqu / forgekey-trust-refactor)
# ---------------------------------------------------------------------------
#
# DeviceIdentity is the per-chip security boundary. The legacy MAC-keyed
# ESP32Device row drops to inventory metadata; pre-existing callers keep
# resolving by MAC. New enrollments bind DeviceIdentity -> ESP32Device via
# ESP32Device.identity and persist an OMS-issued client certificate.


class DeviceIdentity(models.Model):
    """Per-device security anchor keyed by the ESP32 unique chip id.

    A DeviceIdentity is created/updated by the /enroll/ flow and references
    every certificate ever issued to that chip. Decommissioned identities
    cannot be re-enrolled.
    """

    STATUS_ACTIVE = "active"
    STATUS_SUSPENDED = "suspended"
    STATUS_DECOMMISSIONED = "decommissioned"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_SUSPENDED, "Suspended"),
        (STATUS_DECOMMISSIONED, "Decommissioned"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device_id = models.CharField(
        max_length=64,
        unique=True,
        help_text="ESP32 unique chip identifier — the device-identity security boundary.",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["device_id"]
        indexes = [
            models.Index(fields=["device_id"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.device_id} [{self.status}]"


class DeviceCertificate(models.Model):
    """An mTLS client certificate issued by the OMS root CA to a DeviceIdentity."""

    STATUS_VALID = "valid"
    STATUS_REVOKED = "revoked"
    STATUS_EXPIRED = "expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey(
        DeviceIdentity,
        on_delete=models.PROTECT,
        related_name="certificates",
    )
    serial = models.CharField(max_length=128, unique=True)
    subject = models.CharField(max_length=512, help_text="RFC4514 subject DN.")
    fingerprint_sha256 = models.CharField(max_length=64, unique=True)
    not_before = models.DateTimeField()
    not_after = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.CharField(
        max_length=200,
        blank=True,
        help_text="Issuer CA name at the time of issuance (audit breadcrumb).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["fingerprint_sha256"]),
            models.Index(fields=["device", "-created_at"]),
            models.Index(fields=["revoked_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.device.device_id} cert {self.serial[:12]} [{self.status}]"

    @property
    def status(self) -> str:
        if self.revoked_at is not None:
            return self.STATUS_REVOKED
        if self.not_after <= timezone.now():
            return self.STATUS_EXPIRED
        return self.STATUS_VALID


class DeviceEnrollment(models.Model):
    """Bootstrap session: CSR submission, validation, signing, and issuance."""

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_ISSUED = "issued"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_ISSUED, "Issued"),
        (STATUS_EXPIRED, "Expired"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey(
        DeviceIdentity,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="enrollments",
    )
    csr_pem = models.TextField()
    nonce = models.CharField(max_length=64, blank=True, db_index=True)
    unique_chip_id = models.CharField(max_length=64, blank=True)
    mac_address = models.CharField(max_length=32, blank=True)
    sensor_kind = models.CharField(max_length=50, blank=True)
    firmware_version = models.CharField(max_length=50, blank=True)
    chip_info = models.JSONField(default=dict, blank=True)
    boot_count = models.PositiveIntegerField(null=True, blank=True)
    free_heap = models.PositiveIntegerField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    flash_memory_id = models.CharField(max_length=64, blank=True)
    token_fingerprint = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA-256 hex of the provisioning token used (full digest).",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forgekey_enrollments_approved",
    )
    certificate = models.ForeignKey(
        DeviceCertificate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollments",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    enrollment_photo = models.ImageField(
        upload_to="forgekey/enrollment_photos/",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["status", "-requested_at"]),
        ]

    def __str__(self) -> str:
        return f"enrollment {self.unique_chip_id or self.id} [{self.status}]"


class CertificateAuthority(models.Model):
    """OMS-internal root CA backing :mod:`forgekey.services.csr_signing`.

    Only one row may be ``is_active=True`` at a time. The encrypted private
    key blob carries its own ``key_kid`` so KEK rotation is non-destructive
    (older blobs still decrypt with their original kid).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=64, default="forgekey-root", db_index=True)
    cert_pem = models.TextField()
    encrypted_private_key = models.BinaryField()
    key_kid = models.CharField(max_length=64, blank=True)
    not_before = models.DateTimeField()
    not_after = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="forgekey_ca_single_active",
            ),
        ]

    def __str__(self) -> str:
        flag = "active" if self.is_active else "retired"
        return f"{self.name} [{flag}]"

    @classmethod
    def get_active(cls) -> Optional["CertificateAuthority"]:
        return cls.objects.filter(is_active=True).order_by("-created_at").first()


class EPaperDisplay(models.Model):
    """A XIAO 7.5" ePaper panel bound to an asset, showing PM status.

    Each panel is one ESP32 + e-paper combo glued to the side of a
    machine. Firmware wakes from deep sleep on its own schedule (or on
    an MQTT command), pulls the latest PNG from
    ``GET /api/forgekey/epaper/<display_id>/image.png`` over HTTPS,
    flashes the e-paper, reports battery level via
    ``POST /api/forgekey/epaper/<display_id>/battery/``, then sleeps
    again.

    Pre-rendering server-side keeps the firmware simple — the panel
    only needs to draw a single PNG, not run any layout logic. Battery
    telemetry lives on this row so the operator dashboard can flag a
    panel that needs to swap to a charged twin (the swap workflow is
    a follow-up; for now low battery surfaces as a Sentry warning).
    """

    LOW_BATTERY_PERCENT_DEFAULT = 20

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.OneToOneField(
        ESP32Device,
        on_delete=models.CASCADE,
        related_name="epaper_display",
        null=True,
        blank=True,
        help_text=(
            "ESP32 driving the panel. Nullable for HTTPS-only ePaper "
            "devices that self-register by display_id and never enroll "
            "via the MAC-based MQTT pathway."
        ),
    )
    asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="epaper_displays",
        help_text="Asset whose PM status the panel is currently showing.",
    )
    # Battery telemetry. Nullable because a freshly-enrolled panel may
    # not have reported yet; the dashboards treat null as "unknown" and
    # don't page on it.
    battery_percent = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Last reported battery percentage (0-100).",
    )
    last_battery_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the device last reported its battery percentage.",
    )
    # Image cache fields. The render service computes a deterministic
    # ETag from the snapshot inputs (asset id + each schedule's
    # status + days_since_last) so the device GET can short-circuit
    # with 304 Not Modified when nothing changed.
    last_image_etag = models.CharField(
        max_length=64,
        blank=True,
        help_text="ETag of the most recently rendered PNG (snapshot fingerprint).",
    )
    last_image_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the panel last fetched a non-304 image.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive panels stop receiving refresh commands.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset__name", "device__mac_address"]
        indexes = [
            models.Index(fields=["is_active"]),
            models.Index(fields=["asset"]),
        ]

    def __str__(self) -> str:
        asset_name = self.asset.name if self.asset_id else "unbound"
        if self.device_id:
            label = self.device.mac_address
        else:
            label = f"did:{str(self.pk)[:8]}"
        return f"EPaperDisplay({label} → {asset_name})"

    @property
    def is_low_battery(self) -> bool:
        """True when the panel is below the low-battery threshold."""
        if self.battery_percent is None:
            return False
        threshold = getattr(
            settings,
            "FORGEKEY_EPAPER_LOW_BATTERY_PERCENT",
            self.LOW_BATTERY_PERCENT_DEFAULT,
        )
        return self.battery_percent < threshold
