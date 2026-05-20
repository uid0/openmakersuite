"""Resolve the breaker that de-energizes a thermostat's controlled HVAC asset.

Reads the asset's direct ``breaker`` FK (set when the cable/cordset model was
removed). If the asset has no breaker assigned, the caller flags
``needs_review=True``.

Returned breaker dicts retain a ``source`` field for compatibility with the
safety-sign renderer — always ``"direct"`` now that asset → breaker is a
single FK lookup.
"""

from __future__ import annotations

from typing import List, Optional

from inventory.models import Asset


def resolve_kill_breakers(asset: Optional[Asset]) -> List[dict]:
    """Return the list of breaker dicts that isolate ``asset``.

    Each dict: ``{panel, position, amperage, breaker_id, source}``.
    """

    if asset is None or asset.breaker_id is None:
        return []

    # Defer import to avoid a circular dep at module import time —
    # electrical_circuits already depends on inventory.
    from electrical_circuits.models import PowerBreaker

    breaker = PowerBreaker.objects.select_related("panel").filter(pk=asset.breaker_id).first()
    if breaker is None or breaker.panel is None:
        return []

    return [
        {
            "breaker_id": breaker.pk,
            "panel": breaker.panel.name,
            "position": breaker.position,
            "amperage": breaker.amperage,
            "source": "direct",
        }
    ]
