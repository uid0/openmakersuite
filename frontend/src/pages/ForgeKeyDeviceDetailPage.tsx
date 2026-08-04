/**
 * ForgeKey Device Detail Page
 *
 * Live occupancy chart + bidirectional control panel for a single ForgeKey
 * device. Polls the occupancy endpoint on a fixed interval; SSE/WebSocket
 * push is a follow-up. Staff/superuser only.
 */
import { Paper, Text } from '@mantine/core';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import DeviceControlsCard from '../components/DeviceControlsCard';
import DeviceLifecycleCard from '../components/DeviceLifecycleCard';
import DeviceSectionGate from '../components/DeviceSectionGate';
import IndicatorManagementCard from '../components/IndicatorManagementCard';
import IndicatorSwatch from '../components/IndicatorSwatch';
import ServiceUnavailableNotice, {
  DEVICE_CONTROL_UNAVAILABLE,
} from '../components/ServiceUnavailableNotice';
import WorkspacePage from '../components/landing/WorkspacePage';
import { useServiceStatus } from '../hooks/useServiceStatus';
import {
  ForgeKeyCommandResponse,
  ForgeKeyDevice,
  ForgeKeyDeviceType,
  ForgeKeyIndicatorState,
  ForgeKeyOccupancyResponse,
  ForgeKeyTemperatureResponse,
  forgekeyAPI,
} from '../services/api';
import { resolveDeviceTypeCode, sectionRelevance } from '../utils/deviceSectionRelevance';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const POLL_INTERVAL_MS = 30_000;

type ControlKey = 'ota' | 'blink';

interface ControlState {
  pending: boolean;
  lastResult: ForgeKeyCommandResponse | null;
  lastError: string | null;
}

const initialControl: ControlState = { pending: false, lastResult: null, lastError: null };

const ForgeKeyDeviceDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  // Every control on this page ends in an MQTT publish — forgekey's
  // device_commands service routes all of them through the "mqtt" circuit
  // breaker — so when device_control is degraded none of them can reach the
  // hardware, and a click would only buy a broker timeout.
  const { isDegraded } = useServiceStatus();
  const deviceControlDown = isDegraded('device_control');
  const isStaff = typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' && localStorage.getItem('is_superuser') === 'true';

  const [device, setDevice] = useState<ForgeKeyDevice | null>(null);
  const [deviceTypes, setDeviceTypes] = useState<ForgeKeyDeviceType[]>([]);
  const [occupancy, setOccupancy] = useState<ForgeKeyOccupancyResponse | null>(null);
  const [temperature, setTemperature] = useState<ForgeKeyTemperatureResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [otaInputs, setOtaInputs] = useState({ version: '', url: '' });
  const [controls, setControls] = useState<Record<ControlKey, ControlState>>({
    ota: initialControl,
    blink: initialControl,
  });

  const loadAll = useCallback(async () => {
    if (!id) return;
    try {
      const [deviceRes, occRes, tempRes, typesRes] = await Promise.all([
        forgekeyAPI.getDevice(id),
        forgekeyAPI.getOccupancy(id, '24h'),
        forgekeyAPI.getTemperature(id, '24h'),
        forgekeyAPI.listDeviceTypes(),
      ]);
      setDevice(deviceRes.data);
      setOccupancy(occRes.data);
      setTemperature(tempRes.data);
      const typesData = typesRes?.data;
      setDeviceTypes(Array.isArray(typesData) ? typesData : (typesData?.results ?? []));
      setLoadError(null);
    } catch (err: any) {
      setLoadError(extractErrorMessage(err, 'Failed to load device.'));
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

  const tempChartData = useMemo(() => {
    if (!temperature) return [];
    return temperature.readings.map((reading) => ({
      ts: reading.recorded_at,
      temp: reading.temperature_c,
      humidity: reading.humidity_percent,
    }));
  }, [temperature]);

  // Decide which type-specific sections apply to this device, keying off the
  // announced capabilities first and the resolved device_type code as a
  // fallback (utils/deviceSectionRelevance). Irrelevant sections are greyed
  // out rather than hidden.
  const deviceTypeCode = useMemo(
    () => (device ? resolveDeviceTypeCode(device, deviceTypes) : null),
    [device, deviceTypes],
  );
  const occupancyRelevance = useMemo(
    () => (device ? sectionRelevance('occupancy', device, deviceTypeCode) : 'unknown'),
    [device, deviceTypeCode],
  );
  const temperatureRelevance = useMemo(
    () => (device ? sectionRelevance('temperature', device, deviceTypeCode) : 'unknown'),
    [device, deviceTypeCode],
  );

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
            lastError: extractErrorMessage(err, 'Command failed.'),
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
    return (
      <WorkspacePage
        testId="forgekey-device-detail-page"
        hero={{
          eyebrow: 'Facilities · ForgeKey device',
          title: 'ForgeKey device',
          description: 'Missing device id.',
        }}
      >
        <Paper withBorder p="md">
          <Text c="red">Missing device id.</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      testId="forgekey-device-detail-page"
      hero={{
        eyebrow: device
          ? `Facilities · ForgeKey · ${device.device_type_name ?? 'device'}`
          : 'Facilities · ForgeKey device',
        title: device ? device.name || device.mac_address : 'ForgeKey device',
        description: device
          ? `${device.mac_address} · ${device.is_online ? 'Online' : 'Offline'} · last seen ${
              device.last_seen ? new Date(device.last_seen).toLocaleString() : '—'
            }`
          : 'Loading device…',
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {loadError && <p style={{ color: '#c0392b' }}>{loadError}</p>}
      {loading && !device ? (
        <p>Loading…</p>
      ) : device ? (
        <>

          <DeviceSectionGate relevant={occupancyRelevance} testId="section-gate-occupancy">
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
                      labelFormatter={(v) => new Date(String(v)).toLocaleString()}
                      formatter={(value) => [`${value} people`, 'Occupancy']}
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
          </DeviceSectionGate>

          {tempChartData.length > 0 ? (
            <DeviceSectionGate relevant={temperatureRelevance} testId="section-gate-temperature">
            <section aria-label="Temperature chart">
              <h3>Temperature (last 24h)</h3>
              <p style={{ color: '#555', marginTop: 0 }}>
                Latest:{' '}
                <strong data-testid="latest-temperature">
                  {temperature?.latest_temperature_c != null
                    ? `${temperature.latest_temperature_c.toFixed(1)}°C`
                    : '—'}
                </strong>
                {temperature?.latest_humidity_percent != null && (
                  <> · {temperature.latest_humidity_percent.toFixed(0)}% RH</>
                )}
              </p>
              <div style={{ width: '100%', height: 240 }}>
                <ResponsiveContainer>
                  <LineChart data={tempChartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
                    <XAxis
                      dataKey="ts"
                      tickFormatter={(v: string) => new Date(v).toLocaleTimeString()}
                    />
                    <YAxis
                      yAxisId="temp"
                      domain={['auto', 'auto']}
                      tickFormatter={(v) => `${v}°`}
                    />
                    <YAxis
                      yAxisId="humidity"
                      orientation="right"
                      domain={[0, 100]}
                      tickFormatter={(v) => `${v}%`}
                    />
                    <Tooltip labelFormatter={(v) => new Date(String(v)).toLocaleString()} />
                    <Line
                      yAxisId="temp"
                      type="monotone"
                      dataKey="temp"
                      name="Temp °C"
                      stroke="#e8590c"
                      strokeWidth={2}
                      dot={false}
                      isAnimationActive={false}
                    />
                    <Line
                      yAxisId="humidity"
                      type="monotone"
                      dataKey="humidity"
                      name="Humidity %"
                      stroke="#228be6"
                      strokeWidth={2}
                      dot={false}
                      isAnimationActive={false}
                      connectNulls
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </section>
            </DeviceSectionGate>
          ) : temperatureRelevance === 'no' ? (
            <DeviceSectionGate relevant="no" testId="section-gate-temperature">
              <section aria-label="Temperature chart">
                <h3>Temperature (last 24h)</h3>
              </section>
            </DeviceSectionGate>
          ) : null}

          <DeviceControlsCard device={device} />

          <IndicatorManagementCard device={device} onChanged={loadAll} />

          <DeviceLifecycleCard
            device={device}
            onChanged={loadAll}
            onDeleted={() => navigate('/facilities/forgekey-devices')}
          />

          <CapabilitiesSection
            device={device}
            onBlink={() =>
              runCommand('blink', () =>
                forgekeyAPI.blink(id, { pattern: 'sos', duration_s: 5 }),
              )
            }
            blinkState={controls.blink}
            deviceControlDown={deviceControlDown}
          />

          <section aria-label="OTA firmware update">
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
                style={{ flex: '1 1 20rem', minWidth: '12rem' }}
              />
              <ControlButton
                label="Send OTA"
                state={controls.ota}
                disabled={!otaInputs.version || !otaInputs.url || deviceControlDown}
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
            <ServiceUnavailableNotice
              service="device_control"
              message={DEVICE_CONTROL_UNAVAILABLE}
              testId="ota-device-control-notice"
            />
          </section>
        </>
      ) : (
        <p>Device not found.</p>
      )}
      </div>
    </WorkspacePage>
  );
};

// Per-capability render registry. Capabilities not present here render the
// generic "UI not yet implemented" badge defined in CapabilitiesSection.
const KNOWN_CAPABILITIES: Record<string, { icon: string; label: string }> = {
  people_counter: { icon: '👥', label: 'People counter' },
  mmwave_presence: { icon: '📡', label: 'mmWave presence' },
  button: { icon: '🔘', label: 'Button' },
  status_led: { icon: '💡', label: 'Status LED' },
  power_relay: { icon: '🔌', label: 'Power relay' },
};

interface CapabilitiesSectionProps {
  device: ForgeKeyDevice;
  blinkState: ControlState;
  onBlink: () => void;
  /** True when the "mqtt" breaker is open — no command can reach the device. */
  deviceControlDown: boolean;
}

const CapabilitiesSection: React.FC<CapabilitiesSectionProps> = ({
  device,
  blinkState,
  onBlink,
  deviceControlDown,
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
              deviceControlDown={deviceControlDown}
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
  deviceControlDown: boolean;
}

const CapabilityRow: React.FC<CapabilityRowProps> = ({
  capability,
  device,
  blinkState,
  onBlink,
  deviceControlDown,
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
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <IndicatorStateInline state={device.indicator_state} />
          <button
            type="button"
            onClick={onBlink}
            disabled={blinkState.pending || deviceControlDown}
          >
            {blinkState.pending ? 'Blinking…' : 'Blink LED'}
          </button>
          {blinkState.lastError && (
            <small style={{ color: '#c0392b' }}>{blinkState.lastError}</small>
          )}
        </div>
        <ServiceUnavailableNotice
          service="device_control"
          message={DEVICE_CONTROL_UNAVAILABLE}
          testId="blink-device-control-notice"
        />
      </div>
    );
  } else if (capability === 'power_relay') {
    body = <PowerRelayWidget device={device} deviceControlDown={deviceControlDown} />;
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

// Live indicator/status-LED colour the firmware reports over its status message
// (op-2cr). Shows a swatch + colour name (and non-solid pattern); falls back to
// "State: —" until the device reports a state.
const IndicatorStateInline: React.FC<{ state: ForgeKeyIndicatorState | undefined }> = ({
  state,
}) => {
  const color = state?.color ?? null;
  const pattern = state?.pattern ?? null;
  if (!color && !pattern) {
    return (
      <span style={{ color: '#555' }} data-testid="indicator-state">
        State: —
      </span>
    );
  }
  return (
    <span
      style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', color: '#555' }}
      data-testid="indicator-state"
    >
      State:
      <IndicatorSwatch
        presentation={{ color, pattern: pattern ?? undefined }}
        size={14}
        testId="indicator-state-swatch"
      />
      <strong data-testid="indicator-state-color">{color ?? pattern}</strong>
      {color && pattern && pattern !== 'solid' && (
        <span style={{ color: '#777' }}>· {pattern}</span>
      )}
    </span>
  );
};

const RELAY_CHANNELS = [1, 2];

// Current on/off pill for a channel, from the cached live sub-state (op-2cr).
// `undefined` means the firmware hasn't reported this channel yet.
const RelayChannelState: React.FC<{ on: boolean | undefined }> = ({ on }) => {
  if (on === undefined) {
    return (
      <span data-testid="relay-channel-state" style={{ color: '#777', minWidth: '3.75rem' }}>
        —
      </span>
    );
  }
  return (
    <span
      data-testid="relay-channel-state"
      data-on={on ? 'true' : 'false'}
      style={{ minWidth: '3.75rem', fontWeight: 600, color: on ? '#1f8a3a' : '#c0392b' }}
    >
      {on ? '● On' : '○ Off'}
    </span>
  );
};

// Per-channel control of the 2-channel power relay (ga-40w). Each click emits a
// signed `power_set` command for that channel; the current on/off is surfaced
// from the live sub-state the firmware reports over its status message (op-2cr).
const PowerRelayWidget: React.FC<{
  device: ForgeKeyDevice;
  deviceControlDown: boolean;
}> = ({ device, deviceControlDown }) => {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const channelState = useMemo(() => {
    const map = new Map<number, boolean>();
    for (const ch of device.relay_channels ?? []) {
      if (typeof ch?.channel === 'number') map.set(ch.channel, Boolean(ch.on));
    }
    return map;
  }, [device.relay_channels]);
  const hasLiveState = (device.relay_channels?.length ?? 0) > 0;

  const send = async (channel: number, on: boolean) => {
    setBusy(`${channel}:${on}`);
    setError(null);
    try {
      await forgekeyAPI.setRelayChannel(device.id, channel, on);
    } catch (err) {
      setError(
        extractErrorMessage(err, `Failed to ${on ? 'enable' : 'disable'} channel ${channel}.`),
      );
    } finally {
      setBusy(null);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
      {RELAY_CHANNELS.map((ch) => (
        <div
          key={ch}
          data-testid={`relay-channel-${ch}`}
          style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
        >
          <span style={{ color: '#555', minWidth: '5.5rem' }}>Channel {ch}</span>
          <RelayChannelState on={channelState.get(ch)} />
          <button
            type="button"
            onClick={() => send(ch, true)}
            disabled={busy !== null || deviceControlDown}
            data-testid={`relay-channel-${ch}-enable`}
          >
            {busy === `${ch}:true` ? 'Enabling…' : 'Enable'}
          </button>
          <button
            type="button"
            onClick={() => send(ch, false)}
            disabled={busy !== null || deviceControlDown}
            data-testid={`relay-channel-${ch}-disable`}
          >
            {busy === `${ch}:false` ? 'Disabling…' : 'Disable'}
          </button>
        </div>
      ))}
      <ServiceUnavailableNotice
        service="device_control"
        message={DEVICE_CONTROL_UNAVAILABLE}
        testId="relay-device-control-notice"
      />
      {error && (
        <small style={{ color: '#c0392b' }} data-testid="relay-channel-error">
          {error}
        </small>
      )}
      {!hasLiveState && (
        <small style={{ color: '#777' }}>Live on/off state not reported yet.</small>
      )}
    </div>
  );
};

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
