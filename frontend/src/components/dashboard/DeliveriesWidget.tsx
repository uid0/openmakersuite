/**
 * Deliveries Widget
 * Displays recent deliveries from the last 30 days
 */
import { Badge, ScrollArea, Table, Text } from '@mantine/core';
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { dashboardAPI } from '../../services/api';
import { DeliveriesData } from '../../types';
import DashboardWidget from './DashboardWidget';

const DeliveriesWidget: React.FC = () => {
  const [data, setData] = useState<DeliveriesData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 30000);
    return () => clearInterval(interval);
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await dashboardAPI.getDeliveriesData();
      setData(response.data);
    } catch (err: any) {
      setError(err.response?.data?.error || 'Failed to load deliveries data');
      console.error('Error loading deliveries data:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleDeliveryClick = (poId: number) => {
    navigate(`/reorders/purchase-orders/${poId}`);
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
    <DashboardWidget title="Recent Deliveries" loading={loading} error={error || undefined}>
      {data && (
        <>
          <div style={{ marginBottom: 12 }}>
            <Badge size="lg" color="green" variant="light">
              {data.count} {data.count === 1 ? 'delivery' : 'deliveries'}
            </Badge>
          </div>
          {data.deliveries.length > 0 ? (
            <ScrollArea h={200}>
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Date</Table.Th>
                    <Table.Th>Supplier</Table.Th>
                    <Table.Th>Items</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {data.deliveries.map((delivery) => (
                    <Table.Tr
                      key={delivery.id}
                      style={{ cursor: 'pointer' }}
                      onClick={() => handleDeliveryClick(delivery.purchase_order_id)}
                    >
                      <Table.Td>
                        <Text size="sm">{formatDate(delivery.delivery_date)}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm" fw={500}>
                          {delivery.supplier_name || 'Unknown'}
                        </Text>
                        {delivery.received_by && (
                          <Text size="xs" c="dimmed">
                            by {delivery.received_by}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm">
                          {delivery.items_count} {delivery.items_count === 1 ? 'item' : 'items'}
                        </Text>
                        <Text size="xs" c="dimmed">
                          {delivery.total_quantity} units
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          ) : (
            <Text c="dimmed" size="sm" ta="center" py="xl">
              No deliveries in the last 30 days
            </Text>
          )}
        </>
      )}
    </DashboardWidget>
  );
};

export default DeliveriesWidget;
