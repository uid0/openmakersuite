"""MockAdapter — make sure the contract is honored so tests built on top
of it match real-adapter behavior."""

from __future__ import annotations

import pytest

from bms.adapters import BmsAdapterError, BmsThermostatState, MockAdapter

pytestmark = pytest.mark.django_db


def test_list_thermostats_returns_preset_entries():
    a = MockAdapter(
        states={
            "dev-a": BmsThermostatState(indoor_temp_f=72.0),
            "dev-b": BmsThermostatState(indoor_temp_f=68.0),
        },
        names={"dev-a": "Room A"},
    )
    infos = a.list_thermostats()
    assert {i.device_id for i in infos} == {"dev-a", "dev-b"}
    # Names fall through to a default when not preset.
    by_id = {i.device_id: i for i in infos}
    assert by_id["dev-a"].name == "Room A"
    assert "dev-b" in by_id["dev-b"].name


def test_get_state_returns_preset():
    a = MockAdapter(states={"dev-a": BmsThermostatState(indoor_temp_f=72.0)})
    state = a.get_state("dev-a", "any-loc")
    assert state.indoor_temp_f == 72.0


def test_get_state_unknown_device_raises_adapter_error():
    a = MockAdapter()
    with pytest.raises(BmsAdapterError):
        a.get_state("nope", "any-loc")


def test_get_state_per_device_injected_error_raises():
    """Lets bms_sync_all tests exercise the per-binding fail-isolation path."""
    a = MockAdapter(
        states={"dev-ok": BmsThermostatState(indoor_temp_f=72.0)},
        raise_for={"dev-broken": BmsAdapterError("simulated upstream 503")},
    )
    a._states["dev-broken"] = BmsThermostatState()
    with pytest.raises(BmsAdapterError, match="503"):
        a.get_state("dev-broken", "any-loc")
    # Sibling still works.
    assert a.get_state("dev-ok", "any-loc").indoor_temp_f == 72.0
