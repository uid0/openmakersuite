"""Actor-identity coverage for FixtureRefillRequest (#888, AC-2/AC-3/AC-4).

The flagship migration of the ``membership.actor`` convention. This model had
ZERO tests before this file. Covers, one block per AC clause:

* the ``actor_display`` helper for authenticated / anonymous / system actors;
* anon scan + anon create → ``requested_user`` null, ``requested_by`` name kept;
* authenticated scan / create → ``requested_user`` set, name = handle/username;
* authenticated resolve + resolve_all → ``resolved_user`` / ``resolved_by`` set;
* the additive, read-only serializer output (``*_actor`` / ``*_username`` +
  the preserved legacy ``requested_by`` / ``resolved_by`` strings);
* the anon scan/create paths stay AllowAny while resolve stays auth-only;
* the data-migration backfill (0089): legacy string → FK on a unique username /
  handle match, null otherwise, reversibly.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

import pytest
from rest_framework.test import APIClient

from inventory.models import Fixture, FixtureRefillRequest, InventoryItem, Location
from inventory.serializers import FixtureRefillRequestSerializer
from membership.actor import ANONYMOUS_ACTOR, SYSTEM_ACTOR, actor_display
from membership.tests.factories import UserFactory

User = get_user_model()


@pytest.fixture
def fixture(db):
    """A minimal active Fixture (Location + InventoryItem chain, built by hand
    to avoid factory image/supplier side effects)."""
    location = Location.objects.create(name="Bathroom 1")
    item = InventoryItem.objects.create(
        name="Hand Soap", description="Refill soap", reorder_quantity=10
    )
    return Fixture.objects.create(name="Soap Dispenser", location=location, refill_item=item)


SCAN_URL = "/api/inventory/fixtures/{}/scan/"
RESOLVE_ALL_URL = "/api/inventory/fixtures/{}/resolve_all/"
CREATE_URL = "/api/inventory/fixture-refill-requests/"
RESOLVE_URL = "/api/inventory/fixture-refill-requests/{}/resolve/"


# --------------------------------------------------------------------------- #
# actor_display helper (AC-1 behaviour, exercised as a unit)                   #
# --------------------------------------------------------------------------- #
class TestActorDisplay:
    def test_authenticated_prefers_handle(self, db):
        user = UserFactory(username="alice", handle="AliceHandle")
        assert actor_display(user, "ignored-name") == "AliceHandle"

    def test_authenticated_falls_back_to_username(self, db):
        user = UserFactory(username="bob", handle=None)
        assert actor_display(user, "ignored-name") == "bob"

    def test_anonymous_uses_supplied_name(self):
        assert actor_display(None, "Walk-in Guest") == "Walk-in Guest"

    def test_anonymous_without_name_is_anonymous(self):
        assert actor_display(None, "") == ANONYMOUS_ACTOR
        assert actor_display(None, None) == ANONYMOUS_ACTOR

    def test_system_actor_uses_fixed_label(self):
        # System action: null user + the conventional SYSTEM_ACTOR name.
        assert actor_display(None, SYSTEM_ACTOR) == "System"

    def test_unauthenticated_user_is_treated_as_anonymous(self):
        # A stray AnonymousUser must not raise (no .handle) — falls to name.
        assert actor_display(AnonymousUser(), "Walk-in Guest") == "Walk-in Guest"


# --------------------------------------------------------------------------- #
# scan write site (FixtureViewSet.scan, AllowAny)                             #
# --------------------------------------------------------------------------- #
class TestScan:
    def test_anon_scan_leaves_user_null_and_name_blank(self, fixture):
        resp = APIClient().post(SCAN_URL.format(fixture.id), {}, format="json")
        assert resp.status_code == 201

        req = FixtureRefillRequest.objects.get(id=resp.data["id"])
        assert req.requested_user is None
        assert req.requested_by == ""
        # Serializer collapses the null pair to "Anonymous".
        assert resp.data["requested_actor"] == ANONYMOUS_ACTOR
        assert resp.data["requested_username"] is None

    def test_auth_scan_sets_user_and_handle(self, fixture):
        user = UserFactory(username="scanner", handle="ScanHandle")
        client = APIClient()
        client.force_authenticate(user=user)

        resp = client.post(SCAN_URL.format(fixture.id), {}, format="json")
        assert resp.status_code == 201

        req = FixtureRefillRequest.objects.get(id=resp.data["id"])
        assert req.requested_user == user
        assert req.requested_by == "ScanHandle"
        assert resp.data["requested_actor"] == "ScanHandle"
        assert resp.data["requested_username"] == "scanner"

    def test_auth_scan_falls_back_to_username(self, fixture):
        user = UserFactory(username="nohandle", handle=None)
        client = APIClient()
        client.force_authenticate(user=user)

        resp = client.post(SCAN_URL.format(fixture.id), {}, format="json")

        req = FixtureRefillRequest.objects.get(id=resp.data["id"])
        assert req.requested_user == user
        assert req.requested_by == "nohandle"
        assert resp.data["requested_actor"] == "nohandle"


# --------------------------------------------------------------------------- #
# create write site (FixtureRefillRequestViewSet.perform_create, AllowAny)    #
# --------------------------------------------------------------------------- #
class TestCreate:
    def test_anon_create_preserves_supplied_name(self, fixture):
        resp = APIClient().post(
            CREATE_URL,
            {"fixture": str(fixture.id), "requested_by": "Walk-in Bob"},
            format="json",
        )
        assert resp.status_code == 201

        req = FixtureRefillRequest.objects.get(id=resp.data["id"])
        assert req.requested_user is None
        assert req.requested_by == "Walk-in Bob"
        assert resp.data["requested_actor"] == "Walk-in Bob"
        assert resp.data["requested_username"] is None

    def test_auth_create_sets_user_and_overrides_supplied_name(self, fixture):
        user = UserFactory(username="creator", handle="CreatorHandle")
        client = APIClient()
        client.force_authenticate(user=user)

        # A body-supplied requested_by is ignored for an authenticated actor —
        # the field is read-only and perform_create stamps handle/username.
        resp = client.post(
            CREATE_URL,
            {"fixture": str(fixture.id), "requested_by": "spoofed"},
            format="json",
        )
        assert resp.status_code == 201

        req = FixtureRefillRequest.objects.get(id=resp.data["id"])
        assert req.requested_user == user
        assert req.requested_by == "CreatorHandle"
        assert resp.data["requested_username"] == "creator"


# --------------------------------------------------------------------------- #
# resolve write sites (single + bulk, auth-only)                              #
# --------------------------------------------------------------------------- #
class TestResolve:
    def test_auth_resolve_sets_resolved_user_and_name(self, fixture):
        req = FixtureRefillRequest.objects.create(fixture=fixture)  # anon request
        resolver = UserFactory(username="fixer", handle="FixerHandle")
        client = APIClient()
        client.force_authenticate(user=resolver)

        resp = client.post(RESOLVE_URL.format(req.id), {}, format="json")
        assert resp.status_code == 200

        req.refresh_from_db()
        assert req.status == FixtureRefillRequest.Status.COMPLETED
        assert req.resolved_user == resolver
        assert req.resolved_by == "FixerHandle"
        assert resp.data["resolved_actor"] == "FixerHandle"
        assert resp.data["resolved_username"] == "fixer"
        # Requester side is still anonymous — the two roles are independent.
        assert resp.data["requested_actor"] == ANONYMOUS_ACTOR

    def test_auth_resolve_all_sets_resolved_user_on_every_pending(self, fixture):
        FixtureRefillRequest.objects.create(fixture=fixture)
        FixtureRefillRequest.objects.create(fixture=fixture)
        resolver = UserFactory(username="bulk", handle="BulkHandle")
        client = APIClient()
        client.force_authenticate(user=resolver)

        resp = client.post(RESOLVE_ALL_URL.format(fixture.id), {}, format="json")
        assert resp.status_code == 200

        requests = FixtureRefillRequest.objects.filter(fixture=fixture)
        assert requests.count() == 2
        for req in requests:
            assert req.status == FixtureRefillRequest.Status.COMPLETED
            assert req.resolved_user == resolver
            assert req.resolved_by == "BulkHandle"


# --------------------------------------------------------------------------- #
# serializer shape (additive, read-only)                                       #
# --------------------------------------------------------------------------- #
class TestSerializer:
    def test_resolved_actor_none_while_pending(self, fixture):
        req = FixtureRefillRequest.objects.create(fixture=fixture, requested_by="Guest")
        data = FixtureRefillRequestSerializer(req).data

        assert data["requested_actor"] == "Guest"
        assert data["resolved_actor"] is None
        assert data["resolved_username"] is None
        # Legacy string fields are still present (frontend depends on them).
        assert data["requested_by"] == "Guest"
        assert data["resolved_by"] == ""

    def test_legacy_string_fields_are_read_only(self):
        serializer = FixtureRefillRequestSerializer()
        for field in ("requested_by", "resolved_by"):
            assert serializer.fields[field].read_only is True
        # New actor fields exist and are additive.
        for field in (
            "requested_actor",
            "resolved_actor",
            "requested_username",
            "resolved_username",
        ):
            assert field in serializer.fields


# --------------------------------------------------------------------------- #
# permission guarantees — anon stays AllowAny, resolve stays auth-only         #
# --------------------------------------------------------------------------- #
class TestPermissions:
    def test_anon_scan_and_create_stay_allowany(self, fixture):
        client = APIClient()  # unauthenticated
        assert client.post(SCAN_URL.format(fixture.id), {}, format="json").status_code == 201
        assert (
            client.post(CREATE_URL, {"fixture": str(fixture.id)}, format="json").status_code == 201
        )

    def test_anon_resolve_is_rejected_and_leaves_request_pending(self, fixture):
        req = FixtureRefillRequest.objects.create(fixture=fixture)

        resp = APIClient().post(RESOLVE_URL.format(req.id), {}, format="json")
        assert resp.status_code in (401, 403)

        req.refresh_from_db()
        assert req.resolved_user is None
        assert req.status == FixtureRefillRequest.Status.PENDING


# --------------------------------------------------------------------------- #
# data-migration backfill (0089) — MigrationExecutor, modelled on             #
# facilities/tests/test_migration.py                                          #
# --------------------------------------------------------------------------- #
START = [("inventory", "0088_actor_identity_fixture_refill")]
END = [("inventory", "0089_backfill_fixture_refill_actor")]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    executor.loader.build_graph()
    return executor


@pytest.mark.django_db(transaction=True)
def test_data_migration_backfills_actor_fks_reversibly():
    executor = _migrate(START)
    old_apps = executor.loader.project_state(START).apps
    try:
        HUser = old_apps.get_model("membership", "User")
        HLocation = old_apps.get_model("inventory", "Location")
        HItem = old_apps.get_model("inventory", "InventoryItem")
        HFixture = old_apps.get_model("inventory", "Fixture")
        HFRR = old_apps.get_model("inventory", "FixtureRefillRequest")

        alice = HUser.objects.create(username="alice")
        bob = HUser.objects.create(username="bob_user", handle="bob")
        # Precedence probe: someone else OWNS the handle "alice"; a username
        # match must still win over a handle match for the string "alice".
        HUser.objects.create(username="yuser", handle="alice")

        loc = HLocation.objects.create(name="Loc")
        item = HItem.objects.create(name="Soap", description="d", reorder_quantity=1)
        fix = HFixture.objects.create(name="F", location=loc, refill_item=item)

        by_username = HFRR.objects.create(fixture=fix, requested_by="alice")
        by_handle = HFRR.objects.create(fixture=fix, requested_by="bob")
        resolved = HFRR.objects.create(fixture=fix, requested_by="", resolved_by="alice")
        no_match = HFRR.objects.create(fixture=fix, requested_by="ghost", resolved_by="phantom")
        empty = HFRR.objects.create(fixture=fix, requested_by="", resolved_by="")

        # --- forward: backfill ------------------------------------------------
        executor2 = _migrate(END)
        FRRNew = executor2.loader.project_state(END).apps.get_model(
            "inventory", "FixtureRefillRequest"
        )

        # matched by username (and username wins over the handle="alice" owner)
        assert FRRNew.objects.get(id=by_username.id).requested_user_id == alice.id
        # matched by handle fallback
        assert FRRNew.objects.get(id=by_handle.id).requested_user_id == bob.id
        # resolved role matched independently by username; requester stays null
        resolved_row = FRRNew.objects.get(id=resolved.id)
        assert resolved_row.resolved_user_id == alice.id
        assert resolved_row.requested_user_id is None
        # no unique match anywhere → both FKs null
        nm = FRRNew.objects.get(id=no_match.id)
        assert nm.requested_user_id is None and nm.resolved_user_id is None
        # empty strings → both FKs null
        em = FRRNew.objects.get(id=empty.id)
        assert em.requested_user_id is None and em.resolved_user_id is None
        # legacy strings are never touched by the backfill
        assert FRRNew.objects.get(id=by_username.id).requested_by == "alice"

        # --- reverse: nulls the FKs, keeps the strings + rows -----------------
        executor3 = _migrate(START)
        FRRBack = executor3.loader.project_state(START).apps.get_model(
            "inventory", "FixtureRefillRequest"
        )
        back = FRRBack.objects.get(id=by_username.id)
        assert back.requested_user_id is None
        assert back.requested_by == "alice"
        assert FRRBack.objects.count() == 5
    finally:
        # Always restore the graph to HEAD so later tests see a normal schema.
        _migrate(executor.loader.graph.leaf_nodes())
