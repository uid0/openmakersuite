/**
 * SerializedForecastPanel — consumption forecast + low-stock report for
 * serialized components (#819). Renders one row per active serialized item
 * with its depletion rate, projected days-until-stockout and reorder point,
 * most-urgent first (the backend sorts). Items at/below their reorder point
 * are flagged so the inventory + purchasing overviews can surface what is
 * running low and what to reorder.
 *
 * Controls: a trailing-window selector (feeds `window_days`) and a
 * "Low stock only" switch (feeds `low_stock_only`).
 */
import {
  Badge,
  Group,
  Loader,
  Paper,
  Select,
  Switch,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import React, { useCallback, useEffect, useState } from 'react';

import { reportsAPI, SerializedForecastRow } from '../../services/api';
import { extractErrorMessage } from '../../utils/extractErrorMessage';

interface Props {
  /** Start with the low-stock-only filter on (e.g. the purchasing view). */
  defaultLowStockOnly?: boolean;
  /** Trailing window (days) for the depletion rate. */
  defaultWindowDays?: number;
  title?: string;
  /** Called when a row is activated, e.g. to navigate to the item. */
  onSelectItem?: (itemId: string) => void;
}

const WINDOW_OPTIONS = [
  { value: '30', label: 'Last 30 days' },
  { value: '60', label: 'Last 60 days' },
  { value: '90', label: 'Last 90 days' },
  { value: '180', label: 'Last 180 days' },
];

const fmtDays = (n: number | null): string => (n === null ? '—' : `${n} d`);

const SerializedForecastPanel: React.FC<Props> = ({
  defaultLowStockOnly = false,
  defaultWindowDays = 90,
  title = 'Serialized-component forecast',
  onSelectItem,
}) => {
  const [rows, setRows] = useState<SerializedForecastRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lowStockOnly, setLowStockOnly] = useState(defaultLowStockOnly);
  const [windowDays, setWindowDays] = useState(String(defaultWindowDays));

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await reportsAPI.getSerializedForecast({
        window_days: Number(windowDays),
        low_stock_only: lowStockOnly,
      });
      setRows(res?.data ?? []);
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not load the serialized forecast.'));
    } finally {
      setLoading(false);
    }
  }, [windowDays, lowStockOnly]);

  useEffect(() => {
    load();
  }, [load]);

  const lowStockCount = rows.filter((r) => r.needs_reorder).length;

  return (
    <Paper withBorder p="md" data-testid="serialized-forecast-panel">
      <Group justify="space-between" align="center" mb="sm" wrap="wrap">
        <Group gap="xs">
          <Title order={4}>{title}</Title>
          {!loading && (
            <Badge color={lowStockCount > 0 ? 'orange' : 'gray'} variant="light">
              {lowStockCount} low
            </Badge>
          )}
        </Group>
        <Group gap="sm">
          <Select
            size="xs"
            data={WINDOW_OPTIONS}
            value={windowDays}
            onChange={(v) => setWindowDays(v ?? '90')}
            allowDeselect={false}
            w={150}
            aria-label="Forecast window"
            data-testid="serialized-forecast-window"
          />
          <Switch
            size="sm"
            label="Low stock only"
            checked={lowStockOnly}
            onChange={(e) => setLowStockOnly(e.currentTarget.checked)}
            data-testid="serialized-forecast-low-only"
          />
        </Group>
      </Group>

      {loading ? (
        <Group justify="center" py="lg">
          <Loader size="sm" />
        </Group>
      ) : error ? (
        <Text c="red" size="sm">
          {error}
        </Text>
      ) : rows.length === 0 ? (
        <Text c="dimmed">
          {lowStockOnly
            ? 'No serialized items are below their reorder point.'
            : 'No serialized items to forecast yet.'}
        </Text>
      ) : (
        <Table.ScrollContainer minWidth={800}>
          <Table highlightOnHover verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Item</Table.Th>
                <Table.Th>Mode</Table.Th>
                <Table.Th ta="right">Available</Table.Th>
                <Table.Th ta="right">On-hand</Table.Th>
                <Table.Th ta="right">Avg/day</Table.Th>
                <Table.Th ta="right">Stockout</Table.Th>
                <Table.Th ta="right">Reorder pt</Table.Th>
                <Table.Th>Status</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((row) => (
                <Table.Tr
                  key={row.item_id}
                  data-testid={`serialized-forecast-row-${row.item_id}`}
                  onClick={onSelectItem ? () => onSelectItem(row.item_id) : undefined}
                  style={{
                    cursor: onSelectItem ? 'pointer' : undefined,
                    background: row.needs_reorder
                      ? 'var(--mantine-color-orange-0)'
                      : undefined,
                  }}
                >
                  <Table.Td>
                    <Text fw={500} lineClamp={1}>
                      {row.item_name}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {row.sku || 'no SKU'}
                    </Text>
                  </Table.Td>
                  <Table.Td tt="capitalize">{row.serial_tracking_mode}</Table.Td>
                  <Table.Td ta="right" fw={600}>
                    {row.available}
                  </Table.Td>
                  <Table.Td ta="right" c="dimmed">
                    {row.on_hand}
                  </Table.Td>
                  <Table.Td ta="right">{row.avg_daily_use}</Table.Td>
                  <Table.Td ta="right">{fmtDays(row.days_until_stockout)}</Table.Td>
                  <Table.Td ta="right">
                    {row.lead_time_known === false ? (
                      // No lead time is known for this item (it carries no
                      // supplier link), so the reorder point is the safety
                      // stock alone — a lower bound, not a horizon (op-c1ke).
                      // Say so rather than quoting the number as complete.
                      <Tooltip label="No supplier lead time recorded — this is the safety stock only, with no lead-time allowance. Add a supplier to complete it.">
                        <Text
                          span
                          c="dimmed"
                          data-testid={`serialized-forecast-partial-rp-${row.item_id}`}
                        >
                          ≥ {row.reorder_point}
                        </Text>
                      </Tooltip>
                    ) : (
                      row.reorder_point
                    )}
                  </Table.Td>
                  <Table.Td>
                    {row.needs_reorder ? (
                      <Badge color="orange" variant="filled">
                        Reorder
                      </Badge>
                    ) : (
                      <Badge color="green" variant="light">
                        OK
                      </Badge>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}

      {!loading && !error && rows.length > 0 && (
        <Text size="xs" c="dimmed" mt="xs" data-testid="serialized-forecast-legend">
          Reorder is driven by <b>available</b> (on-hand minus units installed in
          an asset), not on-hand.
        </Text>
      )}
    </Paper>
  );
};

export default SerializedForecastPanel;
