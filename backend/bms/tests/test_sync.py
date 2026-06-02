"""bms.services.sync_config / sync_all — verify the per-binding fail
isolation, the state-fields upsert, and the parent-config summary."""

from __future__ import annotations

import pytest

from bms.adapters import BmsAdapterError, BmsThermostatState, MockAdapter
from bms.models import BmsConfig, ThermostatBinding
from bms.services import sync_config
from climate.models import Thermostat
from inventory.models import Location

pytestmark = pytest.mark.django_db


# ----- helpers ---------------------------------------------------------------


def _thermostat(label: str) -> Thermostat:
    loc = Location.objects.create(name=f"loc-{label}")
    return Thermostat.objects.create(location=loc, label=label)


def _config() -> BmsConfig:
    return BmsConfig.objects.create(name="test-cfg", adapter_type=BmsConfig.ADAPTER_MOCK)


def _bind(cfg: BmsConfig, label: str, device_id: str) -> ThermostatBinding:
    return ThermostatBinding.objects.create(
        thermostat=_thermostat(label),
        config=cfg,
        external_device_id=device_id,
        external_location_id="loc-1",
    )


# ----- happy path ------------------------------------------------------------


def test_sync_config_upserts_state_onto_each_binding():
    cfg = _config()
    b1 = _bind(cfg, "Wood shop", "dev-a")
    b2 = _bind(cfg, "Electronics", "dev-b")
    adapter = MockAdapter(
        states={
            "dev-a": BmsThermostatState(
                indoor_temp_f=73.0,
                cool_setpoint_f=74.0,
                heat_setpoint_f=68.0,
                hvac_mode="cool",
                raw={"deviceID": "dev-a"},
            ),
            "dev-b": BmsThermostatState(
                indoor_temp_f=70.0,
                hvac_mode="off",
                raw={"deviceID": "dev-b"},
            ),
        }
    )

    result = sync_config(cfg, adapter=adapter)

    assert set(result.succeeded) == {"dev-a", "dev-b"}
    assert result.failed == []

    b1.refresh_from_db()
    b2.refresh_from_db()
    assert b1.indoor_temp_f == 73.0
    assert b1.cool_setpoint_f == 74.0
    assert b1.hvac_mode == "cool"
    assert b1.state_raw == {"deviceID": "dev-a"}
    assert b1.last_sync_error == ""
    assert b1.last_synced_at is not None
    assert b2.indoor_temp_f == 70.0
    assert b2.hvac_mode == "off"


# ----- fail isolation --------------------------------------------------------


def test_one_failing_binding_does_not_abort_the_sweep():
    cfg = _config()
    ok = _bind(cfg, "ok room", "dev-ok")
    bad = _bind(cfg, "bad room", "dev-bad")
    adapter = MockAdapter(
        states={
            "dev-ok": BmsThermostatState(indoor_temp_f=73.0),
            "dev-bad": BmsThermostatState(),  # exercised via raise_for
        },
        raise_for={"dev-bad": BmsAdapterError("simulated upstream 503")},
    )

    result = sync_config(cfg, adapter=adapter)

    assert result.succeeded == ["dev-ok"]
    assert result.failed == [("dev-bad", "simulated upstream 503")]

    ok.refresh_from_db()
    bad.refresh_from_db()
    assert ok.indoor_temp_f == 73.0
    assert ok.last_sync_error == ""
    assert bad.last_sync_error == "simulated upstream 503"
    assert bad.last_synced_at is not None
    assert bad.indoor_temp_f is None  # never overwritten


def test_config_level_summary_records_failure_count():
    cfg = _config()
    _bind(cfg, "a", "dev-a")
    _bind(cfg, "b", "dev-b")
    adapter = MockAdapter(
        states={"dev-a": BmsThermostatState(), "dev-b": BmsThermostatState()},
        raise_for={
            "dev-a": BmsAdapterError("err-a"),
            "dev-b": BmsAdapterError("err-b"),
        },
    )

    sync_config(cfg, adapter=adapter)

    cfg.refresh_from_db()
    assert cfg.last_synced_at is not None
    assert "2/2 bindings failed" in cfg.last_sync_error


def test_no_bindings_records_clean_sync():
    cfg = _config()
    adapter = MockAdapter()
    result = sync_config(cfg, adapter=adapter)
    assert result.succeeded == []
    assert result.failed == []
    cfg.refresh_from_db()
    assert cfg.last_sync_error == ""
    assert cfg.last_synced_at is not None
