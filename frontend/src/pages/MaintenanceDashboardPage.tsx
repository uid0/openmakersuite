import {
  Alert,
  Anchor,
  Badge,
  Box,
  Card,
  Container,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { IconAlertTriangle, IconCalendar, IconCash, IconClipboardList } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { activeMaintenanceAPI, maintenanceAPI, MaintenanceDashboardData } from '../services/api';
import { ActiveMaintenanceRow } from '../types';

const PERIOD_LABELS: Array<{ key: keyof MaintenanceDashboardData['costs']['per_period']; label: string }> = [
  { key: 'today', label: 'Today' },
  { key: 'this_week', label: 'This Week' },
  { key: 'this_month', label: 'This Month' },
  { key: 'this_year', label: 'This Year' },
  { key: 'all_time', label: 'All Time' },
];

const formatCurrency = (value: string): string => {
  const n = Number(value);
  if (Number.isNaN(n)) return value;
  return n.toLocaleString(undefined, { style: 'currency', currency: 'USD' });
};

const formatDate = (iso: string | null): string => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString();
};

type ActiveKindFilter = 'all' | ActiveMaintenanceRow['kind'];

const KIND_LABELS: Record<ActiveMaintenanceRow['kind'], string> = {
  work_order: 'Work Order',
  asset_problem: 'Asset Problem',
  location_problem: 'Location Problem',
};

const KIND_COLORS: Record<ActiveMaintenanceRow['kind'], string> = {
  work_order: 'blue',
  asset_problem: 'orange',
  location_problem: 'grape',
};

const SEVERITY_COLORS: Record<string, string> = {
  low: 'gray',
  medium: 'cyan',
  high: 'orange',
  urgent: 'red',
};

const detailLinkFor = (row: ActiveMaintenanceRow): string => {
  if (row.kind === 'work_order') return `/maintenance/work-orders/${row.id}`;
  if (row.kind === 'location_problem') return `/maintenance/location-problems/${row.id}`;
  return `/assets/${row.asset_id ?? ''}`;
};

const MaintenanceDashboardPage: React.FC = () => {
  const [data, setData] = useState<MaintenanceDashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<ActiveMaintenanceRow[]>([]);
  const [activeFilter, setActiveFilter] = useState<ActiveKindFilter>('all');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([maintenanceAPI.getDashboard(), activeMaintenanceAPI.list()])
      .then(([dash, act]) => {
        if (cancelled) return;
        setData(dash.data);
        setActive(act.data.results);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(extractErrorMessage(err, 'Failed to load maintenance dashboard.'));
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filteredActive = active.filter(
    (row) => activeFilter === 'all' || row.kind === activeFilter,
  );

  return (
    <Container size="xl" py="md" data-testid="maintenance-dashboard-page">
      <Group justify="space-between" mb="md">
        <Title order={2}>Maintenance</Title>
        <Anchor component={Link} to="/maintenance/dashboard" size="sm">
          Open PM Dashboard →
        </Anchor>
      </Group>

      {loading && (
        <Group justify="center" py="xl">
          <Loader />
          <Text c="dimmed">Loading maintenance dashboard…</Text>
        </Group>
      )}

      {!loading && error && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
          {error}
        </Alert>
      )}

      {!loading && !error && data && (
        <Stack gap="lg">
          <Box data-testid="active-maintenance-list">
            <Group mb="sm" gap="xs">
              <IconClipboardList size={18} />
              <Title order={4}>Active Maintenance</Title>
              <Badge>{filteredActive.length}</Badge>
              <Group gap={4} ml="auto">
                {(['all', 'work_order', 'asset_problem', 'location_problem'] as ActiveKindFilter[]).map(
                  (kind) => (
                    <Badge
                      key={kind}
                      onClick={() => setActiveFilter(kind)}
                      variant={activeFilter === kind ? 'filled' : 'light'}
                      style={{ cursor: 'pointer' }}
                    >
                      {kind === 'all' ? 'All' : KIND_LABELS[kind]}
                    </Badge>
                  ),
                )}
              </Group>
            </Group>
            {filteredActive.length === 0 ? (
              <Card withBorder p="md" radius="sm">
                <Text size="sm" c="dimmed">
                  No active maintenance items.
                </Text>
              </Card>
            ) : (
              <Card withBorder p={0} radius="sm">
                <Table striped highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Kind</Table.Th>
                      <Table.Th>ID</Table.Th>
                      <Table.Th>Title</Table.Th>
                      <Table.Th>Asset / Location</Table.Th>
                      <Table.Th>Severity</Table.Th>
                      <Table.Th>Status</Table.Th>
                      <Table.Th>Opened</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {filteredActive.map((row) => (
                      <Table.Tr
                        key={`${row.kind}-${row.id}`}
                        data-testid={`active-row-${row.kind}`}
                      >
                        <Table.Td>
                          <Badge color={KIND_COLORS[row.kind]} variant="light">
                            {KIND_LABELS[row.kind]}
                          </Badge>
                        </Table.Td>
                        <Table.Td>
                          <Anchor component={Link} to={detailLinkFor(row)}>
                            {row.short_id}
                          </Anchor>
                        </Table.Td>
                        <Table.Td>{row.title}</Table.Td>
                        <Table.Td>
                          {row.asset_name ?? row.location_name ?? '—'}
                        </Table.Td>
                        <Table.Td>
                          {row.severity ? (
                            <Badge color={SEVERITY_COLORS[row.severity]} variant="filled">
                              {row.severity}
                            </Badge>
                          ) : (
                            '—'
                          )}
                        </Table.Td>
                        <Table.Td>
                          <Badge variant="light">{row.status_display}</Badge>
                        </Table.Td>
                        <Table.Td>{formatDate(row.opened_at)}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Card>
            )}
          </Box>

          <Box>
            <Group mb="sm" gap="xs">
              <IconCash size={18} />
              <Title order={4}>Maintenance Cost</Title>
            </Group>
            <SimpleGrid cols={{ base: 2, sm: 5 }}>
              {PERIOD_LABELS.map(({ key, label }) => (
                <Card withBorder p="md" radius="md" key={key} data-testid={`cost-card-${key}`}>
                  <Text size="sm" c="dimmed" fw={500}>
                    {label}
                  </Text>
                  <Text size="xl" fw={700}>
                    {formatCurrency(data.costs.per_period[key])}
                  </Text>
                </Card>
              ))}
            </SimpleGrid>
          </Box>

          <Box>
            <Group mb="sm" gap="xs">
              <IconCalendar size={18} />
              <Title order={4}>Scheduled PM</Title>
              <Badge>{data.scheduled_pm.length}</Badge>
            </Group>
            {data.scheduled_pm.length === 0 ? (
              <Card withBorder p="md" radius="sm">
                <Text size="sm" c="dimmed" data-testid="empty-scheduled">
                  No scheduled maintenance.
                </Text>
              </Card>
            ) : (
              <Card withBorder p={0} radius="sm">
                <Table striped highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Asset</Table.Th>
                      <Table.Th>Task</Table.Th>
                      <Table.Th>Interval</Table.Th>
                      <Table.Th>Next Due</Table.Th>
                      <Table.Th>Days Until</Table.Th>
                      <Table.Th>Last Completed</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {data.scheduled_pm.map((row) => (
                      <Table.Tr key={row.maintenance_item_id} data-testid="scheduled-row">
                        <Table.Td>
                          <Anchor component={Link} to={`/assets/${row.asset_id}`}>
                            {row.asset_name}
                          </Anchor>
                        </Table.Td>
                        <Table.Td>{row.title}</Table.Td>
                        <Table.Td>{row.interval_days ? `${row.interval_days}d` : '—'}</Table.Td>
                        <Table.Td>{formatDate(row.next_due)}</Table.Td>
                        <Table.Td>
                          {row.is_overdue ? (
                            <Badge color="red">
                              {row.days_until != null ? `${Math.abs(row.days_until)}d overdue` : 'Overdue'}
                            </Badge>
                          ) : row.days_until != null ? (
                            `${row.days_until}d`
                          ) : (
                            '—'
                          )}
                        </Table.Td>
                        <Table.Td>{formatDate(row.last_completed_at)}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Card>
            )}
          </Box>

          <Box>
            <Group mb="sm" gap="xs">
              <IconClipboardList size={18} />
              <Title order={4}>Unscheduled / Open Problems</Title>
              <Badge>{data.unscheduled.length}</Badge>
            </Group>
            {data.unscheduled.length === 0 ? (
              <Card withBorder p="md" radius="sm">
                <Text size="sm" c="dimmed" data-testid="empty-unscheduled">
                  No open problems.
                </Text>
              </Card>
            ) : (
              <Card withBorder p={0} radius="sm">
                <Table striped highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Work Order</Table.Th>
                      <Table.Th>Asset</Table.Th>
                      <Table.Th>Problem</Table.Th>
                      <Table.Th>Status</Table.Th>
                      <Table.Th>Opened</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {data.unscheduled.map((row) => (
                      <Table.Tr key={row.workorder_id} data-testid="unscheduled-row">
                        <Table.Td>
                          <Anchor component={Link} to={`/maintenance/work-orders/${row.workorder_id}`}>
                            {row.short_id}
                          </Anchor>
                        </Table.Td>
                        <Table.Td>
                          <Anchor component={Link} to={`/assets/${row.asset_id}`}>
                            {row.asset_name}
                          </Anchor>
                        </Table.Td>
                        <Table.Td>{row.problem}</Table.Td>
                        <Table.Td>
                          <Badge variant="light">{row.status}</Badge>
                        </Table.Td>
                        <Table.Td>{formatDate(row.opened_at)}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Card>
            )}
          </Box>

          <Box>
            <Group mb="sm" gap="xs">
              <IconCash size={18} />
              <Title order={4}>Cost by Asset (last 90 days)</Title>
              <Badge>{data.costs.by_asset.length}</Badge>
            </Group>
            {data.costs.by_asset.length === 0 ? (
              <Card withBorder p="md" radius="sm">
                <Text size="sm" c="dimmed" data-testid="empty-by-asset">
                  No maintenance cost recorded in the last 90 days.
                </Text>
              </Card>
            ) : (
              <Card withBorder p={0} radius="sm">
                <Table striped highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Asset</Table.Th>
                      <Table.Th>Total Cost</Table.Th>
                      <Table.Th>Days in Maintenance</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {data.costs.by_asset.map((row) => (
                      <Table.Tr key={row.asset_id} data-testid="by-asset-row">
                        <Table.Td>
                          <Anchor component={Link} to={`/assets/${row.asset_id}`}>
                            {row.asset_name}
                          </Anchor>
                        </Table.Td>
                        <Table.Td>{formatCurrency(row.total_cost)}</Table.Td>
                        <Table.Td>{row.days_in_maintenance_90d}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Card>
            )}
          </Box>
        </Stack>
      )}
    </Container>
  );
};

export default MaintenanceDashboardPage;
