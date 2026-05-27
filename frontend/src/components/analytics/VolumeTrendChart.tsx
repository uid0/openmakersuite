import { Card, Text, Title } from '@mantine/core';
import React from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { AnalyticsTrendPoint } from '../../services/api';

interface VolumeTrendChartProps {
  data: AnalyticsTrendPoint[];
}

const formatPeriodLabel = (iso: string) => {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: 'short', year: '2-digit' });
};

export const VolumeTrendChart: React.FC<VolumeTrendChartProps> = ({ data }) => (
  <Card withBorder p="md" radius="md" data-testid="volume-trend-chart">
    <Title order={4}>Work order volume — last 12 months</Title>
    {data.length === 0 ? (
      <Text size="sm" c="dimmed" mt="sm">No completed work orders in the trend window.</Text>
    ) : (
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data} margin={{ top: 16, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="period" tickFormatter={formatPeriodLabel} />
          <YAxis allowDecimals={false} />
          <Tooltip labelFormatter={(label) => formatPeriodLabel(String(label))} />
          <Line type="monotone" dataKey="count" stroke="var(--mantine-color-blue-6)" strokeWidth={2} dot={{ r: 3 }} />
        </LineChart>
      </ResponsiveContainer>
    )}
  </Card>
);
