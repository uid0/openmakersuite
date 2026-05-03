/**
 * ForgeKey Device Detail Page
 *
 * Live occupancy chart + bidirectional control panel for a single ForgeKey
 * device. Polls the occupancy endpoint on a fixed interval; SSE/WebSocket
 * push is a follow-up. Staff/superuser only.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Navigate, useParams } from 'react-router-dom';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import {
  ForgeKeyCommandResponse,
  ForgeKeyDevice,
  ForgeKeyOccupancyResponse,
  forgekeyAPI,
} from '../services/api';

const POLL_INTERVAL_MS = 30_000;

type ControlKey = 'restart' | 'capture' | 'blink' | 'ota';

interface ControlState {
  pending: boolean;
  lastResult: ForgeKeyCommandResponse | null;
  lastError: string | null;
}

const initialControl: ControlState = { pending: false, lastResult: null, lastError: null };

const ForgeKeyDeviceDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const isStaff = typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' && localStorage.getItem('is_superuser') === 'true';

  const [device, setDevice] = useState<ForgeKeyDevice | null>(null);
  const [occupancy, setOccupancy] = useState<ForgeKeyOccupancyResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [otaInputs, setOtaInputs] = useState({ version: '', url: '' });
  const [controls, setControls] = useState<Record<ControlKey, ControlState>>({
    restart: initialControl,
    capture: initialControl,
    blink: initialControl,
    ota: initialControl,
  });

  const loadAll = useCallback(async () => {
    if (!id) return;
    try {
      const [deviceRes, occRes] = await Promise.all([
        forgekeyAPI.getDevice(id),
        forgekeyAPI.getOccupancy(id, '24h'),
      ]);
      setDevice(deviceRes.data);
      setOccupancy(occRes.data);
      setLoadError(null);
    } catch (err: any) {
      setLoadError(err?.response?.data?.detail || 'Failed to load device.');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    if (!id || (!isStaff && !isSuperuser)) return;
    loadAll();
    const handle = window.setInterval(loadAll, POLL_INTERVAL_MS);
    return () => window.clearInterval(handle);
  }, [id, isStaff, isSuperuser, loadAll]);

  const chartData = useMemo(() => {
    if (!occupancy) return [];
    let running = 0;
    return occupancy.events.map((event) => {
      running += event.occupancy_delta;
      return {
        ts: event.event_timestamp_utc,
        occupancy: Math.max(running, 0),
        delta: event.occupancy_delta,
      };
    });
  }, [occupancy]);

  const runCommand = useCallback(
    async (key: ControlKey, fn: () => Promise<{ data: ForgeKeyCommandResponse }>) => {
      setControls((prev) => ({
        ...prev,
        [key]: { pending: true, lastResult: prev[key].lastResult, lastError: null },
      }));
      try {
        const response = await fn();
        setControls((prev) => ({
          ...prev,
          [key]: { pending: false, lastResult: response.data, lastError: null },
        }));
      } catch (err: any) {
        setControls((prev) => ({
          ...prev,
          [key]: {
            pending: false,
            lastResult: prev[key].lastResult,
            lastError: err?.response?.data?.detail || 'Command failed.',
          },
        }));
      }
    },
    [],
  );

  if (!isStaff && !isSuperuser) {
    return <Navigate to="/" replace />;
  }

  if (!id) {
    return <p style={{ padding: '1.5rem' }}>Missing device id.</p>;
  }

  return (
    <div style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <h1>ForgeKey Device</h1>
      {loadError && <p style={{ color: '#c0392b' }}>{loadError}</p>}
      {loading && !device ? (
        <p>Loading…</p>
      ) : device ? (
        <>
          <section>
            <h2 style={{ marginBottom: '0.25rem' }}>{device.name || device.mac_address}</h2>
            <p style={{ color: '#555', margin: 0, fontFamily: 'monospace' }}>
              {device.mac_address} · {device.device_type_name ?? '—'}
            </p>
            <p style={{ color: '#555', margin: 0 }}>
              Last seen: {device.last_seen ? new Date(device.last_seen).toLocaleString() : '—'} ·{' '}
              <span style={{ color: device.is_online ? '#1f8a3a' : '#777' }}>
                {device.is_online ? 'Online' : 'Offline'}
              </span>
            </p>
          </section>

          <section aria-label="Occupancy chart">
            <h3>Occupancy (last 24h)</h3>
            <p style={{ color: '#555', marginTop: 0 }}>
              Current occupancy:{' '}
              <strong data-testid="current-occupancy">{occupancy?.current_occupancy ?? 0}</strong>
            </p>
            {chartData.length > 0 ? (
              <div style={{ width: '100%', height: 240 }}>
                <ResponsiveContainer>
                  <LineChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
                    <XAxis
                      dataKey="ts"
                      tickFormatter={(v: string) => new Date(v).toLocaleTimeString()}
                    />
                    <YAxis allowDecimals={false} domain={[0, 'auto']} />
                    <Tooltip
                      labelFormatter={(v: string) => new Date(v).toLocaleString()}
                      formatter={(value: any) => [`${value} people`, 'Occupancy']}
                    />
                    <Line
                      type="stepAfter"
                      dataKey="occupancy"
                      stroke="#228be6"
                      strokeWidth={2}
                      dot={false}
                      isAnimationActive={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <p style={{ color: '#777' }}>
                No occupancy events in the last 24 hours.
              </p>
            )}
          </section>

          <CapabilitiesSection
            device={device}
            onBlink={() =>
              runCommand('blink', () =>
                forgekeyAPI.blink(id, { pattern: 'sos', duration_s: 5 }),
              )
            }
            blinkState={controls.blink}
          />

          <section aria-label="Device controls">
            <h3>Device Controls</h3>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
              <ControlButton
                label="Restart"
                state={controls.restart}
                onClick={() => runCommand('restart', () => forgekeyAPI.restart(id))}
              />
              <ControlButton
                label="Capture photo"
                state={controls.capture}
                onClick={() => runCommand('capture', () => forgekeyAPI.capturePhoto(id))}
              />
              <ControlButton
                label="Blink"
                state={controls.blink}
                onClick={() =>
                  runCommand('blink', () =>
                    forgekeyAPI.blink(id, { pattern: 'sos', duration_s: 5 }),
                  )
                }
              />
            </div>

            <div style={{ marginTop: '1rem' }}>
              <h4 style={{ marginBottom: '0.25rem' }}>OTA firmware update</h4>
              <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                <input
                  type="text"
                  placeholder="version (e.g. 2.3.4)"
                  value={otaInputs.version}
                  onChange={(e) => setOtaInputs((p) => ({ ...p, version: e.target.value }))}
                />
                <input
                  type="text"
                  placeholder="signed firmware URL"
                  value={otaInputs.url}
                  onChange={(e) => setOtaInputs((p) => ({ ...p, url: e.target.value }))}
                  style={{ minWidth: '20rem' }}
                />
                <ControlButton
                  label="Send OTA"
                  state={controls.ota}
                  disabled={!otaInputs.version || !otaInputs.url}
                  onClick={() =>
                    runCommand('ota', () =>
                      forgekeyAPI.firmwareUpdate(id, {
                        version: otaInputs.version,
                        url: otaInputs.url,
                      }),
                    )
                  }
                />
              </div>
            </div>
          </section>
        </>
      ) : (
        <p>Device not found.</p>
      )}
    </div>
  );
};

// Per-capability render registry. Capabilities not present here render the
// generic "UI not yet implemented" badge defined in CapabilitiesSection.
const KNOWN_CAPABILITIES: Record<string, { icon: string; label: string }> = {
  people_counter: { icon: '👥', label: 'People counter' },
  mmwave_presence: { icon: '📡', label: 'mmWave presence' },
  button: { icon: '🔘', label: 'Button' },
  status_led: { icon: '💡', label: 'Status LED' },
};

interface CapabilitiesSectionProps {
  device: ForgeKeyDevice;
  blinkState: ControlState;
  onBlink: () => void;
}

const CapabilitiesSection: React.FC<CapabilitiesSectionProps> = ({
  device,
  blinkState,
  onBlink,
}) => {
  const capabilities = device.capabilities || [];
  if (capabilities.length === 0) {
    return (
      <section aria-label="Device capabilities">
        <h3>Capabilities</h3>
        <p style={{ color: '#777' }}>
          No capabilities announced yet. Devices publish their capability set
          on boot via the <code>capabilities</code> MQTT topic.
        </p>
      </section>
    );
  }

  return (
    <section aria-label="Device capabilities">
      <h3>Capabilities</h3>
      <p style={{ color: '#555', marginTop: 0 }}>
        Last announced:{' '}
        {device.capabilities_announced_at
          ? new Date(device.capabilities_announced_at).toLocaleString()
          : '—'}
      </p>
      <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        {capabilities.map((cap) => (
          <li
            key={cap}
            data-testid={`capability-${cap}`}
            style={{ border: '1px solid #ddd', borderRadius: '4px', padding: '0.5rem 0.75rem' }}
          >
            <CapabilityRow
              capability={cap}
              blinkState={blinkState}
              onBlink={onBlink}
              device={device}
            />
          </li>
        ))}
      </ul>
    </section>
  );
};

interface CapabilityRowProps {
  capability: string;
  device: ForgeKeyDevice;
  blinkState: ControlState;
  onBlink: () => void;
}

const CapabilityRow: React.FC<CapabilityRowProps> = ({
  capability,
  device,
  blinkState,
  onBlink,
}) => {
  const meta = KNOWN_CAPABILITIES[capability];
  const header = meta ? (
    <strong>
      <span aria-hidden="true">{meta.icon} </span>
      {meta.label}
    </strong>
  ) : (
    <span
      title="UI not yet implemented"
      style={{ color: '#777', fontStyle: 'italic' }}
      data-testid={`capability-generic-${capability}`}
    >
      Detected: {capability}
    </span>
  );

  let body: React.ReactNode = null;
  if (capability === 'people_counter') {
    body = (
      <small style={{ color: '#555' }}>
        See the occupancy chart above for live counts.
      </small>
    );
  } else if (capability === 'mmwave_presence') {
    body = <MmwavePresenceWidget device={device} />;
  } else if (capability === 'button') {
    body = <ButtonEventWidget device={device} />;
  } else if (capability === 'status_led') {
    body = (
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <span style={{ color: '#555' }}>State: —</span>
        <button type="button" onClick={onBlink} disabled={blinkState.pending}>
          {blinkState.pending ? 'Blinking…' : 'Blink LED'}
        </button>
        {blinkState.lastError && (
          <small style={{ color: '#c0392b' }}>{blinkState.lastError}</small>
        )}
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
      {header}
      {body}
    </div>
  );
};

const MmwavePresenceWidget: React.FC<{ device: ForgeKeyDevice }> = () => (
  // Backend processor for mmwave events not yet wired (paired firmware bead).
  // Render a placeholder so the section is present the moment a device
  // announces this capability; data plane lands in a follow-up.
  <small style={{ color: '#777' }}>
    Presence: <strong>—</strong> · last changed: —
  </small>
);

const ButtonEventWidget: React.FC<{ device: ForgeKeyDevice }> = () => (
  <small style={{ color: '#777' }}>No recent button events.</small>
);

interface ControlButtonProps {
  label: string;
  state: ControlState;
  onClick: () => void;
  disabled?: boolean;
}

const ControlButton: React.FC<ControlButtonProps> = ({ label, state, onClick, disabled }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem' }}>
    <button type="button" disabled={disabled || state.pending} onClick={onClick}>
      {state.pending ? `${label}…` : label}
    </button>
    {state.lastError && (
      <small style={{ color: '#c0392b' }}>{state.lastError}</small>
    )}
    {state.lastResult && !state.lastError && (
      <small style={{ color: '#1f8a3a' }}>
        sent {new Date(state.lastResult.dispatched_at).toLocaleTimeString()}
      </small>
    )}
  </div>
);

export default ForgeKeyDeviceDetailPage;
