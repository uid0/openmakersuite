import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Loader,
  SegmentedControl,
  Table,
  Text,
} from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { locationProblemsAPI } from '../services/api';
import { LocationProblem } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const SEVERITY_COLORS: Record<string, string> = {
  low: 'gray',
  medium: 'cyan',
  high: 'orange',
  urgent: 'red',
};

type StatusFilter = 'open' | 'all' | 'resolved';

const LocationProblemsListPage: React.FC = () => {
  const [problems, setProblems] = useState<LocationProblem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('open');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    locationProblemsAPI
      .list()
      .then((resp) => {
        if (cancelled) return;
        setProblems(resp.data.results || []);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(extractErrorMessage(err, 'Failed to load location problems.'));
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    if (statusFilter === 'all') return problems;
    if (statusFilter === 'resolved') {
      return problems.filter((p) => p.status === 'resolved' || p.status === 'closed');
    }
    return problems.filter((p) => p.status !== 'resolved' && p.status !== 'closed');
  }, [problems, statusFilter]);

  return (
    <WorkspacePage
      testId="location-problems-list-page"
      hero={{
        eyebrow: 'Maintenance',
        title: 'Location problems',
        description: 'Reported issues for shop locations — open, resolved, and the full history.',
        action: (
          <Button component={Link} to="/maintenance" variant="default">
            Back to maintenance
          </Button>
        ),
      }}
    >
      <SegmentedControl
        mb="md"
        value={statusFilter}
        onChange={(v) => setStatusFilter(v as StatusFilter)}
        data={[
          { label: 'Open', value: 'open' },
          { label: 'Resolved', value: 'resolved' },
          { label: 'All', value: 'all' },
        ]}
        data-testid="location-problems-filter"
      />

      {loading && (
        <Group justify="center" py="xl">
          <Loader />
          <Text c="dimmed">Loading location problems…</Text>
        </Group>
      )}

      {!loading && error && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
          {error}
        </Alert>
      )}

      {!loading && !error && filtered.length === 0 && (
        <Text c="dimmed" data-testid="location-problems-empty">
          No location problems match this filter.
        </Text>
      )}

      {!loading && !error && filtered.length > 0 && (
        <Table striped highlightOnHover data-testid="location-problems-table">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Location</Table.Th>
              <Table.Th>Description</Table.Th>
              <Table.Th>Severity</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Reported</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {filtered.map((p) => (
              <Table.Tr key={p.id}>
                <Table.Td>
                  <Anchor component={Link} to={`/inventory/locations/${p.location}`}>
                    {p.location_name}
                  </Anchor>
                </Table.Td>
                <Table.Td>
                  <Anchor component={Link} to={`/maintenance/location-problems/${p.id}`}>
                    {p.description.length > 80
                      ? `${p.description.slice(0, 80)}…`
                      : p.description}
                  </Anchor>
                </Table.Td>
                <Table.Td>
                  <Badge color={SEVERITY_COLORS[p.severity] || 'gray'}>
                    {p.severity_display}
                  </Badge>
                </Table.Td>
                <Table.Td>
                  <Badge variant="light">{p.status_display}</Badge>
                </Table.Td>
                <Table.Td>
                  {new Date(p.reported_at).toLocaleDateString()}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </WorkspacePage>
  );
};

export default LocationProblemsListPage;
