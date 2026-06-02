"""ResideoAdapter — exercise the auth + GET flow with a mocked
``requests.Session`` so the tests don't talk to the real Honeywell
endpoints. Covers the proactive token refresh and the 401-retry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from bms.adapters import BmsAdapterError, ResideoAdapter
from bms.models import BmsConfig

pytestmark = pytest.mark.django_db


# ----- helpers ---------------------------------------------------------------


def _fake_response(status: int, body):
    r = MagicMock()
    r.status_code = status
    r.text = str(body)
    r.json.return_value = body
    return r


def _config_with_tokens(*, expires_in: int = 1800) -> BmsConfig:
    cfg = BmsConfig.objects.create(name="test", adapter_type=BmsConfig.ADAPTER_RESIDEO)
    cfg.set_tokens(
        access_token="A1",
        refresh_token="R1",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    )
    return cfg


@pytest.fixture(autouse=True)
def _resideo_settings(settings):
    settings.RESIDEO_CLIENT_ID = "test-client"
    settings.RESIDEO_CLIENT_SECRET = "test-secret"
    settings.RESIDEO_API_BASE = "https://api.honeywell.test"


# ----- list_thermostats ------------------------------------------------------


def test_list_thermostats_walks_locations_and_filters_to_thermostats():
    http = MagicMock()
    http.get.return_value = _fake_response(
        200,
        [
            {
                "locationID": 12345,
                "devices": [
                    {
                        "deviceID": "TCC-1",
                        "deviceClass": "Thermostat",
                        "userDefinedDeviceName": "Wood shop",
                        "deviceModel": "T9",
                    },
                    {
                        "deviceID": "WLD-1",
                        "deviceClass": "LeakDetector",
                        "userDefinedDeviceName": "Under sink",
                    },
                ],
            }
        ],
    )
    cfg = _config_with_tokens()
    adapter = ResideoAdapter(cfg, http=http)

    infos = adapter.list_thermostats()

    assert len(infos) == 1
    assert infos[0].device_id == "TCC-1"
    assert infos[0].location_id == "12345"
    assert infos[0].name == "Wood shop"
    assert infos[0].model == "T9"


# ----- get_state -------------------------------------------------------------


def test_get_state_parses_temp_humidity_setpoints_and_modes():
    http = MagicMock()
    http.get.return_value = _fake_response(
        200,
        {
            "deviceID": "TCC-1",
            "indoorTemperature": 73.5,
            "indoorHumidity": 41,
            "changeableValues": {
                "coolSetpoint": 74,
                "heatSetpoint": 68,
                "mode": "Cool",
            },
            "fan": {"changeableValues": {"mode": "Auto"}},
        },
    )
    cfg = _config_with_tokens()
    adapter = ResideoAdapter(cfg, http=http)

    state = adapter.get_state("TCC-1", "12345")

    assert state.indoor_temp_f == 73.5
    assert state.indoor_humidity_pct == 41.0
    assert state.cool_setpoint_f == 74.0
    assert state.heat_setpoint_f == 68.0
    assert state.hvac_mode == "cool"  # normalized to lowercase
    assert state.fan_mode == "auto"
    assert state.raw["deviceID"] == "TCC-1"


def test_get_state_handles_missing_humidity_and_setpoints():
    http = MagicMock()
    http.get.return_value = _fake_response(
        200,
        {
            "deviceID": "TCC-2",
            "indoorTemperature": 70.0,
            # No indoorHumidity → keep None
            "changeableValues": {},  # device is off — no setpoints
        },
    )
    cfg = _config_with_tokens()
    adapter = ResideoAdapter(cfg, http=http)
    state = adapter.get_state("TCC-2", "12345")
    assert state.indoor_temp_f == 70.0
    assert state.indoor_humidity_pct is None
    assert state.cool_setpoint_f is None
    assert state.heat_setpoint_f is None


def test_get_state_requires_location_id():
    cfg = _config_with_tokens()
    adapter = ResideoAdapter(cfg)
    with pytest.raises(BmsAdapterError, match="device_id and location_id"):
        adapter.get_state("TCC-1", "")


# ----- token refresh ---------------------------------------------------------


def test_proactive_refresh_when_token_within_buffer():
    """When the access token is past (or close to) expiry, the adapter
    calls /oauth2/token before issuing the GET."""
    http = MagicMock()
    http.post.return_value = _fake_response(
        200,
        {
            "access_token": "A2",
            "refresh_token": "R2",
            "expires_in": 1800,
        },
    )
    http.get.return_value = _fake_response(200, [])
    cfg = _config_with_tokens(expires_in=-10)  # already expired

    adapter = ResideoAdapter(cfg, http=http)
    adapter.list_thermostats()

    # Token refresh was issued once.
    assert http.post.call_count == 1
    refresh_url = http.post.call_args[0][0]
    assert refresh_url.endswith("/oauth2/token")
    # And the new tokens were persisted.
    cfg.refresh_from_db()
    assert cfg.access_token() == "A2"
    assert cfg.refresh_token() == "R2"


def test_401_triggers_force_refresh_and_retry():
    http = MagicMock()
    http.post.return_value = _fake_response(
        200,
        {"access_token": "A2", "refresh_token": "R2", "expires_in": 1800},
    )
    http.get.side_effect = [
        _fake_response(401, "unauthorized"),
        _fake_response(200, []),
    ]
    cfg = _config_with_tokens()
    adapter = ResideoAdapter(cfg, http=http)

    adapter.list_thermostats()

    assert http.get.call_count == 2  # initial 401 + retry
    assert http.post.call_count == 1  # forced refresh after 401


def test_refresh_failure_surfaces_as_adapter_error():
    http = MagicMock()
    http.post.return_value = _fake_response(400, {"error": "invalid_grant"})
    cfg = _config_with_tokens(expires_in=-10)
    adapter = ResideoAdapter(cfg, http=http)

    with pytest.raises(BmsAdapterError, match="token refresh failed"):
        adapter.list_thermostats()


def test_missing_settings_block_token_refresh(settings):
    settings.RESIDEO_CLIENT_ID = ""
    settings.RESIDEO_CLIENT_SECRET = ""
    cfg = _config_with_tokens(expires_in=-10)
    adapter = ResideoAdapter(cfg, http=MagicMock())
    with pytest.raises(BmsAdapterError, match="RESIDEO_CLIENT_ID"):
        adapter.list_thermostats()


def test_construct_with_wrong_adapter_type_raises():
    cfg = BmsConfig.objects.create(name="wrong", adapter_type=BmsConfig.ADAPTER_MOCK)
    with pytest.raises(BmsAdapterError, match="adapter_type=resideo"):
        ResideoAdapter(cfg)


def test_authorize_url_includes_client_id_and_redirect():
    url = ResideoAdapter.authorize_url("https://cb.example/callback", state="s1")
    assert "client_id=test-client" in url
    assert "redirect_uri=https%3A%2F%2Fcb.example%2Fcallback" in url
    assert "state=s1" in url
    assert url.startswith("https://api.honeywell.test/oauth2/authorize?")
