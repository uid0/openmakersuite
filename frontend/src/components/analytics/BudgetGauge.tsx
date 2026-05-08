import { Card, Group, Stack, Text, Title } from '@mantine/core';
import React from 'react';
import {
  PolarAngleAxis,
  RadialBar,
  RadialBarChart,
  ResponsiveContainer,
} from 'recharts';

interface BudgetGaugeProps {
  budget: string | null;
  externalActualCost: string;
}

const fmt = (n: number) =>
  n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

export const BudgetGauge: React.FC<BudgetGaugeProps> = ({ budget, externalActualCost }) => {
  if (!budget) {
    return (
      <Card withBorder p="md" radius="md" data-testid="budget-gauge-disabled">
        <Title order={4}>Monthly budget</Title>
        <Text size="sm" c="dimmed" mt="sm">
          Set <code>MONTHLY_BUDGET_TOTAL</code> in backend settings to enable this gauge.
        </Text>
      </Card>
    );
  }

  const budgetNum = Number(budget);
  const spent = Number(externalActualCost);
  const pct = budgetNum > 0 ? Math.min(100, Math.round((spent / budgetNum) * 100)) : 0;
  const data = [{ name: 'spent', value: pct, fill: pct >= 90 ? 'var(--mantine-color-red-6)' : pct >= 70 ? 'var(--mantine-color-yellow-6)' : 'var(--mantine-color-green-6)' }];

  return (
    <Card withBorder p="md" radius="md" data-testid="budget-gauge">
      <Title order={4}>Monthly budget</Title>
      <Stack gap="xs" mt="sm">
        <ResponsiveContainer width="100%" height={180}>
          <RadialBarChart innerRadius="70%" outerRadius="100%" data={data} startAngle={180} endAngle={0}>
            <PolarAngleAxis type="number" domain={[0, 100]} angleAxisId={0} tick={false} />
            <RadialBar background dataKey="value" angleAxisId={0} />
          </RadialBarChart>
        </ResponsiveContainer>
        <Group justify="space-between">
          <Text size="sm" c="dimmed">Spent</Text>
          <Text fw={600}>{fmt(spent)} / {fmt(budgetNum)} ({pct}%)</Text>
        </Group>
      </Stack>
    </Card>
  );
};
