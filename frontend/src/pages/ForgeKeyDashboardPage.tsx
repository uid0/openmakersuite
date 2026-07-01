/**
 * ForgeKey Fleet Dashboard
 *
 * Staff-only, at-a-glance health across the whole ForgeKey device fleet:
 * online/offline counts, breakdowns by type / capability / firmware, a
 * "needs attention" feed (offline devices, low-battery e-paper panels, failed
 * OTA), and recent command / firmware activity. Backed by a single
 * /forgekey/devices/fleet-summary/ aggregate, so the page never fans out per
 * device. Refreshes every 30s.
 */
import {
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Card,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import React, { useEffect, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { ForgeKeyFleetSummary, forgekeyAPI } from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const POLL_INTERVAL_MS = 30_000;

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

const ACK_COLORS: Record<string, string> = {
  pending: 'gray',
  acked: 'green',
  error: 'red',
  timeout: 'orange',
};

const OTA_COLORS: Record<string, string> = {
  pending: 'gray',
  in_progress: 'blue',
  completed: 'green',
  failed: 'red',
  cancelled: 'gray',
};

const StatCard: React.FC<{
  label: string;
  value: React.ReactNode;
  color?: string;
  testId?: string;
}> = ({ label, value, color, testId }) => (
  <Card withBorder p="md" radius="md" data-testid={testId}>
    <Text size="sm" c="dimmed" fw={500}>
      {label}
    </Text>
    <Text size="xl" fw={700} c={color}>
      {value}
    </Text>
  </Card>
);

const BreakdownCard: React.FC<{
  title: string;
  rows: Array<{ label: string; count: number; extra?: React.ReactNode }>;
  testId?: string;
}> = ({ title, rows, testId }) => (
  <Card withBorder p="md" radius="sm" data-testid={testId}>
    <Text fw={600} size="sm" mb="xs">
      {title}
    </Text>
    {rows.length === 0 ? (
      <Text size="sm" c="dimmed">
        No data yet.
      </Text>
    ) : (
      <Stack gap={4}>
        {rows.map((r) => (
          <Group key={r.label} justify="space-between" gap="xs">
            <Text size="sm">{r.label}</Text>
            <Group gap="xs">
              {r.extra}
              <Badge variant="light">{r.count}</Badge>
            </Group>
          </Group>
        ))}
      </Stack>
    )}
  </Card>
);

const ForgeKeyDashboardPage: React.FC = () => {
  const isStaff = typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' && localStorage.getItem('is_superuser') === 'true';

  const [summary, setSummary] = useState<ForgeKeyFleetSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isStaff && !isSuperuser) return undefined;
    let cancelled = false;
    const load = async () => {
      try {
        const res = await forgekeyAPI.getFleetSummary();
        if (!cancelled) {
          setSummary(res.data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(extractErrorMessage(err, 'Failed to load the ForgeKey fleet summary.'));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    load();
    const handle = window.setInterval(load, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [isStaff, isSuperuser]);

  if (!isStaff && !isSuperuser) {
    return <Navigate to="/" replace />;
  }

  const attentionCount = summary
    ? summary.attention.offline.length +
      summary.attention.low_battery.length +
      summary.attention.ota_failed.length
    : 0;

  return (
    <WorkspacePage
      testId="forgekey-dashboard-page"
      hero={{
        eyebrow: 'Facilities · ForgeKey',
        title: 'Fleet dashboard',
        description:
          'Health across every ForgeKey device — connectivity, firmware, e-paper panels, and recent activity.',
        action: (
          <Group gap="xs">
            <Button component={Link} to="/facilities/forgekey-devices" variant="default">
              Manage devices
            </Button>
            <Button component={Link} to="/facilities/forgekey-badges" variant="default">
              Badge enrollment
            </Button>
            <Button component={Link} to="/facilities/forgekey-access-log" variant="default">
              Access log
            </Button>
          </Group>
        ),
      }}
    >
      {error && (
        <Alert color="red" variant="light" data-testid="dashboard-error">
          {error}
        </Alert>
      )}

      {loading && !summary ? (
        <Group justify="center" p="xl">
          <Loader />
        </Group>
      ) : summary ? (
        <Stack gap="lg">
          {/* Headline stats */}
          <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }} data-testid="fleet-stat-cards">
            <StatCard label="Devices" value={summary.devices.total} testId="stat-total" />
            <StatCard label="Online" value={summary.devices.online} color="green" testId="stat-online" />
            <StatCard
              label="Offline"
              value={summary.devices.offline}
              color={summary.devices.offline > 0 ? 'red' : undefined}
              testId="stat-offline"
            />
            <StatCard label="e-Paper panels" value={summary.epaper.total} testId="stat-epaper" />
            <StatCard
              label="Low battery"
              value={summary.epaper.low_battery}
              color={summary.epaper.low_battery > 0 ? 'orange' : undefined}
              testId="stat-lowbatt"
            />
            <StatCard
              label="OTA in flight"
              value={summary.firmware.updates_in_flight}
              testId="stat-ota"
            />
          </SimpleGrid>

          {/* Needs attention */}
          <Box data-testid="needs-attention">
            <Group mb="sm" gap="xs">
              <Title order={4}>Needs attention</Title>
              <Badge color={attentionCount > 0 ? 'red' : 'gray'}>{attentionCount}</Badge>
            </Group>
            {attentionCount === 0 ? (
              <Card withBorder p="md" radius="sm">
                <Text size="sm" c="dimmed" data-testid="all-clear">
                  All clear — every device is online and healthy.
                </Text>
              </Card>
            ) : (
              <Stack gap="sm">
                {summary.attention.offline.length > 0 && (
                  <Card withBorder p="md" radius="sm" data-testid="attention-offline">
                    <Text fw={600} size="sm" mb="xs">
                      Offline ({summary.attention.offline.length})
                    </Text>
                    <Stack gap={4}>
                      {summary.attention.offline.map((d) => (
                        <Group key={d.device_id} justify="space-between" gap="xs">
                          <Anchor component={Link} to={`/facilities/forgekey-devices/${d.device_id}`}>
                            {d.name}
                          </Anchor>
                          <Text size="sm" c="dimmed">
                            last seen {formatRelative(d.last_seen)}
                          </Text>
                        </Group>
                      ))}
                    </Stack>
                  </Card>
                )}
                {summary.attention.low_battery.length > 0 && (
                  <Card withBorder p="md" radius="sm" data-testid="attention-lowbatt">
                    <Text fw={600} size="sm" mb="xs">
                      Low-battery e-paper panels ({summary.attention.low_battery.length})
                    </Text>
                    <Stack gap={4}>
                      {summary.attention.low_battery.map((p) => (
                        <Group key={p.display_id} justify="space-between" gap="xs">
                          <Text size="sm">{p.asset_name || 'Unbound panel'}</Text>
                          <Badge color="orange" variant="light">
                            {p.battery_percent}%
                          </Badge>
                        </Group>
                      ))}
                    </Stack>
                  </Card>
                )}
                {summary.attention.ota_failed.length > 0 && (
                  <Card withBorder p="md" radius="sm" data-testid="attention-ota">
                    <Text fw={600} size="sm" mb="xs">
                      Failed firmware updates ({summary.attention.ota_failed.length})
                    </Text>
                    <Stack gap={4}>
                      {summary.attention.ota_failed.map((u) => (
                        <Group key={u.device_id + u.requested_at} justify="space-between" gap="xs">
                          <Anchor component={Link} to={`/facilities/forgekey-devices/${u.device_id}`}>
                            {u.name}
                          </Anchor>
                          <Text size="sm" c="dimmed">
                            {u.version || '—'} · {formatRelative(u.requested_at)}
                          </Text>
                        </Group>
                      ))}
                    </Stack>
                  </Card>
                )}
              </Stack>
            )}
          </Box>

          {/* Breakdowns */}
          <SimpleGrid cols={{ base: 1, md: 3 }}>
            <BreakdownCard
              title="By type"
              testId="breakdown-type"
              rows={summary.devices.by_type.map((t) => ({
                label: t.name,
                count: t.count,
                extra: (
                  <Text size="xs" c="dimmed">
                    {t.online} online
                  </Text>
                ),
              }))}
            />
            <BreakdownCard
              title="By capability"
              testId="breakdown-capability"
              rows={summary.devices.by_capability.map((c) => ({
                label: c.capability,
                count: c.count,
              }))}
            />
            <BreakdownCard
              title="By firmware"
              testId="breakdown-firmware"
              rows={summary.devices.by_firmware.map((f) => ({
                label: f.version,
                count: f.count,
              }))}
            />
          </SimpleGrid>

          {/* Recent activity */}
          <SimpleGrid cols={{ base: 1, md: 2 }}>
            <Card withBorder p="md" radius="sm" data-testid="recent-commands">
              <Text fw={600} size="sm" mb="xs">
                Recent commands
              </Text>
              {summary.recent_commands.length === 0 ? (
                <Text size="sm" c="dimmed">
                  No commands sent yet.
                </Text>
              ) : (
                <Table>
                  <Table.Tbody>
                    {summary.recent_commands.map((c) => (
                      <Table.Tr key={c.id}>
                        <Table.Td>
                          <Anchor component={Link} to={`/facilities/forgekey-devices/${c.device_id}`}>
                            {c.device_name}
                          </Anchor>
                        </Table.Td>
                        <Table.Td>
                          <Text size="sm">{c.command}</Text>
                        </Table.Td>
                        <Table.Td>
                          <Badge size="sm" color={ACK_COLORS[c.ack_status] || 'gray'}>
                            {c.ack_status}
                          </Badge>
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs" c="dimmed">
                            {formatRelative(c.sent_at)}
                          </Text>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
            </Card>
            <Card withBorder p="md" radius="sm" data-testid="recent-updates">
              <Text fw={600} size="sm" mb="xs">
                Recent firmware updates
              </Text>
              {summary.recent_updates.length === 0 ? (
                <Text size="sm" c="dimmed">
                  No firmware updates yet.
                </Text>
              ) : (
                <Table>
                  <Table.Tbody>
                    {summary.recent_updates.map((u) => (
                      <Table.Tr key={u.id}>
                        <Table.Td>
                          <Anchor component={Link} to={`/facilities/forgekey-devices/${u.device_id}`}>
                            {u.device_name}
                          </Anchor>
                        </Table.Td>
                        <Table.Td>
                          <Text size="sm">{u.version || '—'}</Text>
                        </Table.Td>
                        <Table.Td>
                          <Badge size="sm" color={OTA_COLORS[u.status] || 'gray'}>
                            {u.status}
                          </Badge>
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs" c="dimmed">
                            {formatRelative(u.requested_at)}
                          </Text>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
            </Card>
          </SimpleGrid>
        </Stack>
      ) : null}
    </WorkspacePage>
  );
};

export default ForgeKeyDashboardPage;
