"""
Power chain + safety query resolvers (oms-i6g).

These walk the topology PowerPanel → PowerBreaker → PowerCircuit →
PowerOutlet ↔ Cable ↔ PowerPort → Asset and answer the questions a
maintainer needs before touching a panel:

* :func:`get_power_chain` — what feeds this asset?
* :func:`get_devices_on_circuit` / :func:`get_devices_on_breaker` —
  who else is on the same circuit / breaker?
* :func:`get_trip_impact` — if I trip this breaker, what goes dark, and
  which of those are flagged ``is_critical``?

The resolvers favour a small number of pre-fetched queries over per-row
lookups so a panel with 40 breakers / 200 outlets / 50 connected devices
stays well under the 50ms AC-3 budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q, QuerySet

from inventory.models import Asset

from ..models import Cable, PowerBreaker, PowerCircuit, PowerOutlet, PowerPanel, PowerPort


@dataclass(frozen=True)
class PowerChainHop:
    """A single node in the power chain returned by :func:`get_power_chain`.

    ``kind`` is a short string identifier (``panel``, ``breaker``,
    ``circuit``, ``outlet``, ``cable``, ``port``) so callers can render
    the hop without having to import the model classes.
    """

    kind: str
    obj: object


def _power_outlet_ct() -> ContentType:
    return ContentType.objects.get_for_model(PowerOutlet)


def _power_port_ct() -> ContentType:
    return ContentType.objects.get_for_model(PowerPort)


def _cable_endpoint_q(content_type: ContentType, object_ids: Iterable[str]) -> Q:
    """Q() matching cables whose endpoint_a OR endpoint_b is in (ct, ids)."""

    ids = list(object_ids)
    return Q(
        endpoint_a_content_type=content_type,
        endpoint_a_object_id__in=ids,
    ) | Q(
        endpoint_b_content_type=content_type,
        endpoint_b_object_id__in=ids,
    )


def _primary_power_port(asset: Asset) -> Optional[PowerPort]:
    """Return the asset's primary PowerPort, or ``None`` if it has none.

    There is no explicit "primary" flag, so we use a stable ordering
    (label, pk) that keeps results deterministic across calls.
    """

    return PowerPort.objects.filter(asset=asset).order_by("label", "pk").first()


def _other_endpoint(cable: Cable, content_type: ContentType, object_id: str):
    """Given one side of a cable, return the object on the other side."""

    if cable.endpoint_a_content_type_id == content_type.id and str(
        cable.endpoint_a_object_id
    ) == str(object_id):
        return cable.endpoint_b
    return cable.endpoint_a


def get_power_chain(asset: Asset) -> List[PowerChainHop]:
    """Return the full power chain for ``asset``.

    Order: ``[Panel, Breaker, Circuit, Outlet, Cable, Port]``.

    Returns an empty list if the asset has no PowerPort, no connecting
    cable, or the cable terminates somewhere other than a PowerOutlet.
    Every hop in the returned list is a real, persisted object.
    """

    port = _primary_power_port(asset)
    if port is None:
        return []

    port_ct = _power_port_ct()
    cable = (
        Cable.objects.filter(cable_type=Cable.CABLE_TYPE_POWER)
        .filter(_cable_endpoint_q(port_ct, [str(port.pk)]))
        .select_related("endpoint_a_content_type", "endpoint_b_content_type")
        .first()
    )
    if cable is None:
        return []

    other = _other_endpoint(cable, port_ct, str(port.pk))
    if not isinstance(other, PowerOutlet):
        return []

    outlet = PowerOutlet.objects.select_related(
        "circuit",
        "circuit__breaker",
        "circuit__breaker__panel",
    ).get(pk=other.pk)
    circuit = outlet.circuit
    breaker = circuit.breaker
    panel = breaker.panel

    return [
        PowerChainHop("panel", panel),
        PowerChainHop("breaker", breaker),
        PowerChainHop("circuit", circuit),
        PowerChainHop("outlet", outlet),
        PowerChainHop("cable", cable),
        PowerChainHop("port", port),
    ]


def _assets_for_outlets(outlet_pks: Iterable[int]) -> QuerySet[Asset]:
    """Return distinct Assets whose PowerPort is cabled to any of these outlets.

    Three queries: cables-by-outlet, ports-by-pk, assets-by-pk. The cable
    join is single-pass over the outlet set (one query, regardless of how
    many outlets), keeping the resolver cheap on dense panels.
    """

    outlet_pks = list(outlet_pks)
    if not outlet_pks:
        return Asset.objects.none()

    outlet_ct = _power_outlet_ct()
    port_ct = _power_port_ct()

    cables = (
        Cable.objects.filter(cable_type=Cable.CABLE_TYPE_POWER)
        .filter(_cable_endpoint_q(outlet_ct, [str(pk) for pk in outlet_pks]))
        .only(
            "endpoint_a_content_type_id",
            "endpoint_a_object_id",
            "endpoint_b_content_type_id",
            "endpoint_b_object_id",
        )
    )

    port_ids: set[int] = set()
    for cable in cables:
        if (
            cable.endpoint_a_content_type_id == port_ct.id
            and cable.endpoint_b_content_type_id == outlet_ct.id
        ):
            port_ids.add(int(cable.endpoint_a_object_id))
        elif (
            cable.endpoint_b_content_type_id == port_ct.id
            and cable.endpoint_a_content_type_id == outlet_ct.id
        ):
            port_ids.add(int(cable.endpoint_b_object_id))

    if not port_ids:
        return Asset.objects.none()

    asset_ids = list(PowerPort.objects.filter(pk__in=port_ids).values_list("asset_id", flat=True))
    return Asset.objects.filter(pk__in=asset_ids).distinct()


def assets_by_outlet(outlet_pks: Iterable[int]) -> dict[int, list[Asset]]:
    """Return ``{outlet_pk: [Asset, ...]}`` for the given outlets.

    Used by the panel topology view to surface "what is plugged in here?"
    per outlet without N+1 query fanout. Outlets with no cabled asset are
    present in the result with an empty list, so callers can rely on
    ``.get(pk, [])`` semantics being unnecessary.

    Only connected power cables are walked (``status='connected'``,
    ``cable_type='power'``). Decommissioned or planned cables don't count
    as a live load.
    """

    outlet_pks = list(outlet_pks)
    result: dict[int, list[Asset]] = {pk: [] for pk in outlet_pks}
    if not outlet_pks:
        return result

    outlet_ct = _power_outlet_ct()
    port_ct = _power_port_ct()

    cables = (
        Cable.objects.filter(
            cable_type=Cable.CABLE_TYPE_POWER,
            status=Cable.STATUS_CONNECTED,
        )
        .filter(_cable_endpoint_q(outlet_ct, [str(pk) for pk in outlet_pks]))
        .only(
            "endpoint_a_content_type_id",
            "endpoint_a_object_id",
            "endpoint_b_content_type_id",
            "endpoint_b_object_id",
        )
    )

    # outlet_pk -> {port_pk, ...}
    outlet_to_ports: dict[int, set[int]] = {}
    all_port_ids: set[int] = set()
    for cable in cables:
        if (
            cable.endpoint_a_content_type_id == outlet_ct.id
            and cable.endpoint_b_content_type_id == port_ct.id
        ):
            outlet_pk = int(cable.endpoint_a_object_id)
            port_pk = int(cable.endpoint_b_object_id)
        elif (
            cable.endpoint_b_content_type_id == outlet_ct.id
            and cable.endpoint_a_content_type_id == port_ct.id
        ):
            outlet_pk = int(cable.endpoint_b_object_id)
            port_pk = int(cable.endpoint_a_object_id)
        else:
            continue
        outlet_to_ports.setdefault(outlet_pk, set()).add(port_pk)
        all_port_ids.add(port_pk)

    if not all_port_ids:
        return result

    # Bulk-load ports + assets in two queries.
    port_to_asset_id = dict(
        PowerPort.objects.filter(pk__in=all_port_ids).values_list("pk", "asset_id")
    )
    asset_ids = {aid for aid in port_to_asset_id.values() if aid is not None}
    assets_by_id = {a.pk: a for a in Asset.objects.filter(pk__in=asset_ids)}

    for outlet_pk, port_pks in outlet_to_ports.items():
        seen: set = set()
        for port_pk in port_pks:
            asset_id = port_to_asset_id.get(port_pk)
            if asset_id is None or asset_id in seen:
                continue
            asset = assets_by_id.get(asset_id)
            if asset is None:
                continue
            seen.add(asset_id)
            result.setdefault(outlet_pk, []).append(asset)

    return result


def get_devices_on_circuit(circuit: PowerCircuit) -> QuerySet[Asset]:
    """Return Assets whose PowerPort is cabled to any outlet on this circuit."""

    outlet_pks = PowerOutlet.objects.filter(circuit=circuit).values_list("pk", flat=True)
    return _assets_for_outlets(outlet_pks)


def get_devices_on_breaker(breaker: PowerBreaker) -> QuerySet[Asset]:
    """Return Assets connected to any outlet fed by this breaker.

    Spans every PowerCircuit on the breaker (multi-wire branch circuits).
    """

    outlet_pks = PowerOutlet.objects.filter(circuit__breaker=breaker).values_list("pk", flat=True)
    return _assets_for_outlets(outlet_pks)


def get_trip_impact(breaker: PowerBreaker) -> dict:
    """Return the asset-impact summary for tripping this breaker.

    Shape::

        {
            "breaker": <PowerBreaker>,
            "assets": [<Asset>, ...],          # everyone who loses power
            "critical_loads": [<Asset>, ...],  # subset where is_critical=True
        }

    V1 only considers direct power dependencies (the assets physically
    plugged into outlets on this breaker). Tracing transitive network
    dependencies (a camera that loses connectivity because its WAP lost
    power) is intentionally out of scope here — see [4/7] for that.
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
    "PowerPort",
]
