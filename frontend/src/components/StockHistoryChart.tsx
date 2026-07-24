/**
 * StockHistoryChart — stock levels over time, driven by the stock-history
 * endpoint (op-2dqu / backend op-izy5).
 *
 * The stock line is built from BOTH weekly snapshots (`series`) and real
 * physical counts (`cycle_counts`) so it is populated before weekly snapshots
 * accumulate. On top of the line it draws:
 *   - reorder-request markers at each `reorder_events` date,
 *   - cycle-count markers at each `cycle_counts` date,
 *   - a reorder threshold line (red dashed) at `thresholds.reorder_point`,
 *   - a desired threshold line (green dashed) at `thresholds.desired`,
 * plus a small legend distinguishing each element.
 */
import { Box, Card, Group, Text } from '@mantine/core';
import React, { useMemo } from 'react';
import {
  Line,
  LineChart,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { StockHistory } from '../types';

export interface StockHistoryChartProps {
  data: StockHistory | null;
}

const STOCK_COLOR = '#228be6'; // blue — stock line
const REORDER_LINE_COLOR = '#fa5252'; // red dashed — reorder point
const DESIRED_LINE_COLOR = '#40c057'; // green dashed — desired level
const REORDER_MARKER_COLOR = '#fd7e14'; // orange — reorder requested
const CYCLE_MARKER_COLOR = '#7048e8'; // violet — cycle count

interface ChartDataPoint {
  date: string;
  stock: number | null;
}

interface Marker {
  date: string;
  y: number;
}

const formatTick = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getMonth() + 1}/${date.getDate()}`;
};

const LegendItem: React.FC<{ color: string; label: string; dashed?: boolean }> = ({
  color,
  label,
  dashed,
}) => (
  <Group gap={6} wrap="nowrap">
    <Box
      w={dashed ? 18 : 12}
      h={dashed ? 0 : 12}
      style={{
        borderRadius: dashed ? 0 : '50%',
        backgroundColor: dashed ? 'transparent' : color,
        borderTop: dashed ? `2px dashed ${color}` : undefined,
      }}
    />
    <Text size="xs" c="dimmed">
      {label}
    </Text>
  </Group>
);

export const StockHistoryChart: React.FC<StockHistoryChartProps> = ({ data }) => {
  const { chartData, hasStockData, reorderMarkers, cycleMarkers } = useMemo(() => {
    if (!data) {
      return {
        chartData: [] as ChartDataPoint[],
        hasStockData: false,
        reorderMarkers: [] as Marker[],
        cycleMarkers: [] as Marker[],
      };
    }

    // Stock line = weekly snapshots + real cycle counts. When a snapshot and a
    // physical count fall on the same day, the physical count wins.
    const stockByDate = new Map<string, number>();
    data.series.forEach((p) => stockByDate.set(p.date, p.count));
    data.cycle_counts.forEach((c) => stockByDate.set(c.date, c.count));

    // Union of every date (including reorder-only dates) so reorder markers land
    // on a valid category on the x-axis. ISO date strings sort chronologically.
    const allDates = new Set<string>();
    data.series.forEach((p) => allDates.add(p.date));
    data.cycle_counts.forEach((c) => allDates.add(c.date));
    data.reorder_events.forEach((e) => allDates.add(e.date));

    const points: ChartDataPoint[] = Array.from(allDates)
      .sort()
      .map((date) => ({
        date,
        stock: stockByDate.has(date) ? (stockByDate.get(date) as number) : null,
      }));

    const reorderPoint = data.thresholds?.reorder_point ?? 0;
    const reorder: Marker[] = data.reorder_events.map((e) => ({
      date: e.date,
      // Sit the marker on the stock line if there's a point that day, otherwise
      // pin it to the reorder threshold (reorders trigger around that level).
      y: stockByDate.has(e.date) ? (stockByDate.get(e.date) as number) : reorderPoint,
    }));
    const cycle: Marker[] = data.cycle_counts.map((c) => ({ date: c.date, y: c.count }));

    return {
      chartData: points,
      hasStockData: stockByDate.size > 0,
      reorderMarkers: reorder,
      cycleMarkers: cycle,
    };
  }, [data]);

  if (!data || !hasStockData) {
    return (
      <Card withBorder p="md">
        <Text size="lg" fw={500} mb="md">
          Stock History
        </Text>
        <Text c="dimmed" ta="center">
          No stock history data available yet
        </Text>
      </Card>
    );
  }

  const reorderPoint = data.thresholds?.reorder_point;
  const desired = data.thresholds?.desired;
  const hasReorder = typeof reorderPoint === 'number';
  const hasDesired = typeof desired === 'number';

  // Keep both threshold lines inside the plot even when they sit above the
  // highest stock value.
  const yMax = (dataMax: number) => {
    const candidates = [dataMax];
    if (hasReorder) candidates.push(reorderPoint as number);
    if (hasDesired) candidates.push(desired as number);
    return Math.ceil(Math.max(...candidates) * 1.1);
  };

  return (
    <Card withBorder p="md">
      <Text size="lg" fw={500} mb="md">
        Stock History
      </Text>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={chartData} margin={{ top: 5, right: 24, left: 0, bottom: 5 }}>
          <XAxis dataKey="date" tickFormatter={formatTick} />
          <YAxis domain={[0, yMax]} allowDecimals={false} />
          <Tooltip
            labelFormatter={(value) => {
              const date = new Date(value);
              return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString();
            }}
            formatter={(value) => [`${value} units`, 'Stock Level']}
          />
          <Line
            type="monotone"
            dataKey="stock"
            stroke={STOCK_COLOR}
            strokeWidth={2}
            dot={{ r: 3 }}
            connectNulls
            name="stock"
            isAnimationActive={false}
          />

          {hasReorder && (
            <ReferenceLine
              y={reorderPoint}
              stroke={REORDER_LINE_COLOR}
              strokeDasharray="5 5"
              label={{
                value: 'Reorder',
                position: 'insideTopRight',
                fill: REORDER_LINE_COLOR,
                fontSize: 11,
              }}
            />
          )}
          {hasDesired && (
            <ReferenceLine
              y={desired}
              stroke={DESIRED_LINE_COLOR}
              strokeDasharray="5 5"
              label={{
                value: 'Desired',
                position: 'insideBottomRight',
                fill: DESIRED_LINE_COLOR,
                fontSize: 11,
              }}
            />
          )}

          {reorderMarkers.map((m, i) => (
            <ReferenceDot
              key={`reorder-${m.date}-${i}`}
              x={m.date}
              y={m.y}
              r={5}
              fill={REORDER_MARKER_COLOR}
              stroke="#fff"
              strokeWidth={1}
            />
          ))}
          {cycleMarkers.map((m, i) => (
            <ReferenceDot
              key={`cycle-${m.date}-${i}`}
              x={m.date}
              y={m.y}
              r={5}
              fill={CYCLE_MARKER_COLOR}
              stroke="#fff"
              strokeWidth={1}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>

      <Group gap="lg" justify="center" mt="sm">
        <LegendItem color={STOCK_COLOR} label="Stock" />
        {hasReorder && <LegendItem color={REORDER_LINE_COLOR} label="Reorder point" dashed />}
        {hasDesired && <LegendItem color={DESIRED_LINE_COLOR} label="Desired" dashed />}
        {reorderMarkers.length > 0 && (
          <LegendItem color={REORDER_MARKER_COLOR} label="Reorder requested" />
        )}
        {cycleMarkers.length > 0 && <LegendItem color={CYCLE_MARKER_COLOR} label="Cycle count" />}
      </Group>
    </Card>
  );
};

export default StockHistoryChart;
