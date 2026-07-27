/**
 * CommittedBreakdown — "Committed to" attribution strip that sits directly
 * under the QA/QC metrics row on the inventory item detail page (op-u9ap).
 *
 * Renders the `committed_breakdown` entries carried in the metrics payload
 * (op-l4i0): which open work orders — and so which machines — hold the item's
 * committed quantity. Entries arrive oldest work order first and sum to QC, so
 * this is the answer to "why is available less than on hand, and who has it?".
 *
 * Purely presentational; the page fetches the metrics and passes them in.
 */
import { Anchor, Group, Paper, Stack, Text } from '@mantine/core';
import React from 'react';
import { Link } from 'react-router-dom';

import { CommittedBreakdownEntry } from '../../types';

interface CommittedBreakdownProps {
  entries: CommittedBreakdownEntry[];
  totalCommitted: number;
}

// Committed quantities are floats (a template can commit a fraction of a
// unit), so only show decimals when there are any. Mirrors InventoryMetricsRow.
const formatQuantity = (value: number): string =>
  Number.isInteger(value) ? String(value) : value.toFixed(2);

const CommittedBreakdown: React.FC<CommittedBreakdownProps> = ({ entries, totalCommitted }) => (
  <Paper withBorder p="md" radius="md" data-testid="committed-breakdown">
    <Group justify="space-between" align="baseline" mb={entries.length === 0 ? 0 : 'xs'}>
      <Text size="xs" c="dimmed" fw={700} tt="uppercase">
        Committed to
      </Text>
      <Text size="sm" c="dimmed">
        {formatQuantity(totalCommitted)} committed across {entries.length} open work order
        {entries.length === 1 ? '' : 's'}
      </Text>
    </Group>
    {entries.length === 0 ? (
      <Text size="sm" c="dimmed" mt="xs" data-testid="committed-breakdown-empty">
        Nothing is committed to an open work order.
      </Text>
    ) : (
      <Stack gap={4}>
        {entries.map((entry) => (
          <Group
            key={entry.work_order_id}
            justify="space-between"
            gap="sm"
            wrap="nowrap"
            data-testid={`committed-entry-${entry.work_order_id}`}
          >
            <Group gap="xs" wrap="nowrap">
              <Anchor
                component={Link}
                to={`/maintenance/work-orders/${entry.work_order_id}`}
                size="sm"
              >
                {entry.work_order_short_id}
              </Anchor>
              {/* A work order need not target an asset (op-svut), so the
                  machine column degrades instead of disappearing. */}
              <Text size="sm" c={entry.asset_name ? undefined : 'dimmed'}>
                {entry.asset_name || 'No asset'}
              </Text>
            </Group>
            <Text size="sm" fw={600}>
              {formatQuantity(entry.quantity)}
            </Text>
          </Group>
        ))}
      </Stack>
    )}
  </Paper>
);

export default CommittedBreakdown;
