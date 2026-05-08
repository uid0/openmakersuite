/**
 * Device Controls card for the ForgeKey device-detail page (oms-zta).
 *
 * Renders five command buttons (restart, blink, capture, ping, identify) plus
 * a recent-command history table. Each button:
 *   - POSTs to its command endpoint
 *   - shows a spinner while the round-trip is in flight
 *   - resolves to a green checkmark + age once the firmware acks
 *   - falls back to a red 'no ack' after 10s, or an orange warning if the
 *     firmware reported an error
 *
 * Ack state is computed from the recent-commands endpoint, which is polled
 * every 2 seconds while at least one command is pending. This keeps polling
 * load to roughly one request per active session — the page already polls
 * occupancy on a separate interval, so the two cycles can run independently.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
import { extractErrorMessage } from '../utils/extractErrorMessage';
  ForgeKeyCommandResponse,
  ForgeKeyDevice,
  ForgeKeyDeviceCommand,
  forgekeyAPI,
} from '../services/api';

const ACK_TIMEOUT_MS = 10_000;
const POLL_INTERVAL_MS = 2_000;

interface CommandDef {
  key: string;
  label: string;
  emoji: string;
  command: string; // matches DeviceCommand.command on the backend
  run: (deviceId: string) => Promise<{ data: ForgeKeyCommandResponse }>;
}

const COMMANDS: CommandDef[] = [
  {
    key: 'blink',
    label: 'Blink',
    emoji: '🔆',
    command: 'blink',
    run: (id) => forgekeyAPI.blink(id, { pattern: 'sos', duration_s: 5 }),
  },
  {
    key: 'restart',
    label: 'Restart',
    emoji: '🔄',
    command: 'restart',
    run: (id) => forgekeyAPI.restart(id),
  },
  {
    key: 'capture',
    label: 'Capture photo',
    emoji: '📸',
    command: 'capture_photo',
    run: (id) => forgekeyAPI.capturePhoto(id),
  },
  {
    key: 'ping',
    label: 'Status / ping',
    emoji: '📡',
    command: 'ping',
    run: (id) => forgekeyAPI.ping(id),
  },
  {
    key: 'identify',
    label: 'Identify',
    emoji: '🔍',
    command: 'identify',
    run: (id) => forgekeyAPI.identify(id, { duration_s: 30 }),
  },
];

interface PendingCommand {
  commandId: string;
  startedAt: number;
}

interface InFlightState {
  pending: boolean;
  pendingCommandId: string | null;
  error: string | null;
}

const initialInFlight: InFlightState = {
  pending: false,
  pendingCommandId: null,
  error: null,
};

interface DeviceControlsCardProps {
  device: ForgeKeyDevice;
}

const DeviceControlsCard: React.FC<DeviceControlsCardProps> = ({ device }) => {
  const deviceId = device.id;

  const [inFlight, setInFlight] = useState<Record<string, InFlightState>>(() =>
    Object.fromEntries(COMMANDS.map((c) => [c.key, initialInFlight])),
  );
  const [history, setHistory] = useState<ForgeKeyDeviceCommand[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [now, setNow] = useState<number>(() => Date.now());

  // Track which command IDs we're still waiting on so the poll loop can stop
  // as soon as everything resolves. Stored in a ref to avoid re-render churn
  // and to give the poll callback a fresh view without stale closures.
  const pendingByKey = useRef<Record<string, PendingCommand>>({});

  const refreshHistory = useCallback(async () => {
    try {
      const response = await forgekeyAPI.recentCommands(deviceId, 10);
      setHistory(response.data.results);
      setHistoryError(null);
      return response.data.results;
    } catch (err) {
      const detail =
        extractErrorMessage(err, 'Failed to load recent commands.');
      setHistoryError(detail);
      return null;
    }
  }, [deviceId]);

  // Initial load.
  useEffect(() => {
    refreshHistory();
  }, [refreshHistory]);

  // Tick a clock every second so the "sent 3s ago" label updates without
  // triggering a network call. Cheap; only runs while the card is mounted.
  useEffect(() => {
    const handle = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(handle);
  }, []);

  // Poll the recent-commands endpoint while any command is awaiting an ack.
  // Stops when nothing is pending, so a quiet panel costs nothing.
  useEffect(() => {
    const anyPending = Object.values(inFlight).some((s) => s.pending);
    if (!anyPending) return undefined;

    const handle = window.setInterval(async () => {
      const results = await refreshHistory();
      if (!results) return;

      const byId = new Map(results.map((c) => [c.id, c]));
      setInFlight((prev) => {
        let changed = false;
        const next = { ...prev };
        for (const def of COMMANDS) {
          const state = prev[def.key];
          if (!state.pending || !state.pendingCommandId) continue;

          const match = byId.get(state.pendingCommandId);
          const tracked = pendingByKey.current[def.key];
          const startedAt = tracked?.startedAt ?? 0;
          const ackStatus = match?.effective_ack_status;
          const elapsed = Date.now() - startedAt;

          if (ackStatus === 'acked' || ackStatus === 'error') {
            next[def.key] = {
              pending: false,
              pendingCommandId: state.pendingCommandId,
              error: null,
            };
            delete pendingByKey.current[def.key];
            changed = true;
          } else if (elapsed >= ACK_TIMEOUT_MS || ackStatus === 'timeout') {
            next[def.key] = {
              pending: false,
              pendingCommandId: state.pendingCommandId,
              error: null,
            };
            delete pendingByKey.current[def.key];
            changed = true;
          }
        }
        return changed ? next : prev;
      });
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(handle);
  }, [inFlight, refreshHistory]);

  const runCommand = useCallback(
    async (def: CommandDef) => {
      setInFlight((prev) => ({
        ...prev,
        [def.key]: { pending: true, pendingCommandId: null, error: null },
      }));
      try {
        const response = await def.run(deviceId);
        const commandId = response.data.command_id || null;
        if (commandId) {
          pendingByKey.current[def.key] = {
            commandId,
            startedAt: Date.now(),
          };
        }
        setInFlight((prev) => ({
          ...prev,
          [def.key]: {
            pending: !!commandId,
            pendingCommandId: commandId,
            error: null,
          },
        }));
        // Schedule a hard timeout so the spinner clears even if the poll
        // happens to miss the window (e.g. tab backgrounded).
        if (commandId) {
          window.setTimeout(() => {
            setInFlight((prev) => {
              const cur = prev[def.key];
              if (!cur.pending || cur.pendingCommandId !== commandId) return prev;
              delete pendingByKey.current[def.key];
              return {
                ...prev,
                [def.key]: { pending: false, pendingCommandId: commandId, error: null },
              };
            });
          }, ACK_TIMEOUT_MS + 500);
        }
        // Refresh once immediately so the new row shows up in the history.
        refreshHistory();
      } catch (err) {
        const detail =
          extractErrorMessage(err, 'Command failed.');
        setInFlight((prev) => ({
          ...prev,
          [def.key]: { pending: false, pendingCommandId: null, error: detail },
        }));
      }
    },
    [deviceId, refreshHistory],
  );

  const lastSeenLabel = useMemo(() => {
    if (!device.last_seen) return '—';
    return new Date(device.last_seen).toLocaleString();
  }, [device.last_seen]);

  const historyById = useMemo(() => {
    return new Map(history.map((c) => [c.id, c]));
  }, [history]);

  return (
    <section aria-label="Device controls">
      <h3>Device Controls</h3>
      <p style={{ color: '#555', margin: '0 0 0.5rem' }}>
        Last seen: <strong>{lastSeenLabel}</strong>{' '}
        <span
          data-testid="device-online-state"
          style={{ color: device.is_online ? '#1f8a3a' : '#777' }}
        >
          ({device.is_online ? 'Online' : 'Offline'})
        </span>
      </p>
      <div
        data-testid="device-control-buttons"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '0.5rem',
          rowGap: '0.75rem',
        }}
      >
        {COMMANDS.map((def) => {
          const state = inFlight[def.key];
          const ackedRow = state.pendingCommandId
            ? historyById.get(state.pendingCommandId)
            : null;
          return (
            <ControlButton
              key={def.key}
              def={def}
              state={state}
              ackedRow={ackedRow ?? null}
              now={now}
              onClick={() => runCommand(def)}
            />
          );
        })}
      </div>

      <RecentCommandsTable
        commands={history}
        error={historyError}
        now={now}
      />
    </section>
  );
};

interface ControlButtonProps {
  def: CommandDef;
  state: InFlightState;
  ackedRow: ForgeKeyDeviceCommand | null;
  now: number;
  onClick: () => void;
}

const ControlButton: React.FC<ControlButtonProps> = ({
  def,
  state,
  ackedRow,
  now,
  onClick,
}) => {
  // Resolve the visible feedback for this button. The row may not be present
  // in the history yet on the very first poll, so fall back to a 'sent'
  // placeholder until the recent-commands endpoint catches up.
  let feedback: React.ReactNode = null;
  if (state.pending) {
    feedback = (
      <small
        style={{ color: '#555' }}
        data-testid={`control-feedback-${def.key}`}
        data-state="pending"
      >
        ⏳ waiting for ack…
      </small>
    );
  } else if (state.error) {
    feedback = (
      <small
        style={{ color: '#c0392b' }}
        data-testid={`control-feedback-${def.key}`}
        data-state="error"
      >
        ✕ {state.error}
      </small>
    );
  } else if (ackedRow) {
    const ageSec = Math.max(
      0,
      Math.round((now - new Date(ackedRow.sent_at).getTime()) / 1000),
    );
    if (ackedRow.effective_ack_status === 'acked') {
      feedback = (
        <small
          style={{ color: '#1f8a3a' }}
          data-testid={`control-feedback-${def.key}`}
          data-state="acked"
        >
          ✓ acknowledged {ageSec}s ago
        </small>
      );
    } else if (ackedRow.effective_ack_status === 'error') {
      const errMsg =
        (ackedRow.ack_payload as { error?: string } | null)?.error ||
        'firmware reported an error';
      feedback = (
        <small
          style={{ color: '#d68910' }}
          data-testid={`control-feedback-${def.key}`}
          data-state="firmware-error"
        >
          ⚠ {errMsg}
        </small>
      );
    } else if (ackedRow.effective_ack_status === 'timeout') {
      feedback = (
        <small
          style={{ color: '#c0392b' }}
          data-testid={`control-feedback-${def.key}`}
          data-state="timeout"
        >
          ✕ no ack — device may be offline
        </small>
      );
    }
  } else if (state.pendingCommandId) {
    // Command sent, no row visible yet, no longer pending → assume timeout.
    feedback = (
      <small
        style={{ color: '#c0392b' }}
        data-testid={`control-feedback-${def.key}`}
        data-state="timeout"
      >
        ✕ no ack — device may be offline
      </small>
    );
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.2rem',
        minWidth: '9rem',
        flex: '1 1 9rem',
      }}
    >
      <button
        type="button"
        disabled={state.pending}
        onClick={onClick}
        data-testid={`control-btn-${def.key}`}
        style={{
          padding: '0.5rem 0.75rem',
          fontSize: '0.95rem',
          minHeight: '2.5rem',
        }}
      >
        <span aria-hidden="true">{def.emoji}</span> {def.label}
        {state.pending ? '…' : ''}
      </button>
      {feedback}
    </div>
  );
};

interface RecentCommandsTableProps {
  commands: ForgeKeyDeviceCommand[];
  error: string | null;
  now: number;
}

const RecentCommandsTable: React.FC<RecentCommandsTableProps> = ({
  commands,
  error,
  now,
}) => {
  if (error) {
    return (
      <p style={{ color: '#c0392b', marginTop: '1rem' }}>
        {error}
      </p>
    );
  }
  if (commands.length === 0) {
    return (
      <p style={{ color: '#777', marginTop: '1rem' }}>
        No commands sent yet.
      </p>
    );
  }
  return (
    <div style={{ marginTop: '1rem', overflowX: 'auto' }}>
      <h4 style={{ margin: '0 0 0.25rem' }}>Recent commands</h4>
      <table
        data-testid="recent-commands-table"
        style={{
          borderCollapse: 'collapse',
          width: '100%',
          minWidth: '32rem',
          fontSize: '0.9rem',
        }}
      >
        <thead>
          <tr style={{ textAlign: 'left', borderBottom: '1px solid #ddd' }}>
            <th style={{ padding: '0.25rem 0.5rem' }}>When</th>
            <th style={{ padding: '0.25rem 0.5rem' }}>Command</th>
            <th style={{ padding: '0.25rem 0.5rem' }}>By</th>
            <th style={{ padding: '0.25rem 0.5rem' }}>Ack</th>
          </tr>
        </thead>
        <tbody>
          {commands.map((row) => {
            const ageSec = Math.max(
              0,
              Math.round((now - new Date(row.sent_at).getTime()) / 1000),
            );
            return (
              <tr
                key={row.id}
                data-testid={`recent-command-${row.id}`}
                style={{ borderBottom: '1px solid #f0f0f0' }}
              >
                <td style={{ padding: '0.25rem 0.5rem', whiteSpace: 'nowrap' }}>
                  {ageSec < 60
                    ? `${ageSec}s ago`
                    : new Date(row.sent_at).toLocaleString()}
                </td>
                <td style={{ padding: '0.25rem 0.5rem' }}>{row.command}</td>
                <td style={{ padding: '0.25rem 0.5rem' }}>
                  {row.sent_by_username || '—'}
                </td>
                <td
                  style={{ padding: '0.25rem 0.5rem' }}
                  data-state={row.effective_ack_status}
                >
                  <AckBadge status={row.effective_ack_status} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

const AckBadge: React.FC<{ status: ForgeKeyDeviceCommand['effective_ack_status'] }> = ({
  status,
}) => {
  switch (status) {
    case 'acked':
      return <span style={{ color: '#1f8a3a' }}>✓ acked</span>;
    case 'error':
      return <span style={{ color: '#d68910' }}>⚠ error</span>;
    case 'timeout':
      return <span style={{ color: '#c0392b' }}>✕ no ack</span>;
    case 'pending':
    default:
      return <span style={{ color: '#777' }}>⏳ pending</span>;
  }
};

export default DeviceControlsCard;
