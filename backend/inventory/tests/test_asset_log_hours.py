"""Tests for the ``POST /api/inventory/assets/<id>/log-hours/`` action.

Exists to feed analytics utilization metrics + maintenance forecast.
Restricted to staff / SIG admins per the WO-write permission gate.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from inventory.tests.factories import AssetFactory
from membership.models import SIGAdmin

User = get_user_model()
pytestmark = pytest.mark.django_db


def _user(username, **flags):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=get_random_string(24),
        **flags,
    )


def _client(user=None):
    client = APIClient()
    if user:
        client.force_authenticate(user=user)
    return client


def _url(asset):
    return f"/api/inventory/assets/{asset.id}/log-hours/"


class TestLogHoursPermissions:
    def test_anonymous_denied(self):
        asset = AssetFactory()
        resp = _client().post(_url(asset), data={"hours": 5}, format="json")
        assert resp.status_code in (401, 403)

    def test_volunteer_denied(self):
        asset = AssetFactory()
        resp = _client(_user("volunteer")).post(_url(asset), data={"hours": 5}, format="json")
        assert resp.status_code == 403

    def test_staff_allowed(self):
        asset = AssetFactory(hours_used=0)
        resp = _client(_user("admin", is_staff=True)).post(
            _url(asset), data={"hours": 7}, format="json"
        )
        assert resp.status_code == 200
        assert resp.json()["hours_used"] == 7

    def test_sig_admin_allowed_for_their_sig_asset(self):
        user = _user("sig_lead")
        wood_sig = Group.objects.create(name="Wood SIG")
        SIGAdmin.objects.create(user=user, group=wood_sig)
        # AssetViewSet scopes to assets owned by the user's SIGs for non-staff.
        asset = AssetFactory(hours_used=10, owning_group=wood_sig)
        resp = _client(user).post(_url(asset), data={"hours": 3}, format="json")
        assert resp.status_code == 200
        assert resp.json()["hours_used"] == 13


class TestLogHoursValidation:
    def setup_method(self):
        self.client = _client(_user("admin", is_staff=True))

    def test_increment_is_atomic(self):
        asset = AssetFactory(hours_used=100)
        self.client.post(_url(asset), data={"hours": 5}, format="json")
        self.client.post(_url(asset), data={"hours": 3}, format="json")
        asset.refresh_from_db()
        assert asset.hours_used == 108

    def test_rejects_zero(self):
        asset = AssetFactory()
        resp = self.client.post(_url(asset), data={"hours": 0}, format="json")
        assert resp.status_code == 400

    def test_rejects_negative(self):
        asset = AssetFactory()
        resp = self.client.post(_url(asset), data={"hours": -2}, format="json")
        assert resp.status_code == 400

    def test_rejects_non_integer(self):
        asset = AssetFactory()
        resp = self.client.post(_url(asset), data={"hours": "many"}, format="json")
        assert resp.status_code == 400

    def test_rejects_missing(self):
        asset = AssetFactory()
        resp = self.client.post(_url(asset), data={}, format="json")
        assert resp.status_code == 400
