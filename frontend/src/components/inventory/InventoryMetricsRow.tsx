/**
 * InventoryMetricsRow — compact, prominent stock + cost strip for the item
 * detail page (issue-5): SKU · QOH · QOO · QA · QC · QIT · RP · Lead · Cost.
 *
 * Fed by GET /api/inventory/items/<id>/metrics/. Ian flagged that this
 * information was hard to get from a single screen, so the row sits at the top
 * of the detail view. Purely presentational — the page fetches the metrics and
 * passes them in (the paired ScanTTY TUI renders the same contract).
 */
import { Group, Paper, Stack, Text } from '@mantine/core';
import React from 'react';

import { InventoryCostTrend, InventoryItemMetrics } from '../../types';

interface InventoryMetricsRowProps {
  sku: string;
  metrics: InventoryItemMetrics;
}

const TREND_ARROW: Record<InventoryCostTrend, string> = {
  up: '↑',
  down: '↓',
  flat: '→',
  no_history: '',
};

// Cost going up is bad (red), down is good (green); flat/unknown stay neutral.
const TREND_COLOR: Record<InventoryCostTrend, string | undefined> = {
  up: 'red',
  down: 'green',
  flat: undefined,
  no_history: undefined,
};

const TREND_LABEL: Record<InventoryCostTrend, string> = {
  up: 'Cost up vs the last purchase order',
  down: 'Cost down vs the last purchase order',
  flat: 'Cost unchanged vs the last purchase order',
  no_history: 'No purchase-order history yet',
};

const formatMoney = (value: string | null): string =>
  value == null ? '—' : `$${parseFloat(value).toFixed(2)}`;

const formatQuantity = (value: number): string =>
  Number.isInteger(value) ? String(value) : value.toFixed(2);

interface MetricCellProps {
  label: string;
  value: React.ReactNode;
  tooltip: string;
  testId: string;
}

const MetricCell: React.FC<MetricCellProps> = ({ label, value, tooltip, testId }) => (
  <Stack gap={2} align="center" miw={52} title={tooltip} data-testid={testId}>
    <Text size="xs" c="dimmed" fw={700} tt="uppercase">
      {label}
    </Text>
    <Text size="lg" fw={700}>
      {value}
    </Text>
  </Stack>
);

const InventoryMetricsRow: React.FC<InventoryMetricsRowProps> = ({ sku, metrics }) => {
  const costLabel = metrics.is_case_based ? 'Cost/case' : 'Cost';
  const costTooltipBase = metrics.is_case_based
    ? `Cost per case${metrics.case_size ? ` of ${metrics.case_size}` : ''}`
    : 'Cost per unit';
  const trendArrow = TREND_ARROW[metrics.cost_trend];
  const trendColor = TREND_COLOR[metrics.cost_trend];

  const costValue = (
    <>
      {formatMoney(metrics.unit_cost)}
      {trendArrow && (
        <Text span c={trendColor} data-testid={`cost-trend-${metrics.cost_trend}`}>
          {' '}
          {trendArrow}
        </Text>
      )}
    </>
  );

  // What the scoring shrugged off to pick this supplier. The rule does not
  // punish a missing price or an empty delivery record (op-2rsp), so a supplier
  // can win carrying one — and a blank Cost cell would otherwise read as "no
  // supplier" rather than "the supplier we picked has no price on file".
  const gaps = [
    metrics.supplier_scored_without_price && 'no price on file',
    metrics.supplier_scored_without_history && 'no delivery history',
  ].filter(Boolean) as string[];

  return (
    <Paper withBorder p="md" radius="md" data-testid="inventory-metrics-row">
      <Group justify="space-between" wrap="wrap" gap="md">
        <MetricCell testId="metric-sku" label="SKU" value={sku} tooltip="Stock keeping unit" />
        <MetricCell
          testId="metric-qoh"
          label="QOH"
          value={formatQuantity(metrics.current_stock)}
          tooltip="Quantity on hand"
        />
        <MetricCell
          testId="metric-qoo"
          label="QOO"
          value={formatQuantity(metrics.quantity_on_order)}
          tooltip="Quantity on order (open purchase orders)"
        />
        <MetricCell
          testId="metric-qa"
          label="QA"
          value={formatQuantity(metrics.quantity_available)}
          tooltip="Quantity available (on hand − committed)"
        />
        <MetricCell
          testId="metric-qc"
          label="QC"
          value={formatQuantity(metrics.quantity_committed)}
          tooltip="Quantity committed to open work orders"
        />
        <MetricCell
          testId="metric-qit"
          label="QIT"
          value={formatQuantity(metrics.quantity_in_transit)}
          tooltip="Quantity in transit (partially received)"
        />
        <MetricCell
          testId="metric-rp"
          label="RP"
          value={formatQuantity(metrics.reorder_point)}
          tooltip="Reorder point"
        />
        <MetricCell
          testId="metric-lead"
          label="Lead"
          value={metrics.lead_time_days == null ? '—' : `${metrics.lead_time_days}d`}
          tooltip="Lead time in days"
        />
        <MetricCell
          testId="metric-cost"
          label={costLabel}
          value={costValue}
          tooltip={`${costTooltipBase} — ${TREND_LABEL[metrics.cost_trend]}`}
        />
      </Group>
      {gaps.length > 0 && (
        <Text size="xs" c="dimmed" mt="xs" data-testid="metric-supplier-gaps">
          Chosen supplier has {gaps.join(' and ')} — it was picked on what we do know.
        </Text>
      )}
    </Paper>
  );
};

export default InventoryMetricsRow;
