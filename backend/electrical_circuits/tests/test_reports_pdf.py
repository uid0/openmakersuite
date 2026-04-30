"""PDF report endpoint tests for electrical_circuits (oms-zde)."""

import io

from django.urls import reverse

import pytest
from pypdf import PdfReader

from electrical_circuits.models import Breaker, LightSwitch, NetworkDrop, Outlet
from electrical_circuits.utils.panel_directory_pdf import (
    generate_network_drop_list_pdf,
    generate_panel_directory_pdf,
)
from inventory.tests.factories import LocationFactory


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


@pytest.fixture
def panel_room(db):
    return LocationFactory(name="Test Electrical Room")


@pytest.fixture
def shop(db):
    return LocationFactory(name="Test Wood Shop")


@pytest.fixture
def hallway(db):
    return LocationFactory(name="Test Hallway")


@pytest.fixture
def closet(db):
    return LocationFactory(name="Test IDF-1")


@pytest.fixture
def panel_a_breaker(panel_room):
    return Breaker.objects.create(
        location=panel_room,
        panel="Panel A",
        breaker_number="12",
        amperage=20,
        voltage=120,
        description="Wood shop benches",
    )


@pytest.fixture
def panel_b_breaker(panel_room):
    return Breaker.objects.create(
        location=panel_room,
        panel="Panel B",
        breaker_number="3",
        amperage=30,
        voltage=240,
        poles=2,
    )


# ── Panel directory report ──────────────────────────────────────────────────


def test_panel_directory_requires_auth(api_client):
    url = reverse("electrical-panel-directory-pdf")
    resp = api_client.get(url)
    assert resp.status_code == 401


def test_panel_directory_returns_pdf_with_breaker_and_outlet(
    authenticated_client, panel_a_breaker, shop
):
    client, _ = authenticated_client
    Outlet.objects.create(
        location=shop,
        identifier="bench-1",
        breaker=panel_a_breaker,
        outlet_type="nema_5_20",
        plugged_in_notes="Table saw",
    )

    resp = client.get(reverse("electrical-panel-directory-pdf"))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert "panel-directory" in resp["Content-Disposition"]

    text = _pdf_text(resp.content)
    assert "Panel A" in text
    assert "Breaker 12" in text
    assert "bench-1" in text
    assert "Test Wood Shop" in text


def test_panel_directory_filtered_by_panel_excludes_other_panels(
    authenticated_client, panel_a_breaker, panel_b_breaker, shop
):
    client, _ = authenticated_client
    Outlet.objects.create(location=shop, identifier="a-out", breaker=panel_a_breaker)
    Outlet.objects.create(location=shop, identifier="b-out", breaker=panel_b_breaker)

    resp = client.get(reverse("electrical-panel-directory-pdf"), {"panel": "Panel A"})
    assert resp.status_code == 200
    assert "panel-directory-Panel-A" in resp["Content-Disposition"]
    text = _pdf_text(resp.content)
    assert "Panel A" in text
    assert "a-out" in text
    # Panel B's breaker / outlet should not be in the filtered report.
    assert "b-out" not in text


def test_panel_directory_includes_light_switches(
    authenticated_client, panel_a_breaker, hallway, shop
):
    client, _ = authenticated_client
    LightSwitch.objects.create(
        location=hallway,
        identifier="east-door",
        breaker=panel_a_breaker,
        controls_location=shop,
    )

    resp = client.get(reverse("electrical-panel-directory-pdf"))
    assert resp.status_code == 200
    text = _pdf_text(resp.content)
    assert "east-door" in text
    assert "Test Hallway" in text
    # The "controls:" annotation should reference the controlled location.
    assert "Test Wood Shop" in text


def test_panel_directory_unfiltered_lists_orphan_outlet(authenticated_client, shop):
    """Outlets without a breaker show up under an 'Unassigned' section."""
    client, _ = authenticated_client
    Outlet.objects.create(location=shop, identifier="orphan-1")

    resp = client.get(reverse("electrical-panel-directory-pdf"))
    assert resp.status_code == 200
    text = _pdf_text(resp.content)
    assert "Unassigned" in text
    assert "orphan-1" in text


def test_panel_directory_filtered_skips_orphans(authenticated_client, panel_a_breaker, shop):
    """When a specific panel is requested, orphan outlets are not surfaced."""
    client, _ = authenticated_client
    Outlet.objects.create(location=shop, identifier="orphan-1")
    Outlet.objects.create(location=shop, identifier="a-out", breaker=panel_a_breaker)

    resp = client.get(reverse("electrical-panel-directory-pdf"), {"panel": "Panel A"})
    text = _pdf_text(resp.content)
    assert "a-out" in text
    assert "orphan-1" not in text


def test_panel_directory_handles_empty_database(authenticated_client):
    client, _ = authenticated_client
    resp = client.get(reverse("electrical-panel-directory-pdf"))
    assert resp.status_code == 200
    # Still a valid PDF.
    PdfReader(io.BytesIO(resp.content))


def test_panel_directory_unknown_panel_returns_empty_pdf(authenticated_client):
    client, _ = authenticated_client
    resp = client.get(reverse("electrical-panel-directory-pdf"), {"panel": "DoesNotExist"})
    assert resp.status_code == 200
    text = _pdf_text(resp.content)
    assert "DoesNotExist" in text
    assert "No breakers recorded" in text


# ── Network drop list report ────────────────────────────────────────────────


def test_network_drop_list_requires_auth(api_client):
    resp = api_client.get(reverse("electrical-network-drop-list-pdf"))
    assert resp.status_code == 401


def test_network_drop_list_includes_drops(authenticated_client, closet):
    client, _ = authenticated_client
    NetworkDrop.objects.create(
        location=closet,
        identifier="rack-1-1",
        drop_type="patch_panel",
        patch_panel="IDF-1 patch panel B",
        patch_port="3",
        mac_address="aa:bb:cc:dd:ee:ff",
    )

    resp = client.get(reverse("electrical-network-drop-list-pdf"))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    text = _pdf_text(resp.content)
    assert "rack-1-1" in text
    assert "Test IDF-1" in text
    assert "aa:bb:cc:dd:ee:ff" in text


def test_network_drop_list_filtered_by_location(authenticated_client, closet):
    client, _ = authenticated_client
    other = LocationFactory(name="Conference Room")
    NetworkDrop.objects.create(location=closet, identifier="rack-1-1", drop_type="patch_panel")
    NetworkDrop.objects.create(location=other, identifier="conf-jack-1", drop_type="data")

    resp = client.get(
        reverse("electrical-network-drop-list-pdf"),
        {"location": closet.pk},
    )
    assert resp.status_code == 200
    assert f"network-drop-list-{closet.pk}" in resp["Content-Disposition"]
    text = _pdf_text(resp.content)
    assert "rack-1-1" in text
    assert "conf-jack-1" not in text


def test_network_drop_list_invalid_location_returns_400(authenticated_client):
    client, _ = authenticated_client
    resp = client.get(
        reverse("electrical-network-drop-list-pdf"),
        {"location": "abc"},
    )
    assert resp.status_code == 400


def test_network_drop_list_empty_returns_pdf(authenticated_client):
    client, _ = authenticated_client
    resp = client.get(reverse("electrical-network-drop-list-pdf"))
    assert resp.status_code == 200
    text = _pdf_text(resp.content)
    assert "No network drops" in text


# ── Module-level smoke tests ────────────────────────────────────────────────


def test_generate_panel_directory_pdf_returns_bytes(panel_a_breaker, shop):
    Outlet.objects.create(location=shop, identifier="bench-1", breaker=panel_a_breaker)
    pdf_bytes = generate_panel_directory_pdf()
    assert pdf_bytes.startswith(b"%PDF")


def test_generate_network_drop_list_pdf_returns_bytes(closet):
    NetworkDrop.objects.create(location=closet, identifier="d-1", drop_type="data")
    pdf_bytes = generate_network_drop_list_pdf()
    assert pdf_bytes.startswith(b"%PDF")
