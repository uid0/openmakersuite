"""
Auto-derive AssetEnergySource rows from PowerCable connections (oms-qc3).

When a PowerCable is created or updated, the asset on the PowerPort end gains
an electrical energy source describing the upstream breaker. The derived
AssetEnergySource captures the cable in ``derived_from`` so subsequent edits
update the same row instead of creating duplicates, and records all LOTO
devices the upstream PowerBreaker is locked out with via
``required_devices``.

When a cable is removed or marked decommissioned, the derived row is kept and
flagged ``status=historical`` so the audit trail survives (AC-3).
"""

from __future__ import annotations

from typing import Iterable, Optional

from django.contrib.contenttypes.models import ContentType

from electrical_circuits.models import Cable, PowerOutlet, PowerPort

from ..models import AssetEnergySource


def _power_endpoints(cable: Cable) -> Optional[tuple[PowerOutlet, PowerPort]]:
    """Resolve a power cable's endpoints to (outlet, port).

    Returns ``None`` if the cable is not a power cable, is missing an
    endpoint, or doesn't pair an outlet with a port. Misconfigured cables
    silently produce no derivation rather than raising — derivation is a
    best-effort process triggered by signals.
    """

    if cable.cable_type != Cable.CABLE_TYPE_POWER:
        return None

    outlet_ct_id = ContentType.objects.get_for_model(PowerOutlet).id
    port_ct_id = ContentType.objects.get_for_model(PowerPort).id

    a_ct = cable.endpoint_a_content_type_id
    b_ct = cable.endpoint_b_content_type_id

    if a_ct == outlet_ct_id and b_ct == port_ct_id:
        outlet_id, port_id = cable.endpoint_a_object_id, cable.endpoint_b_object_id
    elif b_ct == outlet_ct_id and a_ct == port_ct_id:
        outlet_id, port_id = cable.endpoint_b_object_id, cable.endpoint_a_object_id
    else:
        return None

    outlet = (
        PowerOutlet.objects.select_related("circuit__breaker__panel").filter(pk=outlet_id).first()
    )
    port = PowerPort.objects.select_related("asset").filter(pk=port_id).first()
    if outlet is None or port is None:
        return None
    return outlet, port


def _format_magnitude(panel_voltage: int, breaker_amperage: int) -> str:
    return f"{panel_voltage}V {breaker_amperage}A"


def _format_isolation_point(panel_name: str, breaker_position: str) -> str:
    return f"{panel_name} Breaker {breaker_position}"


def derive_energy_source_for_cable(cable: Cable) -> Optional[AssetEnergySource]:
    """Create or refresh the derived AssetEnergySource for ``cable``.

    Returns the (created or updated) row, or ``None`` if the cable can't be
    resolved to an outlet+port pair (e.g. a non-power cable, or missing
    endpoints). For decommissioned cables this marks the existing derived
    row historical instead of refreshing it.
    """

    if cable.status == Cable.STATUS_DECOMMISSIONED:
        mark_cable_derived_sources_historical(cable.pk)
        return None

    endpoints = _power_endpoints(cable)
    if endpoints is None:
        return None
    outlet, port = endpoints

    breaker = outlet.circuit.breaker
    panel = breaker.panel

    defaults = {
        "asset": port.asset,
        "source_type": AssetEnergySource.SOURCE_ELECTRICAL,
        "magnitude": _format_magnitude(panel.voltage, breaker.amperage),
        "isolation_point": _format_isolation_point(panel.name, breaker.position),
        "status": AssetEnergySource.STATUS_ACTIVE,
    }

    source, _created = AssetEnergySource.objects.update_or_create(
        derived_from=cable,
        defaults=defaults,
    )
    # Sync required_devices to whatever the breaker is locked with right now.
    # Using set() preserves manual additions only on non-derived rows; for
    # derived rows the breaker's lockout_devices is the source of truth.
    source.required_devices.set(breaker.lockout_devices.all())
    return source


def mark_cable_derived_sources_historical(cable_pk: int) -> int:
    """Flag every AssetEnergySource derived from this cable as historical.

    Used both when a cable is decommissioned (status change, cable still
    exists) and as part of the pre-delete signal so the row survives the
    cable's removal with its derivation context intact.

    Returns the number of rows updated.
    """

    return AssetEnergySource.objects.filter(derived_from_id=cable_pk).update(
        status=AssetEnergySource.STATUS_HISTORICAL,
    )


def rederive_for_cables(cables: Iterable[Cable]) -> int:
    """Run derivation for a batch of cables. Returns count of rows touched."""

    touched = 0
    for cable in cables:
        if derive_energy_source_for_cable(cable) is not None:
            touched += 1
    return touched
