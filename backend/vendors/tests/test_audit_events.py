from datetime import date

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from vendors.audit import diff_compliance_fields, record_event
from vendors.models import Vendor, VendorAuditEvent

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def admin_user():
    return User.objects.create_user(
        username="vendor-audit-admin",
        email="vendor-audit-admin@example.com",
        password=get_random_string(24),
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def admin_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


def test_vendor_create_records_audit_event(admin_client, admin_user):
    response = admin_client.post(
        reverse("vendor-list"),
        {"name": "Acme", "vendor_kind": Vendor.KIND_HVAC},
        format="json",
    )

    assert response.status_code == 201, response.content

    vendor = Vendor.objects.get(id=response.json()["id"])
    event = VendorAuditEvent.objects.get(action=VendorAuditEvent.ACTION_VENDOR_CREATE)
    assert event.vendor == vendor
    assert event.actor == admin_user
    assert event.metadata["name"] == "Acme"
    assert event.metadata["vendor_kind"] == Vendor.KIND_HVAC


def test_compliance_update_records_diff(admin_client, admin_user):
    vendor = Vendor.objects.create(
        name="Acme",
        vendor_kind=Vendor.KIND_HVAC,
        tdlr_license_number="TX-100",
        tdlr_license_expires_at=date(2026, 1, 15),
    )

    response = admin_client.patch(
        reverse("vendor-detail", args=[vendor.id]),
        {
            "tdlr_license_number": "TX-200",
            "tdlr_license_expires_at": "2026-02-20",
        },
        format="json",
    )

    assert response.status_code == 200, response.content

    event = VendorAuditEvent.objects.get(action=VendorAuditEvent.ACTION_COMPLIANCE_UPDATE)
    changes = event.metadata["changes"]
    assert event.vendor == vendor
    assert event.actor == admin_user
    assert changes["tdlr_license_number"] == {"before": "TX-100", "after": "TX-200"}
    assert changes["tdlr_license_expires_at"] == {
        "before": "2026-01-15",
        "after": "2026-02-20",
    }


def test_compliance_update_no_event_when_unchanged(admin_client):
    vendor = Vendor.objects.create(
        name="Acme",
        vendor_kind=Vendor.KIND_HVAC,
        tdlr_license_number="TX-100",
    )

    response = admin_client.patch(
        reverse("vendor-detail", args=[vendor.id]),
        {"phone": "555-0100"},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert not VendorAuditEvent.objects.filter(
        vendor=vendor,
        action=VendorAuditEvent.ACTION_COMPLIANCE_UPDATE,
    ).exists()


def test_deactivate_records_audit_event(admin_client, admin_user):
    vendor = Vendor.objects.create(
        name="Acme",
        vendor_kind=Vendor.KIND_HVAC,
        is_active=True,
    )

    response = admin_client.patch(
        reverse("vendor-detail", args=[vendor.id]),
        {"is_active": False},
        format="json",
    )

    assert response.status_code == 200, response.content

    event = VendorAuditEvent.objects.get(action=VendorAuditEvent.ACTION_VENDOR_DEACTIVATE)
    assert event.vendor == vendor
    assert event.actor == admin_user


def test_reactivate_records_audit_event(admin_client, admin_user):
    vendor = Vendor.objects.create(
        name="Acme",
        vendor_kind=Vendor.KIND_HVAC,
        is_active=False,
    )

    response = admin_client.patch(
        reverse("vendor-detail", args=[vendor.id]),
        {"is_active": True},
        format="json",
    )

    assert response.status_code == 200, response.content

    event = VendorAuditEvent.objects.get(action=VendorAuditEvent.ACTION_VENDOR_REACTIVATE)
    assert event.vendor == vendor
    assert event.actor == admin_user


def test_active_unchanged_no_lifecycle_event(admin_client):
    vendor = Vendor.objects.create(
        name="Acme",
        vendor_kind=Vendor.KIND_HVAC,
        is_active=True,
    )

    response = admin_client.patch(
        reverse("vendor-detail", args=[vendor.id]),
        {"phone": "555-0101"},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert not VendorAuditEvent.objects.filter(
        vendor=vendor,
        action__in=[
            VendorAuditEvent.ACTION_VENDOR_DEACTIVATE,
            VendorAuditEvent.ACTION_VENDOR_REACTIVATE,
        ],
    ).exists()


def test_combined_compliance_and_lifecycle_emit_both(admin_client, admin_user):
    vendor = Vendor.objects.create(
        name="Acme",
        vendor_kind=Vendor.KIND_HVAC,
        is_active=True,
        coi_provider="Old Carrier",
    )

    response = admin_client.patch(
        reverse("vendor-detail", args=[vendor.id]),
        {"coi_provider": "New Carrier", "is_active": False},
        format="json",
    )

    assert response.status_code == 200, response.content

    events = list(VendorAuditEvent.objects.filter(vendor=vendor).order_by("created_at"))
    assert len(events) == 2
    assert {event.action for event in events} == {
        VendorAuditEvent.ACTION_COMPLIANCE_UPDATE,
        VendorAuditEvent.ACTION_VENDOR_DEACTIVATE,
    }
    assert {event.actor_id for event in events} == {admin_user.id}


def test_diff_compliance_fields_returns_empty_when_unchanged():
    vendor = Vendor(
        name="Acme",
        vendor_kind=Vendor.KIND_HVAC,
        tdlr_license_number="TX-100",
        tdlr_license_expires_at=date(2026, 1, 15),
        coi_provider="Carrier",
        coi_policy_number="POL-1",
        coi_expires_at=date(2026, 3, 1),
    )

    assert diff_compliance_fields(vendor, vendor) == {}


def test_diff_compliance_fields_serializes_dates_iso():
    before = Vendor(
        name="Acme",
        vendor_kind=Vendor.KIND_HVAC,
        coi_expires_at=date(2026, 4, 1),
    )
    after = Vendor(
        name="Acme",
        vendor_kind=Vendor.KIND_HVAC,
        coi_expires_at=date(2026, 5, 1),
    )

    changes = diff_compliance_fields(before, after)

    assert changes["coi_expires_at"]["before"] == "2026-04-01"
    assert changes["coi_expires_at"]["after"] == "2026-05-01"
    assert not isinstance(changes["coi_expires_at"]["before"], date)
    assert not isinstance(changes["coi_expires_at"]["after"], date)


def test_record_event_with_anonymous_actor_creates_row_with_null_actor():
    vendor = Vendor.objects.create(name="Acme", vendor_kind=Vendor.KIND_HVAC)

    event = record_event(
        action=VendorAuditEvent.ACTION_VENDOR_CREATE,
        vendor=vendor,
        actor=None,
    )

    assert event.actor is None
    assert event.vendor == vendor
