/**
 * DemandForecastPanel — ML demand forecast + predictive reorder alerts for
 * *non-serialized* inventory items (op-3). Sibling of
 * <SerializedForecastPanel>, which does the same job for serialized
 * components; this one reads the rows the nightly forecasting task stores
 * (op-1 storage, op-2 engine) rather than computing anything client-side.
 *
 * Two surfaces in one panel:
 *  - a reorder-alerts banner atop the table — the daily "pings" for items
 *    someone opted in via the item form's "Watch for reorder alerts" switch
 *    *and* that the forecast says are due;
 *  - the forecast table itself, most-urgent first (the backend sorts), with a
 *    "Due to reorder only" switch feeding `low_stock_only`.
 *
 * Both endpoints return [] until the nightly task has run, so the empty state
 * says so rather than implying nothing needs reordering.
 */
import {
  Alert,
  Badge,
  Group,
  Loader,
  Paper,
  Switch,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { IconBellRinging } from '@tabler/icons-react';
import React, { useCallback, useEffect, useState } from 'react';

import { DemandForecastMethod, DemandForecastRow, reportsAPI } from '../../services/api';
import { extractErrorMessage } from '../../utils/extractErrorMessage';

interface Props {
  /** Start with the due-to-reorder-only filter on (e.g. the purchasing view). */
  defaultLowStockOnly?: boolean;
  title?: string;
  /** Called when a row is activated, e.g. to navigate to the item. */
  onSelectItem?: (itemId: string) => void;
}

const METHOD_LABELS: Record<DemandForecastMethod, string> = {
  prophet: 'Prophet',
  holtwinters: 'Holt-Winters',
  fallback: 'Fallback',
};

// Holt-Winters means the item had enough history for the seasonal model;
// fallback is a plain run-rate, so it gets a quieter badge.
const METHOD_COLORS: Record<DemandForecastMethod, string> = {
  prophet: 'grape',
  holtwinters: 'blue',
  fallback: 'gray',
};

const fmtDays = (n: number | null): string => (n === null ? '—' : `${n} d`);

const fmtRate = (n: number): string => n.toFixed(2);

const DemandForecastPanel: React.FC<Props> = ({
  defaultLowStockOnly = false,
  title = 'Demand forecast',
  onSelectItem,
}) => {
  const [rows, setRows] = useState<DemandForecastRow[]>([]);
  const [alerts, setAlerts] = useState<DemandForecastRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lowStockOnly, setLowStockOnly] = useState(defaultLowStockOnly);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [forecastRes, alertsRes] = await Promise.all([
        reportsAPI.getDemandForecast({ low_stock_only: lowStockOnly }),
        reportsAPI.getReorderAlerts(),
      ]);
      setRows(forecastRes?.data ?? []);
      setAlerts(alertsRes?.data ?? []);
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not load the demand forecast.'));
    } finally {
      setLoading(false);
    }
  }, [lowStockOnly]);

  useEffect(() => {
    load();
  }, [load]);

  const dueCount = rows.filter((r) => r.needs_reorder).length;

  return (
    <Paper withBorder p="md" data-testid="demand-forecast-panel">
      <Group justify="space-between" align="center" mb="sm" wrap="wrap">
        <Group gap="xs">
          <Title order={4}>{title}</Title>
          {!loading && (
            <Badge color={dueCount > 0 ? 'orange' : 'gray'} variant="light">
              {dueCount} due
            </Badge>
          )}
        </Group>
        <Switch
          size="sm"
          label="Due to reorder only"
          checked={lowStockOnly}
          onChange={(e) => setLowStockOnly(e.currentTarget.checked)}
          data-testid="demand-forecast-low-only"
        />
      </Group>

      {!loading && !error && alerts.length > 0 && (
        <Alert
          color="orange"
          variant="light"
          mb="sm"
          icon={<IconBellRinging size={18} />}
          title={
            <Group gap="xs">
              <span>Reorder alerts</span>
              <Badge color="orange" variant="filled" radius="sm">
                {alerts.length}
              </Badge>
            </Group>
          }
          data-testid="demand-forecast-alerts"
        >
          <Text size="sm" mb={4}>
            Watched items the forecast says are due to reorder now.
          </Text>
          {alerts.map((alert) => (
            <Text
              key={alert.id}
              size="sm"
              data-testid={`demand-forecast-alert-${alert.item}`}
              onClick={onSelectItem ? () => onSelectItem(alert.item) : undefined}
              style={{ cursor: onSelectItem ? 'pointer' : undefined }}
            >
              <b>{alert.item_name}</b> — {alert.available_at_generation} left,
              reorder at {alert.predictive_reorder_point}
              {alert.days_until_stockout !== null &&
                ` (out in ${alert.days_until_stockout} d)`}
            </Text>
          ))}
        </Alert>
      )}

      {loading ? (
        <Group justify="center" py="lg">
          <Loader size="sm" />
        </Group>
      ) : error ? (
        <Text c="red" size="sm">
          {error}
        </Text>
      ) : rows.length === 0 ? (
        <Text c="dimmed" data-testid="demand-forecast-empty">
          {lowStockOnly
            ? 'No forecasted items are due to reorder.'
            : 'No demand forecast yet — it is generated nightly once items have usage history.'}
        </Text>
      ) : (
        <Table.ScrollContainer minWidth={800}>
          <Table highlightOnHover verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Item</Table.Th>
                <Table.Th>Method</Table.Th>
                <Table.Th ta="right">Avg/day</Table.Th>
                <Table.Th ta="right">Stockout</Table.Th>
                <Table.Th ta="right">Reorder pt</Table.Th>
                <Table.Th ta="right">Available</Table.Th>
                <Table.Th>Status</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((row) => (
                <Table.Tr
                  key={row.id}
                  data-testid={`demand-forecast-row-${row.item}`}
                  onClick={onSelectItem ? () => onSelectItem(row.item) : undefined}
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
                  <Table.Td>
                    <Badge color={METHOD_COLORS[row.method] ?? 'gray'} variant="light">
                      {METHOD_LABELS[row.method] ?? row.method}
                    </Badge>
                  </Table.Td>
                  <Table.Td ta="right">{fmtRate(row.predicted_daily_demand)}</Table.Td>
                  <Table.Td ta="right">{fmtDays(row.days_until_stockout)}</Table.Td>
                  <Table.Td ta="right">{row.predictive_reorder_point}</Table.Td>
                  <Table.Td ta="right" fw={600}>
                    {row.available_at_generation}
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
        <Text size="xs" c="dimmed" mt="xs" data-testid="demand-forecast-legend">
          Reorder is driven by <b>available</b> stock versus the demand predicted
          over the lead time, snapshotted when the forecast ran.
        </Text>
      )}
    </Paper>
  );
};

export default DemandForecastPanel;
