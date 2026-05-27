import { Card, Text, Title } from '@mantine/core';
import React from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { AnalyticsCategorySpendRow } from '../../services/api';

interface CategorySpendChartProps {
  data: AnalyticsCategorySpendRow[];
}

const fmtMoney = (n: number) =>
  n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

export const CategorySpendChart: React.FC<CategorySpendChartProps> = ({ data }) => {
  const chartData = data.map((row) => ({
    category: row.category_name ?? 'Uncategorized',
    'Internal estimated': Number(row.internal_estimated),
    'External actual': Number(row.external_actual),
  }));

  return (
    <Card withBorder p="md" radius="md" data-testid="category-spend-chart">
      <Title order={4}>Spend by category</Title>
      {chartData.length === 0 ? (
        <Text size="sm" c="dimmed" mt="sm">No work orders attributed to a category in this window.</Text>
      ) : (
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={chartData} margin={{ top: 16, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="category" />
            <YAxis tickFormatter={fmtMoney} />
            <Tooltip formatter={(value) => fmtMoney(Number(value))} />
            <Legend />
            <Bar dataKey="Internal estimated" fill="var(--mantine-color-blue-5)" />
            <Bar dataKey="External actual" fill="var(--mantine-color-orange-6)" />
          </BarChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
};
