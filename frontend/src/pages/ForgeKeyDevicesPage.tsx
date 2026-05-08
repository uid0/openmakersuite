/**
 * ForgeKey Devices Admin Page
 *
 * Staff-only screen for assigning ESP32 devices (e.g. ForgeKey traffic
 * counters) to a default Location.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { ForgeKeyDevice, forgekeyAPI, inventoryAPI } from '../services/api';
import { Location } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const ForgeKeyDevicesPage: React.FC = () => {
  const isStaff = typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser = typeof window !== 'undefined' && localStorage.getItem('is_superuser') === 'true';

  const [devices, setDevices] = useState<ForgeKeyDevice[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [capabilityFilter, setCapabilityFilter] = useState<string>('');

  const load = useCallback(
    async (capability: string) => {
      setLoading(true);
      try {
        const [devRes, locRes] = await Promise.all([
          forgekeyAPI.listDevices(capability ? { capability } : undefined),
          inventoryAPI.listLocations(),
        ]);
        const rows = Array.isArray(devRes.data) ? devRes.data : devRes.data.results ?? [];
        setDevices(rows);
        setLocations(locRes.data.results);
        setError(null);
      } catch (err: any) {
        setError(extractErrorMessage(err, 'Failed to load ForgeKey devices.'));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (isStaff || isSuperuser) {
      load(capabilityFilter);
    }
  }, [load, isStaff, isSuperuser, capabilityFilter]);

  const locationLookup = useMemo(() => {
    const map = new Map<number, Location>();
    locations.forEach((loc) => map.set(loc.id, loc));
    return map;
  }, [locations]);

  const handleLocationChange = useCallback(
    async (deviceId: string, raw: string) => {
      const newLocation = raw === '' ? null : Number(raw);
      setSavingId(deviceId);
      setSavedId(null);
      try {
        const response = await forgekeyAPI.updateDevice(deviceId, { location: newLocation });
        setDevices((prev) =>
          prev.map((d) => (d.id === deviceId ? { ...d, ...response.data } : d)),
        );
        setSavedId(deviceId);
        setError(null);
      } catch (err: any) {
        setError(extractErrorMessage(err, 'Failed to update device location.'));
      } finally {
        setSavingId(null);
      }
    },
    [],
  );

  if (!isStaff && !isSuperuser) {
    return <Navigate to="/" replace />;
  }

  return (
    <div style={{ padding: '1.5rem' }}>
      <h1>ForgeKey Devices</h1>
      <p style={{ color: '#555' }}>
        Assign each ForgeKey device to a default location (area). Required so that
        traffic counts and sensor readings roll up to the right space.
      </p>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', margin: '0.5rem 0' }}>
        <label htmlFor="capability-filter" style={{ color: '#555' }}>
          Filter by capability:
        </label>
        <input
          id="capability-filter"
          type="text"
          placeholder="e.g. mmwave_presence"
          value={capabilityFilter}
          onChange={(e) => setCapabilityFilter(e.target.value.trim())}
          style={{ minWidth: '14rem' }}
        />
        {capabilityFilter && (
          <button type="button" onClick={() => setCapabilityFilter('')}>
            Clear
          </button>
        )}
      </div>

      {error && <p style={{ color: '#c0392b' }}>{error}</p>}

      {loading ? (
        <p>Loading…</p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '0.25rem' }}>Device</th>
              <th style={{ textAlign: 'left', padding: '0.25rem' }}>MAC</th>
              <th style={{ textAlign: 'left', padding: '0.25rem' }}>Type</th>
              <th style={{ textAlign: 'left', padding: '0.25rem' }}>Capabilities</th>
              <th style={{ textAlign: 'left', padding: '0.25rem' }}>Location</th>
              <th style={{ textAlign: 'left', padding: '0.25rem' }}>Last seen</th>
              <th style={{ textAlign: 'left', padding: '0.25rem' }}>Online</th>
            </tr>
          </thead>
          <tbody>
            {devices.map((device) => {
              const currentLocation = device.location != null ? locationLookup.get(device.location) : null;
              const isSaving = savingId === device.id;
              const justSaved = savedId === device.id;
              return (
                <tr key={device.id} style={{ borderTop: '1px solid #ddd' }}>
                  <td style={{ padding: '0.25rem' }}>
                    <Link to={`/facilities/forgekey-devices/${device.id}`}>
                      {device.name || <em style={{ color: '#777' }}>unnamed</em>}
                    </Link>
                  </td>
                  <td style={{ padding: '0.25rem', fontFamily: 'monospace' }}>{device.mac_address}</td>
                  <td style={{ padding: '0.25rem' }}>{device.device_type_name ?? '—'}</td>
                  <td style={{ padding: '0.25rem' }}>
                    {device.capabilities && device.capabilities.length > 0 ? (
                      <span style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem' }}>
                        {device.capabilities.map((cap) => (
                          <span
                            key={cap}
                            style={{
                              background: '#eef',
                              padding: '0.05rem 0.4rem',
                              borderRadius: '3px',
                              fontSize: '0.8rem',
                              fontFamily: 'monospace',
                            }}
                          >
                            {cap}
                          </span>
                        ))}
                      </span>
                    ) : (
                      <span style={{ color: '#999' }}>—</span>
                    )}
                  </td>
                  <td style={{ padding: '0.25rem' }}>
                    <select
                      aria-label={`Location for ${device.name || device.mac_address}`}
                      value={device.location ?? ''}
                      onChange={(e) => handleLocationChange(device.id, e.target.value)}
                      disabled={isSaving}
                    >
                      <option value="">—</option>
                      {locations.map((loc) => (
                        <option key={loc.id} value={loc.id}>
                          {loc.name}
                        </option>
                      ))}
                    </select>
                    {isSaving && <span style={{ marginLeft: '0.5rem', color: '#777' }}>saving…</span>}
                    {justSaved && !isSaving && (
                      <span style={{ marginLeft: '0.5rem', color: '#1f8a3a' }}>saved</span>
                    )}
                    {currentLocation && currentLocation.parent_name && (
                      <div style={{ fontSize: '0.8rem', color: '#777' }}>
                        in {currentLocation.parent_name}
                      </div>
                    )}
                  </td>
                  <td style={{ padding: '0.25rem' }}>
                    {device.last_seen ? new Date(device.last_seen).toLocaleString() : '—'}
                  </td>
                  <td style={{ padding: '0.25rem' }}>
                    <span
                      style={{
                        display: 'inline-block',
                        padding: '0.1rem 0.4rem',
                        borderRadius: '4px',
                        background: device.is_online ? '#1f8a3a' : '#777',
                        color: 'white',
                        fontSize: '0.85rem',
                      }}
                    >
                      {device.is_online ? 'Online' : 'Offline'}
                    </span>
                  </td>
                </tr>
              );
            })}
            {devices.length === 0 && (
              <tr>
                <td colSpan={7} style={{ padding: '0.5rem', color: '#777' }}>
                  {capabilityFilter
                    ? `No devices match capability "${capabilityFilter}".`
                    : 'No ForgeKey devices registered yet.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
};

export default ForgeKeyDevicesPage;
