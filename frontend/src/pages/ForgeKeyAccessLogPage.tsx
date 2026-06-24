/**
 * ForgeKeyAccessLogPage — access / denial log (op-tup).
 *
 * A read-only view of recent access-control events from the ForgeKey audit log
 * (op-vj9): grants, denials (with reason), session ends, and badge enrollments.
 * Staff filter by action (e.g. just denials) and can widen to the full audit
 * stream. Backs "staff can see who was denied and why".
 */
import {
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Select,
  Switch,
  Table,
  Text,
} from '@mantine/core';
import React, { useCallback, useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { ForgeKeyAuditEvent, forgekeyAPI } from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

function unwrap<T>(data: { results?: T[] } | T[]): T[] {
  if (Array.isArray(data)) return data;
  return data.results ?? [];
}

function isStaffUser(): boolean {
  if (typeof window === 'undefined') return false;
  return (
    localStorage.getItem('is_staff') === 'true' ||
    localStorage.getItem('is_superuser') === 'true'
  );
}

const ACTION_OPTIONS = [
  { value: 'all', label: 'All access events' },
  { value: 'access_granted', label: 'Access granted' },
  { value: 'access_denied', label: 'Denied' },
  { value: 'session_ended', label: 'Session ended' },
  { value: 'badge_enrolled', label: 'Badge enrolled' },
];

const ACTION_COLORS: Record<string, string> = {
  access_granted: 'green',
  access_denied: 'red',
  session_ended: 'gray',
  badge_enrolled: 'blue',
};

const DENY_REASON_LABELS: Record<string, string> = {
  not_authorized: 'Not authorized',
  in_use: 'In use by another member',
  unknown_card: 'Unknown card',
  unknown_device: 'Unknown device',
  no_asset: 'No asset bound to device',
  relay_error: 'Relay error',
  malformed: 'Malformed request',
  badge_in_use: 'Badge already assigned',
};

// The deny reason lives in ``metadata.reason``; fall back to the free-text note.
function describeReason(event: ForgeKeyAuditEvent): string {
  const reason = event.metadata && typeof event.metadata.reason === 'string'
    ? (event.metadata.reason as string)
    : '';
  if (reason) return DENY_REASON_LABELS[reason] ?? reason;
  return event.notes || '';
}

function formatTime(value: string): string {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

const ForgeKeyAccessLogPage: React.FC = () => {
  const isStaff = isStaffUser();

  const [events, setEvents] = useState<ForgeKeyAuditEvent[]>([]);
  const [action, setAction] = useState<string>('all');
  const [accessOnly, setAccessOnly] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await forgekeyAPI.listAccessLog({
        accessOnly,
        action: action !== 'all' ? action : undefined,
      });
      setEvents(unwrap<ForgeKeyAuditEvent>(res.data));
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load the access log.'));
    } finally {
      setLoading(false);
    }
  }, [accessOnly, action]);

  useEffect(() => {
    if (isStaff) load();
  }, [isStaff, load]);

  if (!isStaff) {
    return <Navigate to="/" replace />;
  }

  return (
    <WorkspacePage
      testId="forgekey-access-log-page"
      hero={{
        eyebrow: 'Facilities · ForgeKey',
        title: 'Access log',
        description:
          'Recent badge access grants, denials (with reason), session ends, and badge enrollments.',
        action: (
          <Button variant="default" onClick={load} data-testid="access-log-refresh">
            Refresh
          </Button>
        ),
      }}
    >
      <Group mb="md" gap="md" align="flex-end">
        <Select
          label="Action"
          data={ACTION_OPTIONS}
          value={action}
          onChange={(v) => setAction(v ?? 'all')}
          aria-label="Action"
          data-testid="access-log-action-filter"
          allowDeselect={false}
          w={220}
        />
        <Switch
          label="Access events only"
          checked={accessOnly}
          onChange={(e) => setAccessOnly(e.currentTarget.checked)}
          data-testid="access-log-access-only"
        />
      </Group>

      {error && (
        <Alert color="red" variant="light" data-testid="access-log-error" mb="md">
          {error}
        </Alert>
      )}

      {loading && events.length === 0 ? (
        <Group justify="center" p="xl">
          <Loader />
        </Group>
      ) : events.length === 0 ? (
        <Text c="dimmed" data-testid="access-log-empty">
          No access events match these filters.
        </Text>
      ) : (
        <Table.ScrollContainer minWidth={680}>
          <Table striped highlightOnHover data-testid="access-log-table">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Time</Table.Th>
                <Table.Th>Action</Table.Th>
                <Table.Th>Member</Table.Th>
                <Table.Th>Asset</Table.Th>
                <Table.Th>Reason / notes</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {events.map((e) => (
                <Table.Tr key={e.id} data-testid={`access-log-row-${e.id}`}>
                  <Table.Td>{formatTime(e.created_at)}</Table.Td>
                  <Table.Td>
                    <Badge color={ACTION_COLORS[e.action] ?? 'gray'} variant="light">
                      {e.action_display}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{e.actor_username ?? <Text c="dimmed">unknown card</Text>}</Table.Td>
                  <Table.Td>{e.asset_name ?? <Text c="dimmed">—</Text>}</Table.Td>
                  <Table.Td>{describeReason(e) || <Text c="dimmed">—</Text>}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </WorkspacePage>
  );
};

export default ForgeKeyAccessLogPage;
