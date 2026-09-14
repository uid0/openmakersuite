"""Every URL the OMS QR generators print must resolve through the dispatcher.

The web scanner page (``UniversalScannerPage``) and the camera code-entry
page post whatever a camera or wedge scanner read off a label to
``/api/scanner/dispatch/``. Labels carry the generators' short
``{FRONTEND_URL}/scan/...`` URLs, so the dispatcher has to parse exactly
what the generators emit.

Each table row calls a public generator entry point and captures the string it
hands to ``qrcode.QRCode.add_data``, then posts that string.
"""

from unittest import mock
from urllib.parse import urlparse

from django.test import override_settings
from django.urls import reverse

import pytest
import qrcode
from rest_framework import status

from donations.services.qr_code_service import DonationItemQRCodeService
from donations.tests.factories import DonationItemFactory
from index_cards.services import IndexCardRenderer, TestSheetRenderer
from inventory.serializers import FixtureSerializer
from inventory.services.asset_tag_service import render_asset_tag
from inventory.services.qr_code_service import QRCodeService
from inventory.tests.factories import (
    AssetFactory,
    FixtureFactory,
    InventoryItemFactory,
    LocationFactory,
)
from inventory.utils import qr_generator
from project_storage.tests.factories import ProjectStorageStintFactory

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

FRONTEND_URL = "https://oms.example.org"


class _Captured(BaseException):
    """Stops a generator right after it hands its URL to the QR encoder.

    A BaseException so a generator's own ``except Exception`` cannot swallow
    it, and nothing after ``add_data`` (image render, file save) runs.
    """


def _printed_url(generate) -> str:
    seen = []

    def fake_add_data(self, data, *args, **kwargs):
        seen.append(data)
        raise _Captured

    with mock.patch.object(qrcode.QRCode, "add_data", fake_add_data):
        try:
            generate()
        except _Captured:
            pass
    assert len(seen) == 1, f"generator encoded {len(seen)} payloads"
    return seen[0]


def _item():
    item = InventoryItemFactory()
    return item, "inventory_reorder", str(item.id)


def _asset():
    asset = AssetFactory()
    return asset, "asset_checkin", str(asset.id)


def _location():
    location = LocationFactory()
    return location, "location_checkin", str(location.id)


def _stint():
    stint = ProjectStorageStintFactory()
    return stint, "project_storage_stint", stint.stint_id


def _donation_item():
    donation_item = DonationItemFactory()
    return donation_item, "donation_item", str(donation_item.id)


def _fixture():
    fixture = FixtureFactory()
    return fixture, "fixture", str(fixture.id)


# generator name -> (row builder, call that makes the generator encode its URL)
GENERATOR_TABLE = {
    "qr_generator.save_qr_code_to_item": (_item, qr_generator.save_qr_code_to_item),
    "qr_generator.save_qr_code_to_asset": (_asset, qr_generator.save_qr_code_to_asset),
    "qr_generator.save_qr_code_to_location": (_location, qr_generator.save_qr_code_to_location),
    "QRCodeService.generate_for_item": (
        _item,
        lambda row: QRCodeService(include_logo=False).generate_for_item(row),
    ),
    "QRCodeService.generate_for_asset": (
        _asset,
        lambda row: QRCodeService(include_logo=False).generate_for_asset(row),
    ),
    "QRCodeService.generate_for_location": (
        _location,
        lambda row: QRCodeService(include_logo=False).generate_for_location(row),
    ),
    "QRCodeService.generate_for_stint": (
        _stint,
        lambda row: QRCodeService(include_logo=False).generate_for_stint(row),
    ),
    "DonationItemQRCodeService.generate_for_donation_item": (
        _donation_item,
        lambda row: DonationItemQRCodeService(include_logo=False).generate_for_donation_item(row),
    ),
    "render_asset_tag": (
        _asset,
        lambda row: render_asset_tag(row, dpi=100),
    ),
    "IndexCardRenderer.render_to_bytes": (
        _item,
        lambda row: IndexCardRenderer(base_url=FRONTEND_URL).render_to_bytes([row]),
    ),
    "TestSheetRenderer.render_to_bytes": (
        _item,
        lambda row: TestSheetRenderer(base_url=FRONTEND_URL).render_to_bytes([row]),
    ),
}


def _dispatch(api_client, payload):
    return api_client.post(reverse("scanner:dispatch"), data={"payload": payload}, format="json")


@override_settings(FRONTEND_URL=FRONTEND_URL)
@pytest.mark.parametrize("generator", sorted(GENERATOR_TABLE))
def test_dispatcher_resolves_generated_qr_url(api_client, generator):
    build_row, generate = GENERATOR_TABLE[generator]
    row, expected_action, expected_id = build_row()
    printed = _printed_url(lambda: generate(row))
    assert urlparse(printed).path.startswith("/scan/")

    response = _dispatch(api_client, printed)

    assert response.status_code == status.HTTP_200_OK
    assert response.data["action"] == expected_action, response.data
    assert response.data["target_id"] == expected_id


@override_settings(FRONTEND_URL=FRONTEND_URL)
def test_dispatcher_resolves_fixture_serializer_qr_url(api_client):
    # Fixture labels are printed from FixtureSerializer.qr_code_url rather
    # than a qr_code_service generator; same /scan/<type>/<id> shape.
    row, expected_action, expected_id = _fixture()
    printed = FixtureSerializer().get_qr_code_url(row)
    assert urlparse(printed).path.startswith("/scan/")

    response = _dispatch(api_client, printed)

    assert response.status_code == status.HTTP_200_OK
    assert response.data["action"] == expected_action, response.data
    assert response.data["target_id"] == expected_id


@pytest.mark.parametrize(
    "payload",
    [
        f"{FRONTEND_URL}/scan",
        f"{FRONTEND_URL}/scan/not-an-item-id",
        f"{FRONTEND_URL}/scan/makerbox/BIN-1/alice/",
        f"{FRONTEND_URL}/scan/asset/00000000-0000-0000-0000-000000000000/anything",
        f"{FRONTEND_URL}/scan/asset/not-a-uuid",
        f"{FRONTEND_URL}/scan/donation-item/not-an-int",
    ],
)
def test_other_scan_namespace_urls_stay_unknown(api_client, payload):
    # /scan/... is the frontend's shared scan namespace; only the shapes the
    # QR generators print may become a lookup.
    response = _dispatch(api_client, payload)

    assert response.status_code == status.HTTP_200_OK
    assert response.data["action"] == "unknown"
