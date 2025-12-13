/**
 * PriceTrendChart - Chart showing price trends over time
 */
import { Card, Stack, Text } from '@mantine/core';
import React, { useMemo } from 'react';
import {
    CartesianGrid,
    Legend,
    Line,
    LineChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from 'recharts';

export interface PriceTrends {
  trends: Array<{
    item_id: string;
    item_name: string;
    price_history: Array<{
      recorded_at: string;
      unit_cost: number | null;
      package_cost: number | null;
      change_type: string;
      price_change_percentage: number | null;
    }>;
  }>;
  summary: {
    average_unit_cost: number | null;
    min_unit_cost: number | null;
    max_unit_cost: number | null;
    price_changes_count: number;
  };
}

export interface PriceTrendChartProps {
  priceTrends: PriceTrends;
}

const PriceTrendChart: React.FC<PriceTrendChartProps> = ({ priceTrends }) => {
  const chartData = useMemo(() => {
    if (!priceTrends.trends || priceTrends.trends.length === 0) {
      return [];
    }

    // Combine all price history into a single timeline
    const allDataPoints: Array<{
      date: string;
      [key: string]: string | number | null;
    }> = [];

    priceTrends.trends.forEach((trend) => {
      trend.price_history.forEach((ph) => {
        const date = ph.recorded_at.split('T')[0];
        const existingPoint = allDataPoints.find((p) => p.date === date);

        if (existingPoint) {
          // Add this item's price to the existing data point
          const itemKey = `item_${trend.item_id}`;
          existingPoint[itemKey] = ph.unit_cost || ph.package_cost || null;
        } else {
          // Create new data point
          const newPoint: { date: string; [key: string]: string | number | null } = {
            date,
          };
          const itemKey = `item_${trend.item_id}`;
          newPoint[itemKey] = ph.unit_cost || ph.package_cost || null;
          allDataPoints.push(newPoint);
        }
      });
    });

    // Sort by date
    return allDataPoints.sort(
      (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime()
    );
  }, [priceTrends.trends]);

  const itemNames = useMemo(() => {
    return priceTrends.trends.map((t) => ({
      id: t.item_id,
      name: t.item_name,
      key: `item_${t.item_id}`,
    }));
  }, [priceTrends.trends]);

  if (!priceTrends.trends || priceTrends.trends.length === 0) {
    return (
      <Card withBorder p="md">
        <Text c="dimmed" ta="center">
          No price trend data available
        </Text>
      </Card>
    );
  }

  // Generate colors for lines
  const colors = [
    '#8884d8',
    '#82ca9d',
    '#ffc658',
    '#ff7300',
    '#00ff00',
    '#0088fe',
    '#00c49f',
    '#ffbb28',
  ];

  return (
    <Stack gap="md">
      {/* Summary Statistics */}
      <Card withBorder p="md">
        <Stack gap="sm">
          <Text size="lg" fw={500}>
            Price Summary
          </Text>
          <Text size="sm">
            <strong>Average Unit Cost:</strong>{' '}
            {priceTrends.summary.average_unit_cost !== null
              ? `$${priceTrends.summary.average_unit_cost.toFixed(2)}`
              : 'N/A'}
          </Text>
          <Text size="sm">
            <strong>Price Range:</strong>{' '}
            {priceTrends.summary.min_unit_cost !== null &&
            priceTrends.summary.max_unit_cost !== null
              ? `$${priceTrends.summary.min_unit_cost.toFixed(2)} - $${priceTrends.summary.max_unit_cost.toFixed(2)}`
              : 'N/A'}
          </Text>
          <Text size="sm">
            <strong>Total Price Changes:</strong> {priceTrends.summary.price_changes_count}
          </Text>
        </Stack>
      </Card>

      {/* Chart */}
      <Card withBorder p="md">
        <Text size="lg" fw={500} mb="md">
          Price Trends Over Time
        </Text>
        <ResponsiveContainer width="100%" height={400}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              angle={-45}
              textAnchor="end"
              height={100}
              tickFormatter={(value) => new Date(value).toLocaleDateString()}
            />
            <YAxis
              tickFormatter={(value) => `$${value.toFixed(2)}`}
            />
            <Tooltip
              formatter={(value: any) => {
                if (value === null || value === undefined) return 'N/A';
                const numValue = typeof value === 'number' ? value : parseFloat(value);
                if (isNaN(numValue)) return 'N/A';
                return `$${numValue.toFixed(2)}`;
              }}
              labelFormatter={(label) => `Date: ${new Date(label).toLocaleDateString()}`}
            />
            <Legend />
            {itemNames.map((item, index) => (
              <Line
                key={item.key}
                type="monotone"
                dataKey={item.key}
                stroke={colors[index % colors.length]}
                name={item.name.length > 30 ? item.name.substring(0, 30) + '...' : item.name}
                strokeWidth={2}
                dot={{ r: 3 }}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </Card>
    </Stack>
  );
};

export default PriceTrendChart;
