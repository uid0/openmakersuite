/**
 * Screens list / status page.
 *
 * Shows all kiosk screens the user has permission to see, with live-ish
 * status (online/offline based on last heartbeat), ownership info, and
 * quick links to edit or open the kiosk URL.
 */
import {
  Badge,
  Button,
  Group,
  Loader,
  Paper,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { screensAPI } from '../services/api';
import { Screen, ScreenStatusEntry } from '../types';

const formatTimestamp = (iso?: string | null): string => {
  if (!iso) return 'never';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};

const ScreensListPage: React.FC = () => {
  const navigate = useNavigate();
  const [statuses, setStatuses] = useState<ScreenStatusEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const resp = await screensAPI.getStatus();
      setStatuses(resp.data.screens || []);
    } catch (err) {
      console.error('Failed to load screens', err);
      setError('Unable to load screens.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = window.setInterval(load, 30000);
    return () => window.clearInterval(interval);
  }, [load]);

  const handleCreate = async () => {
    const slugInput = window.prompt('New screen slug (URL-friendly, e.g. "lobby"):');
    if (!slugInput) return;
    const slug = slugInput.trim().toLowerCase().replace(/[^a-z0-9-]/g, '-');
    if (!slug) return;
    const name = window.prompt('Display name for the screen:', slug) || slug;
    try {
      const resp = await screensAPI.createScreen({ slug, name, is_active: true } as Partial<Screen>);
      navigate(`/facilities/screens/${resp.data.slug}`);
    } catch (err) {
      console.error('Create failed', err);
      window.alert('Could not create screen. Check your permissions and slug uniqueness.');
    }
  };

  return (
    <Paper p="md" shadow="xs">
      <Group justify="space-between" mb="md">
        <Title order={2}>Interactive Screens</Title>
        <Group>
          <Button variant="default" onClick={load}>Refresh</Button>
          <Button onClick={handleCreate}>New Screen</Button>
        </Group>
      </Group>

      {loading && (
        <Group>
          <Loader size="sm" />
          <Text>Loading screens…</Text>
        </Group>
      )}

      {error && <Text c="red">{error}</Text>}

      {!loading && !error && statuses.length === 0 && (
        <Text c="dimmed">
          No screens configured yet. Click <b>New Screen</b> to create one.
        </Text>
      )}

      {!loading && statuses.length > 0 && (
        <Table striped highlightOnHover withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Owner (SIG)</Table.Th>
              <Table.Th>Location</Table.Th>
              <Table.Th>Active</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Last Heartbeat</Table.Th>
              <Table.Th>Actions</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {statuses.map((s) => (
              <Table.Tr key={s.id} data-testid={`screen-row-${s.slug}`}>
                <Table.Td>
                  <Stack gap={2}>
                    <Text fw={600}>{s.name}</Text>
                    <Text size="xs" c="dimmed">{s.slug}</Text>
                  </Stack>
                </Table.Td>
                <Table.Td>{s.sig_name || '—'}</Table.Td>
                <Table.Td>{s.location_name || '—'}</Table.Td>
                <Table.Td>
                  <Badge color={s.is_active ? 'green' : 'gray'}>
                    {s.is_active ? 'Active' : 'Disabled'}
                  </Badge>
                </Table.Td>
                <Table.Td>
                  <Badge color={s.is_online ? 'teal' : 'red'} variant="light">
                    {s.is_online ? 'Online' : 'Offline'}
                  </Badge>
                </Table.Td>
                <Table.Td>{formatTimestamp(s.last_heartbeat_at)}</Table.Td>
                <Table.Td>
                  <Group gap="xs">
                    <Button
                      size="xs"
                      variant="default"
                      onClick={() => navigate(`/facilities/screens/${s.slug}`)}
                    >
                      Edit
                    </Button>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Paper>
  );
};

export default ScreensListPage;
