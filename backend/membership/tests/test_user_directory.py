"""Staff-only user directory endpoint for access-control pickers (op-tup).

The access-control frontend (asset authorization grant, badge enrollment) needs
to resolve a member to a user id and read their current badge. These tests cover
the ``/api/membership/users/`` listing: staff-only access, ``?search=`` over
name/username/email, and the ``?has_badge=`` filter.
"""

import pytest
from rest_framework.test import APIClient

from membership.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _results(response):
    body = response.json()
    return body["results"] if isinstance(body, dict) and "results" in body else body


@pytest.fixture
def staff_client():
    api = APIClient()
    api.force_authenticate(user=UserFactory(is_staff=True))
    return api


class TestUserDirectoryPermissions:
    def test_non_staff_is_forbidden(self):
        api = APIClient()
        api.force_authenticate(user=UserFactory(is_staff=False))
        resp = api.get("/api/membership/users/")
        assert resp.status_code == 403

    def test_anonymous_is_forbidden(self):
        resp = APIClient().get("/api/membership/users/")
        assert resp.status_code in (401, 403)


class TestUserDirectoryListing:
    def test_lists_users_with_badge_and_name(self, staff_client):
        UserFactory(username="alice", first_name="Alice", last_name="Adams", badge_number="A1")
        resp = staff_client.get("/api/membership/users/")
        assert resp.status_code == 200, resp.data
        rows = _results(resp)
        alice = next(r for r in rows if r["username"] == "alice")
        assert alice["badge_number"] == "A1"
        assert alice["full_name"] == "Alice Adams"
        assert "id" in alice

    def test_search_matches_username_name_email(self, staff_client):
        UserFactory(username="zoe_smith", first_name="Zoe", last_name="Smith", email="zoe@x.io")
        UserFactory(username="other", first_name="Pat", last_name="Jones", email="pat@x.io")

        by_name = _results(staff_client.get("/api/membership/users/?search=smith"))
        assert {r["username"] for r in by_name} == {"zoe_smith"}

        by_email = _results(staff_client.get("/api/membership/users/?search=pat@x.io"))
        assert {r["username"] for r in by_email} == {"other"}

    def test_has_badge_filter(self, staff_client):
        UserFactory(username="hasbadge", badge_number="BDG123")
        UserFactory(username="nobadge", badge_number=None)

        with_badge = _results(staff_client.get("/api/membership/users/?has_badge=true"))
        assert "hasbadge" in {r["username"] for r in with_badge}
        assert "nobadge" not in {r["username"] for r in with_badge}

        without_badge = _results(staff_client.get("/api/membership/users/?has_badge=false"))
        assert "nobadge" in {r["username"] for r in without_badge}
        assert "hasbadge" not in {r["username"] for r in without_badge}
