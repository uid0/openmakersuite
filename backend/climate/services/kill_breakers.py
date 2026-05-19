"""Resolve the breakers that de-energize a thermostat's controlled HVAC asset.

Logic (in priority order):

1. If the asset has a ``HardwiredConnection`` → walk to
   ``disconnect.circuit.breaker``. This is the common case for RTUs,
   water heaters, and exhaust fans.
2. Else if the asset has a ``PowerPort`` cabled to a ``PowerOutlet`` →
   walk to ``outlet.circuit.breaker``. Edge case for cordset HVAC.
3. Else → no breakers; the caller flags ``needs_review=True``.

Returned breaker dicts carry a ``source`` field so the safety sign can render
``hardwired`` vs ``cordset`` provenance.
"""

from __future__ import annotations

from typing import List, Optional

from inventory.models import Asset


def resolve_kill_breakers(asset: Optional[Asset]) -> List[dict]:
    """Return the list of breaker dicts that isolate ``asset``.

    Each dict: ``{panel, position, amperage, breaker_id, source}``.
    """

    if asset is None:
        return []

    hardwired = asset.hardwired_connections.select_related(
        "disconnect",
        "disconnect__circuit",
        "disconnect__circuit__breaker",
        "disconnect__circuit__breaker__panel",
    ).all()
    out: List[dict] = []
    for hc in hardwired:
        circuit = hc.disconnect.circuit
        breaker = circuit.breaker
        panel = breaker.panel
        out.append(
            {
                "breaker_id": breaker.pk,
                "panel": panel.name,
                "position": breaker.position,
                "amperage": breaker.amperage,
                "source": "hardwired",
            }
        )
    if out:
        return out

    # Cordset fallback: PowerPort → Cable → PowerOutlet → circuit.breaker.
    # Defer import to avoid a circular dep at module import time —
    # electrical_circuits already depends on inventory.
    from electrical_circuits.services.power_chain import get_power_chain

    chain = get_power_chain(asset)
    if not chain:
        return out

    panel_hop = chain[0]
    breaker_hop = chain[1]
    panel = panel_hop.obj
    breaker = breaker_hop.obj
    out.append(
        {
            "breaker_id": breaker.pk,
            "panel": panel.name,
            "position": breaker.position,
            "amperage": breaker.amperage,
            "source": "cordset",
        }
    )
    return out
