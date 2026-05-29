/**
 * Audit Feed Page (closes AC-26 / R-06)
 *
 * Staff-only review surface for the unified audit-event feed at
 * `/api/dashboard/audit-feed/`. Renders normalized rows from every
 * per-domain audit table (forgekey, purchase_orders, webhooks,
 * donations, maintenance, vendors, customization,
 * third_party_work_orders) in a single filterable timeline so a
 * reviewer can answer "who did what, when, on this entity" without
 * grepping eight tables by hand.
 *
 * Filters server-side: domain, since/until, limit. Filters
 * client-side: actor username substring (cheap, avoids a user
 * lookup round-trip). Click a row to expand the JSON metadata
 * blob — kept collapsed by default to keep the page scannable.
 */
import {
  Alert,
  Badge,
  Button,
  Code,
  Collapse,
  Group,
  Loader,
  Paper,
  ScrollArea,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { AuditFeedEvent, dashboardAPI } from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const DOMAINS = [
  { value: '', label: 'All domains' },
  { value: 'forgekey', label: 'ForgeKey (device / safety)' },
  { value: 'purchase_orders', label: 'Purchase orders' },
  { value: 'webhooks', label: 'Webhooks' },
  { value: 'donations', label: 'Donations' },
  { value: 'maintenance', label: 'Maintenance' },
  { value: 'vendors', label: 'Vendors' },
  { value: 'customization', label: 'Site settings' },
  { value: 'third_party_work_orders', label: 'Third-party WOs' },
];

const LIMITS = [
  { value: '100', label: '100' },
  { value: '200', label: '200' },
  { value: '500', label: '500' },
  { value: '1000', label: '1000' },
];

const DOMAIN_COLORS: Record<string, string> = {
  forgekey: 'orange',
  purchase_orders: 'blue',
  webhooks: 'grape',
  donations: 'pink',
  maintenance: 'teal',
  vendors: 'indigo',
  customization: 'gray',
  third_party_work_orders: 'cyan',
};

const formatTimestamp = (iso: string): string => {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};

const AuditFeedPage: React.FC = () => {
  const isStaff =
    typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' && localStorage.getItem('is_superuser') === 'true';

  const [events, setEvents] = useState<AuditFeedEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [domain, setDomain] = useState<string>('');
  const [since, setSince] = useState<string>('');
  const [until, setUntil] = useState<string>('');
  const [limit, setLimit] = useState<string>('200');
  const [actorFilter, setActorFilter] = useState<string>('');
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await dashboardAPI.getAuditFeed({
        domain: domain || undefined,
        since: since || undefined,
        until: until || undefined,
        limit: limit ? parseInt(limit, 10) : undefined,
      });
      setEvents(response.data.events);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load audit feed.'));
      setEvents([]);
    } finally {
      setLoading(false);
    }
  }, [domain, since, until, limit]);

  useEffect(() => {
    if (!isStaff && !isSuperuser) {
      return;
    }
    load();
  }, [isStaff, isSuperuser, load]);

  const visibleEvents = useMemo(() => {
    const needle = actorFilter.trim().toLowerCase();
    if (!needle) {
      return events;
    }
    return events.filter((event) =>
      (event.actor_username ?? '').toLowerCase().includes(needle),
    );
  }, [events, actorFilter]);

  const toggleRow = useCallback((index: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }, []);

  if (!isStaff && !isSuperuser) {
    return <Navigate to="/" replace />;
  }

  return (
    <Paper p="lg" m="md" withBorder>
      <Stack gap="md">
        <Stack gap={4}>
          <Title order={2}>Audit Feed</Title>
          <Text size="sm" c="dimmed">
            Unified review surface for every domain audit table. Filter
            by domain, actor, or time window. Newest first.
          </Text>
        </Stack>

        <Group grow align="end" wrap="wrap">
          <Select
            label="Domain"
            data={DOMAINS}
            value={domain}
            onChange={(value) => setDomain(value ?? '')}
            allowDeselect={false}
            comboboxProps={{ withinPortal: true }}
          />
          <TextInput
            label="Actor (username contains)"
            placeholder="uid0"
            value={actorFilter}
            onChange={(event) => setActorFilter(event.currentTarget.value)}
          />
          <TextInput
            label="Since (ISO)"
            placeholder="2026-05-01"
            value={since}
            onChange={(event) => setSince(event.currentTarget.value)}
          />
          <TextInput
            label="Until (ISO)"
            placeholder="2026-05-29"
            value={until}
            onChange={(event) => setUntil(event.currentTarget.value)}
          />
          <Select
            label="Limit"
            data={LIMITS}
            value={limit}
            onChange={(value) => setLimit(value ?? '200')}
            allowDeselect={false}
            comboboxProps={{ withinPortal: true }}
          />
          <Button onClick={load} loading={loading}>
            Refresh
          </Button>
        </Group>

        {error && (
          <Alert color="red" variant="light">
            {error}
          </Alert>
        )}

        {loading ? (
          <Stack align="center" gap="xs" py="xl">
            <Loader />
            <Text size="sm" c="dimmed">
              Loading audit events…
            </Text>
          </Stack>
        ) : visibleEvents.length === 0 ? (
          <Text size="sm" c="dimmed">
            No events matched the current filters.
          </Text>
        ) : (
          <>
            <Text size="sm" c="dimmed">
              Showing {visibleEvents.length} of {events.length} fetched
              {actorFilter ? ' (actor-filtered client-side)' : ''}.
            </Text>
            <ScrollArea>
              <Table striped highlightOnHover withTableBorder>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>When</Table.Th>
                    <Table.Th>Actor</Table.Th>
                    <Table.Th>Domain</Table.Th>
                    <Table.Th>Action</Table.Th>
                    <Table.Th>Entity</Table.Th>
                    <Table.Th>Notes</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {visibleEvents.map((event, index) => {
                    const open = expanded.has(index);
                    return (
                      <React.Fragment key={`${event.domain}-${event.created_at}-${index}`}>
                        <Table.Tr
                          style={{ cursor: 'pointer' }}
                          onClick={() => toggleRow(index)}
                          data-testid={`audit-row-${index}`}
                        >
                          <Table.Td>{formatTimestamp(event.created_at)}</Table.Td>
                          <Table.Td>{event.actor_username ?? <Text c="dimmed">system</Text>}</Table.Td>
                          <Table.Td>
                            <Badge color={DOMAIN_COLORS[event.domain] ?? 'gray'} variant="light">
                              {event.domain}
                            </Badge>
                          </Table.Td>
                          <Table.Td>
                            <Code>{event.action}</Code>
                          </Table.Td>
                          <Table.Td>
                            {event.entity_type ? (
                              <Text size="xs">
                                {event.entity_type}
                                {event.entity_id ? ` · ${event.entity_id}` : ''}
                              </Text>
                            ) : (
                              <Text c="dimmed">—</Text>
                            )}
                          </Table.Td>
                          <Table.Td>{event.notes || <Text c="dimmed">—</Text>}</Table.Td>
                        </Table.Tr>
                        <Table.Tr>
                          <Table.Td colSpan={6} style={{ padding: 0, border: 0 }}>
                            <Collapse in={open}>
                              <Paper p="sm" m="sm" withBorder bg="var(--mantine-color-gray-0)">
                                <Stack gap="xs">
                                  <Text size="xs" c="dimmed">
                                    Metadata
                                  </Text>
                                  <Code block>
                                    {JSON.stringify(event.metadata, null, 2)}
                                  </Code>
                                </Stack>
                              </Paper>
                            </Collapse>
                          </Table.Td>
                        </Table.Tr>
                      </React.Fragment>
                    );
                  })}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          </>
        )}
      </Stack>
    </Paper>
  );
};

export default AuditFeedPage;
