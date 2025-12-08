/**
 * StockHistoryChart - Line chart showing stock levels over time
 */
import { Card, Text } from '@mantine/core';
import React, { useMemo } from 'react';
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { UsageLog } from '../types';

export interface StockHistoryChartProps {
  usageLogs: UsageLog[];
  currentStock: number;
  initialStock?: number;
  minimumStock?: number;
}

interface ChartDataPoint {
  date: string;
  stock: number;
  usage?: number;
}

export const StockHistoryChart: React.FC<StockHistoryChartProps> = ({
  usageLogs,
  currentStock,
  initialStock,
  minimumStock,
}) => {
  const chartData = useMemo(() => {
    // Sort logs by date
    const sortedLogs = [...usageLogs].sort(
      (a, b) => new Date(a.usage_date).getTime() - new Date(b.usage_date).getTime()
    );

    // Calculate stock levels over time
    // Start from current stock and work backwards, or use initial stock if provided
    let runningStock = initialStock !== undefined ? initialStock : currentStock;
    const dataPoints: ChartDataPoint[] = [];

    // Add current point
    dataPoints.push({
      date: new Date().toISOString().split('T')[0],
      stock: currentStock,
    });

    // Work backwards through usage logs
    for (let i = sortedLogs.length - 1; i >= 0; i--) {
      const log = sortedLogs[i];
      runningStock += log.quantity_used; // Add back the used quantity
      dataPoints.unshift({
        date: log.usage_date.split('T')[0],
        stock: runningStock,
        usage: log.quantity_used,
      });
    }

    // If we have an initial stock, add it as the first point
    if (initialStock !== undefined && dataPoints.length > 0) {
      const firstDate = dataPoints[0].date;
      const earlierDate = new Date(firstDate);
      earlierDate.setDate(earlierDate.getDate() - 1);
      dataPoints.unshift({
        date: earlierDate.toISOString().split('T')[0],
        stock: initialStock,
      });
    }

    // Reverse to show chronological order
    return dataPoints.reverse();
  }, [usageLogs, currentStock, initialStock]);

  if (chartData.length === 0) {
    return (
      <Card withBorder p="md">
        <Text c="dimmed" ta="center">
          No stock history data available
        </Text>
      </Card>
    );
  }

  return (
    <Card withBorder p="md">
      <Text size="lg" fw={500} mb="md">
        Stock History
      </Text>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
          <XAxis
            dataKey="date"
            tickFormatter={(value) => {
              const date = new Date(value);
              return `${date.getMonth() + 1}/${date.getDate()}`;
            }}
          />
          <YAxis
            domain={[
              minimumStock !== undefined ? Math.min(0, minimumStock - 10) : 'auto',
              'auto',
            ]}
          />
          <Tooltip
            labelFormatter={(value) => {
              const date = new Date(value);
              return date.toLocaleDateString();
            }}
            formatter={(value: any, name: string) => {
              if (name === 'stock') {
                return [`${value} units`, 'Stock Level'];
              }
              return value;
            }}
          />
          <Line
            type="monotone"
            dataKey="stock"
            stroke="#228be6"
            strokeWidth={2}
            dot={{ r: 4 }}
            name="stock"
          />
          {minimumStock !== undefined && (
            <Line
              type="monotone"
              dataKey={() => minimumStock}
              stroke="#fa5252"
              strokeWidth={1}
              strokeDasharray="5 5"
              dot={false}
              name="minimum"
            />
          )}
        </LineChart>
      </ResponsiveContainer>
      {minimumStock !== undefined && (
        <Text size="xs" c="dimmed" mt="xs" ta="center">
          Red dashed line indicates minimum stock level ({minimumStock} units)
        </Text>
      )}
    </Card>
  );
};

export default StockHistoryChart;
