/**
 * LeadTimeChart - Chart showing lead time analytics.
 *
 * Every number on this surface is measured against the supplier link's STANDING
 * QUOTED lead time, never against the delivery dates the operator confirmed on
 * the orders (see `LeadTimeLog` in `backend/reorder_queue/models.py`). Those are
 * two different promises, and a vendor that quotes 3 days, has the order
 * confirmed for day 10 and delivers on day 10 is over its quote by 7 while
 * having hit the date it agreed. So no label here says a bare "on-time" or
 * "variance": each names the quote, and the per-row tooltip carries the
 * confirmed-date verdict beside it.
 */
import { Card, Group, Stack, Text } from '@mantine/core';
import React, { useMemo } from 'react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

/** The words this surface uses for the promise every number here scores. */
export const YARDSTICK_LABEL = 'quoted lead time';

export interface LeadTimeAnalytics {
  average_lead_time: number | null;
  min_lead_time: number | null;
  max_lead_time: number | null;
  /**
   * Average of `variance_days`, measured against the supplier link's STANDING
   * QUOTED lead time — never against the delivery dates confirmed on the
   * orders. `variance_measured_against` carries that yardstick from the API so
   * no label here has to restate it from memory.
   */
  average_variance: number | null;
  total_orders: number;
  on_time_percentage: number | null;
  variance_measured_against?: string;
  recent_logs?: Array<{
    item_name: string;
    order_date: string;
    expected_delivery_date: string;
    actual_delivery_date: string;
    estimated_lead_time_days: number;
    actual_lead_time_days: number;
    variance_days: number;
    /** Later than the QUOTE. A row can be `true` having hit the agreed date. */
    was_late: boolean;
    /** Met the date the operator confirmed; `null` when none was confirmed. */
    met_confirmed_date?: boolean | null;
  }>;
}

export interface LeadTimeChartProps {
  analytics: LeadTimeAnalytics;
}

/**
 * The row's OTHER promise, in words, or `null` when there is nothing to say.
 *
 * `met_confirmed_date` is tri-state on purpose: `null` means the order carried
 * no confirmed delivery date, so there is no agreed date to have met or missed
 * and this returns `null` rather than guessing. Kept separate from the variance
 * phrasing because a row can be over its quote and still have met this date —
 * that pair is exactly what the operator needs to see together.
 */
export function confirmedDatePhrase(metConfirmedDate: boolean | null | undefined): string | null {
  if (metConfirmedDate === null || metConfirmedDate === undefined) {
    return null;
  }
  return metConfirmedDate ? 'Met the confirmed delivery date' : 'Missed the confirmed delivery date';
}

interface LeadTimeTooltipRow {
  name: string;
  estimated: number;
  actual: number;
  variance: number;
  date: string;
  metConfirmedDate: boolean | null | undefined;
}

/**
 * Per-row tooltip. Exported so it can be asserted directly: the chart renders
 * inside a `ResponsiveContainer`, which has no size under jsdom, so nothing
 * drawn inside the SVG is reachable from a mounted-component test.
 */
export const LeadTimeTooltip: React.FC<{
  active?: boolean;
  payload?: Array<{ payload: LeadTimeTooltipRow }>;
}> = ({ active, payload }) => {
  if (!active || !payload || payload.length === 0) {
    return null;
  }
  const row = payload[0].payload;
  const confirmed = confirmedDatePhrase(row.metConfirmedDate);
  return (
    <Card withBorder p="xs">
      <Text size="sm" fw={600}>
        {row.name}
      </Text>
      <Text size="xs">Quoted lead time: {row.estimated} days</Text>
      <Text size="xs">Actual lead time: {row.actual} days</Text>
      <Text size="xs" c={row.variance > 0 ? 'red' : 'green'}>
        {row.variance > 0 ? '+' : ''}
        {row.variance} days vs. {YARDSTICK_LABEL}
      </Text>
      {confirmed !== null && (
        <Text size="xs" c={row.metConfirmedDate ? 'green' : 'red'}>
          {confirmed}
        </Text>
      )}
    </Card>
  );
};

const LeadTimeChart: React.FC<LeadTimeChartProps> = ({ analytics }) => {
  const chartData = useMemo(() => {
    if (!analytics.recent_logs || analytics.recent_logs.length === 0) {
      return [];
    }

    return analytics.recent_logs.map((log) => ({
      name: log.item_name.length > 20 ? log.item_name.substring(0, 20) + '...' : log.item_name,
      estimated: log.estimated_lead_time_days,
      actual: log.actual_lead_time_days,
      variance: log.variance_days,
      date: new Date(log.order_date).toLocaleDateString(),
      metConfirmedDate: log.met_confirmed_date,
    }));
  }, [analytics.recent_logs]);

  if (!analytics.recent_logs || analytics.recent_logs.length === 0) {
    return (
      <Card withBorder p="md">
        <Text c="dimmed" ta="center">
          No lead time data available
        </Text>
      </Card>
    );
  }

  return (
    <Stack gap="md">
      {/* Statistics Cards */}
      <Group grow>
        <Card withBorder p="md">
          <Text size="sm" c="dimmed">
            Average Lead Time
          </Text>
          <Text size="xl" fw={700}>
            {analytics.average_lead_time !== null
              ? `${analytics.average_lead_time.toFixed(1)} days`
              : 'N/A'}
          </Text>
        </Card>
        <Card withBorder p="md">
          <Text size="sm" c="dimmed">
            Within Quoted Lead Time
          </Text>
          <Text size="xl" fw={700}>
            {analytics.on_time_percentage !== null
              ? `${analytics.on_time_percentage.toFixed(1)}%`
              : 'N/A'}
          </Text>
        </Card>
        <Card withBorder p="md">
          <Text size="sm" c="dimmed">
            Min / Max Lead Time
          </Text>
          <Text size="xl" fw={700}>
            {analytics.min_lead_time !== null && analytics.max_lead_time !== null
              ? `${analytics.min_lead_time} / ${analytics.max_lead_time} days`
              : 'N/A'}
          </Text>
        </Card>
        <Card withBorder p="md">
          <Text size="sm" c="dimmed">
            Avg Variance vs. Quoted Lead Time
          </Text>
          <Text
            size="xl"
            fw={700}
            c={analytics.average_variance !== null && analytics.average_variance > 0 ? 'red' : 'green'}
          >
            {analytics.average_variance !== null
              ? `${analytics.average_variance > 0 ? '+' : ''}${analytics.average_variance.toFixed(1)} days`
              : 'N/A'}
          </Text>
        </Card>
      </Group>

      <Text size="xs" c="dimmed">
        Measured against each supplier link&apos;s standing {YARDSTICK_LABEL}, not against
        the delivery dates confirmed on the orders. An order can run past the quote and still
        have arrived on the date that was agreed — hover a bar to see both.
      </Text>

      {/* Chart */}
      <Card withBorder p="md">
        <Text size="lg" fw={500} mb="md">
          Quoted vs. Actual Lead Time (Recent Orders)
        </Text>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="name"
              angle={-45}
              textAnchor="end"
              height={100}
              interval={0}
            />
            <YAxis />
            <Tooltip content={<LeadTimeTooltip />} />
            <Bar dataKey="estimated" fill="#8884d8" name="Quoted" />
            <Bar dataKey="actual" fill="#82ca9d" name="Actual" />
          </BarChart>
        </ResponsiveContainer>
      </Card>
    </Stack>
  );
};

export default LeadTimeChart;
