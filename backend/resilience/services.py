"""User-facing service-status registry over the circuit breakers.

``resilience.circuit`` knows about *breakers* — machine names like ``mqtt``
or ``webhook:12``. Members do not. This module maps breakers onto the
capability a person actually loses when one trips ("Device control",
"Turning equipment on and off remotely") so the web app and ScanTTY can say
so out loud instead of failing silently.

Two kinds of entry live in :data:`SERVICE_REGISTRY`:

* a fixed ``breaker`` name — one breaker, one service;
* a ``breaker_prefix`` for a dynamic family (``webhook:<id>``), which
  aggregates every member breaker: the family reads degraded when *any*
  member is open/half-open, and reports ``degraded_count`` /
  ``total_count`` so the UI can say "3 of 12 endpoints degraded".

Deliberately non-sensitive: labels and states only. Never breaker config,
URLs, credentials, or endpoint hostnames — the status endpoint is readable
by every authenticated member.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .circuit import STATE_CLOSED, STATE_HALF_OPEN, STATE_OPEN, BaseStorage, default_storage

#: States a member counts as "degraded" in. Half-open is degraded on
#: purpose: the dependency is still on trial and calls may still fail.
DEGRADED_STATES = (STATE_OPEN, STATE_HALF_OPEN)


@dataclass(frozen=True)
class ServiceDescriptor:
    """One user-facing capability backed by one breaker or one breaker family."""

    key: str
    """Stable machine key for the frontend/ScanTTY to switch on."""

    label: str
    """User-facing name, e.g. "Device control"."""

    description: str
    """What the user loses while this is degraded."""

    breaker: str = ""
    """Exact breaker name. Mutually exclusive with ``breaker_prefix``."""

    breaker_prefix: str = ""
    """Prefix for a dynamic family, e.g. ``"webhook:"``."""

    def __post_init__(self) -> None:
        if bool(self.breaker) == bool(self.breaker_prefix):
            raise ValueError(
                f"ServiceDescriptor '{self.key}' must set exactly one of "
                "breaker / breaker_prefix"
            )


SERVICE_REGISTRY: tuple[ServiceDescriptor, ...] = (
    ServiceDescriptor(
        key="device_control",
        label="Device control",
        description="Turning equipment on and off remotely",
        breaker="mqtt",
    ),
    ServiceDescriptor(
        key="webhooks",
        label="Webhook delivery",
        description="Notifying connected external systems",
        breaker_prefix="webhook:",
    ),
    ServiceDescriptor(
        key="whmcs",
        label="Maker Box billing",
        description="Maker Box subscription and billing lookups",
        breaker="whmcs",
    ),
    ServiceDescriptor(
        key="common_api",
        label="Member directory",
        description="Member lookups from the shared directory",
        breaker="common_api",
    ),
    # Seam for PR2: once outbound mail routes through a breaker, add
    #     ServiceDescriptor(key="email", label="Email delivery", ...,
    #                       breaker="postmark")
    # here. Nothing else changes — the status endpoint renders whatever this
    # tuple contains.
)


def _breakers_enabled() -> bool:
    return bool(getattr(settings, "CIRCUIT_BREAKERS_ENABLED", True))


def _member_states(descriptor: ServiceDescriptor, storage: BaseStorage) -> dict[str, str]:
    """Current state of every known member breaker for ``descriptor``."""
    if descriptor.breaker_prefix:
        return storage.states(descriptor.breaker_prefix)
    return {descriptor.breaker: storage.state(descriptor.breaker)}


def _aggregate_state(member_states: dict[str, str]) -> str:
    """Worst state across the family: open beats half-open beats closed."""
    values = set(member_states.values())
    if STATE_OPEN in values:
        return STATE_OPEN
    if STATE_HALF_OPEN in values:
        return STATE_HALF_OPEN
    return STATE_CLOSED


def _latest_transitions(pairs: set[tuple[str, str]]) -> dict[str, tuple]:
    """Most recent ``CircuitBreakerEvent`` per breaker name, for the given
    ``(name, to_state)`` pairs — one query for all names, never N+1.

    ``order_by("name", "-created_at")`` rides the model's
    ``(name, -created_at)`` index, so taking the first row per name is an
    index scan rather than a sort.
    """
    if not pairs:
        return {}

    from .models import CircuitBreakerEvent

    query = Q()
    for name, state in sorted(pairs):
        query |= Q(name=name, to_state=state)

    latest: dict[str, tuple] = {}
    rows = (
        CircuitBreakerEvent.objects.filter(query)
        .order_by("name", "-created_at")
        .values_list("name", "created_at", "detail")
    )
    for name, created_at, detail in rows.iterator():
        if name not in latest:
            latest[name] = (created_at, detail or None)
    return latest


def _attribution_name(member_states: dict[str, str], state: str) -> str:
    """Which member breaker explains the service's state — the first member
    sitting in that state (deterministic by name so repeated calls agree)."""
    matching = sorted(name for name, value in member_states.items() if value == state)
    return matching[0] if matching else ""


def service_statuses(storage: BaseStorage | None = None) -> list[dict]:
    """Per-service status rows for every entry in :data:`SERVICE_REGISTRY`.

    When ``CIRCUIT_BREAKERS_ENABLED`` is off, breaker state is not consulted
    at all and every service reports closed/healthy — stale keys from a
    previous run with the feature on must not surface as a fake outage.
    """
    enabled = _breakers_enabled()
    storage = storage if storage is not None else default_storage()

    resolved: list[tuple[ServiceDescriptor, dict[str, str], str, str]] = []
    for descriptor in SERVICE_REGISTRY:
        if not enabled:
            # Families have no known members while disabled; a fixed breaker
            # is still one service, reported closed. No attribution either:
            # a transition recorded while the feature was on is not history
            # for a state we are asserting rather than observing.
            member_states = {descriptor.breaker: STATE_CLOSED} if descriptor.breaker else {}
            resolved.append((descriptor, member_states, STATE_CLOSED, ""))
            continue
        member_states = _member_states(descriptor, storage)
        state = _aggregate_state(member_states)
        resolved.append((descriptor, member_states, state, _attribution_name(member_states, state)))

    transitions = _latest_transitions({(name, state) for _, _, state, name in resolved if name})

    rows: list[dict] = []
    for descriptor, member_states, state, attributed_to in resolved:
        since, last_error = transitions.get(attributed_to, (None, None))
        rows.append(
            {
                "key": descriptor.key,
                "label": descriptor.label,
                "description": descriptor.description,
                "state": state,
                "healthy": state == STATE_CLOSED,
                "since": since,
                "last_error": last_error,
                "degraded_count": sum(
                    1 for value in member_states.values() if value in DEGRADED_STATES
                ),
                "total_count": len(member_states),
            }
        )
    return rows


def status_snapshot(storage: BaseStorage | None = None) -> dict:
    """The full payload behind ``GET /api/resilience/status/``."""
    services = service_statuses(storage)
    return {
        "degraded": any(not service["healthy"] for service in services),
        "checked_at": timezone.now(),
        "services": services,
    }
