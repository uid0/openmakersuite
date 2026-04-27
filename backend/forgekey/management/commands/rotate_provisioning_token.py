"""
Publish a new provisioning token to every active ESP32 device via MQTT.

Devices subscribe to ``forgekey/<mac>/config`` (retained). On receipt the
device persists ``provisioning_token`` and re-registers any time after
``valid_after`` using the new token. Operators rotate the env var
``FORGEKEY_PROVISIONING_TOKEN`` separately on the backend; once every
device has acked via re-registration, the old token can be revoked.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from forgekey.models import ESP32Device
from forgekey.tasks import get_mqtt_client
from forgekey.utils import get_mqtt_topic

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Publish forgekey/<mac>/config with a new provisioning token to all active devices."

    def add_arguments(self, parser):
        parser.add_argument(
            "--token",
            required=True,
            help="The new provisioning token to publish.",
        )
        parser.add_argument(
            "--valid-after",
            help=(
                "ISO-8601 timestamp after which devices should switch tokens. "
                "Defaults to now + 5 minutes to give the message time to fan out."
            ),
        )
        parser.add_argument(
            "--mac",
            action="append",
            help="Restrict to a single MAC (may be repeated). Default: all active devices.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the targets and payload without publishing.",
        )

    def handle(self, *args, **options):
        token = options["token"].strip()
        if not token:
            raise CommandError("--token must be a non-empty string")

        if options.get("valid_after"):
            try:
                valid_after = timezone.datetime.fromisoformat(options["valid_after"])
            except ValueError as exc:
                raise CommandError(f"--valid-after must be ISO-8601: {exc}") from exc
            if timezone.is_naive(valid_after):
                valid_after = timezone.make_aware(valid_after, timezone.get_current_timezone())
        else:
            valid_after = timezone.now() + timedelta(minutes=5)

        qs = ESP32Device.objects.filter(is_active=True)
        if options.get("mac"):
            qs = qs.filter(mac_address__in=options["mac"])

        devices = list(qs)
        if not devices:
            self.stdout.write(self.style.WARNING("no active devices matched"))
            return

        payload = {
            "provisioning_token": token,
            "valid_after": valid_after.isoformat(),
        }
        payload_json = json.dumps(payload)

        if options["dry_run"]:
            self.stdout.write(f"[dry-run] would publish {payload_json} to {len(devices)} device(s)")
            for device in devices:
                self.stdout.write(f"  -> {get_mqtt_topic(device.mac_address, 'config')}")
            return

        client = get_mqtt_client()
        published = 0
        failures = 0
        for device in devices:
            topic = get_mqtt_topic(device.mac_address, "config")
            try:
                result = client.publish(topic, payload_json, qos=1, retain=True)
            except Exception as exc:  # pragma: no cover - logged for ops
                logger.exception("MQTT publish raised for %s", device.mac_address)
                self.stderr.write(self.style.ERROR(f"fail {device.mac_address}: {exc}"))
                failures += 1
                continue
            rc = getattr(result, "rc", 0)
            if rc != 0:
                self.stderr.write(self.style.ERROR(f"fail {device.mac_address}: MQTT rc={rc}"))
                failures += 1
                continue
            published += 1
            self.stdout.write(f"published -> {topic}")

        self.stdout.write(
            self.style.NOTICE(
                f"published={published} failed={failures} valid_after={valid_after.isoformat()}"
            )
        )
        if failures:
            raise CommandError(f"{failures} device(s) failed to receive the new token")
