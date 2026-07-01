/**
 * AssetSerializedComponentsSection — serialized-component surface for
 * AssetDetailPage (#818). Two views for one machine:
 *
 *   1. "Installed now" — the serial-numbered units currently installed in this
 *      asset (status=installed, installed_in_asset=<asset>).
 *   2. "Serial history" — every serial this machine has used, drawn from the
 *      ComponentUsageEvent log filtered by ?asset=<asset> (install / remove /
 *      consume / retire / dispose), newest first.
 *
 * Read-only: the lifecycle actions live on the item's instances view
 * (SerializedComponentsPanel). Self-contained so the asset page adds one
 * import + one JSX line.
 */
import { Badge, Group, Loader, Table, Text, Title } from '@mantine/core';
import React, { useEffect, useMemo, useState } from 'react';

import {
  ComponentUsageEvent,
  SerializedComponent,
  serializedComponentsAPI,
} from '../../services/api';
import { extractErrorMessage } from '../../utils/extractErrorMessage';
import { serializedStatusColor } from '../../utils/serializedComponents';

interface Props {
  assetId: string;
}

const fmtDateTime = (iso: string | null): string =>
  iso
    ? new Date(iso).toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—';

const AssetSerializedComponentsSection: React.FC<Props> = ({ assetId }) => {
  const [installed, setInstalled] = useState<SerializedComponent[]>([]);
  const [history, setHistory] = useState<ComponentUsageEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError(null);
      const [installedRes, historyRes] = await Promise.allSettled([
        serializedComponentsAPI.list({ installed_in_asset: assetId, status: 'installed' }),
        serializedComponentsAPI.listUsageEvents({ asset: assetId }),
      ]);
      if (cancelled) return;
      if (installedRes.status === 'fulfilled') {
        setInstalled(installedRes.value?.data?.results ?? []);
      } else {
        setError(extractErrorMessage(installedRes.reason, 'Could not load installed components.'));
      }
      if (historyRes.status === 'fulfilled') {
        setHistory(historyRes.value?.data?.results ?? []);
      }
      setLoading(false);
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [assetId]);

  const totalUsedSerials = useMemo(
    () => new Set(history.map((ev) => ev.component)).size,
    [history],
  );

  if (loading) {
    return (
      <section className="asset-detail-section" data-testid="asset-serialized-section">
        <h2>Serialized components</h2>
        <Group gap="xs">
          <Loader size="sm" />
          <Text c="dimmed">Loading serialized components…</Text>
        </Group>
      </section>
    );
  }

  // Nothing serialized has ever touched this machine — stay quiet rather than
  // showing two empty tables on every asset.
  if (!error && installed.length === 0 && history.length === 0) {
    return null;
  }

  return (
    <section className="asset-detail-section" data-testid="asset-serialized-section">
      <h2>Serialized components</h2>

      {error && (
        <Text c="red" size="sm" mb="sm">
          {error}
        </Text>
      )}

      <Group gap="xs" mb="xs">
        <Title order={5}>Installed now</Title>
        <Badge variant="light">{installed.length}</Badge>
      </Group>
      {installed.length === 0 ? (
        <Text c="dimmed" mb="md">
          No serialized components are currently installed in this asset.
        </Text>
      ) : (
        <Table mb="md" verticalSpacing="xs" data-testid="asset-serialized-installed">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Serial</Table.Th>
              <Table.Th>Component</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Installed</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {installed.map((unit) => (
              <Table.Tr key={unit.id}>
                <Table.Td>
                  <Text fw={500}>{unit.serial_number}</Text>
                </Table.Td>
                <Table.Td>{unit.item_name}</Table.Td>
                <Table.Td>
                  <Badge color={serializedStatusColor(unit.status)} variant="light">
                    {unit.status_display}
                  </Badge>
                </Table.Td>
                <Table.Td>{fmtDateTime(unit.installed_at)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Group gap="xs" mb="xs">
        <Title order={5}>Serial history</Title>
        <Badge variant="light" color="gray">
          {totalUsedSerials} serial{totalUsedSerials === 1 ? '' : 's'}
        </Badge>
      </Group>
      {history.length === 0 ? (
        <Text c="dimmed">No serialized-component history for this asset.</Text>
      ) : (
        <Table verticalSpacing="xs" data-testid="asset-serialized-history">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>When</Table.Th>
              <Table.Th>Serial</Table.Th>
              <Table.Th>Component</Table.Th>
              <Table.Th>Action</Table.Th>
              <Table.Th>By</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {history.map((ev) => (
              <Table.Tr key={ev.id}>
                <Table.Td>{fmtDateTime(ev.at)}</Table.Td>
                <Table.Td>{ev.component_serial}</Table.Td>
                <Table.Td>{ev.component_item_name}</Table.Td>
                <Table.Td>{ev.action_display}</Table.Td>
                <Table.Td>{ev.actor_username || '—'}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </section>
  );
};

export default AssetSerializedComponentsSection;
