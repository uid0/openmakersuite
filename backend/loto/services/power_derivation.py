"""
Auto-population of AssetEnergySource rows from the power topology (oms-qc3).

Walks the power chain (Asset → PowerPort → Cable → PowerOutlet → PowerCircuit
→ PowerBreaker → PowerPanel) and synthesises an electrical
:class:`AssetEnergySource` whose ``derived_from`` points at the cable. Any
LOTO devices declared on the upstream breaker propagate onto the derived
row, so the LOTO procedure for a cabled asset shows the right padlocks/tags
without manual entry.

Manual rows (hydraulic, pneumatic, etc.) carry ``derived_from = NULL`` and
are never touched by this code path. Conversely, decommissioned cables flag
their derived rows ``is_stale = True`` rather than deleting them, preserving
the audit trail.
"""

from __future__ import annotations

from typing import Iterable, Optional

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from electrical_circuits.models import Cable, PowerOutlet, PowerPort
from electrical_circuits.services.power_chain import get_power_chain
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


def _assets_for_cable(cable: Cable) -> list[Asset]:
    """Return any Assets attached to a power cable's endpoints.

    A power cable connects exactly one PowerOutlet to one PowerPort, so this
    is at most one asset. We tolerate weird shapes (planned/decommissioned
    cables with missing ends) by skipping rather than raising.
    """

    if cable.cable_type != Cable.CABLE_TYPE_POWER:
        return []

    port_ct = ContentType.objects.get_for_model(PowerPort)
    port_pk: Optional[int] = None
    for ct_id, obj_id in (
        (cable.endpoint_a_content_type_id, cable.endpoint_a_object_id),
        (cable.endpoint_b_content_type_id, cable.endpoint_b_object_id),
    ):
        if ct_id == port_ct.id:
            try:
                port_pk = int(obj_id)
            except (TypeError, ValueError):
                port_pk = None
            break

    if port_pk is None:
        return []
    port = PowerPort.objects.filter(pk=port_pk).select_related("asset").first()
    if port is None:
        return []
    return [port.asset]


def _power_outlet_for_cable(cable: Cable) -> Optional[PowerOutlet]:
    outlet_ct = ContentType.objects.get_for_model(PowerOutlet)
    for ct_id, obj_id in (
        (cable.endpoint_a_content_type_id, cable.endpoint_a_object_id),
        (cable.endpoint_b_content_type_id, cable.endpoint_b_object_id),
    ):
        if ct_id == outlet_ct.id:
            try:
                return PowerOutlet.objects.get(pk=int(obj_id))
            except (PowerOutlet.DoesNotExist, TypeError, ValueError):
                return None
    return None


@transaction.atomic
def derive_for_asset(asset: Asset) -> Optional[AssetEnergySource]:
    """(Re)derive the electrical AssetEnergySource for ``asset``.

    Returns the derived row, or ``None`` if the asset has no completable
    power chain (no port, no cable, dangling cable). Existing derived rows
    (``derived_from`` set) are updated in place so manual edits to magnitude
    / required_devices are NOT clobbered for non-derived rows but ARE the
    source of truth for derived rows.
    """

    chain = get_power_chain(asset)
    if not chain:
        return None

    by_kind = {hop.kind: hop.obj for hop in chain}
    panel = by_kind.get("panel")
    breaker = by_kind.get("breaker")
    cable = by_kind.get("cable")
    if panel is None or breaker is None or cable is None:
        return None

    isolation_point = _format_isolation_point(panel, breaker)
    magnitude = _format_magnitude(panel)

    source, _ = AssetEnergySource.objects.update_or_create(
        asset=asset,
        derived_from=cable,
        defaults={
            "source_type": AssetEnergySource.SOURCE_ELECTRICAL,
            "magnitude": magnitude,
            "isolation_point": isolation_point,
            "is_stale": cable.status != Cable.STATUS_CONNECTED,
        },
    )

    breaker_devices = list(breaker.required_loto_devices.all())
    source.required_devices.set(breaker_devices)
    return source


def derive_for_cable(cable: Cable) -> list[AssetEnergySource]:
    """Re-derive energy sources for any asset attached to ``cable``."""

    results: list[AssetEnergySource] = []
    for asset in _assets_for_cable(cable):
        derived = derive_for_asset(asset)
        if derived is not None:
            results.append(derived)
    return results


def mark_stale_for_cable(cable: Cable) -> int:
    """Flag derived rows from ``cable`` as historical, return count touched.

    Used when a cable is decommissioned or hard-deleted. We never delete the
    AssetEnergySource — keeping the row preserves the LOTO audit trail (who
    locked out what, when) which is the whole point of the table.
    """

    return AssetEnergySource.objects.filter(derived_from=cable).update(is_stale=True)


def rederive_all() -> dict[str, int]:
    """Backfill / repair: re-derive every connected power cable.

    Returns a small summary suitable for management-command output.
    """

    derived = 0
    skipped = 0
    for cable in Cable.objects.filter(
        cable_type=Cable.CABLE_TYPE_POWER,
        status=Cable.STATUS_CONNECTED,
    ):
        results = derive_for_cable(cable)
        if results:
            derived += len(results)
        else:
            skipped += 1

    stale = AssetEnergySource.objects.filter(
        derived_from__status__in=[Cable.STATUS_DECOMMISSIONED, Cable.STATUS_PLANNED],
        is_stale=False,
    ).update(is_stale=True)

    return {"derived": derived, "skipped": skipped, "marked_stale": stale}


__all__ = [
    "derive_for_asset",
    "derive_for_cable",
    "mark_stale_for_cable",
    "rederive_all",
]


def _iter_assets_for_cables(cables: Iterable[Cable]) -> Iterable[Asset]:
    """Yield every Asset reachable from ``cables`` (de-duplicated)."""

    seen: set = set()
    for cable in cables:
        for asset in _assets_for_cable(cable):
            if asset.pk in seen:
                continue
            seen.add(asset.pk)
            yield asset
