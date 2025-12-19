/**
 * Pending Reorders Widget
 * Displays pending reorder requests awaiting approval
 */
import { Badge, ScrollArea, Table, Text } from '@mantine/core';
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { dashboardAPI } from '../../services/api';
import { PendingReordersData } from '../../types';
import DashboardWidget from './DashboardWidget';

const PendingReordersWidget: React.FC = () => {
  const [data, setData] = useState<PendingReordersData | null>(null);
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
      const response = await dashboardAPI.getPendingReordersData();
      setData(response.data);
    } catch (err: any) {
      setError(err.response?.data?.error || 'Failed to load pending reorders');
      console.error('Error loading pending reorders:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleRequestClick = (itemId: string) => {
    navigate(`/inventory/items/${itemId}`);
  };

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case 'urgent':
        return 'red';
      case 'high':
        return 'orange';
      case 'normal':
        return 'blue';
      case 'low':
        return 'gray';
      default:
        return 'blue';
    }
  };

  return (
    <DashboardWidget title="Pending Reorders" loading={loading} error={error || undefined}>
      {data && (
        <>
          <div style={{ marginBottom: 12 }}>
            <Badge size="lg" color="orange" variant="light">
              {data.count} {data.count === 1 ? 'request' : 'requests'}
            </Badge>
          </div>
          {data.requests.length > 0 ? (
            <ScrollArea h={200}>
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Item</Table.Th>
                    <Table.Th>Qty</Table.Th>
                    <Table.Th>Priority</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {data.requests.map((request) => (
                    <Table.Tr
                      key={request.id}
                      style={{ cursor: 'pointer' }}
                      onClick={() => handleRequestClick(request.item_id)}
                    >
                      <Table.Td>
                        <Text size="sm" fw={500}>
                          {request.item_name}
                        </Text>
                        <Text size="xs" c="dimmed">
                          {request.requested_by}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm">{request.quantity}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Badge color={getPriorityColor(request.priority)} variant="light" size="sm">
                          {request.priority}
                        </Badge>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          ) : (
            <Text c="dimmed" size="sm" ta="center" py="xl">
              No pending reorders
            </Text>
          )}
        </>
      )}
    </DashboardWidget>
  );
};

export default PendingReordersWidget;
