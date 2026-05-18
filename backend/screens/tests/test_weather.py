"""Tests for the OpenWeather proxy endpoint."""

from __future__ import annotations

from unittest.mock import patch

from django.core.cache import cache
from django.urls import reverse

import pytest
import requests
from rest_framework.test import APIClient


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture(autouse=True)
def _clear_weather_cache():
    cache.delete("screens.weather.current.v1")
    yield
    cache.delete("screens.weather.current.v1")


def _fake_openweather_payload():
    return {
        "name": "Carrollton",
        "sys": {"country": "US"},
        "weather": [{"main": "Clouds", "description": "scattered clouds", "icon": "03d"}],
        "main": {"temp": 78.4, "feels_like": 79.1, "humidity": 55},
        "wind": {"speed": 7.2},
        "dt": 1747523400,
    }


def test_weather_returns_503_when_unconfigured(api_client, settings):
    settings.OPENWEATHER_API_KEY = ""
    resp = api_client.get(reverse("weather-current"))
    assert resp.status_code == 503
    assert resp.json()["code"] == "weather_not_configured"


def test_weather_returns_payload_when_configured(api_client, settings):
    settings.OPENWEATHER_API_KEY = "test-key"
    settings.OPENWEATHER_LAT = 32.95
    settings.OPENWEATHER_LON = -96.89
    settings.OPENWEATHER_UNITS = "imperial"

    with patch("screens.weather_views.requests.get") as get:
        get.return_value.status_code = 200
        get.return_value.json.return_value = _fake_openweather_payload()
        get.return_value.raise_for_status.return_value = None

        resp = api_client.get(reverse("weather-current"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["location_name"] == "Carrollton"
    assert body["country"] == "US"
    assert body["condition"] == "Clouds"
    assert body["description"] == "scattered clouds"
    assert body["icon"] == "03d"
    assert body["temperature"] == 78.4
    assert body["feels_like"] == 79.1
    assert body["humidity"] == 55
    assert body["wind_speed"] == 7.2
    assert body["units"] == "imperial"
    assert body["cache_hit"] is False


def test_weather_caches_between_calls(api_client, settings):
    settings.OPENWEATHER_API_KEY = "test-key"
    settings.OPENWEATHER_LAT = 32.95
    settings.OPENWEATHER_LON = -96.89

    with patch("screens.weather_views.requests.get") as get:
        get.return_value.status_code = 200
        get.return_value.json.return_value = _fake_openweather_payload()
        get.return_value.raise_for_status.return_value = None

        first = api_client.get(reverse("weather-current"))
        second = api_client.get(reverse("weather-current"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    # Upstream only hit once thanks to the cache.
    assert get.call_count == 1


def test_weather_returns_502_when_upstream_fails(api_client, settings):
    settings.OPENWEATHER_API_KEY = "test-key"
    settings.OPENWEATHER_LAT = 32.95
    settings.OPENWEATHER_LON = -96.89

    with patch("screens.weather_views.requests.get") as get:
        get.return_value.raise_for_status.side_effect = requests.HTTPError("boom")

        resp = api_client.get(reverse("weather-current"))

    assert resp.status_code == 502
    assert resp.json()["code"] == "weather_upstream"


def test_weather_requires_location(api_client, settings):
    settings.OPENWEATHER_API_KEY = "test-key"
    settings.OPENWEATHER_LAT = None
    settings.OPENWEATHER_LON = None
    settings.OPENWEATHER_ZIP = ""

    resp = api_client.get(reverse("weather-current"))
    assert resp.status_code == 503
    assert resp.json()["code"] == "weather_not_configured"
