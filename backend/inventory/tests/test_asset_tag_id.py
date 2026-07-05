"""Tests for the DMS-YYANNNSS asset tag identifier.

Covers the checksum/validation helpers, the atomic per-year sequence, the
``Asset.save()`` auto-generator, the backfill management command, and the
searchable-everywhere requirement (API search by partial tag). The physical
QR payload must stay a UUID throughout — several tests assert the tag never
leaks onto the scan path.
"""

from datetime import date
from io import BytesIO, StringIO

from django.core.management import call_command

import pytest
from freezegun import freeze_time
from PIL import Image
from rest_framework.test import APIClient

from inventory.models import AssetTagSequence
from inventory.services.asset_tag_id import (
    compose_asset_tag,
    compute_asset_tag_checksum,
    validate_asset_tag,
)
from inventory.services.asset_tag_service import get_scan_url, render_asset_tag
from inventory.tests.factories import AssetFactory

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Checksum + validation (pure, no DB)                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.unit
class TestChecksum:
    def test_checksum_is_deterministic(self):
        assert compute_asset_tag_checksum("26A001") == compute_asset_tag_checksum("26A001")

    def test_checksum_is_two_base36_chars(self):
        checksum = compute_asset_tag_checksum("26A001")
        assert len(checksum) == 2
        assert all(c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" for c in checksum)

    def test_checksum_case_insensitive_on_core(self):
        assert compute_asset_tag_checksum("26a001") == compute_asset_tag_checksum("26A001")

    def test_distinct_cores_generally_differ(self):
        # Not a hard guarantee, but neighbouring cores should not collide.
        assert compute_asset_tag_checksum("26A001") != compute_asset_tag_checksum("26A002")


@pytest.mark.unit
class TestValidate:
    def test_validate_accepts_generated_tag(self):
        tag = compose_asset_tag("26A001")
        assert validate_asset_tag(tag) is True

    def test_validate_accepts_lowercase(self):
        tag = compose_asset_tag("26A001")
        assert validate_asset_tag(tag.lower()) is True

    def test_validate_rejects_corrupted_significant_char(self):
        tag = compose_asset_tag("26A001")  # e.g. DMS-26A001XX
        # Flip the alpha section A -> B; checksum no longer matches.
        corrupted = tag[:6] + "B" + tag[7:]
        assert corrupted != tag
        assert validate_asset_tag(corrupted) is False

    def test_validate_rejects_corrupted_checksum(self):
        tag = compose_asset_tag("26A001")
        # Bump the final checksum char to something else.
        last = tag[-1]
        replacement = "0" if last != "0" else "1"
        assert validate_asset_tag(tag[:-1] + replacement) is False

    def test_validate_rejects_transposition(self):
        # Core 26A012 -> transpose the last two digits to 26A021.
        good = compose_asset_tag("26A012")
        transposed = "DMS-26A021" + good[-2:]
        assert validate_asset_tag(transposed) is False

    def test_validate_rejects_legacy_random_tag(self):
        # Old format DMS-<8 hex>; structurally close but checksum won't hold.
        assert validate_asset_tag("DMS-1A2B3C4D") is False

    def test_validate_rejects_factory_placeholder(self):
        assert validate_asset_tag("AST-00001") is False

    def test_validate_rejects_malformed(self):
        for bad in ["", "DMS-", "DMS-26A01", "DMS-2XA001AA", "nonsense", None, 12345]:
            assert validate_asset_tag(bad) is False


# --------------------------------------------------------------------------- #
# AssetTagSequence — atomic per-year counter                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.unit
class TestAssetTagSequence:
    def test_first_allocation_is_a001(self):
        assert AssetTagSequence.allocate_core(2026) == "26A001"

    def test_increments_within_year(self):
        cores = [AssetTagSequence.allocate_core(2026) for _ in range(3)]
        assert cores == ["26A001", "26A002", "26A003"]

    def test_alpha_rollover_past_999(self):
        AssetTagSequence.objects.create(year=2026, alpha="A", number=999)
        assert AssetTagSequence.allocate_core(2026) == "26B001"

    def test_alpha_rollover_continues(self):
        AssetTagSequence.objects.create(year=2026, alpha="B", number=999)
        assert AssetTagSequence.allocate_core(2026) == "26C001"

    def test_per_year_reset(self):
        AssetTagSequence.allocate_core(2026)
        AssetTagSequence.allocate_core(2026)
        # A brand-new year starts its own run at 001.
        assert AssetTagSequence.allocate_core(2027) == "27A001"

    def test_two_digit_year_wraps(self):
        assert AssetTagSequence.allocate_core(2030) == "30A001"
        assert AssetTagSequence.allocate_core(2100) == "00A001"

    def test_exhaustion_past_z_raises(self):
        AssetTagSequence.objects.create(year=2026, alpha="Z", number=999)
        with pytest.raises(ValueError):
            AssetTagSequence.allocate_core(2026)

    def test_persisted_state_survives_reload(self):
        AssetTagSequence.allocate_core(2026)
        AssetTagSequence.allocate_core(2026)
        seq = AssetTagSequence.objects.get(year=2026)
        assert (seq.alpha, seq.number) == ("A", 2)


# --------------------------------------------------------------------------- #
# Asset.save() auto-generation                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.unit
class TestAssetSaveGenerator:
    def test_new_asset_gets_dms_yyannnss_from_date_received(self):
        asset = AssetFactory(asset_tag="", date_received=date(2024, 3, 1))
        assert asset.asset_tag == compose_asset_tag("24A001")
        assert validate_asset_tag(asset.asset_tag)

    @freeze_time("2026-07-05")
    def test_falls_back_to_current_year_without_date_received(self):
        asset = AssetFactory(asset_tag="", date_received=None)
        assert asset.asset_tag.startswith("DMS-26A")
        assert validate_asset_tag(asset.asset_tag)

    def test_existing_tag_is_never_regenerated(self):
        asset = AssetFactory(asset_tag="", date_received=date(2026, 1, 1))
        original = asset.asset_tag
        asset.name = "renamed"
        asset.save()
        asset.refresh_from_db()
        assert asset.asset_tag == original

    def test_explicit_tag_is_respected(self):
        asset = AssetFactory(asset_tag="DMS-CUSTOM99")
        assert asset.asset_tag == "DMS-CUSTOM99"

    def test_tags_are_unique_and_sequential(self):
        a = AssetFactory(asset_tag="", date_received=date(2026, 1, 1))
        b = AssetFactory(asset_tag="", date_received=date(2026, 1, 1))
        assert a.asset_tag != b.asset_tag
        assert a.asset_tag.startswith("DMS-26A001")
        assert b.asset_tag.startswith("DMS-26A002")

    def test_year_comes_from_date_received_not_now(self):
        with freeze_time("2026-07-05"):
            asset = AssetFactory(asset_tag="", date_received=date(2019, 12, 31))
        assert asset.asset_tag.startswith("DMS-19A")


# --------------------------------------------------------------------------- #
# Label renders the tag; QR still encodes the UUID                            #
# --------------------------------------------------------------------------- #


def _decode_qr(png_bytes):
    """Return the QR payload decoded from PNG bytes, or None if undecodable."""
    try:
        from pyzbar.pyzbar import decode
    except ImportError:  # pragma: no cover - environment without zbar
        return None
    img = Image.open(BytesIO(png_bytes))
    decoded = decode(img)
    return decoded[0].data.decode("utf-8") if decoded else None


@pytest.mark.unit
class TestLabelAndQr:
    def test_scan_url_encodes_uuid_not_tag(self):
        asset = AssetFactory(asset_tag="", date_received=date(2026, 1, 1))
        url = get_scan_url(asset)
        assert str(asset.id) in url
        assert asset.asset_tag not in url

    def test_rendered_tag_qr_decodes_to_uuid_url(self):
        asset = AssetFactory(asset_tag="", date_received=date(2026, 1, 1))
        png = render_asset_tag(asset, size="standard")
        payload = _decode_qr(png)
        if payload is None:
            pytest.skip("pyzbar/zbar not available to decode QR")
        assert str(asset.id) in payload
        # The human tag must never ride on the scan path.
        assert asset.asset_tag not in payload

    def test_printed_label_renders_new_tag(self):
        from inventory.utils.label_generator import BrotherLabelRenderer

        asset = AssetFactory(asset_tag="", date_received=date(2026, 1, 1))
        pdf = BrotherLabelRenderer().render_label(asset)
        assert isinstance(pdf, bytes) and pdf.startswith(b"%PDF")
        # The label draws "Tag: {asset_tag}"; confirm the value is new-format.
        assert validate_asset_tag(asset.asset_tag)


# --------------------------------------------------------------------------- #
# Searchable everywhere — API search by partial tag                           #
# --------------------------------------------------------------------------- #


@pytest.mark.unit
class TestAssetSearch:
    def _search(self, client, term):
        response = client.get("/api/inventory/assets/", {"search": term})
        assert response.status_code == 200
        data = response.data
        return data["results"] if isinstance(data, dict) else data

    def test_search_matches_full_tag(self):
        client = APIClient()
        asset = AssetFactory(asset_tag="", date_received=date(2026, 1, 1))
        results = self._search(client, asset.asset_tag)
        assert str(asset.id) in [row["id"] for row in results]

    def test_search_matches_partial_tag_case_insensitive(self):
        client = APIClient()
        asset = AssetFactory(asset_tag="", date_received=date(2026, 1, 1))
        # Partial significant chars, lower-cased — icontains must still match.
        partial = asset.asset_tag[4:9].lower()  # e.g. "26a00"
        results = self._search(client, partial)
        assert str(asset.id) in [row["id"] for row in results]

    def test_search_excludes_non_matching(self):
        client = APIClient()
        AssetFactory(asset_tag="", date_received=date(2026, 1, 1))
        results = self._search(client, "DMS-99Z999")
        assert results == []


# --------------------------------------------------------------------------- #
# Backfill management command                                                 #
# --------------------------------------------------------------------------- #


def _legacy_asset(name, year, day=1):
    """Create an asset carrying a legacy random tag for a given received year."""
    asset = AssetFactory(
        asset_tag=f"DMS-LEGACY{name[:2].upper()}",
        name=name,
        date_received=date(year, 1, day),
    )
    return asset


@pytest.mark.unit
class TestBackfillCommand:
    def test_reassigns_in_date_received_order(self):
        # Created out of order; backfill should number by received date.
        second = _legacy_asset("Bravo", 2026, day=10)
        first = _legacy_asset("Alpha", 2026, day=1)

        call_command("backfill_asset_tags", stdout=StringIO())

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.asset_tag.startswith("DMS-26A001")
        assert second.asset_tag.startswith("DMS-26A002")
        assert validate_asset_tag(first.asset_tag)
        assert validate_asset_tag(second.asset_tag)

    def test_per_year_sequences_independent(self):
        a2025 = _legacy_asset("Old", 2025)
        a2026 = _legacy_asset("New", 2026)

        call_command("backfill_asset_tags", stdout=StringIO())

        a2025.refresh_from_db()
        a2026.refresh_from_db()
        assert a2025.asset_tag.startswith("DMS-25A001")
        assert a2026.asset_tag.startswith("DMS-26A001")

    def test_seeds_live_counter_so_next_save_continues(self):
        legacy = _legacy_asset("One", 2026)
        call_command("backfill_asset_tags", stdout=StringIO())
        legacy.refresh_from_db()
        assert legacy.asset_tag.startswith("DMS-26A001")

        # A freshly created 2026 asset must pick up at 002, not collide.
        fresh = AssetFactory(asset_tag="", date_received=date(2026, 6, 1))
        assert fresh.asset_tag.startswith("DMS-26A002")

    def test_is_idempotent(self):
        legacy = _legacy_asset("One", 2026)
        call_command("backfill_asset_tags", stdout=StringIO())
        legacy.refresh_from_db()
        first_pass = legacy.asset_tag

        call_command("backfill_asset_tags", stdout=StringIO())
        legacy.refresh_from_db()
        assert legacy.asset_tag == first_pass

    def test_dry_run_changes_nothing(self):
        legacy = _legacy_asset("One", 2026)
        original = legacy.asset_tag
        call_command("backfill_asset_tags", "--dry-run", stdout=StringIO())
        legacy.refresh_from_db()
        assert legacy.asset_tag == original
        assert not AssetTagSequence.objects.exists()

    def test_undated_falls_back_to_created_year(self):
        with freeze_time("2022-05-05"):
            legacy = AssetFactory(asset_tag="DMS-LEGACYUN", date_received=None)
        call_command("backfill_asset_tags", stdout=StringIO())
        legacy.refresh_from_db()
        assert legacy.asset_tag.startswith("DMS-22A001")

    def test_skip_undated_flag_leaves_tag(self):
        with freeze_time("2022-05-05"):
            legacy = AssetFactory(asset_tag="DMS-LEGACYUN", date_received=None)
        original = legacy.asset_tag
        call_command("backfill_asset_tags", "--skip-undated", stdout=StringIO())
        legacy.refresh_from_db()
        assert legacy.asset_tag == original
