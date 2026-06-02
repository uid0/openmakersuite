"""
Resideo / Honeywell Home Pro adapter.

Auth: OAuth 2.0 authorization_code grant; access tokens TTL ~30 min,
refresh tokens are long-lived but revocable from the Resideo dashboard.
The adapter refreshes the access token on its own when it's within a
60-second buffer of expiry; refresh failures fall through as
BmsAdapterError so the caller (sync sweep / decider) can record the
state and move on without crashing.

Read-only v1 exposes two methods:

* ``list_thermostats()`` — GET /v2/locations + walk each location's
  ``devices[]`` for entries that look like thermostats. One round trip
  total (Resideo includes devices inline on the locations response).
* ``get_state(device_id, location_id)`` — GET /v2/devices/thermostats/{id}
  with ``locationId`` as a query param (Resideo requires it).

Field mapping deliberately strict: unknown ``operationMode`` values are
passed through as-is rather than normalized so the operator sees the
real string in the admin and can file an issue with a sample. The full
response is also stashed on the binding as ``state_raw`` for debugging.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List

from django.conf import settings

import requests

from ..models import BmsConfig
from .base import BmsAdapter, BmsAdapterError, BmsThermostatInfo, BmsThermostatState

logger = logging.getLogger(__name__)

# Resideo endpoints. Override via settings.RESIDEO_API_BASE for a
# sandbox / staging deployment.
_DEFAULT_BASE = "https://api.honeywell.com"
_TOKEN_PATH = "/oauth2/token"
_LOCATIONS_PATH = "/v2/locations"
_THERMOSTAT_PATH = "/v2/devices/thermostats/{device_id}"
_AUTHORIZE_PATH = "/oauth2/authorize"

# Refresh tokens this much before the recorded expiry so a sync that
# runs right at the boundary doesn't fail with a stale token.
_REFRESH_BUFFER = timedelta(seconds=60)

# Resideo throttles to ~1 req/sec per app and a daily ceiling; keep a
# generous request timeout but don't hold a worker forever if the
# upstream stalls.
_HTTP_TIMEOUT_SECONDS = 15


class ResideoAdapter(BmsAdapter):
    def __init__(self, config: BmsConfig, http: requests.Session | None = None):
        if config.adapter_type != BmsConfig.ADAPTER_RESIDEO:
            raise BmsAdapterError(
                f"ResideoAdapter requires adapter_type=resideo, " f"got {config.adapter_type!r}"
            )
        self.config = config
        self._http = http or requests.Session()

    # ----- discovery + state ------------------------------------------------

    def list_thermostats(self) -> List[BmsThermostatInfo]:
        data = self._get(_LOCATIONS_PATH)
        if not isinstance(data, list):
            raise BmsAdapterError(f"Resideo /v2/locations returned non-list: {type(data).__name__}")
        out: List[BmsThermostatInfo] = []
        for loc in data:
            loc_id = str(loc.get("locationID") or "")
            for dev in loc.get("devices") or []:
                if not self._is_thermostat(dev):
                    continue
                out.append(
                    BmsThermostatInfo(
                        device_id=str(dev.get("deviceID") or ""),
                        location_id=loc_id,
                        name=str(dev.get("userDefinedDeviceName") or dev.get("name") or ""),
                        model=str(dev.get("deviceModel") or ""),
                        raw=dev,
                    )
                )
        return out

    def get_state(self, device_id: str, location_id: str) -> BmsThermostatState:
        if not device_id or not location_id:
            raise BmsAdapterError("Resideo get_state requires both device_id and location_id")
        path = _THERMOSTAT_PATH.format(device_id=device_id)
        dev = self._get(path, params={"locationId": location_id})
        if not isinstance(dev, dict):
            raise BmsAdapterError(f"Resideo {path} returned non-dict: {type(dev).__name__}")

        # Resideo's `changeableValues` is the writable target state; the
        # top-level `indoorTemperature` / `indoorHumidity` are the
        # measured current values. Thermostats without a humidity sensor
        # omit the latter — keep it None rather than fabricating 0.
        cv = dev.get("changeableValues") or {}
        return BmsThermostatState(
            indoor_temp_f=_safe_float(dev.get("indoorTemperature")),
            indoor_humidity_pct=_safe_float(dev.get("indoorHumidity")),
            cool_setpoint_f=_safe_float(cv.get("coolSetpoint")),
            heat_setpoint_f=_safe_float(cv.get("heatSetpoint")),
            hvac_mode=str(cv.get("mode") or "").lower(),
            fan_mode=str(
                (dev.get("fan") or {}).get("changeableValues", {}).get("mode") or ""
            ).lower(),
            raw=dev,
        )

    # ----- auth -------------------------------------------------------------

    def _ensure_access_token(self) -> str:
        """Refresh the access token if missing or close to expiry. Returns
        the token to use for the next request."""
        access = self.config.access_token()
        expires_at = self.config.access_token_expires_at
        if access and expires_at and expires_at - _REFRESH_BUFFER > _now_utc():
            return access
        return self._refresh()

    def _refresh(self) -> str:
        refresh_token = self.config.refresh_token()
        if not refresh_token:
            raise BmsAdapterError(
                f"Resideo config {self.config.name!r} has no refresh token — "
                "run `manage.py bms_resideo_auth` to bootstrap."
            )
        client_id = getattr(settings, "RESIDEO_CLIENT_ID", "") or ""
        client_secret = getattr(settings, "RESIDEO_CLIENT_SECRET", "") or ""
        if not client_id or not client_secret:
            raise BmsAdapterError(
                "RESIDEO_CLIENT_ID / RESIDEO_CLIENT_SECRET are not configured "
                "on the backend — set them in the prod .env."
            )

        url = self._base() + _TOKEN_PATH
        try:
            resp = self._http.post(
                url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                auth=(client_id, client_secret),
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BmsAdapterError(f"Resideo token refresh transport: {exc}") from exc
        if resp.status_code != 200:
            raise BmsAdapterError(
                f"Resideo token refresh failed: HTTP {resp.status_code} {resp.text[:200]}"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise BmsAdapterError(f"Resideo token refresh returned non-JSON body: {exc}") from exc

        access = str(body.get("access_token") or "")
        if not access:
            raise BmsAdapterError(f"Resideo token refresh returned no access_token: {body!r}")
        # Some refresh responses omit refresh_token (Resideo keeps the
        # existing one valid in that case). Pass None to keep ours.
        new_refresh = body.get("refresh_token")
        expires_in = int(body.get("expires_in") or 0)
        expires_at = _now_utc() + timedelta(seconds=max(60, expires_in))
        self.config.set_tokens(
            access_token=access,
            refresh_token=str(new_refresh) if new_refresh else None,
            expires_at=expires_at,
        )
        return access

    def exchange_authorization_code(self, code: str, redirect_uri: str) -> None:
        """Walk the one-time code → tokens dance and persist the result.

        Called by the bms_resideo_auth management command. Not part of
        the BmsAdapter ABC because the OAuth handshake is provider-specific.
        """
        client_id = getattr(settings, "RESIDEO_CLIENT_ID", "") or ""
        client_secret = getattr(settings, "RESIDEO_CLIENT_SECRET", "") or ""
        if not client_id or not client_secret:
            raise BmsAdapterError("RESIDEO_CLIENT_ID / RESIDEO_CLIENT_SECRET are not configured.")
        url = self._base() + _TOKEN_PATH
        try:
            resp = self._http.post(
                url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                auth=(client_id, client_secret),
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BmsAdapterError(f"Resideo code exchange transport: {exc}") from exc
        if resp.status_code != 200:
            raise BmsAdapterError(
                f"Resideo code exchange failed: HTTP {resp.status_code} " f"{resp.text[:200]}"
            )
        body = resp.json()
        access = str(body.get("access_token") or "")
        refresh = str(body.get("refresh_token") or "")
        if not access or not refresh:
            raise BmsAdapterError(
                "Resideo code exchange returned an incomplete token pair "
                "(missing access_token or refresh_token)."
            )
        expires_in = int(body.get("expires_in") or 0)
        expires_at = _now_utc() + timedelta(seconds=max(60, expires_in))
        self.config.set_tokens(
            access_token=access,
            refresh_token=refresh,
            expires_at=expires_at,
        )

    @classmethod
    def authorize_url(cls, redirect_uri: str, state: str = "") -> str:
        """Build the URL the operator opens in a browser to start OAuth."""
        client_id = getattr(settings, "RESIDEO_CLIENT_ID", "") or ""
        if not client_id:
            raise BmsAdapterError("RESIDEO_CLIENT_ID is not configured.")
        base = getattr(settings, "RESIDEO_API_BASE", "") or _DEFAULT_BASE
        from urllib.parse import urlencode

        qs = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                **({"state": state} if state else {}),
            }
        )
        return f"{base}{_AUTHORIZE_PATH}?{qs}"

    # ----- internals --------------------------------------------------------

    def _base(self) -> str:
        return getattr(settings, "RESIDEO_API_BASE", "") or _DEFAULT_BASE

    def _get(self, path: str, params: dict | None = None):
        token = self._ensure_access_token()
        url = self._base() + path
        # Resideo requires the API key as `apikey=` on every request, even
        # alongside a Bearer token.
        merged_params = dict(params or {})
        merged_params["apikey"] = getattr(settings, "RESIDEO_CLIENT_ID", "") or ""
        try:
            resp = self._http.get(
                url,
                params=merged_params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BmsAdapterError(f"Resideo GET {path} transport: {exc}") from exc
        if resp.status_code == 401:
            # Token might've been revoked / rotated since the last refresh.
            # One retry after a force-refresh; if that fails too we surface.
            logger.info("Resideo GET %s: 401 — forcing refresh + retry", path)
            self.config.access_token_expires_at = _now_utc() - timedelta(seconds=1)
            self.config.save(update_fields=["access_token_expires_at", "updated_at"])
            token = self._ensure_access_token()
            try:
                resp = self._http.get(
                    url,
                    params=merged_params,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=_HTTP_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                raise BmsAdapterError(f"Resideo GET {path} retry transport: {exc}") from exc
        if resp.status_code != 200:
            raise BmsAdapterError(
                f"Resideo GET {path} failed: HTTP {resp.status_code} {resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise BmsAdapterError(f"Resideo GET {path} returned non-JSON body: {exc}") from exc

    @staticmethod
    def _is_thermostat(device: dict) -> bool:
        # Resideo's `deviceClass` is the cleanest signal; fall back to the
        # `deviceType` string for older firmware that doesn't populate it.
        cls = str(device.get("deviceClass") or "").lower()
        if cls == "thermostat":
            return True
        typ = str(device.get("deviceType") or "").lower()
        return "thermostat" in typ


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
