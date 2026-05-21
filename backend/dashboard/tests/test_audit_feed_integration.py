"""Cross-domain audit-feed integration tests (gh-459 / AC-26 / R-06).

The per-domain audit modules each have their own emission tests. The unified
feed has its own collector/filter tests with mocked collectors. What was
missing — and what this file proves — is the end-to-end contract:

1. A real row written via each domain's ``record_event`` helper actually
   surfaces through the unified ``collect_events`` + ``get_audit_feed``
   endpoint with the documented fields (who, when, what, entity reference).
2. Audit rows survive entity teardown. Every per-domain audit table uses
   SET_NULL on its entity FKs precisely so reviewers can answer "what
   happened to this device/vendor/donation" after the row is gone.
3. Secret values rotated through the audit hooks never appear in the
   feed response payload — this is the AC-27 / R-06 guardrail.
4. The unified-feed endpoint is staff-only for every domain.

These tests intentionally avoid factories that pull in ``InventoryItem`` /
``ItemSupplier`` (and their ``imagekit.ImageSpecField`` thumbnails) so they
stay independent of the Python 3.14 / pilkit pickle interaction that
breaks the heavier PO-create test.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from customization.audit import record_event as record_settings_audit
from customization.models import SiteSettings, SiteSettingsAuditEvent
from donations.audit import record_event as record_donation_audit
from donations.models import Donation, DonationAuditEvent
from forgekey.audit import record_event as record_forgekey_audit
from forgekey.models import AssetAuthorization, DeviceLockout, ForgeKeyAuditEvent, LockoutLevel
from forgekey.tests.factories import AssetAuthorizationFactory, DeviceLockoutFactory
from inventory.audit import record_event as record_maintenance_audit
from inventory.models import LocationProblem, MaintenanceAuditEvent
from inventory.tests.factories import LocationFactory
from maintenance_orders.models import ThirdPartyWorkOrder, ThirdPartyWorkOrderAuditLog
from reorder_queue.audit import record_event as record_po_audit
from reorder_queue.models import PurchaseOrder, PurchaseOrderAuditEvent, WebHook, WebhookAuditEvent
from reorder_queue.webhook_audit import record_event as record_webhook_audit
from vendors.audit import record_event as record_vendor_audit
from vendors.models import Vendor, VendorAuditEvent

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def actor():
    return User.objects.create_user(
        username="audit-actor",
        email="audit-actor@example.com",
        password=get_random_string(24),
    )


@pytest.fixture
def staff_admin():
    return User.objects.create_user(
        username="audit-admin",
        email="audit-admin@example.com",
        password=get_random_string(24),
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def admin_client(staff_admin):
    client = APIClient()
    client.force_authenticate(user=staff_admin)
    return client


def _make_vendor(**overrides) -> Vendor:
    defaults = {
        "name": "Audit Vendor",
        "vendor_kind": Vendor.KIND_HVAC,
        "is_active": True,
    }
    defaults.update(overrides)
    return Vendor.objects.create(**defaults)


def _make_webhook(**overrides) -> WebHook:
    defaults = {
        "name": "Audit Webhook",
        "url": "https://example.com/audit-webhook",
        "event_type": WebHook.REORDER_REQUEST_CREATED,
        "is_active": True,
        "secret": "initial-shared-secret",
    }
    defaults.update(overrides)
    return WebHook.objects.create(**defaults)


def _seed_one_event_per_domain(actor) -> dict:
    """Write one audit event in every domain the unified feed knows about.

    Returns a mapping of domain string -> the audit row that was created so
    individual tests can assert on the specific row.
    """
    # forgekey: an authorization grant
    authorization = AssetAuthorizationFactory(user=actor, is_active=True)
    forgekey_event = record_forgekey_audit(
        action=ForgeKeyAuditEvent.ACTION_AUTHORIZATION_GRANT,
        actor=actor,
        authorization=authorization,
        notes="initial grant",
    )

    # purchase_orders: emit a PO_VOID against a draft PO. The PO model
    # requires a supplier, but the Supplier factory has no image fields so
    # it doesn't trigger the imagekit pickle path.
    from inventory.tests.factories import SupplierFactory

    supplier = SupplierFactory()
    purchase_order = PurchaseOrder.objects.create(
        supplier=supplier,
        status=PurchaseOrder.VOIDED,
        created_by=actor,
    )
    po_event = record_po_audit(
        action=PurchaseOrderAuditEvent.ACTION_PO_VOID,
        actor=actor,
        purchase_order=purchase_order,
        notes="audit feed integration test",
    )

    # webhooks: emit a config update with a known diff so the metadata
    # carries before/after values we can assert against later.
    webhook = _make_webhook(name="Before Name")
    webhook_event = record_webhook_audit(
        action=WebhookAuditEvent.ACTION_WEBHOOK_UPDATE,
        actor=actor,
        webhook=webhook,
        metadata={
            "changes": {
                "name": {"before": "Before Name", "after": "After Name"},
            }
        },
    )

    # donations: a donation create event
    donation = Donation.objects.create(
        donor_name="Audit Donor",
        date_received=timezone.now().date(),
        status=Donation.PENDING,
    )
    donation_event = record_donation_audit(
        action=DonationAuditEvent.ACTION_DONATION_CREATE,
        actor=actor,
        donation=donation,
        metadata={"donor_name": donation.donor_name},
    )

    # maintenance: location problem resolution
    location = LocationFactory()
    problem = LocationProblem.objects.create(
        location=location,
        description="Audit feed integration problem",
        status=LocationProblem.REPORTED,
    )
    maintenance_event = record_maintenance_audit(
        action=MaintenanceAuditEvent.ACTION_LOCATION_PROBLEM_RESOLVE,
        actor=actor,
        location_problem=problem,
        notes="resolved",
    )

    # vendors: vendor lifecycle
    vendor = _make_vendor()
    vendor_event = record_vendor_audit(
        action=VendorAuditEvent.ACTION_VENDOR_CREATE,
        actor=actor,
        vendor=vendor,
        metadata={"name": vendor.name, "vendor_kind": vendor.vendor_kind},
    )

    # customization: settings update with a diff
    site_settings = SiteSettings.get()
    settings_event = record_settings_audit(
        action=SiteSettingsAuditEvent.ACTION_SETTINGS_UPDATE,
        actor=actor,
        site_settings=site_settings,
        metadata={
            "changes": {
                "site_name": {"before": "old", "after": "new"},
            }
        },
    )

    # third_party_work_orders: emit a manual audit row against a vendor +
    # WO. Vendor and ThirdPartyWorkOrder have no image fields, so this stays
    # cheap and avoids the imagekit pickle path.
    tp_wo = ThirdPartyWorkOrder.objects.create(
        title="Audit feed integration WO",
        vendor=vendor,
        status=ThirdPartyWorkOrder.STATUS_REQUESTED,
    )
    tp_event = ThirdPartyWorkOrderAuditLog.objects.create(
        work_order=tp_wo,
        actor=actor,
        action=ThirdPartyWorkOrderAuditLog.ACTION_TRANSITION,
        old_state=ThirdPartyWorkOrder.STATUS_REQUESTED,
        new_state=ThirdPartyWorkOrder.STATUS_SOURCING,
    )

    return {
        "forgekey": forgekey_event,
        "purchase_orders": po_event,
        "webhooks": webhook_event,
        "donations": donation_event,
        "maintenance": maintenance_event,
        "vendors": vendor_event,
        "customization": settings_event,
        "third_party_work_orders": tp_event,
        # Side handles for survival/redaction tests
        "_authorization": authorization,
        "_purchase_order": purchase_order,
        "_webhook": webhook,
        "_donation": donation,
        "_problem": problem,
        "_vendor": vendor,
        "_site_settings": site_settings,
        "_tp_wo": tp_wo,
    }


class TestUnifiedFeedSurfacesEveryDomain:
    """AC-26: 'Unified or staff-facing review surfaces include the relevant
    domain events.' Every per-domain table the audit_feed knows about must
    surface a real event end-to-end."""

    def test_every_domain_appears_in_feed_with_required_fields(self, admin_client, actor):
        _seed_one_event_per_domain(actor)

        response = admin_client.get(reverse("get_audit_feed"))

        assert response.status_code == status.HTTP_200_OK
        events_by_domain: dict[str, list] = {}
        for event in response.data["events"]:
            events_by_domain.setdefault(event["domain"], []).append(event)

        expected_domains = {
            "forgekey",
            "purchase_orders",
            "webhooks",
            "donations",
            "maintenance",
            "vendors",
            "customization",
            "third_party_work_orders",
        }
        assert expected_domains.issubset(
            events_by_domain.keys()
        ), f"Missing domains in feed: {expected_domains - set(events_by_domain.keys())}"

        # Every row must answer who/when/what — these are the AC-26
        # bare-minimum review fields. ``actor_username`` is allowed to be
        # None for system-initiated rows, but the actor_id should be set
        # for every row we just emitted from a real user.
        for domain in expected_domains:
            rows = events_by_domain[domain]
            assert rows, f"{domain} produced no rows"
            row = rows[0]
            assert row["action"], f"{domain} row missing action"
            assert row["created_at"], f"{domain} row missing created_at"
            assert row["actor_id"] == actor.id, f"{domain} row lost actor"
            assert row["actor_username"] == actor.username, f"{domain} row lost actor_username"

    def test_feed_filters_by_domain_against_real_rows(self, admin_client, actor):
        _seed_one_event_per_domain(actor)

        response = admin_client.get(reverse("get_audit_feed"), {"domain": "webhooks"})

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] >= 1
        assert all(event["domain"] == "webhooks" for event in response.data["events"])

    def test_feed_filters_by_actor_against_real_rows(self, admin_client, actor):
        _seed_one_event_per_domain(actor)
        other = User.objects.create_user(
            username="other-actor",
            email="other@example.com",
            password=get_random_string(24),
        )
        # Emit one event under a different actor that should NOT appear when
        # the feed is filtered to ``actor``.
        other_vendor = _make_vendor(name="Other vendor")
        record_vendor_audit(
            action=VendorAuditEvent.ACTION_VENDOR_CREATE,
            actor=other,
            vendor=other_vendor,
        )

        response = admin_client.get(reverse("get_audit_feed"), {"actor_id": str(actor.id)})

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] >= 1
        assert all(event["actor_id"] == actor.id for event in response.data["events"])


class TestAuditRowSurvivesEntityDeletion:
    """AC-26: 'Verify audit rows survive entity deletion where required by
    business rules.' Every per-domain audit table uses SET_NULL so the row
    persists; the FK simply nulls out. Note that ``ThirdPartyWorkOrder``
    intentionally cascades — see test below for the explicit accepted gap."""

    def test_forgekey_audit_survives_authorization_delete(self, actor):
        authorization = AssetAuthorizationFactory(user=actor, is_active=True)
        event = record_forgekey_audit(
            action=ForgeKeyAuditEvent.ACTION_AUTHORIZATION_REVOKE,
            actor=actor,
            authorization=authorization,
        )
        authorization_pk = authorization.pk

        authorization.delete()

        event.refresh_from_db()
        assert event.authorization_id is None
        assert event.action == ForgeKeyAuditEvent.ACTION_AUTHORIZATION_REVOKE
        assert event.actor_id == actor.id
        assert not AssetAuthorization.objects.filter(pk=authorization_pk).exists()

    def test_forgekey_audit_survives_lockout_delete(self, actor):
        lockout = DeviceLockoutFactory(locked_by=actor, lockout_level=LockoutLevel.USER)
        event = record_forgekey_audit(
            action=ForgeKeyAuditEvent.ACTION_LOCKOUT_UNLOCK,
            actor=actor,
            lockout=lockout,
        )

        lockout.delete()
        event.refresh_from_db()

        assert event.lockout_id is None
        assert event.actor_id == actor.id
        assert not DeviceLockout.objects.filter(pk=lockout.pk).exists()

    def test_purchase_order_audit_survives_po_delete(self, actor):
        from inventory.tests.factories import SupplierFactory

        purchase_order = PurchaseOrder.objects.create(
            supplier=SupplierFactory(),
            status=PurchaseOrder.VOIDED,
            created_by=actor,
        )
        event = record_po_audit(
            action=PurchaseOrderAuditEvent.ACTION_PO_VOID,
            actor=actor,
            purchase_order=purchase_order,
        )

        purchase_order.delete()
        event.refresh_from_db()

        assert event.purchase_order_id is None
        assert event.actor_id == actor.id

    def test_webhook_audit_survives_webhook_delete(self, actor):
        webhook = _make_webhook()
        event = record_webhook_audit(
            action=WebhookAuditEvent.ACTION_WEBHOOK_DELETE,
            actor=actor,
            webhook=webhook,
            metadata={
                "name": webhook.name,
                "url": webhook.url,
                "event_type": webhook.event_type,
            },
        )

        webhook.delete()
        event.refresh_from_db()

        assert event.webhook_id is None
        # Metadata stays — that's what staff review reads after the row is gone.
        assert event.metadata["name"] == "Audit Webhook"

    def test_donation_audit_survives_donation_delete(self, actor):
        donation = Donation.objects.create(
            donor_name="Survival Donor",
            date_received=timezone.now().date(),
            status=Donation.PENDING,
        )
        event = record_donation_audit(
            action=DonationAuditEvent.ACTION_DONATION_CREATE,
            actor=actor,
            donation=donation,
            metadata={"donor_name": donation.donor_name},
        )

        donation.delete()
        event.refresh_from_db()

        assert event.donation_id is None
        assert event.metadata["donor_name"] == "Survival Donor"

    def test_maintenance_audit_survives_location_problem_delete(self, actor):
        problem = LocationProblem.objects.create(
            location=LocationFactory(),
            description="Survival problem",
            status=LocationProblem.REPORTED,
        )
        event = record_maintenance_audit(
            action=MaintenanceAuditEvent.ACTION_LOCATION_PROBLEM_RESOLVE,
            actor=actor,
            location_problem=problem,
        )

        problem.delete()
        event.refresh_from_db()

        assert event.location_problem_id is None
        assert event.actor_id == actor.id

    def test_vendor_audit_survives_vendor_delete(self, actor):
        vendor = _make_vendor()
        event = record_vendor_audit(
            action=VendorAuditEvent.ACTION_VENDOR_DEACTIVATE,
            actor=actor,
            vendor=vendor,
        )

        vendor.delete()
        event.refresh_from_db()

        assert event.vendor_id is None
        assert event.actor_id == actor.id

    def test_settings_audit_survives_settings_delete(self, actor):
        # The SiteSettings singleton is rarely deleted in practice, but the
        # FK SET_NULL is what the model contract documents. Force the
        # deletion to prove the row survives. Skip the singleton .save()
        # validation by using a queryset-level delete.
        settings_obj = SiteSettings.get()
        event = record_settings_audit(
            action=SiteSettingsAuditEvent.ACTION_SETTINGS_UPDATE,
            actor=actor,
            site_settings=settings_obj,
            metadata={"changes": {"site_name": {"before": "a", "after": "b"}}},
        )

        SiteSettings.objects.filter(pk=settings_obj.pk).delete()
        event.refresh_from_db()

        assert event.site_settings_id is None
        # Diff metadata is preserved for review even with no settings row.
        assert event.metadata["changes"]["site_name"]["after"] == "b"

    def test_third_party_wo_audit_is_documented_as_cascade(self, actor):
        """ThirdPartyWorkOrderAuditLog cascades with its work_order — that
        is an intentional Phase-5 design choice (the WO row itself is the
        permanent record). Application code never deletes a WO, but if the
        WO ever IS deleted, the audit log goes with it. This test pins the
        current behavior so a future change to SET_NULL is a conscious
        decision rather than an accidental contract shift."""
        vendor = _make_vendor()
        wo = ThirdPartyWorkOrder.objects.create(
            title="Cascade WO",
            vendor=vendor,
            status=ThirdPartyWorkOrder.STATUS_REQUESTED,
        )
        event = ThirdPartyWorkOrderAuditLog.objects.create(
            work_order=wo,
            actor=actor,
            action=ThirdPartyWorkOrderAuditLog.ACTION_TRANSITION,
        )

        wo.delete()

        assert not ThirdPartyWorkOrderAuditLog.objects.filter(pk=event.pk).exists()


class TestAuditFeedRedactsSecrets:
    """AC-26 + AC-27 + R-06: 'Secret and sensitive fields are redacted in
    audit output.' Webhook secrets and SiteSettings file binaries are the
    two known sensitive fields routed through audit; the feed response must
    surface neither."""

    def test_feed_response_never_includes_webhook_secret(self, admin_client, actor):
        webhook = _make_webhook(secret="old-secret-value")
        # Simulate a rotate (the view path is exercised in
        # ``test_webhook_audit_events.test_webhook_secret_rotate_emits_distinct_event``;
        # here we mirror it via the helper so the assertion is about what
        # gets stored, independent of the view).
        record_webhook_audit(
            action=WebhookAuditEvent.ACTION_WEBHOOK_SECRET_ROTATE,
            actor=actor,
            webhook=webhook,
        )
        # Force a config update that would have leaked secret if the diff
        # helper had not excluded it.
        record_webhook_audit(
            action=WebhookAuditEvent.ACTION_WEBHOOK_UPDATE,
            actor=actor,
            webhook=webhook,
            metadata={"changes": {"name": {"before": "x", "after": "y"}}},
        )

        response = admin_client.get(reverse("get_audit_feed"), {"domain": "webhooks"})

        assert response.status_code == status.HTTP_200_OK
        body = response.content.decode()
        assert "old-secret-value" not in body
        assert "initial-shared-secret" not in body
        # The rotate row should be present so reviewers can see the rotation
        # happened, just without the value.
        actions = {event["action"] for event in response.data["events"]}
        assert WebhookAuditEvent.ACTION_WEBHOOK_SECRET_ROTATE in actions

    def test_feed_response_never_includes_logo_or_favicon_binary(self, admin_client, actor):
        """The settings audit explicitly summarizes file fields by filename
        only. Prove the feed payload never carries a path that looks like a
        raw upload directory binary leak."""
        site_settings = SiteSettings.get()
        record_settings_audit(
            action=SiteSettingsAuditEvent.ACTION_SETTINGS_UPDATE,
            actor=actor,
            site_settings=site_settings,
            metadata={
                "changes": {
                    # Only filenames — the audit helper enforces this.
                    "logo": {
                        "before": None,
                        "after": "customization/logos/new-logo.png",
                    },
                }
            },
        )

        response = admin_client.get(reverse("get_audit_feed"), {"domain": "customization"})

        assert response.status_code == status.HTTP_200_OK
        events = response.data["events"]
        assert events, "settings audit row missing from feed"
        change = events[0]["metadata"]["changes"]["logo"]
        # The contract: before/after carry filenames (or None), never raw
        # bytes, never base64, never a data: URL.
        for value in (change["before"], change["after"]):
            if value is not None:
                assert isinstance(value, str)
                assert not value.startswith("data:")
                assert "base64" not in value


class TestAuditFeedEndpointGatedToStaff:
    """AC-26: 'Unauthorized users cannot access audit data.' The existing
    test_audit_feed.py covers the negative paths against an empty DB; this
    test re-confirms the gate after we've actually seeded events so we
    aren't accidentally permissive on the populated path."""

    def test_anonymous_cannot_read_seeded_feed(self, api_client, actor):
        _seed_one_event_per_domain(actor)

        response = api_client.get(reverse("get_audit_feed"))

        assert response.status_code in {
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        }

    def test_authenticated_non_staff_cannot_read_seeded_feed(self, actor):
        _seed_one_event_per_domain(actor)
        regular = User.objects.create_user(
            username="not-staff",
            email="not-staff@example.com",
            password=get_random_string(24),
            is_staff=False,
        )
        client = APIClient()
        client.force_authenticate(user=regular)

        response = client.get(reverse("get_audit_feed"))

        assert response.status_code == status.HTTP_403_FORBIDDEN
