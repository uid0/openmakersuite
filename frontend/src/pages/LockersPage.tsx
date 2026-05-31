/**
 * ForgeKey Lockers — staff monitoring console.
 *
 * Live status of the locker fleet from the firmware's cabinet_lock/status
 * heartbeat: secure / online / state per locker, with possible intrusions
 * (ALARM or a sustained not-secure reading) flagged. Refreshes every 30s.
 * Setup, OTP issuance, and operator unlock land in follow-up PRs.
 */
import {
  Alert,
  Badge,
  Box,
  Card,
  Group,
  Loader,
  Paper,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import React, { useEffect, useMemo, useState } from 'react';
import { Navigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { ForgeKeyLocker, lockersAPI } from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const POLL_INTERVAL_MS = 30_000;

const STATE_COLORS: Record<string, string> = {
  SECURE: 'green',
  UNLOCKED: 'blue',
  ACCESSING: 'blue',
  ALARM: 'red',
  INITIALIZING: 'gray',
};

const asList = <T,>(data: { results?: T[] } | T[]): T[] =>
  Array.isArray(data) ? data : data.results ?? [];

const formatRelative = (iso: string | null): string => {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
};

const needsAttention = (lk: ForgeKeyLocker): boolean =>
  Boolean(lk.status && (lk.status.is_alarm || lk.status.is_insecure));

const StatCard: React.FC<{ label: string; value: React.ReactNode; color?: string; testId?: string }> = ({
  label,
  value,
  color,
  testId,
}) => (
  <Card withBorder p="md" radius="md" data-testid={testId}>
    <Text size="sm" c="dimmed" fw={500}>
      {label}
    </Text>
    <Text size="xl" fw={700} c={color}>
      {value}
    </Text>
  </Card>
);

const LockersPage: React.FC = () => {
  const isStaff = typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' && localStorage.getItem('is_superuser') === 'true';

  const [lockers, setLockers] = useState<ForgeKeyLocker[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isStaff && !isSuperuser) return undefined;
    let cancelled = false;
    const load = async () => {
      try {
        const res = await lockersAPI.listLockers();
        if (!cancelled) {
          setLockers(asList(res.data));
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(extractErrorMessage(err, 'Failed to load lockers.'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const handle = window.setInterval(load, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [isStaff, isSuperuser]);

  const summary = useMemo(() => {
    const total = lockers.length;
    const secure = lockers.filter((l) => l.status?.secure === true).length;
    const attention = lockers.filter(needsAttention).length;
    return { total, secure, attention };
  }, [lockers]);

  if (!isStaff && !isSuperuser) {
    return <Navigate to="/" replace />;
  }

  return (
    <WorkspacePage
      testId="lockers-page"
      hero={{
        eyebrow: 'Facilities · ForgeKey',
        title: 'Lockers',
        description:
          'Live status of every ForgeKey-gated locker — secure / online state, with possible intrusions flagged.',
      }}
    >
      {error && (
        <Alert color="red" variant="light" data-testid="lockers-error">
          {error}
        </Alert>
      )}

      {loading && lockers.length === 0 ? (
        <Group justify="center" p="xl">
          <Loader />
        </Group>
      ) : (
        <Stack gap="lg">
          <SimpleGrid cols={{ base: 3 }} data-testid="locker-stats">
            <StatCard label="Lockers" value={summary.total} testId="stat-total" />
            <StatCard label="Secure" value={summary.secure} color="green" testId="stat-secure" />
            <StatCard
              label="Needs attention"
              value={summary.attention}
              color={summary.attention > 0 ? 'red' : undefined}
              testId="stat-attention"
            />
          </SimpleGrid>

          {lockers.length === 0 ? (
            <Paper p="xl" withBorder>
              <Text c="dimmed" data-testid="lockers-empty">
                No lockers have been configured yet.
              </Text>
            </Paper>
          ) : (
            <Box>
              <Group mb="sm" gap="xs">
                <Title order={4}>Locker fleet</Title>
              </Group>
              <Paper withBorder>
                <Table.ScrollContainer minWidth={820}>
                  <Table highlightOnHover>
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th>Locker</Table.Th>
                        <Table.Th>Location</Table.Th>
                        <Table.Th>SIG</Table.Th>
                        <Table.Th>Stored asset</Table.Th>
                        <Table.Th>Lock state</Table.Th>
                        <Table.Th>Online</Table.Th>
                        <Table.Th>Updated</Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {lockers.map((lk) => {
                        const attention = needsAttention(lk);
                        const state = lk.status?.state || '';
                        return (
                          <Table.Tr
                            key={lk.id}
                            data-testid={`locker-row-${lk.id}`}
                            style={{ backgroundColor: attention ? '#fff0f0' : undefined }}
                          >
                            <Table.Td>
                              <Text fw={500}>{lk.name}</Text>
                              {attention && (
                                <Badge color="red" size="sm" variant="light">
                                  {lk.status?.is_alarm ? 'Alarm' : 'Not secure'}
                                </Badge>
                              )}
                            </Table.Td>
                            <Table.Td>{lk.location_name || '—'}</Table.Td>
                            <Table.Td>{lk.owning_sig_name || '—'}</Table.Td>
                            <Table.Td>{lk.current_asset_name || '—'}</Table.Td>
                            <Table.Td>
                              {state ? (
                                <Badge color={STATE_COLORS[state] || 'gray'}>{state}</Badge>
                              ) : (
                                <Text size="sm" c="dimmed">
                                  no status
                                </Text>
                              )}
                            </Table.Td>
                            <Table.Td>
                              {lk.status?.device_is_online == null ? (
                                <Text size="sm" c="dimmed">
                                  —
                                </Text>
                              ) : (
                                <Badge color={lk.status.device_is_online ? 'green' : 'gray'}>
                                  {lk.status.device_is_online ? 'Online' : 'Offline'}
                                </Badge>
                              )}
                            </Table.Td>
                            <Table.Td>
                              <Text size="xs" c="dimmed">
                                {formatRelative(lk.status?.last_status_at ?? null)}
                              </Text>
                            </Table.Td>
                          </Table.Tr>
                        );
                      })}
                    </Table.Tbody>
                  </Table>
                </Table.ScrollContainer>
              </Paper>
            </Box>
          )}
        </Stack>
      )}
    </WorkspacePage>
  );
};

export default LockersPage;
