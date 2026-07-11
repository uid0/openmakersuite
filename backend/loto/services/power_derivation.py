"""
Auto-population of AssetEnergySource rows from the direct ``Asset.breaker``
association.

Reads ``asset.breaker`` (and ``breaker.panel``) and synthesises an electrical
:class:`AssetEnergySource` whose ``derived_from`` points at the breaker. Any
LOTO devices declared on the upstream breaker propagate onto the derived
row, so the LOTO procedure for a powered asset shows the right padlocks/
tags without manual entry.

Manual rows (hydraulic, pneumatic, etc.) carry ``derived_from = NULL`` and
are never touched by this code path. When an asset's breaker is cleared, the
previously-derived row is flagged ``is_stale = True`` rather than deleted,
preserving the audit trail.
"""

from __future__ import annotations

from typing import Iterable, Optional

from django.db import transaction

from electrical_circuits.models import PowerBreaker
from inventory.models import Asset

from ..models import AssetEnergySource


def _format_isolation_point(panel, breaker) -> str:
    """'Sewing Panel breaker 14'-style isolation hint for the LOTO sheet."""

    panel_name = (panel.name or "").strip() or f"Panel #{panel.pk}"
    position = (breaker.position or "").strip() or f"#{breaker.pk}"
    return f"{panel_name} breaker {position}"


def _format_magnitude(panel) -> str:
    voltage = getattr(panel, "voltage", None)
    return f"{voltage}V" if voltage else ""


@transaction.atomic
def derive_for_asset(asset: Asset) -> Optional[AssetEnergySource]:
    """(Re)derive the electrical AssetEnergySource for ``asset``.

    Returns the derived row, or ``None`` if the asset has no breaker assigned.
    Existing derived rows (``derived_from`` set) are updated in place; manual
    rows (``derived_from IS NULL``) are never touched.
    """

    breaker = (
        PowerBreaker.objects.select_related("panel").filter(pk=asset.breaker_id).first()
        if asset.breaker_id
        else None
    )
    if breaker is None:
        AssetEnergySource.objects.filter(asset=asset, derived_from__isnull=False).update(
            is_stale=True
        )
        return None
    panel = breaker.panel
    if panel is None:
        return None

    isolation_point = _format_isolation_point(panel, breaker)
    magnitude = _format_magnitude(panel)

    source, _ = AssetEnergySource.objects.update_or_create(
        asset=asset,
        derived_from=breaker,
        defaults={
            "source_type": AssetEnergySource.SOURCE_ELECTRICAL,
            "magnitude": magnitude,
            "isolation_point": isolation_point,
            "is_stale": False,
        },
    )

    breaker_devices = list(breaker.required_loto_devices.all())
    source.required_devices.set(breaker_devices)
    return source


def derive_for_breaker(breaker: PowerBreaker) -> list[AssetEnergySource]:
    """Re-derive energy sources for every asset assigned to ``breaker``."""

    results: list[AssetEnergySource] = []
    for asset in Asset.objects.filter(site_requirements__breaker=breaker):
        derived = derive_for_asset(asset)
        if derived is not None:
            results.append(derived)
    return results


def mark_stale_for_breaker(breaker: PowerBreaker) -> int:
    """Flag derived rows from ``breaker`` as historical, return count touched.

    Used when a breaker is decommissioned / hard-deleted. We never delete the
    AssetEnergySource — keeping the row preserves the LOTO audit trail (who
    locked out what, when) which is the whole point of the table.
    """

    return AssetEnergySource.objects.filter(derived_from=breaker).update(is_stale=True)


def rederive_all() -> dict[str, int]:
    """Backfill / repair: re-derive every asset with a breaker FK."""

    derived = 0
    skipped = 0
    for asset in Asset.objects.exclude(site_requirements__breaker__isnull=True):
        result = derive_for_asset(asset)
        if result is not None:
            derived += 1
        else:
            skipped += 1
    return {"derived": derived, "skipped": skipped}


def _iter_assets_for_breakers(breakers: Iterable[PowerBreaker]) -> Iterable[Asset]:
    """Yield every Asset reachable from ``breakers`` (de-duplicated)."""

    seen: set = set()
    for breaker in breakers:
        for asset in Asset.objects.filter(site_requirements__breaker=breaker):
            if asset.pk in seen:
                continue
            seen.add(asset.pk)
            yield asset


__all__ = [
    "derive_for_asset",
    "derive_for_breaker",
    "mark_stale_for_breaker",
    "rederive_all",
]
