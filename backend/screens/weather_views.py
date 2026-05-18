"""
Server-side OpenWeather proxy for the kiosk weather block.

Kiosks live on TVs inside the makerspace and can't usefully embed
third-party weather widgets — CSP rules on the kiosk page block
iframe embeds, which is why the previous ``SharedWeatherBlock``
rendered as a black square. This endpoint fetches Current Weather +
brief forecast from OpenWeather server-side, caches the result for a
configurable window (so we don't blow through the free-tier rate
limit when ten kiosks all refresh at once), and returns a small JSON
payload the frontend can render natively.

Settings (env-driven; see settings.py):
- OPENWEATHER_API_KEY (required for live data; absent → endpoint
  returns 503 with a clear "not configured" error so the kiosk can
  show "Weather unavailable" instead of a generic crash)
- OPENWEATHER_LAT / OPENWEATHER_LON (decimal degrees; required if no
  zip is set)
- OPENWEATHER_ZIP (US zip,country e.g. "75201,us"; used if no lat/lon)
- OPENWEATHER_UNITS ("imperial" default, "metric" or "standard")
- OPENWEATHER_CACHE_SECONDS (default 600 = 10 minutes)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.cache import cache

import requests
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

logger = logging.getLogger(__name__)

_OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
_CACHE_KEY = "screens.weather.current.v1"


def _fetch_current_weather() -> Dict[str, Any]:
    """Pull live current-weather payload from OpenWeather.

    Raises requests.HTTPError on non-2xx responses; the calling view
    converts that into a 502 so the kiosk can fall back gracefully.
    """
    api_key = getattr(settings, "OPENWEATHER_API_KEY", "") or ""
    if not api_key:
        raise RuntimeError("OPENWEATHER_API_KEY is not configured")

    units = getattr(settings, "OPENWEATHER_UNITS", "imperial")
    params: Dict[str, Any] = {"appid": api_key, "units": units}

    lat = getattr(settings, "OPENWEATHER_LAT", None)
    lon = getattr(settings, "OPENWEATHER_LON", None)
    zip_q = getattr(settings, "OPENWEATHER_ZIP", "") or ""

    if lat is not None and lon is not None:
        params["lat"] = lat
        params["lon"] = lon
    elif zip_q:
        params["zip"] = zip_q
    else:
        raise RuntimeError(
            "Weather location not configured (set OPENWEATHER_LAT/LON or OPENWEATHER_ZIP)"
        )

    resp = requests.get(_OPENWEATHER_URL, params=params, timeout=10)
    resp.raise_for_status()
    raw = resp.json()

    # Project just the fields the kiosk renders. Keeps the cache footprint
    # small and the frontend contract narrow.
    weather_block = (raw.get("weather") or [{}])[0]
    main = raw.get("main") or {}
    wind = raw.get("wind") or {}
    sys_block = raw.get("sys") or {}
    return {
        "location_name": raw.get("name") or "",
        "country": sys_block.get("country") or "",
        "condition": weather_block.get("main") or "",
        "description": weather_block.get("description") or "",
        "icon": weather_block.get("icon") or "",
        "temperature": main.get("temp"),
        "feels_like": main.get("feels_like"),
        "humidity": main.get("humidity"),
        "wind_speed": wind.get("speed"),
        "units": units,
        "observed_at": raw.get("dt"),
    }


@api_view(["GET"])
@permission_classes([AllowAny])
def current_weather(request):
    """``GET /api/screens/weather/current/`` — cached current weather.

    Public/unauthenticated because kiosks have no operator session;
    the same rationale as the kiosk_payload endpoint. Cache TTL is
    long enough that an unauthenticated scraper can't drive up our
    OpenWeather quota.
    """
    cache_seconds = int(getattr(settings, "OPENWEATHER_CACHE_SECONDS", 600))
    cached: Optional[Dict[str, Any]] = cache.get(_CACHE_KEY)
    if cached is not None:
        return Response({**cached, "cache_hit": True})

    try:
        payload = _fetch_current_weather()
    except RuntimeError as exc:
        # Configuration problem — operator-fixable, surface clearly.
        return Response({"error": str(exc), "code": "weather_not_configured"}, status=503)
    except requests.HTTPError as exc:
        logger.warning("OpenWeather HTTP error: %s", exc)
        return Response(
            {"error": "upstream weather provider returned an error", "code": "weather_upstream"},
            status=502,
        )
    except requests.RequestException as exc:
        logger.warning("OpenWeather network error: %s", exc)
        return Response(
            {"error": "weather provider unreachable", "code": "weather_unreachable"}, status=502
        )

    cache.set(_CACHE_KEY, payload, timeout=cache_seconds)
    return Response({**payload, "cache_hit": False})
