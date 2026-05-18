/**
 * Kiosk weather panel — server-side OpenWeather proxy.
 *
 * Replaces the previous iframe-based implementation that CSP rules
 * blocked on most TVs (rendered as a black square). The backend
 * (`/api/screens/weather/current/`) fetches + caches OpenWeather and
 * returns a small JSON payload; this component renders it natively
 * with big TV-friendly typography.
 *
 * `weatherUrl` prop is kept for backward compatibility with existing
 * `ScreenPayload.weather_url` callers — when an OpenWeather payload
 * is unavailable we fall back to an iframe-with-warning so screens
 * still produce visible output during the rollover window.
 */
import axios from 'axios';
import React, { useCallback, useEffect, useState } from 'react';

import api from '../../services/api';

interface SharedWeatherBlockProps {
  /** Legacy iframe URL. Used only as a last-resort fallback. */
  weatherUrl?: string;
}

interface WeatherPayload {
  location_name: string;
  country: string;
  condition: string;
  description: string;
  icon: string;
  temperature: number | null;
  feels_like: number | null;
  humidity: number | null;
  wind_speed: number | null;
  units: 'imperial' | 'metric' | 'standard';
  observed_at: number | null;
  cache_hit?: boolean;
}

const REFRESH_MS = 10 * 60 * 1000; // 10 minutes — matches backend cache TTL.

function tempUnitSymbol(units: WeatherPayload['units']): string {
  if (units === 'metric') return '°C';
  if (units === 'standard') return 'K';
  return '°F';
}

function speedUnitLabel(units: WeatherPayload['units']): string {
  return units === 'imperial' ? 'mph' : 'm/s';
}

const SharedWeatherBlock: React.FC<SharedWeatherBlockProps> = ({ weatherUrl }) => {
  const [data, setData] = useState<WeatherPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = await api.get<WeatherPayload>('/screens/weather/current/');
      setData(resp.data);
      setError(null);
    } catch (err) {
      // Backend already returns clean error envelopes; we just need
      // *something* to differentiate "config missing" from "outage".
      const code = axios.isAxiosError(err)
        ? (err.response?.data as { code?: string } | undefined)?.code
        : undefined;
      setError(code || 'weather_error');
      setData(null);
    }
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [load]);

  if (data) {
    const tempUnit = tempUnitSymbol(data.units);
    const speedUnit = speedUnitLabel(data.units);
    return (
      <div
        data-testid="shared-weather-native"
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 16,
          color: '#fff',
        }}
      >
        {data.icon && (
          <img
            src={`https://openweathermap.org/img/wn/${data.icon}@4x.png`}
            alt={data.description || data.condition}
            style={{ width: 200, height: 200 }}
          />
        )}
        {data.temperature != null && (
          <div style={{ fontSize: 144, fontWeight: 700, lineHeight: 1 }}>
            {Math.round(data.temperature)}
            {tempUnit}
          </div>
        )}
        {data.condition && (
          <div style={{ fontSize: 48, textTransform: 'capitalize' }}>
            {data.description || data.condition}
          </div>
        )}
        <div style={{ fontSize: 24, opacity: 0.8, display: 'flex', gap: 24, flexWrap: 'wrap', justifyContent: 'center' }}>
          {data.location_name && (
            <span>
              {data.location_name}
              {data.country ? `, ${data.country}` : ''}
            </span>
          )}
          {data.feels_like != null && (
            <span>
              feels like {Math.round(data.feels_like)}
              {tempUnit}
            </span>
          )}
          {data.humidity != null && <span>{data.humidity}% humidity</span>}
          {data.wind_speed != null && (
            <span>
              wind {Math.round(data.wind_speed)} {speedUnit}
            </span>
          )}
        </div>
      </div>
    );
  }

  // Error / loading states — keep the panel visible rather than going
  // black, which is what the iframe did and made the bug invisible.
  if (error === 'weather_not_configured') {
    return (
      <div data-testid="shared-weather-not-configured" style={{ fontSize: 28, opacity: 0.7 }}>
        Weather not configured — set OPENWEATHER_API_KEY on the backend.
      </div>
    );
  }

  if (error) {
    // Hard error: optionally fall back to the legacy iframe URL if one
    // was provided. Better than a blank screen on a TV nobody's watching.
    if (weatherUrl) {
      return (
        <iframe
          data-testid="shared-weather-iframe-fallback"
          title="Shared Weather (fallback)"
          src={weatherUrl}
          style={{ width: '100%', height: '100%', minHeight: '70vh', border: 0, background: '#fff' }}
        />
      );
    }
    return (
      <div data-testid="shared-weather-error" style={{ fontSize: 32, opacity: 0.7 }}>
        Weather unavailable
      </div>
    );
  }

  return (
    <div data-testid="shared-weather-loading" style={{ fontSize: 28, opacity: 0.5 }}>
      Loading weather…
    </div>
  );
};

export default SharedWeatherBlock;
