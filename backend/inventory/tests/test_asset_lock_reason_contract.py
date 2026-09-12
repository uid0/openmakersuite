"""The lock endpoint's request contract, and what lock/disable actually do.

The web client posts to ``/api/inventory/assets/<id>/lock/``. It used to post
no body at all, which this endpoint answers with 400 — so the browser's Lock
button could never lock anything, while Disable (which does NOT stop the
machine) worked fine. These tests pin the two halves of that:

* ``lock`` requires a ``reason`` and stores it verbatim on the
  :class:`~forgekey.models.DeviceLockout` someone reads later, so the client
  cannot default or truncate it.
* ``lock`` denies use of the machine (via
  :func:`forgekey.services.access_control.is_authorized`) and ``disable`` does
  not — the distinction the web UI now states to the operator.
"""

from django.contrib.auth import get_user_model
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from forgekey.models import AssetAuthorization, DeviceLockout, OperationalMode
from forgekey.services.access_control import is_authorized
from inventory.tests.factories import AssetFactory

User = get_user_model()
pytestmark = pytest.mark.django_db


def _user(username, **flags):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=get_random_string(24),
        **flags,
    )


@pytest.fixture
def asset():
    return AssetFactory()


@pytest.fixture
def staff():
    return _user(f"locker-{get_random_string(6)}", is_staff=True)


@pytest.fixture
def client(staff):
    c = APIClient()
    c.force_authenticate(user=staff)
    return c


def _lock_url(asset):
    return f"/api/inventory/assets/{asset.id}/lock/"


class TestLockRequiresAReason:
    def test_no_body_is_rejected(self, client, asset):
        """What the web client used to send: a bodyless POST."""
        response = client.post(_lock_url(asset), {}, format="json")

        assert response.status_code == 400
        assert not DeviceLockout.objects.filter(asset=asset).exists()

    def test_blank_reason_is_rejected(self, client, asset):
        response = client.post(_lock_url(asset), {"reason": ""}, format="json")

        assert response.status_code == 400
        assert not DeviceLockout.objects.filter(asset=asset).exists()

    def test_reason_is_stored_verbatim(self, client, asset):
        """The reason is an audit record, so nothing may reshape it."""
        reason = (
            "E-stop latched open after the PM; interlock relay chatters under "
            "load. Do not return to service until the relay is replaced -- see "
            "WO-4471. Tagged by B. Keeler."
        )

        response = client.post(_lock_url(asset), {"reason": reason}, format="json")

        assert response.status_code == 201
        lockout = DeviceLockout.objects.get(asset=asset)
        assert lockout.reason == reason


class TestLockStopsTheMachineAndDisableDoesNot:
    """The distinction the two web buttons now spell out."""

    @pytest.fixture
    def operator(self, asset):
        user = _user(f"operator-{get_random_string(6)}")
        AssetAuthorization.objects.create(asset=asset, user=user, is_active=True)
        return user

    def test_authorized_operator_may_use_the_asset_to_begin_with(self, asset, operator):
        assert is_authorized(operator, asset) is True

    def test_lock_denies_the_operator(self, client, asset, operator):
        client.post(_lock_url(asset), {"reason": "Guard missing"}, format="json")

        assert is_authorized(operator, asset) is False
        assert OperationalMode.objects.get(asset=asset).mode == (OperationalMode.MODE_LOCKED_OUT)

    def test_disable_does_not_deny_the_operator(self, client, asset, operator):
        response = client.post(f"/api/inventory/assets/{asset.id}/disable/", {}, format="json")

        assert response.status_code == 200
        asset.refresh_from_db()
        assert asset.is_active is False
        # The machine still starts: `disable` only hides the record.
        assert is_authorized(operator, asset) is True
