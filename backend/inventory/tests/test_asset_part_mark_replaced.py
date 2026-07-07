"""
Tests for capturing a replacement serial number when an Asset Part is marked
as replaced (op-8nxe).

Covers the hard contract shared with the ScanTTY prompt:
  * ``AssetPart.replacement_serial_number`` (writable CharField).
  * ``mark_replaced`` accepts an optional ``{"replacement_serial_number": ...}``
    body and, when non-empty, records it alongside ``last_replaced_at``.
  * ``AssetPartSerializer.part_details`` exposes ``is_serialized`` so clients
    know whether to prompt.
Back-compat: an absent/empty body behaves exactly as the original one-click
"mark replaced" flow used by non-serialized parts.
"""

from django.urls import reverse

import pytest
from rest_framework import status

from inventory.models import AssetPart
from inventory.tests.factories import (
    AssetFactory,
    AssetPartFactory,
    InventoryItemFactory,
)

pytestmark = pytest.mark.django_db


def _mark_replaced_url(asset_part):
    return reverse("assetpart-mark-replaced", kwargs={"pk": str(asset_part.pk)})


@pytest.mark.integration
class TestMarkReplacedSerialNumber:
    """POST asset-parts/<id>/mark_replaced/ with an optional serial number."""

    def test_mark_replaced_without_serial_is_backwards_compatible(self, authenticated_client):
        """An empty body still stamps last_replaced_at and leaves the serial blank."""
        client, _ = authenticated_client
        part_item = InventoryItemFactory(is_serialized=False)
        asset_part = AssetPartFactory(part=part_item, last_replaced_at=None)

        response = client.post(_mark_replaced_url(asset_part))

        assert response.status_code == status.HTTP_200_OK
        asset_part.refresh_from_db()
        assert asset_part.last_replaced_at is not None
        assert asset_part.replacement_serial_number == ""
        assert response.data["replacement_serial_number"] == ""

    def test_mark_replaced_with_serial_saves_it(self, authenticated_client):
        """A serialized part records the new unit's serial on the AssetPart."""
        client, _ = authenticated_client
        part_item = InventoryItemFactory(is_serialized=True)
        asset_part = AssetPartFactory(part=part_item, last_replaced_at=None)

        response = client.post(
            _mark_replaced_url(asset_part),
            {"replacement_serial_number": "MG-INK-2026-07"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        asset_part.refresh_from_db()
        assert asset_part.replacement_serial_number == "MG-INK-2026-07"
        assert asset_part.last_replaced_at is not None
        assert response.data["replacement_serial_number"] == "MG-INK-2026-07"

    def test_mark_replaced_trims_surrounding_whitespace(self, authenticated_client):
        """The stored serial is stripped of surrounding whitespace."""
        client, _ = authenticated_client
        asset_part = AssetPartFactory(part=InventoryItemFactory(is_serialized=True))

        response = client.post(
            _mark_replaced_url(asset_part),
            {"replacement_serial_number": "  SN-123  "},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        asset_part.refresh_from_db()
        assert asset_part.replacement_serial_number == "SN-123"

    def test_mark_replaced_empty_serial_leaves_existing_value_untouched(self, authenticated_client):
        """An explicit empty string does not clear a previously captured serial."""
        client, _ = authenticated_client
        asset_part = AssetPartFactory(
            part=InventoryItemFactory(is_serialized=True),
            replacement_serial_number="OLD-SERIAL",
        )

        response = client.post(
            _mark_replaced_url(asset_part),
            {"replacement_serial_number": ""},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        asset_part.refresh_from_db()
        # Empty is treated as "no serial provided" — the one-click path — so the
        # prior value is preserved rather than wiped.
        assert asset_part.replacement_serial_number == "OLD-SERIAL"
        assert asset_part.last_replaced_at is not None

    def test_mark_replaced_whitespace_only_serial_is_treated_as_empty(self, authenticated_client):
        """A whitespace-only serial is ignored (stripped to empty)."""
        client, _ = authenticated_client
        asset_part = AssetPartFactory(
            part=InventoryItemFactory(is_serialized=True),
            replacement_serial_number="",
        )

        response = client.post(
            _mark_replaced_url(asset_part),
            {"replacement_serial_number": "   "},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        asset_part.refresh_from_db()
        assert asset_part.replacement_serial_number == ""

    def test_mark_replaced_rejects_overlong_serial(self, authenticated_client):
        """A serial longer than the field max_length is a 400, not a 500."""
        client, _ = authenticated_client
        asset_part = AssetPartFactory(part=InventoryItemFactory(is_serialized=True))
        max_length = AssetPart._meta.get_field("replacement_serial_number").max_length

        response = client.post(
            _mark_replaced_url(asset_part),
            {"replacement_serial_number": "X" * (max_length + 1)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "replacement_serial_number" in response.data["error"]["details"]
        asset_part.refresh_from_db()
        assert asset_part.replacement_serial_number == ""
        # A rejected request must not stamp last_replaced_at either.
        assert asset_part.last_replaced_at is None

    def test_mark_replaced_rejects_non_string_serial(self, authenticated_client):
        """A non-string serial is a 400."""
        client, _ = authenticated_client
        asset_part = AssetPartFactory(part=InventoryItemFactory(is_serialized=True))

        response = client.post(
            _mark_replaced_url(asset_part),
            {"replacement_serial_number": 12345},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "replacement_serial_number" in response.data["error"]["details"]

    def test_mark_replaced_requires_authentication(self, api_client):
        """Unauthenticated POSTs stay rejected (unchanged permission behaviour)."""
        asset_part = AssetPartFactory(part=InventoryItemFactory(is_serialized=True))

        response = api_client.post(
            _mark_replaced_url(asset_part),
            {"replacement_serial_number": "SN-1"},
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        asset_part.refresh_from_db()
        assert asset_part.replacement_serial_number == ""


@pytest.mark.integration
class TestAssetPartSerializerIsSerialized:
    """AssetPartSerializer.part_details exposes is_serialized + serial field."""

    def test_part_details_reports_is_serialized_true(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory()
        part_item = InventoryItemFactory(is_serialized=True)
        asset_part = AssetPartFactory(asset=asset, part=part_item)

        url = reverse("assetpart-detail", kwargs={"pk": str(asset_part.pk)})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["part_details"]["is_serialized"] is True
        assert "replacement_serial_number" in response.data

    def test_part_details_reports_is_serialized_false(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory()
        part_item = InventoryItemFactory(is_serialized=False)
        asset_part = AssetPartFactory(asset=asset, part=part_item)

        url = reverse("assetpart-detail", kwargs={"pk": str(asset_part.pk)})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["part_details"]["is_serialized"] is False


@pytest.mark.integration
class TestAssetPartReplacementSerialField:
    """The model field itself is optional and writable."""

    def test_replacement_serial_number_defaults_to_blank(self):
        asset_part = AssetPartFactory()
        asset_part.refresh_from_db()
        assert asset_part.replacement_serial_number == ""

    def test_replacement_serial_number_can_be_set(self):
        asset_part = AssetPartFactory(replacement_serial_number="SC-99")
        asset_part.refresh_from_db()
        assert asset_part.replacement_serial_number == "SC-99"
