/**
 * DemandForecastPanel — restock-cadence forecast + predictive reorder alerts
 * for *non-serialized* inventory items. Sibling of <SerializedForecastPanel>,
 * which does the same job for serialized components; this one reads the rows
 * the nightly forecasting task stores rather than computing anything
 * client-side.
 *
 * The v2 model answers **"when is this due to be bought again"**, not "how much
 * will be used": cadence is the mean gap between purchase events, the due date
 * is last restock + cadence, and an item is flagged when that date falls inside
 * the supplier lead time. The retired v1 usage-rate columns (avg/day, stockout,
 * reorder point) are deliberately gone — the backend still sends those keys but
 * writes them 0/null, so rendering them would only show zeroes.
 *
 * Two surfaces in one panel:
 *  - a reorder-alerts banner atop the table — the daily "pings" for items
 *    someone opted in via the item form's "Watch for reorder alerts" switch
 *    *and* that the forecast says are due;
 *  - the forecast table itself, most-urgent first (the backend sorts; rows are
 *    rendered in response order), with a "Due to reorder only" switch feeding
 *    `low_stock_only`.
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
import { formatDateOnly } from '../../utils/dates';
import { extractErrorMessage } from '../../utils/extractErrorMessage';

interface Props {
  /** Start with the due-to-reorder-only filter on (e.g. the purchasing view). */
  defaultLowStockOnly?: boolean;
  title?: string;
  /** Called when a row is activated, e.g. to navigate to the item. */
  onSelectItem?: (itemId: string) => void;
}

const METHOD_LABELS: Record<DemandForecastMethod, string> = {
  restock_interval: 'Restock-interval',
  insufficient_history: 'Insufficient history',
  // Retired v1 (usage-rate) methods — pre-v2 rows only.
  prophet: 'Prophet',
  holtwinters: 'Holt-Winters',
  fallback: 'Fallback',
};

// A cadence the model could actually measure gets a live badge; everything
// else — no history yet, or a retired v1 row — stays quiet.
const METHOD_COLORS: Record<DemandForecastMethod, string> = {
  restock_interval: 'blue',
  insufficient_history: 'gray',
  prophet: 'gray',
  holtwinters: 'gray',
  fallback: 'gray',
};

/** Mean days between purchases, e.g. "~48d". Null under two purchase events. */
const fmtCadence = (days: number | null): string =>
  days === null ? '—' : `~${Math.round(days)}d`;

/**
 * Humanise `days_until_due` for the due column and the alert lines. Mirrors the
 * backend digest's phrasing so the in-app alert and this panel read alike.
 */
const fmtDue = (days: number | null): string => {
  if (days === null) return '—';
  const whole = Math.round(days);
  if (whole < 0) return `overdue ${-whole}d`;
  if (whole === 0) return 'due today';
  return `due in ${whole}d`;
};

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
            {alerts.length} watched item{alerts.length === 1 ? ' is' : 's are'} due to
            reorder, based on how often they are normally bought.
          </Text>
          {alerts.map((alert) => (
            <Text
              key={alert.id}
              size="sm"
              data-testid={`demand-forecast-alert-${alert.item}`}
              onClick={onSelectItem ? () => onSelectItem(alert.item) : undefined}
              style={{ cursor: onSelectItem ? 'pointer' : undefined }}
            >
              <b>{alert.item_name}</b> — {fmtDue(alert.days_until_due)}
              {alert.predicted_next_reorder_date &&
                ` (due ${formatDateOnly(alert.predicted_next_reorder_date)})`}
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
            : 'No demand forecast yet — it is generated nightly from how often items are purchased.'}
        </Text>
      ) : (
        <Table.ScrollContainer minWidth={800}>
          <Table highlightOnHover verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Item</Table.Th>
                <Table.Th>Method</Table.Th>
                <Table.Th ta="right">Cadence</Table.Th>
                <Table.Th>Last restock</Table.Th>
                <Table.Th>Next due</Table.Th>
                <Table.Th>Days until due</Table.Th>
                <Table.Th>Status</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((row) => {
                // No measured cadence: say why rather than print a bogus number,
                // and withhold the green all-clear — the model has no opinion.
                const noHistory = row.method === 'insufficient_history';
                return (
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
                      <Text fw={500} lineClamp={1} c={noHistory ? 'dimmed' : undefined}>
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
                    {noHistory ? (
                      <Table.Td colSpan={4}>
                        <Text size="sm" c="dimmed">
                          Not enough purchase history yet
                        </Text>
                      </Table.Td>
                    ) : (
                      <>
                        <Table.Td ta="right">{fmtCadence(row.avg_interval_days)}</Table.Td>
                        <Table.Td>{formatDateOnly(row.last_restock_date)}</Table.Td>
                        <Table.Td>
                          {formatDateOnly(row.predicted_next_reorder_date)}
                        </Table.Td>
                        <Table.Td fw={row.needs_reorder ? 600 : undefined}>
                          {fmtDue(row.days_until_due)}
                        </Table.Td>
                      </>
                    )}
                    <Table.Td>
                      {noHistory ? (
                        <Badge color="gray" variant="light">
                          Unknown
                        </Badge>
                      ) : row.needs_reorder ? (
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
                );
              })}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}

      {!loading && !error && rows.length > 0 && (
        <Text size="xs" c="dimmed" mt="xs" data-testid="demand-forecast-legend">
          Cadence is the average gap between purchases of an item. It is due when{' '}
          <b>last restock + cadence</b> falls inside the supplier lead time,
          snapshotted when the forecast ran.
        </Text>
      )}
    </Paper>
  );
};

export default DemandForecastPanel;
