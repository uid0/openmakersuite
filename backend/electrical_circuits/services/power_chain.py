"""
Power chain + safety query resolvers.

Walks the direct ``Asset → breaker`` association added when the cable model
was removed, plus ``PowerOutlet.disconnect`` and ``Asset.disconnect`` for the
LOTO-relevant disconnect chain. Resolvers answer the questions a maintainer
needs before touching a panel:

* :func:`get_power_chain` — what feeds this asset?
* :func:`get_devices_on_circuit` / :func:`get_devices_on_breaker` —
  who else is on the same circuit / breaker?
* :func:`get_trip_impact` — if I trip this breaker, what goes dark, and
  which of those are flagged ``is_critical``?
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from django.db.models import QuerySet

from inventory.models import Asset

from ..models import PowerBreaker, PowerCircuit, PowerOutlet, PowerPanel


@dataclass(frozen=True)
class PowerChainHop:
    """A single node in the power chain returned by :func:`get_power_chain`.

    ``kind`` is a short string identifier (``panel``, ``breaker``,
    ``circuit``, ``disconnect``) so callers can render the hop without
    importing the model classes.
    """

    kind: str
    obj: object


def get_power_chain(asset: Asset) -> List[PowerChainHop]:
    """Return the power chain for ``asset``.

    Order: ``[Panel, Breaker, (Circuit), (Disconnect)]``. The circuit and
    disconnect hops are included when they can be resolved unambiguously
    from the asset's breaker + disconnect FKs.

    Returns an empty list if the asset has no ``breaker`` set.
    """

    breaker = (
        PowerBreaker.objects.select_related("panel").filter(pk=asset.breaker_id).first()
        if asset.breaker_id
        else None
    )
    if breaker is None:
        return []

    hops: list[PowerChainHop] = [
        PowerChainHop("panel", breaker.panel),
        PowerChainHop("breaker", breaker),
    ]

    # One breaker can host multi-wire branch circuits; only surface the
    # circuit hop when there is exactly one — otherwise the caller can't
    # tell which circuit the asset is on.
    circuits = list(PowerCircuit.objects.filter(breaker=breaker)[:2])
    if len(circuits) == 1:
        hops.append(PowerChainHop("circuit", circuits[0]))

    if asset.disconnect_id is not None:
        hops.append(PowerChainHop("disconnect", asset.disconnect))

    return hops


def get_devices_on_circuit(circuit: PowerCircuit) -> QuerySet[Asset]:
    """Return Assets whose ``breaker`` is the breaker feeding this circuit."""

    return Asset.objects.filter(breaker=circuit.breaker).distinct()


def get_devices_on_breaker(breaker: PowerBreaker) -> QuerySet[Asset]:
    """Return Assets directly assigned to ``breaker``."""

    return Asset.objects.filter(breaker=breaker).distinct()


def assets_by_outlet(outlet_pks) -> dict[int, list[Asset]]:
    """Return ``{outlet_pk: [Asset, ...]}`` for the given outlets.

    With cables removed there is no direct outlet ↔ asset edge; the closest
    structured equivalent is "assets whose breaker feeds the same circuit as
    this outlet". The result keeps the dict shape the panel topology view
    expects so callers continue to work — outlets with no asset on the
    breaker resolve to an empty list.
    """

    outlet_pks = list(outlet_pks)
    result: dict[int, list[Asset]] = {pk: [] for pk in outlet_pks}
    if not outlet_pks:
        return result

    outlets = PowerOutlet.objects.filter(pk__in=outlet_pks).select_related(
        "circuit",
        "circuit__breaker",
    )
    breaker_to_outlets: dict[int, list[int]] = {}
    for outlet in outlets:
        breaker_id = outlet.circuit.breaker_id if outlet.circuit_id else None
        if breaker_id is None:
            continue
        breaker_to_outlets.setdefault(breaker_id, []).append(outlet.pk)

    if not breaker_to_outlets:
        return result

    assets_by_breaker: dict[int, list[Asset]] = {}
    for asset in Asset.objects.filter(breaker_id__in=breaker_to_outlets):
        assets_by_breaker.setdefault(asset.breaker_id, []).append(asset)

    for breaker_id, outlet_ids in breaker_to_outlets.items():
        assets = assets_by_breaker.get(breaker_id, [])
        for outlet_id in outlet_ids:
            result[outlet_id] = list(assets)

    return result


def get_trip_impact(breaker: PowerBreaker) -> dict:
    """Return the asset-impact summary for tripping this breaker.

    Shape::

        {
            "breaker": <PowerBreaker>,
            "assets": [<Asset>, ...],          # everyone who loses power
            "critical_loads": [<Asset>, ...],  # subset where is_critical=True
        }
    """

    assets = list(get_devices_on_breaker(breaker))
    critical = [a for a in assets if a.is_critical]
    return {
        "breaker": breaker,
        "assets": assets,
        "critical_loads": critical,
    }


__all__ = [
    "PowerChainHop",
    "get_power_chain",
    "get_devices_on_circuit",
    "get_devices_on_breaker",
    "get_trip_impact",
    "assets_by_outlet",
    "PowerPanel",
    "PowerBreaker",
    "PowerCircuit",
    "PowerOutlet",
]
