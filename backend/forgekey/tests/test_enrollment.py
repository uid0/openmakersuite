"""
End-to-end tests for ``POST /api/forgekey/devices/enroll/`` (oms-d2axqu).

Each test sets up an active CA via the management command path so the
issued certificate verifies against a real CA. Generates a real P-256 CSR
in the firmware-contract shape (``CN=forgekey-<lowercase-mac-no-sep>``).
"""

from __future__ import annotations

import json
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

import pytest
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from PIL import Image

from forgekey.models import (
    CertificateAuthority,
    DeviceCertificate,
    DeviceEnrollment,
    DeviceIdentity,
    DeviceType,
    ESP32Device,
)
from forgekey.tests.factories import DeviceTypeFactory

pytestmark = pytest.mark.django_db


PROVISIONING_TOKEN = "test-provisioning-token-please"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _provisioning_token(settings):
    settings.FORGEKEY_PROVISIONING_TOKEN = PROVISIONING_TOKEN
    return PROVISIONING_TOKEN


@pytest.fixture(autouse=True)
def _ca_kek(settings):
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    return settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY


@pytest.fixture
def active_ca():
    call_command("forgekey_ca", "init", "--validity-years", "1")
    return CertificateAuthority.get_active()


@pytest.fixture
def people_counter_type():
    return DeviceTypeFactory(code=DeviceType.TYPE_PEOPLE_COUNTER)


@pytest.fixture
def enroll_url():
    return reverse("forgekey:device-enroll")


def _build_csr(mac: str = "aabbccddeeff"):
    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"forgekey-{mac}")])
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .sign(private_key=private_key, algorithm=hashes.SHA256())
    )
    return private_key, csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _jpeg(name: str = "enroll.jpg") -> SimpleUploadedFile:
    img = Image.new("RGB", (16, 16), color="blue")
    buf = BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    return SimpleUploadedFile(name=name, content=buf.read(), content_type="image/jpeg")


def _post_enroll(
    api_client,
    url,
    *,
    meta_overrides=None,
    photo=None,
    token=PROVISIONING_TOKEN,
    token_header="HTTP_X_FORGEKEY_PROVISIONING_TOKEN",
    extra_headers=None,
):
    mac = (meta_overrides or {}).get("mac_address", "AA:BB:CC:DD:EE:FF")
    chip = (meta_overrides or {}).get("unique_chip_id", "chip-aabbccddeeff")
    _key, csr_pem = _build_csr(mac.replace(":", "").lower())
    meta = {
        "mac_address": mac,
        "unique_chip_id": chip,
        "csr_pem": csr_pem,
        "firmware_version": "1.0.0",
        "sensor_kind": DeviceType.TYPE_PEOPLE_COUNTER,
        "chip_info": {"chip_model": "ESP32-C6"},
        "boot_count": 1,
        "free_heap": 250000,
        "ip": "10.0.0.10",
        "flash_memory_id": "abc",
    }
    if meta_overrides:
        meta.update(meta_overrides)
    data = {"metadata": json.dumps(meta)}
    if photo is not None:
        data["photo"] = photo
    kwargs = {"format": "multipart"}
    if token is not None:
        kwargs[token_header] = token
    if extra_headers:
        kwargs.update(extra_headers)
    return api_client.post(url, data=data, **kwargs)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_enroll_returns_documented_fields(api_client, enroll_url, active_ca, people_counter_type):
    resp = _post_enroll(api_client, enroll_url, photo=_jpeg())
    assert resp.status_code == 201, resp.content
    body = resp.json()

    assert body["device_id"] == "chip-aabbccddeeff"
    assert body["client_certificate_pem"].startswith("-----BEGIN CERTIFICATE-----")
    assert body["command_public_key_pem"].startswith("-----BEGIN PUBLIC KEY-----")

    policy = body["policy"]
    # Canonical MAC-keyed topics (forgekey/<mac>/...) — the chip-id-keyed
    # legacy form made the firmware contract check fail and wipe its creds.
    assert policy["mqtt_topic_for_firmware"] == "forgekey/aabbccddeeff/firmware"
    assert policy["mqtt_topic_for_commands"] == "forgekey/aabbccddeeff/command"
    assert policy["mqtt_topic_for_status"] == "forgekey/aabbccddeeff/status"
    # People counters carry a contract-valid pings topic (kind/occupancy).
    assert policy["mqtt_topic_for_pings"] == "forgekey/aabbccddeeff/people_counter/occupancy"
    assert isinstance(policy["mqtt_broker_use_tls"], bool)

    # Top-level mirror still present so older firmware can read either form.
    assert body["mqtt_topic_for_firmware"] == policy["mqtt_topic_for_firmware"]
    assert body["mqtt_topic_for_pings"] == policy["mqtt_topic_for_pings"]


def test_enroll_persists_identity_certificate_and_enrollment_row(
    api_client, enroll_url, active_ca, people_counter_type
):
    _post_enroll(api_client, enroll_url, photo=_jpeg())

    identity = DeviceIdentity.objects.get(device_id="chip-aabbccddeeff")
    assert identity.status == DeviceIdentity.STATUS_ACTIVE

    cert = DeviceCertificate.objects.get(device=identity)
    assert cert.revoked_at is None

    enrollment = DeviceEnrollment.objects.get(device=identity)
    assert enrollment.status == DeviceEnrollment.STATUS_ISSUED
    assert enrollment.certificate == cert
    assert enrollment.enrollment_photo

    esp = ESP32Device.objects.get(mac_address="AA:BB:CC:DD:EE:FF")
    assert esp.identity == identity


def test_enroll_issues_cert_that_verifies_against_active_ca(
    api_client, enroll_url, active_ca, people_counter_type
):
    resp = _post_enroll(api_client, enroll_url)
    body = resp.json()
    issued = x509.load_pem_x509_certificate(body["client_certificate_pem"].encode("ascii"))
    ca_cert = x509.load_pem_x509_certificate(active_ca.cert_pem.encode("ascii"))
    ca_cert.public_key().verify(
        issued.signature,
        issued.tbs_certificate_bytes,
        ec.ECDSA(issued.signature_hash_algorithm),
    )


# ---------------------------------------------------------------------------
# Canonical <mac> topics + non-people-counter attribution (op-bej)
# ---------------------------------------------------------------------------


def _firmware_accepts_pings_topic(topic: str) -> bool:
    """Mirror ``isValidPingsTopic()`` in the ForgeKey firmware (src/main.cpp).

    The device clears its credentials when a stored, non-empty
    ``mqtt_topic_for_pings`` fails this check, so an enroll response must hand
    back either a topic this accepts or an empty string.
    """
    if not topic:
        # Empty is fine: the firmware keeps its own MAC-derived default.
        return True
    if not topic.startswith("forgekey/"):
        return False
    segs = topic.split("/")
    if len(segs) != 4:
        return False
    _prefix, mac, kind, leaf = segs
    if len(mac) != 12 or any(c not in "0123456789abcdef" for c in mac):
        return False
    if kind in ("people_counter", "door_counter"):
        return leaf == "occupancy"
    if kind == "temperature_sensor":
        return leaf == "reading"
    return False


def test_enroll_indicator_gets_canonical_topics_and_no_cred_wipe(api_client, enroll_url, active_ca):
    # The indicator DeviceType is seeded by migration 0004; create it
    # explicitly too (get-or-create) so the test holds under --no-migrations.
    DeviceTypeFactory(code=DeviceType.TYPE_INDICATOR)
    resp = _post_enroll(
        api_client,
        enroll_url,
        meta_overrides={
            "mac_address": "8C:BF:EA:8E:A4:0C",
            "unique_chip_id": "00000ca48eeabf8c",
            "sensor_kind": DeviceType.TYPE_INDICATOR,
        },
    )
    assert resp.status_code == 201, resp.content
    policy = resp.json()["policy"]

    # Command/status/firmware are the canonical MAC-keyed topics.
    assert policy["mqtt_topic_for_commands"] == "forgekey/8cbfea8ea40c/command"
    assert policy["mqtt_topic_for_status"] == "forgekey/8cbfea8ea40c/status"
    assert policy["mqtt_topic_for_firmware"] == "forgekey/8cbfea8ea40c/firmware"

    # Indicator is not a pings class: emit nothing so the firmware keeps its
    # default instead of wiping creds. The firmware contract check passes.
    assert policy["mqtt_topic_for_pings"] == ""
    assert _firmware_accepts_pings_topic(policy["mqtt_topic_for_pings"])

    # Attributes to the indicator DeviceType, not people-counter.
    device = ESP32Device.objects.get(mac_address="8C:BF:EA:8E:A4:0C")
    assert device.device_type.code == DeviceType.TYPE_INDICATOR


def test_enroll_people_counter_pings_topic_passes_firmware_contract(
    api_client, enroll_url, active_ca, people_counter_type
):
    resp = _post_enroll(api_client, enroll_url)
    assert resp.status_code == 201, resp.content
    policy = resp.json()["policy"]
    assert policy["mqtt_topic_for_pings"] == "forgekey/aabbccddeeff/people_counter/occupancy"
    assert _firmware_accepts_pings_topic(policy["mqtt_topic_for_pings"])


def test_enroll_temperature_sensor_pings_uses_reading_leaf(api_client, enroll_url, active_ca):
    DeviceTypeFactory(code=DeviceType.TYPE_TEMPERATURE_SENSOR)
    resp = _post_enroll(
        api_client,
        enroll_url,
        meta_overrides={"sensor_kind": DeviceType.TYPE_TEMPERATURE_SENSOR},
    )
    assert resp.status_code == 201, resp.content
    policy = resp.json()["policy"]
    # Temperature sensors publish to .../reading, not .../occupancy — the
    # firmware contract rejects the wrong leaf.
    assert policy["mqtt_topic_for_pings"] == "forgekey/aabbccddeeff/temperature_sensor/reading"
    assert _firmware_accepts_pings_topic(policy["mqtt_topic_for_pings"])


def test_enroll_derives_mac_from_chip_id_when_mac_absent(api_client, enroll_url, active_ca):
    DeviceTypeFactory(code=DeviceType.TYPE_INDICATOR)
    # No mac_address sent: the MAC is reconstructed from the eFuse chip id
    # (lower 6 bytes, byte-reversed) — 00000ca48eeabf8c -> 8cbfea8ea40c.
    _key, csr_pem = _build_csr("8cbfea8ea40c")
    meta = {
        "unique_chip_id": "00000ca48eeabf8c",
        "csr_pem": csr_pem,
        "firmware_version": "1.0.0",
        "sensor_kind": DeviceType.TYPE_INDICATOR,
    }
    resp = api_client.post(
        enroll_url,
        data={"metadata": json.dumps(meta)},
        HTTP_X_FORGEKEY_PROVISIONING_TOKEN=PROVISIONING_TOKEN,
        format="multipart",
    )
    assert resp.status_code == 201, resp.content
    policy = resp.json()["policy"]
    assert policy["mqtt_topic_for_commands"] == "forgekey/8cbfea8ea40c/command"
    assert policy["mqtt_topic_for_firmware"] == "forgekey/8cbfea8ea40c/firmware"
    # Derived MAC is persisted normalized so the device attributes correctly.
    device = ESP32Device.objects.get(mac_address="8C:BF:EA:8E:A4:0C")
    assert device.device_type.code == DeviceType.TYPE_INDICATOR


# ---------------------------------------------------------------------------
# Re-enrollment + decommissioned
# ---------------------------------------------------------------------------


def test_reenrollment_revokes_prior_certificate(
    api_client, enroll_url, active_ca, people_counter_type
):
    first = _post_enroll(api_client, enroll_url)
    assert first.status_code == 201, first.content
    second = _post_enroll(api_client, enroll_url)
    assert second.status_code == 201, second.content

    identity = DeviceIdentity.objects.get(device_id="chip-aabbccddeeff")
    revoked = DeviceCertificate.objects.filter(device=identity, revoked_at__isnull=False)
    valid = DeviceCertificate.objects.filter(device=identity, revoked_at__isnull=True)
    assert revoked.count() == 1
    assert valid.count() == 1


def test_decommissioned_identity_returns_403(
    api_client, enroll_url, active_ca, people_counter_type
):
    DeviceIdentity.objects.create(
        device_id="chip-aabbccddeeff",
        status=DeviceIdentity.STATUS_DECOMMISSIONED,
    )
    resp = _post_enroll(api_client, enroll_url)
    assert resp.status_code == 403
    assert resp.json()["code"] == "identity_decommissioned"


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


def test_enroll_missing_token_returns_401(api_client, enroll_url, active_ca, people_counter_type):
    resp = _post_enroll(api_client, enroll_url, token=None)
    assert resp.status_code == 401
    assert resp.json()["code"] == "token_missing"


def test_enroll_wrong_token_returns_401_with_diagnostics(
    api_client, enroll_url, active_ca, people_counter_type
):
    resp = _post_enroll(api_client, enroll_url, token="not-the-token")
    body = resp.json()
    assert resp.status_code == 401
    assert body["code"] == "token_mismatch"
    assert "expected_token_fingerprint" in body
    # The full secret must NEVER appear in the response.
    assert PROVISIONING_TOKEN not in resp.content.decode("utf-8")


def test_enroll_accepts_bootstrap_token_alias(
    api_client, enroll_url, active_ca, people_counter_type
):
    # Already-flashed devices send the enrollment token in the legacy
    # X-ForgeKey-Bootstrap-Token header; it is accepted as a back-compat alias
    # so they enroll without reflashing.
    resp = _post_enroll(
        api_client,
        enroll_url,
        photo=_jpeg(),
        token_header="HTTP_X_FORGEKEY_BOOTSTRAP_TOKEN",
    )
    assert resp.status_code == 201, resp.content
    assert resp.json()["device_id"] == "chip-aabbccddeeff"


def test_enroll_canonical_token_header_takes_precedence_over_alias(
    api_client, enroll_url, active_ca, people_counter_type
):
    # The canonical header is primary: when it carries a (wrong) value, the
    # legacy alias is NOT consulted as a fallback.
    resp = _post_enroll(
        api_client,
        enroll_url,
        token="not-the-token",
        extra_headers={"HTTP_X_FORGEKEY_BOOTSTRAP_TOKEN": PROVISIONING_TOKEN},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "token_mismatch"


def test_enroll_malformed_csr_returns_400(api_client, enroll_url, active_ca, people_counter_type):
    resp = _post_enroll(
        api_client,
        enroll_url,
        meta_overrides={"csr_pem": "not a csr"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "csr_invalid"


def test_enroll_missing_unique_chip_id_returns_400(
    api_client, enroll_url, active_ca, people_counter_type
):
    # Build meta WITHOUT unique_chip_id by overriding to empty.
    _key, csr = _build_csr()
    meta = {
        "mac_address": "AA:BB:CC:DD:EE:01",
        "csr_pem": csr,
        "firmware_version": "1.0.0",
        "sensor_kind": DeviceType.TYPE_PEOPLE_COUNTER,
    }
    resp = api_client.post(
        enroll_url,
        data={"metadata": json.dumps(meta)},
        HTTP_X_FORGEKEY_PROVISIONING_TOKEN=PROVISIONING_TOKEN,
        format="multipart",
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "unique_chip_id_missing"


def test_enroll_missing_csr_returns_400(api_client, enroll_url, active_ca, people_counter_type):
    meta = {
        "mac_address": "AA:BB:CC:DD:EE:02",
        "unique_chip_id": "chip-2",
        "firmware_version": "1.0.0",
    }
    resp = api_client.post(
        enroll_url,
        data={"metadata": json.dumps(meta)},
        HTTP_X_FORGEKEY_PROVISIONING_TOKEN=PROVISIONING_TOKEN,
        format="multipart",
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "csr_pem_missing"


def test_enroll_invalid_metadata_json_returns_400(
    api_client, enroll_url, active_ca, people_counter_type
):
    resp = api_client.post(
        enroll_url,
        data={"metadata": "{not json"},
        HTTP_X_FORGEKEY_PROVISIONING_TOKEN=PROVISIONING_TOKEN,
        format="multipart",
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "metadata_invalid_json"


def test_enroll_returns_503_when_no_ca_configured(api_client, enroll_url, people_counter_type):
    # No active_ca fixture — service is not bootstrapped.
    resp = _post_enroll(api_client, enroll_url)
    assert resp.status_code == 503
    assert resp.json()["code"] == "ca_unavailable"
