/**
 * QR Scans Widget
 * Displays recent QR code scan activity
 */
import { Badge, ScrollArea, Text } from '@mantine/core';
import React, { useEffect, useState } from 'react';
import { dashboardAPI } from '../../services/api';
import { QRScansData } from '../../types';
import { extractErrorMessage } from '../../utils/extractErrorMessage';
import DashboardWidget from './DashboardWidget';

const QRScansWidget: React.FC = () => {
  const [data, setData] = useState<QRScansData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 30000);
    return () => clearInterval(interval);
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await dashboardAPI.getQRScansData();
      setData(response.data);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to load QR scan data'));
      console.error('Error loading QR scan data:', err);
    } finally {
      setLoading(false);
    }
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);

    if (date.toDateString() === today.toDateString()) {
      return 'Today';
    } else if (date.toDateString() === yesterday.toDateString()) {
      return 'Yesterday';
    } else {
      return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    }
  };

  return (
    <DashboardWidget title="QR Scans (30 days)" loading={loading} error={error || undefined}>
      {data && (
        <>
          <div style={{ marginBottom: 12, display: 'flex', gap: 8 }}>
            <Badge size="lg" color="blue" variant="light">
              {data.total_scans} total
            </Badge>
            <Badge size="sm" color="cyan" variant="light">
              {data.asset_scans} assets
            </Badge>
            <Badge size="sm" color="indigo" variant="light">
              {data.item_scans} items
            </Badge>
          </div>
          {data.daily_scans.length > 0 ? (
            <ScrollArea h={200}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {data.daily_scans.slice(-10).reverse().map((day, index) => (
                  <div
                    key={day.date}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '8px 12px',
                      backgroundColor: index % 2 === 0 ? 'var(--mantine-color-gray-0)' : 'transparent',
                      borderRadius: 4,
                    }}
                  >
                    <Text size="sm" fw={500}>
                      {formatDate(day.date)}
                    </Text>
                    <div style={{ display: 'flex', gap: 8 }}>
                      {day.assets > 0 && (
                        <Badge size="sm" color="cyan" variant="light">
                          {day.assets} assets
                        </Badge>
                      )}
                      {day.items > 0 && (
                        <Badge size="sm" color="indigo" variant="light">
                          {day.items} items
                        </Badge>
                      )}
                      <Text size="sm" c="dimmed">
                        {day.total} total
                      </Text>
                    </div>
                  </div>
                ))}
              </div>
            </ScrollArea>
          ) : (
            <Text c="dimmed" size="sm" ta="center" py="xl">
              No scans in the last 30 days
            </Text>
          )}
        </>
      )}
    </DashboardWidget>
  );
};

export default QRScansWidget;
