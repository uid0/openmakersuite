"""Pure resolution logic for scanner payloads.

`resolve(payload)` returns a `ResolvedScan` describing what to do with the
scanned string. The dispatcher view wraps this in DRF serialization but
the resolution itself has no Django request dependency — all branches
are unit-testable.

Resolution priority:
  1. URL-shaped payloads (our own QR codes encode `/inventory/scan/...`).
  2. All-digit payloads matching UPC-A (12), UPC-E (8), EAN-13 (13),
     ITF-14 (14) — look up against `ItemSupplier.unit_upc`, then
     `package_upc`. Receive flow.
  3. Location `access_code` (6-char A-Z/2-9 excluding I/O/0/1/L).
  4. Asset `asset_tag` exact match.
  5. Unknown.

This module never mutates state. Side effects (creating LocationCheckIn
rows, incrementing stock, marking for reorder) happen in the frontend by
posting to existing per-entity endpoints once the user confirms the
resolved action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

ACTION_INVENTORY_REORDER = "inventory_reorder"
ACTION_INVENTORY_RECEIVE = "inventory_receive"
ACTION_ASSET_CHECKIN = "asset_checkin"
ACTION_LOCATION_CHECKIN = "location_checkin"
ACTION_FIXTURE = "fixture"
ACTION_DONATION_ITEM = "donation_item"
ACTION_UNKNOWN = "unknown"

# UUID pattern (lowercase or mixed) for matching trailing IDs in QR URLs.
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Location.access_code: 6 chars, A-Z + 2-9, excludes I O 0 1 L per the
# model's help_text. The model itself doesn't enforce the alphabet so we
# accept any uppercase A-Z2-9 of length 6 here and let the DB lookup
# settle whether it's a real code.
_LOCATION_CODE_RE = re.compile(r"^[A-HJ-KM-NP-Z2-9]{6}$")

# UPC / EAN / ITF lengths. Pure-digit payloads only.
_UPC_LENGTHS = {8, 12, 13, 14}


@dataclass
class ResolvedScan:
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    target_name: Optional[str] = None
    target_url: Optional[str] = None
    message: Optional[str] = None
    # For inventory_receive only:
    item_supplier_id: Optional[int] = None
    supplier_name: Optional[str] = None
    item_id: Optional[str] = None
    item_name: Optional[str] = None
    is_package: Optional[bool] = None
    current_stock: Optional[int] = None
    raw_payload: str = ""

    def to_dict(self) -> dict:
        # DRF JSONRenderer handles dataclass-to-dict via vars(), but
        # being explicit lets us drop None fields for a tidier response.
        return {k: v for k, v in vars(self).items() if v is not None}


def _strip_url(payload: str) -> tuple[str, bool]:
    """Return (path, is_url_shaped). Tolerates payloads with or without
    a scheme — e.g. `https://oms/.../scan/x`, `oms/.../scan/x`, or just
    `/inventory/scan/x`.
    """
    p = payload.strip()
    if "://" in p:
        try:
            return urlparse(p).path or "", True
        except ValueError:
            return p, False
    if p.startswith("/"):
        return p, True
    return p, False


def _parse_scan_url(path: str) -> Optional[tuple[str, str]]:
    """Match `/inventory/scan/<type>/<id>` or `/inventory/scan/<id>`.

    Returns `(type, id)` or None. Type defaults to `"inventory_item"`
    when only an id segment follows `/scan/`.
    """
    # Normalize: strip trailing slash, split on /.
    parts = [seg for seg in path.strip("/").split("/") if seg]
    if len(parts) < 3 or parts[0] != "inventory" or parts[1] != "scan":
        return None

    if len(parts) == 3:
        # /inventory/scan/<id>  →  bare InventoryItem
        return ("inventory_item", parts[2])

    type_segment = parts[2]
    id_segment = parts[3]
    type_map = {
        "asset": "asset",
        "location": "location",
        "fixture": "fixture",
        "donation-item": "donation_item",
    }
    mapped = type_map.get(type_segment)
    if mapped is None:
        return None
    return (mapped, id_segment)


def resolve(payload: str) -> ResolvedScan:
    """Resolve a raw scan payload to a ResolvedScan.

    Imports the inventory / location_checkins models lazily so this
    module is importable in environments without a full Django setup
    (useful for spec-only unit tests of the URL/UPC regexes).
    """
    if not payload or not payload.strip():
        return ResolvedScan(
            action=ACTION_UNKNOWN,
            message="Empty scan payload.",
            raw_payload=payload or "",
        )

    raw = payload.strip()

    # 1) URL-shaped: our own QR codes.
    path, is_url = _strip_url(raw)
    parsed = _parse_scan_url(path) if is_url or path.startswith("inventory/") else None
    if parsed:
        return _resolve_known_target(parsed[0], parsed[1], raw)

    # 2) UPC / EAN: pure digits matching a known length.
    if raw.isdigit() and len(raw) in _UPC_LENGTHS:
        return _resolve_upc(raw)

    # 3) Location access_code (6-char alphabet).
    if _LOCATION_CODE_RE.match(raw.upper()):
        return _resolve_location_code(raw.upper())

    # 4) Asset tag (exact match — no format constraint).
    asset = _resolve_asset_tag(raw)
    if asset is not None:
        return asset

    # 5) Unknown.
    return ResolvedScan(
        action=ACTION_UNKNOWN,
        message=(
            "Couldn't identify this scan. Expected one of: an OMS QR "
            "URL, a 8/12/13/14-digit UPC, a 6-character location code, "
            "or an asset tag."
        ),
        raw_payload=raw,
    )


def _resolve_known_target(target_type: str, target_id: str, raw: str) -> ResolvedScan:
    """Look up the target by ID and return the appropriate action."""
    # Lazy imports so the resolvers module is loadable without Django settings.
    from inventory.models import Asset, Fixture, InventoryItem, Location

    if target_type == "inventory_item":
        item = InventoryItem.objects.filter(pk=target_id).first()
        if item is None:
            return _not_found("inventory item", target_id, raw)
        return ResolvedScan(
            action=ACTION_INVENTORY_REORDER,
            target_type="inventory_item",
            target_id=str(item.id),
            target_name=item.name,
            target_url=f"/inventory/items/{item.id}",
            current_stock=item.current_stock,
            raw_payload=raw,
        )

    if target_type == "asset":
        asset = Asset.objects.filter(pk=target_id).first()
        if asset is None:
            return _not_found("asset", target_id, raw)
        return ResolvedScan(
            action=ACTION_ASSET_CHECKIN,
            target_type="asset",
            target_id=str(asset.id),
            target_name=asset.name,
            target_url=f"/inventory/assets/{asset.id}",
            raw_payload=raw,
        )

    if target_type == "location":
        try:
            loc_pk: int | str = int(target_id)
        except ValueError:
            loc_pk = target_id  # let the ORM raise / miss gracefully
        loc = Location.objects.filter(pk=loc_pk).first()
        if loc is None:
            return _not_found("location", target_id, raw)
        return ResolvedScan(
            action=ACTION_LOCATION_CHECKIN,
            target_type="location",
            target_id=str(loc.id),
            target_name=loc.name,
            target_url=f"/inventory/locations/{loc.id}",
            raw_payload=raw,
        )

    if target_type == "fixture":
        fixture = Fixture.objects.filter(pk=target_id).first()
        if fixture is None:
            return _not_found("fixture", target_id, raw)
        return ResolvedScan(
            action=ACTION_FIXTURE,
            target_type="fixture",
            target_id=str(fixture.id),
            target_name=getattr(fixture, "name", str(fixture.id)),
            target_url=f"/inventory/scan/fixture/{fixture.id}",
            raw_payload=raw,
        )

    if target_type == "donation_item":
        # Donation items live in the donations app; do an attribute-free
        # lookup so the resolver stays resilient if that app's URL slug
        # is the source of truth for the scan URL.
        from donations.models import DonationItem

        donation = DonationItem.objects.filter(pk=target_id).first()
        if donation is None:
            return _not_found("donation item", target_id, raw)
        return ResolvedScan(
            action=ACTION_DONATION_ITEM,
            target_type="donation_item",
            target_id=str(donation.id),
            target_name=getattr(donation, "name", str(donation.id)),
            target_url=f"/inventory/scan/donation-item/{donation.id}",
            raw_payload=raw,
        )

    return _not_found(target_type, target_id, raw)


def _resolve_upc(raw: str) -> ResolvedScan:
    from inventory.models import ItemSupplier

    # unit_upc first — single-unit scans are the common case at receiving.
    link = ItemSupplier.objects.filter(unit_upc=raw).select_related("item", "supplier").first()
    is_package = False
    if link is None:
        link = (
            ItemSupplier.objects.filter(package_upc=raw).select_related("item", "supplier").first()
        )
        is_package = link is not None

    if link is None:
        return ResolvedScan(
            action=ACTION_UNKNOWN,
            message=(
                f"No item-supplier link found for UPC {raw}. Add the UPC "
                "to the supplier relationship in the item's admin page."
            ),
            raw_payload=raw,
        )

    return ResolvedScan(
        action=ACTION_INVENTORY_RECEIVE,
        target_type="inventory_item",
        target_id=str(link.item.id),
        target_name=link.item.name,
        target_url=f"/inventory/items/{link.item.id}",
        item_supplier_id=link.pk,
        supplier_name=link.supplier.name,
        item_id=str(link.item.id),
        item_name=link.item.name,
        is_package=is_package,
        current_stock=link.item.current_stock,
        raw_payload=raw,
    )


def _resolve_location_code(code: str) -> ResolvedScan:
    from inventory.models import Location

    loc = Location.objects.filter(access_code=code).first()
    if loc is None:
        return ResolvedScan(
            action=ACTION_UNKNOWN,
            message=f"No active location has access code {code}.",
            raw_payload=code,
        )
    return ResolvedScan(
        action=ACTION_LOCATION_CHECKIN,
        target_type="location",
        target_id=str(loc.id),
        target_name=loc.name,
        target_url=f"/inventory/locations/{loc.id}",
        raw_payload=code,
    )


def _resolve_asset_tag(payload: str) -> Optional[ResolvedScan]:
    from inventory.models import Asset

    asset = Asset.objects.filter(asset_tag=payload).first()
    if asset is None:
        return None
    return ResolvedScan(
        action=ACTION_ASSET_CHECKIN,
        target_type="asset",
        target_id=str(asset.id),
        target_name=asset.name,
        target_url=f"/inventory/assets/{asset.id}",
        raw_payload=payload,
    )


def _not_found(target_kind: str, target_id: str, raw: str) -> ResolvedScan:
    return ResolvedScan(
        action=ACTION_UNKNOWN,
        message=f"No {target_kind} found with id {target_id}.",
        raw_payload=raw,
    )
