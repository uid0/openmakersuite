"""Avery 5388 cards for the racking: renderer, batch print, and QR prefill.

Every assertion here reads the *rendered artifact* rather than the renderer's
intentions — the images are pulled back out of the PDF and decoded, so a card
that prints an unreadable QR or somebody else's marker fails the test. Both
decoders (``cv2.QRCodeDetector``, ``cv2.aruco``) come from the same
opencv-headless the scan side uses, which is what makes the round trip
meaningful: what these tests decode is what a scanner will decode.
"""

from __future__ import annotations

import base64
import io

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

import cv2
import numpy as np
import pytest
from PIL import Image
from pypdf import PdfReader
from rest_framework.test import APIClient

from fiducials.models import FAMILY_TAG36H11
from fiducials.services.allocator import active_tag_id_subquery
from fiducials.services.apriltag_render import decode_apriltag_ids
from project_storage.models import StorageSlot
from project_storage.services.slot_cards import (
    StorageSlotCardRenderer,
    build_slot_card_preview,
    render_slot_cards,
)
from project_storage.services.storage_slots import (
    SLOT_TAG_FAMILY,
    LevelSpec,
    ensure_slot_tag,
    generate_rack_slots,
)
from project_storage.tests.factories import StorageSlotFactory

pytestmark = pytest.mark.django_db

SLOTS_URL = "/api/project-storage/slots/"
FRONTEND = "https://make.example.org"


@pytest.fixture(autouse=True)
def _frontend_url(settings):
    """Pin the QR host so payload assertions don't depend on local config."""
    settings.FRONTEND_URL = FRONTEND
    return FRONTEND


@pytest.fixture
def staff_client():
    User = get_user_model()
    user = User.objects.create_user(username="warden", password="x", is_staff=True)
    api = APIClient()
    api.force_authenticate(user=user)
    return api


# ---------------------------------------------------------------------------
# Reading the artifact back
# ---------------------------------------------------------------------------


def _pages(pdf_bytes: bytes):
    return PdfReader(io.BytesIO(pdf_bytes)).pages


def _images(pdf_bytes: bytes) -> list[Image.Image]:
    """Every image the PDF embeds — one QR and (usually) one marker per card."""
    return [embedded.image for page in _pages(pdf_bytes) for embedded in page.images]


# cv2's detector is sensitive at the QR's stored resolution — some payloads
# decode only once the modules are a few pixels bigger, even though the same
# image reads fine upscaled (and prints at ~320 dpi / 0.9 mm modules, well
# inside phone-scanner range). Upscale before detecting so the test measures
# the payload, not the detector's floor.
_QR_DECODE_SCALE = 3


def _decode_qr(image: Image.Image) -> str:
    gray = image.convert("L")
    gray = gray.resize(
        (gray.width * _QR_DECODE_SCALE, gray.height * _QR_DECODE_SCALE),
        Image.Resampling.NEAREST,
    )
    payload, _points, _straight = cv2.QRCodeDetector().detectAndDecode(np.array(gray))
    return payload


def _qr_payloads(pdf_bytes: bytes) -> list[str]:
    return [payload for payload in (_decode_qr(img) for img in _images(pdf_bytes)) if payload]


def _tag_ids(pdf_bytes: bytes, family: str = SLOT_TAG_FAMILY) -> list[int]:
    ids: list[int] = []
    for image in _images(pdf_bytes):
        ids.extend(decode_apriltag_ids(image, family=family))
    return ids


def _text(pdf_bytes: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in _pages(pdf_bytes))


def _codes_in_print_order(pdf_bytes: bytes, codes: list[str]) -> list[str]:
    """The requested codes, ordered as they appear in the rendered text."""
    text = _text(pdf_bytes)
    return sorted((code for code in codes if code in text), key=text.index)


def _kiosk_url(code: str) -> str:
    return f"{FRONTEND}/project-storage/kiosk?slot={code}"


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class TestSlotCardSheet:
    @pytest.mark.parametrize("count,expected_pages", [(1, 1), (3, 1), (4, 2), (7, 3)])
    def test_three_cards_per_sheet(self, count, expected_pages):
        """Avery 5388 is 3-up: N slots fill ceil(N/3) sheets."""
        slots = _annotated(generate_rack_slots(rack=1, levels=[LevelSpec("A", count)]).created)

        pdf_bytes = render_slot_cards(slots)

        assert pdf_bytes[:5] == b"%PDF-"
        assert len(_pages(pdf_bytes)) == expected_pages

    def test_cards_print_in_the_order_given(self):
        slots = _annotated(generate_rack_slots(rack=1, levels=[LevelSpec("A", 4)]).created)
        wanted = ["1A3", "1A1", "1A4", "1A2"]
        ordered = sorted(slots, key=lambda slot: wanted.index(slot.code))

        pdf_bytes = render_slot_cards(ordered)

        assert _codes_in_print_order(pdf_bytes, wanted) == wanted

    def test_empty_selection_is_rejected(self):
        with pytest.raises(ValueError):
            render_slot_cards([])


class TestSlotCardFace:
    def test_code_and_shelf_details_are_printed(self):
        slot = StorageSlotFactory(rack=12, level="Z", position=40, requires_pallet_jack=True)
        slot.owning_group = Group.objects.create(name="Metal SIG")
        slot.save()
        ensure_slot_tag(slot)

        text = _text(render_slot_cards([slot]))

        assert "12Z40" in text
        assert "Rack 12 - Level Z - Position 40" in text
        assert "Pallet jack required" in text
        assert "Reserved for Metal SIG" in text

    def test_qr_encodes_the_kiosk_prefilled_with_this_slot(self):
        slot = StorageSlotFactory(rack=2, level="B", position=3)
        ensure_slot_tag(slot)

        payloads = _qr_payloads(render_slot_cards([slot]))

        assert payloads == [_kiosk_url("2B3")]

    def test_every_card_carries_its_own_prefill_url(self):
        slots = _annotated(generate_rack_slots(rack=1, levels=[LevelSpec("A", 3)]).created)

        payloads = _qr_payloads(render_slot_cards(slots))

        assert sorted(payloads) == [_kiosk_url(code) for code in ("1A1", "1A2", "1A3")]

    def test_apriltag_is_the_slots_allocated_id(self):
        slot = StorageSlotFactory(rack=3, level="C", position=1)
        tag_id = ensure_slot_tag(slot).tag_id

        assert _tag_ids(render_slot_cards([slot])) == [tag_id]

    def test_markers_come_from_the_location_family_not_the_recycling_pool(self):
        """A card's marker has to decode in tag36h10 — the permanent-location
        family — so a rack scan can never be read as a stint's 36h11 tag."""
        slot = StorageSlotFactory()
        ensure_slot_tag(slot)

        pdf_bytes = render_slot_cards([slot])

        assert _tag_ids(pdf_bytes, family=SLOT_TAG_FAMILY)
        assert _tag_ids(pdf_bytes, family=FAMILY_TAG36H11) == []

    def test_each_slot_gets_its_own_marker(self):
        slots = _annotated(generate_rack_slots(rack=1, levels=[LevelSpec("A", 3)]).created)
        expected = [StorageSlotCardRenderer.tag_id_for(slot) for slot in slots]

        assert sorted(_tag_ids(render_slot_cards(slots))) == sorted(expected)

    def test_untagged_slot_still_prints_a_usable_card(self):
        """A slot created while the family was exhausted has no fiducial. The
        card still prints — code and QR — and says the marker is missing
        rather than borrowing another slot's."""
        slot = StorageSlotFactory(rack=9, level="A", position=1)
        assert StorageSlotCardRenderer.tag_id_for(slot) is None

        pdf_bytes = render_slot_cards([slot])

        assert _tag_ids(pdf_bytes) == []
        assert _qr_payloads(pdf_bytes) == [_kiosk_url("9A1")]
        assert "no location tag" in _text(pdf_bytes)

    def test_rendering_an_annotated_sheet_costs_no_extra_queries(self, django_assert_num_queries):
        """The sheet is prepared by one query, the way the viewset builds it —
        no marker lookup or group read per card."""
        sig = Group.objects.create(name="Woodshop SIG")
        generate_rack_slots(rack=5, levels=[LevelSpec("A", 3)], owning_group=sig)
        slots = list(
            StorageSlot.objects.filter(rack=5)
            .select_related("owning_group")
            .annotate(active_april_tag_id=active_tag_id_subquery(StorageSlot))
        )

        with django_assert_num_queries(0):
            render_slot_cards(slots)


class TestSlotCardPreviewPayload:
    def test_preview_is_a_single_card_pdf_plus_what_it_encoded(self):
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        tag_id = ensure_slot_tag(slot).tag_id

        payload = build_slot_card_preview(slot)

        assert payload["code"] == "1A1"
        assert payload["content_type"] == "application/pdf"
        assert payload["filename"] == "storage_slot_1A1_card.pdf"
        assert payload["kiosk_url"] == _kiosk_url("1A1")
        assert payload["april_tag_id"] == tag_id
        pdf_bytes = base64.b64decode(payload["preview"])
        assert len(_pages(pdf_bytes)) == 1
        assert _qr_payloads(pdf_bytes) == [_kiosk_url("1A1")]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestSlotCardApiPermissions:
    def test_anonymous_cannot_preview(self):
        StorageSlotFactory(rack=1, level="A", position=1)
        assert APIClient().get(f"{SLOTS_URL}1A1/card-preview/").status_code in (401, 403)

    def test_anonymous_cannot_batch_print(self):
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        resp = APIClient().post(f"{SLOTS_URL}cards/", {"slot_ids": [slot.pk]}, format="json")
        assert resp.status_code in (401, 403)

    def test_plain_authenticated_user_is_rejected(self):
        StorageSlotFactory(rack=1, level="A", position=1)
        api = APIClient()
        api.force_authenticate(
            user=get_user_model().objects.create_user(username="member", password="x")
        )
        assert api.get(f"{SLOTS_URL}1A1/card-preview/").status_code == 403


class TestSlotCardPreviewEndpoint:
    def test_preview_returns_the_encoded_card(self, staff_client):
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        tag_id = ensure_slot_tag(slot).tag_id

        resp = staff_client.get(f"{SLOTS_URL}1A1/card-preview/")

        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == "1A1"
        assert resp.data["april_tag_id"] == tag_id
        assert resp.data["kiosk_url"] == _kiosk_url("1A1")
        pdf_bytes = base64.b64decode(resp.data["preview"])
        assert _qr_payloads(pdf_bytes) == [_kiosk_url("1A1")]
        assert _tag_ids(pdf_bytes) == [tag_id]

    def test_unknown_code_is_a_404(self, staff_client):
        assert staff_client.get(f"{SLOTS_URL}9Z9/card-preview/").status_code == 404


class TestSlotCardBatchEndpoint:
    def test_selected_slots_render_in_the_requested_order(self, staff_client):
        slots = {
            slot.code: slot
            for slot in generate_rack_slots(rack=1, levels=[LevelSpec("A", 3)]).created
        }
        wanted = ["1A3", "1A1"]

        resp = staff_client.post(
            f"{SLOTS_URL}cards/",
            {"slot_ids": [slots[code].pk for code in wanted]},
            format="json",
        )

        assert resp.status_code == 200, resp.content
        assert resp["Content-Type"] == "application/pdf"
        assert "attachment" in resp["Content-Disposition"]
        assert _codes_in_print_order(resp.content, ["1A1", "1A2", "1A3"]) == wanted

    def test_a_rack_prints_as_sheets_in_code_order(self, staff_client):
        generate_rack_slots(rack=1, levels=[LevelSpec("A", 3), LevelSpec("B", 1)])
        generate_rack_slots(rack=2, levels=[LevelSpec("A", 1)])

        resp = staff_client.post(f"{SLOTS_URL}cards/", {"rack": 1}, format="json")

        assert resp.status_code == 200, resp.content
        assert len(_pages(resp.content)) == 2
        assert _codes_in_print_order(resp.content, ["1A1", "1A2", "1A3", "1B1", "2A1"]) == [
            "1A1",
            "1A2",
            "1A3",
            "1B1",
        ]

    def test_a_level_narrows_the_rack(self, staff_client):
        generate_rack_slots(rack=1, levels=[LevelSpec("A", 2), LevelSpec("Y", 2)])

        resp = staff_client.post(f"{SLOTS_URL}cards/", {"rack": 1, "level": "y"}, format="json")

        assert resp.status_code == 200, resp.content
        assert _codes_in_print_order(resp.content, ["1A1", "1A2", "1Y1", "1Y2"]) == ["1Y1", "1Y2"]

    def test_retired_slots_stay_off_a_rack_print_unless_asked_for(self, staff_client):
        generate_rack_slots(rack=1, levels=[LevelSpec("A", 2)])
        StorageSlot.objects.filter(code="1A2").update(is_active=False)

        default = staff_client.post(f"{SLOTS_URL}cards/", {"rack": 1}, format="json")
        included = staff_client.post(
            f"{SLOTS_URL}cards/", {"rack": 1, "include_inactive": True}, format="json"
        )

        assert _codes_in_print_order(default.content, ["1A1", "1A2"]) == ["1A1"]
        assert _codes_in_print_order(included.content, ["1A1", "1A2"]) == ["1A1", "1A2"]
        assert "Not in service" in _text(included.content)

    def test_batch_qr_codes_are_per_slot(self, staff_client):
        generate_rack_slots(rack=1, levels=[LevelSpec("A", 2)])

        resp = staff_client.post(f"{SLOTS_URL}cards/", {"rack": 1}, format="json")

        assert sorted(_qr_payloads(resp.content)) == [_kiosk_url("1A1"), _kiosk_url("1A2")]

    def test_batch_markers_match_each_slots_allocation(self, staff_client):
        created = generate_rack_slots(rack=7, levels=[LevelSpec("A", 3)]).created
        expected = sorted(StorageSlotCardRenderer.tag_id_for(slot) for slot in created)

        resp = staff_client.post(f"{SLOTS_URL}cards/", {"rack": 7}, format="json")

        assert sorted(_tag_ids(resp.content)) == expected

    def test_missing_slot_ids_are_reported_not_silently_dropped(self, staff_client):
        slot = StorageSlotFactory(rack=1, level="A", position=1)

        resp = staff_client.post(
            f"{SLOTS_URL}cards/", {"slot_ids": [slot.pk, slot.pk + 999]}, format="json"
        )

        assert resp.status_code == 404
        assert resp.data["missing_ids"] == [slot.pk + 999]

    def test_a_rack_with_nothing_to_print_is_a_404(self, staff_client):
        resp = staff_client.post(f"{SLOTS_URL}cards/", {"rack": 42}, format="json")
        assert resp.status_code == 404
        assert resp.data["code"] == "no_slots_matched"

    def test_a_selection_is_required(self, staff_client):
        assert staff_client.post(f"{SLOTS_URL}cards/", {}, format="json").status_code == 400

    def test_ids_and_a_rack_together_are_rejected(self, staff_client):
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        resp = staff_client.post(
            f"{SLOTS_URL}cards/", {"slot_ids": [slot.pk], "rack": 1}, format="json"
        )
        assert resp.status_code == 400

    def test_level_without_a_rack_is_rejected(self, staff_client):
        resp = staff_client.post(f"{SLOTS_URL}cards/", {"level": "A"}, format="json")
        assert resp.status_code == 400


def _annotated(slots) -> list[StorageSlot]:
    """Re-read ``slots`` the way the viewset does: markers annotated in one query."""
    codes = [slot.code for slot in slots]
    by_code = {
        slot.code: slot
        for slot in StorageSlot.objects.filter(code__in=codes)
        .select_related("owning_group")
        .annotate(active_april_tag_id=active_tag_id_subquery(StorageSlot))
    }
    return [by_code[code] for code in codes]
