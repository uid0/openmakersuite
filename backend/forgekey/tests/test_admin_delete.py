from __future__ import annotations

from django.contrib import admin
from django.contrib.admin.options import get_content_type_for_model
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory

import pytest

from forgekey.admin import DeviceCommandAdmin
from forgekey.models import DeviceCommand, ESP32Device
from forgekey.tests.factories import ESP32DeviceFactory, UserFactory

pytestmark = pytest.mark.django_db


def _device_command_admin() -> DeviceCommandAdmin:
    return DeviceCommandAdmin(DeviceCommand, admin.site)


def test_superuser_can_delete_device_with_commands():
    device = ESP32DeviceFactory()
    superuser = UserFactory(is_staff=True, is_superuser=True)
    command = DeviceCommand.objects.create(
        device=device,
        command="restart",
        payload={},
        sent_by=superuser,
    )

    client = Client()
    client.force_login(superuser)

    response = client.post(
        f"/admin/forgekey/esp32device/{device.pk}/delete/",
        {
            "post": "yes",
            "_selected_action": [str(device.pk)],
            "content_type_id": get_content_type_for_model(ESP32Device).pk,
            "object_id": str(device.pk),
        },
    )

    assert response.status_code == 302
    assert not ESP32Device.objects.filter(pk=device.pk).exists()
    assert not DeviceCommand.objects.filter(pk=command.pk).exists()


def test_staff_user_cannot_delete_device_command_directly():
    device = ESP32DeviceFactory()
    staff_user = UserFactory(is_staff=True, is_superuser=False)
    command = DeviceCommand.objects.create(
        device=device,
        command="restart",
        payload={},
        sent_by=staff_user,
    )
    request = RequestFactory().get("/admin/forgekey/devicecommand/")
    request.user = staff_user

    allowed = _device_command_admin().has_delete_permission(request, obj=command)

    assert allowed is False


def test_superuser_can_delete_device_command_directly():
    device = ESP32DeviceFactory()
    superuser = UserFactory(is_staff=True, is_superuser=True)
    command = DeviceCommand.objects.create(
        device=device,
        command="restart",
        payload={},
        sent_by=superuser,
    )
    request = RequestFactory().get("/admin/forgekey/devicecommand/")
    request.user = superuser

    allowed = _device_command_admin().has_delete_permission(request, obj=command)

    assert allowed is True


def test_anonymous_request_cannot_delete_device_command():
    request = RequestFactory().get("/admin/forgekey/devicecommand/")
    request.user = AnonymousUser()

    allowed = _device_command_admin().has_delete_permission(request, obj=None)

    assert allowed is False
