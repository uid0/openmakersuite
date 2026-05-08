/**
 * LocationTrafficPanel - Hourly/daily IoT traffic counts for a location.
 */
import { Group, SegmentedControl, Stack, Text } from '@mantine/core';
import React, { useEffect, useMemo, useState } from 'react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { locationCheckinAPI } from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

export interface LocationTrafficPanelProps {
  locationId: string | number;
}

type Bucket = 'hour' | 'day' | 'week' | 'month';

const BUCKET_LOOKBACK_DAYS: Record<Bucket, number> = {
  hour: 2,
  day: 30,
  week: 90,
  month: 365,
};

const formatBucketLabel = (iso: string, bucket: Bucket): string => {
  const d = new Date(iso);
  if (bucket === 'hour') {
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric' });
  }
  if (bucket === 'month') {
    return d.toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
  }
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
};

const LocationTrafficPanel: React.FC<LocationTrafficPanelProps> = ({ locationId }) => {
  const [bucket, setBucket] = useState<Bucket>('day');
  const [data, setData] = useState<{ bucket_start: string; count: number }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const end = new Date();
        const start = new Date(end.getTime() - BUCKET_LOOKBACK_DAYS[bucket] * 24 * 60 * 60 * 1000);
        const response = await locationCheckinAPI.getTrafficReport({
          location: String(locationId),
          start: start.toISOString(),
          end: end.toISOString(),
          bucket,
        });
        if (!cancelled) {
          setData(response.data.results);
        }
      } catch (err: any) {
        if (!cancelled) {
          setError(extractErrorMessage(err, 'Failed to load traffic data'));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [locationId, bucket]);

  const chartData = useMemo(
    () =>
      data.map((row) => ({
        label: formatBucketLabel(row.bucket_start, bucket),
        count: row.count,
      })),
    [data, bucket]
  );

  const total = useMemo(() => data.reduce((sum, row) => sum + row.count, 0), [data]);

  return (
    <Stack gap="sm">
      <Group justify="space-between" align="flex-end">
        <div>
          <Text size="sm" c="dimmed">
            Traffic over the last {BUCKET_LOOKBACK_DAYS[bucket]} days
          </Text>
          <Text size="xl" fw={700}>
            {total.toLocaleString()} check-ins
          </Text>
        </div>
        <SegmentedControl
          value={bucket}
          onChange={(value) => setBucket(value as Bucket)}
          data={[
            { label: 'Hour', value: 'hour' },
            { label: 'Day', value: 'day' },
            { label: 'Week', value: 'week' },
            { label: 'Month', value: 'month' },
          ]}
        />
      </Group>

      {loading && <Text c="dimmed">Loading traffic data…</Text>}
      {error && <Text c="red">{error}</Text>}
      {!loading && !error && chartData.length === 0 && (
        <Text c="dimmed">
          No check-ins recorded in this window. IoT pings to{' '}
          <code>/api/location-checkins/webhook/</code> will appear here.
        </Text>
      )}
      {!loading && !error && chartData.length > 0 && (
        <div style={{ width: '100%', height: 240 }}>
          <ResponsiveContainer>
            <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 24 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="label" angle={-30} textAnchor="end" height={50} interval="preserveStartEnd" />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="count" fill="#228be6" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Stack>
  );
};

export default LocationTrafficPanel;
