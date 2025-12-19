/**
 * Low Stock Widget
 * Displays items that are below minimum stock levels
 */
import { Badge, ScrollArea, Table, Text } from '@mantine/core';
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { dashboardAPI } from '../../services/api';
import { LowStockData } from '../../types';
import DashboardWidget from './DashboardWidget';

const LowStockWidget: React.FC = () => {
  const [data, setData] = useState<LowStockData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    loadData();
    // Refresh every 30 seconds
    const interval = setInterval(loadData, 30000);
    return () => clearInterval(interval);
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await dashboardAPI.getLowStockData();
      setData(response.data);
    } catch (err: any) {
      setError(err.response?.data?.error || 'Failed to load low stock data');
      console.error('Error loading low stock data:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleItemClick = (itemId: string) => {
    navigate(`/inventory/items/${itemId}`);
  };

  return (
    <DashboardWidget title="Low Stock" loading={loading} error={error || undefined}>
      {data && (
        <>
          <div style={{ marginBottom: 12 }}>
            <Badge size="lg" color="red" variant="light">
              {data.count} {data.count === 1 ? 'item' : 'items'}
            </Badge>
          </div>
          {data.items.length > 0 ? (
            <ScrollArea h={200}>
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Item</Table.Th>
                    <Table.Th>Stock</Table.Th>
                    <Table.Th>Min</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {data.items.map((item) => (
                    <Table.Tr
                      key={item.id}
                      style={{ cursor: 'pointer' }}
                      onClick={() => handleItemClick(item.id)}
                    >
                      <Table.Td>
                        <Text size="sm" fw={500}>
                          {item.name}
                        </Text>
                        {item.category__name && (
                          <Text size="xs" c="dimmed">
                            {item.category__name}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Badge color="red" variant="light">
                          {item.current_stock}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm">{item.minimum_stock}</Text>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          ) : (
            <Text c="dimmed" size="sm" ta="center" py="xl">
              No low stock items
            </Text>
          )}
        </>
      )}
    </DashboardWidget>
  );
};

export default LowStockWidget;
