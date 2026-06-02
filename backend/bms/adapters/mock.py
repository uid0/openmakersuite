"""In-memory adapter for tests and local dev.

Holds whatever device states the test set up. Doesn't touch the
network, doesn't care about tokens. Use ``MockAdapter.preset(...)``
or seed the constructor for a deterministic state map.
"""

from __future__ import annotations

from typing import Dict, List

from .base import BmsAdapter, BmsAdapterError, BmsThermostatInfo, BmsThermostatState


class MockAdapter(BmsAdapter):
    def __init__(
        self,
        states: Dict[str, BmsThermostatState] | None = None,
        names: Dict[str, str] | None = None,
        location_id: str = "mock-loc-1",
        raise_for: Dict[str, Exception] | None = None,
    ):
        self._states = dict(states or {})
        self._names = dict(names or {})
        self._location_id = location_id
        # Per-device exception to raise — exercises the per-binding
        # error-isolation path in bms_sync_all without affecting siblings.
        self._raise_for = dict(raise_for or {})

    def preset(self, device_id: str, state: BmsThermostatState, name: str = ""):
        self._states[device_id] = state
        if name:
            self._names[device_id] = name

    def list_thermostats(self) -> List[BmsThermostatInfo]:
        return [
            BmsThermostatInfo(
                device_id=did,
                location_id=self._location_id,
                name=self._names.get(did, f"Mock thermostat {did}"),
                model="MockModel",
            )
            for did in sorted(self._states)
        ]

    def get_state(self, device_id: str, location_id: str) -> BmsThermostatState:
        if device_id in self._raise_for:
            raise self._raise_for[device_id]
        if device_id not in self._states:
            raise BmsAdapterError(f"mock: unknown device_id {device_id!r}")
        return self._states[device_id]
