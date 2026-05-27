/**
 * LeadTimeChart - Chart showing lead time analytics
 */
import { Card, Group, Stack, Text } from '@mantine/core';
import React, { useMemo } from 'react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

export interface LeadTimeAnalytics {
  average_lead_time: number | null;
  min_lead_time: number | null;
  max_lead_time: number | null;
  average_variance: number | null;
  total_orders: number;
  on_time_percentage: number | null;
  recent_logs?: Array<{
    item_name: string;
    order_date: string;
    expected_delivery_date: string;
    actual_delivery_date: string;
    estimated_lead_time_days: number;
    actual_lead_time_days: number;
    variance_days: number;
    was_late: boolean;
  }>;
}

export interface LeadTimeChartProps {
  analytics: LeadTimeAnalytics;
}

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
            On-Time Percentage
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
            Average Variance
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

      {/* Chart */}
      <Card withBorder p="md">
        <Text size="lg" fw={500} mb="md">
          Lead Time Comparison (Recent Orders)
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
            <Tooltip
              formatter={(value, name) => {
                const v = Number(value);
                const n = String(name);
                if (n === 'estimated') return [`${v} days`, 'Estimated'];
                if (n === 'actual') return [`${v} days`, 'Actual'];
                if (n === 'variance') return [`${v > 0 ? '+' : ''}${v} days`, 'Variance'];
                return [value as string, n];
              }}
              labelFormatter={(label) => `Item: ${label}`}
            />
            <Bar dataKey="estimated" fill="#8884d8" name="Estimated" />
            <Bar dataKey="actual" fill="#82ca9d" name="Actual" />
          </BarChart>
        </ResponsiveContainer>
      </Card>
    </Stack>
  );
};

export default LeadTimeChart;
