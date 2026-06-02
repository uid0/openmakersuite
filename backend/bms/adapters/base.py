"""
BmsAdapter abstraction.

Read-only v1. Two methods, two dataclasses. The abstraction exists so
follow-up PRs can add a Carrier i-Vu / Honeywell WEBs / BACnet bridge
without rewriting the binding + sync + decider plumbing on top.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


class BmsAdapterError(Exception):
    """Wrap any upstream / auth / parse failure so callers have a single
    exception type to catch. Decider / sync paths use this to mark a
    binding's last_sync_error without aborting the whole sweep."""


@dataclass(frozen=True)
class BmsThermostatInfo:
    """Discovery result — one entry per thermostat the adapter knows about."""

    device_id: str
    location_id: str
    name: str
    model: str = ""
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BmsThermostatState:
    """Current state — what get_state returns.

    All temperature fields are Fahrenheit. None means the device didn't
    report that value (humidity isn't always present, setpoints may be
    absent when the mode is OFF, etc.). hvac_mode / fan_mode are
    lowercase free-form strings — adapters normalize to common values
    (off/heat/cool/auto) where possible but pass through unknown ones."""

    indoor_temp_f: Optional[float] = None
    indoor_humidity_pct: Optional[float] = None
    cool_setpoint_f: Optional[float] = None
    heat_setpoint_f: Optional[float] = None
    hvac_mode: str = ""
    fan_mode: str = ""
    raw: dict = field(default_factory=dict)


class BmsAdapter(ABC):
    """Abstract base for every BMS integration. Read-only for v1."""

    @abstractmethod
    def list_thermostats(self) -> List[BmsThermostatInfo]:
        """Discover every thermostat reachable through this account.

        Used by the admin "Discover thermostats" action and by the
        operator's first-time binding flow. Implementations should make
        this idempotent and cheap — it'll be called interactively.
        """

    @abstractmethod
    def get_state(self, device_id: str, location_id: str) -> BmsThermostatState:
        """Fetch the current state of one device.

        ``location_id`` is required because Resideo (and most vendors)
        scope device IDs by location. Implementations that don't need it
        accept and ignore the parameter for interface uniformity.
        """
