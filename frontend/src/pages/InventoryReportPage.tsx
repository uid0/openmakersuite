/**
 * Inventory Report Page
 * Reports for stock levels by category, reorder frequency, and value by location
 */
import {
  Button,
  Group,
  NumberInput,
  Paper,
  Stack,
  Table,
  Tabs,
  Text,
  Title,
} from '@mantine/core';
import { IconDownload } from '@tabler/icons-react';
import React, { useEffect, useMemo, useState } from 'react';
import { reportsAPI } from '../services/api';
import {
  InventoryReorderFrequency,
  InventoryStockByCategory,
  InventoryValueByLocation,
} from '../types';
import {
  exportInventoryReportToCSV,
} from '../utils/csvExport';

type SortField = string;
type SortDirection = 'asc' | 'desc';

const InventoryReportPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState<string>('stock_by_category');
  const [loading, setLoading] = useState(false);
  const [stockByCategory, setStockByCategory] = useState<InventoryStockByCategory[]>([]);
  const [reorderFrequency, setReorderFrequency] = useState<InventoryReorderFrequency[]>([]);
  const [valueByLocation, setValueByLocation] = useState<InventoryValueByLocation[]>([]);
  const [reorderMonths, setReorderMonths] = useState<number>(12);
  const [sortField, setSortField] = useState<SortField>('');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');

  useEffect(() => {
    loadStockByCategory();
  }, []);

  useEffect(() => {
    if (activeTab === 'reorder_frequency') {
      loadReorderFrequency();
    } else if (activeTab === 'value_by_location') {
      loadValueByLocation();
    }
  }, [activeTab, reorderMonths]);

  const loadStockByCategory = async () => {
    try {
      setLoading(true);
      const response = await reportsAPI.getInventoryStockByCategory();
      setStockByCategory(response.data);
    } catch (err) {
      console.error('Error loading stock by category:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadReorderFrequency = async () => {
    try {
      setLoading(true);
      const response = await reportsAPI.getInventoryReorderFrequency({ months: reorderMonths });
      setReorderFrequency(response.data);
    } catch (err) {
      console.error('Error loading reorder frequency:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadValueByLocation = async () => {
    try {
      setLoading(true);
      const response = await reportsAPI.getInventoryValueByLocation();
      setValueByLocation(response.data);
    } catch (err) {
      console.error('Error loading value by location:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
  };

  const sortedStockByCategory = useMemo(() => {
    const sorted = [...stockByCategory];
    if (!sortField) return sorted;
    sorted.sort((a, b) => {
      let aVal: any = a[sortField as keyof InventoryStockByCategory];
      let bVal: any = b[sortField as keyof InventoryStockByCategory];
      if (typeof aVal === 'string') {
        aVal = aVal.toLowerCase();
        bVal = bVal.toLowerCase();
      }
      if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
      if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
      return 0;
    });
    return sorted;
  }, [stockByCategory, sortField, sortDirection]);

  const sortedReorderFrequency = useMemo(() => {
    const sorted = [...reorderFrequency];
    if (!sortField) return sorted;
    sorted.sort((a, b) => {
      let aVal: any = a[sortField as keyof InventoryReorderFrequency];
      let bVal: any = b[sortField as keyof InventoryReorderFrequency];
      if (typeof aVal === 'string') {
        aVal = aVal.toLowerCase();
        bVal = bVal.toLowerCase();
      }
      if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
      if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
      return 0;
    });
    return sorted;
  }, [reorderFrequency, sortField, sortDirection]);

  const sortedValueByLocation = useMemo(() => {
    const sorted = [...valueByLocation];
    if (!sortField) return sorted;
    sorted.sort((a, b) => {
      let aVal: any = a[sortField as keyof InventoryValueByLocation];
      let bVal: any = b[sortField as keyof InventoryValueByLocation];
      if (typeof aVal === 'string') {
        aVal = aVal.toLowerCase();
        bVal = bVal.toLowerCase();
      }
      if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
      if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
      return 0;
    });
    return sorted;
  }, [valueByLocation, sortField, sortDirection]);

  const handleExport = () => {
    if (activeTab === 'stock_by_category') {
      exportInventoryReportToCSV(stockByCategory, 'stock_by_category');
    } else if (activeTab === 'reorder_frequency') {
      exportInventoryReportToCSV(reorderFrequency, 'reorder_frequency');
    } else if (activeTab === 'value_by_location') {
      exportInventoryReportToCSV(valueByLocation, 'value_by_location');
    }
  };

  return (
    <Stack gap="md" p="md">
      <Group justify="space-between">
        <Title order={2}>Inventory Reports</Title>
        <Button
          leftSection={<IconDownload size={16} />}
          onClick={handleExport}
          disabled={loading}
        >
          Export CSV
        </Button>
      </Group>

      <Tabs value={activeTab} onChange={(value) => setActiveTab(value || 'stock_by_category')}>
        <Tabs.List>
          <Tabs.Tab value="stock_by_category">Stock by Category</Tabs.Tab>
          <Tabs.Tab value="reorder_frequency">Reorder Frequency</Tabs.Tab>
          <Tabs.Tab value="value_by_location">Value by Location</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="stock_by_category" pt="md">
          <Paper withBorder>
            <Table.ScrollContainer minWidth={800}>
              <Table highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('category_name')}>
                      Category
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('total_items')}>
                      Total Items
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('total_stock')}>
                      Total Stock
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('total_value')}>
                      Total Value
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('low_stock_count')}>
                      Low Stock Count
                    </Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {loading ? (
                    <Table.Tr>
                      <Table.Td colSpan={5} style={{ textAlign: 'center' }}>
                        <Text>Loading...</Text>
                      </Table.Td>
                    </Table.Tr>
                  ) : sortedStockByCategory.length === 0 ? (
                    <Table.Tr>
                      <Table.Td colSpan={5} style={{ textAlign: 'center' }}>
                        <Text>No data available</Text>
                      </Table.Td>
                    </Table.Tr>
                  ) : (
                    sortedStockByCategory.map((item, idx) => (
                      <Table.Tr key={idx}>
                        <Table.Td>{item.category_name}</Table.Td>
                        <Table.Td>{item.total_items}</Table.Td>
                        <Table.Td>{item.total_stock}</Table.Td>
                        <Table.Td>${item.total_value.toFixed(2)}</Table.Td>
                        <Table.Td>{item.low_stock_count}</Table.Td>
                      </Table.Tr>
                    ))
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="reorder_frequency" pt="md">
          <Stack gap="md">
            <Group>
              <NumberInput
                label="Time Period (months)"
                value={reorderMonths}
                onChange={(value) => setReorderMonths(Number(value) || 12)}
                min={1}
                max={60}
                style={{ width: 200 }}
              />
            </Group>
            <Paper withBorder>
              <Table.ScrollContainer minWidth={800}>
                <Table highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('item_name')}>
                        Item Name
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('item_sku')}>
                        SKU
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('category_name')}>
                        Category
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('reorder_count')}>
                        Reorder Count
                      </Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {loading ? (
                      <Table.Tr>
                        <Table.Td colSpan={4} style={{ textAlign: 'center' }}>
                          <Text>Loading...</Text>
                        </Table.Td>
                      </Table.Tr>
                    ) : sortedReorderFrequency.length === 0 ? (
                      <Table.Tr>
                        <Table.Td colSpan={4} style={{ textAlign: 'center' }}>
                          <Text>No data available</Text>
                        </Table.Td>
                      </Table.Tr>
                    ) : (
                      sortedReorderFrequency.map((item, idx) => (
                        <Table.Tr key={idx}>
                          <Table.Td>{item.item_name}</Table.Td>
                          <Table.Td>{item.item_sku || '-'}</Table.Td>
                          <Table.Td>{item.category_name}</Table.Td>
                          <Table.Td>{item.reorder_count}</Table.Td>
                        </Table.Tr>
                      ))
                    )}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            </Paper>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="value_by_location" pt="md">
          <Paper withBorder>
            <Table.ScrollContainer minWidth={800}>
              <Table highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('location_name')}>
                      Location
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('total_items')}>
                      Total Items
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('total_stock')}>
                      Total Stock
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('total_value')}>
                      Total Value
                    </Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {loading ? (
                    <Table.Tr>
                      <Table.Td colSpan={4} style={{ textAlign: 'center' }}>
                        <Text>Loading...</Text>
                      </Table.Td>
                    </Table.Tr>
                  ) : sortedValueByLocation.length === 0 ? (
                    <Table.Tr>
                      <Table.Td colSpan={4} style={{ textAlign: 'center' }}>
                        <Text>No data available</Text>
                      </Table.Td>
                    </Table.Tr>
                  ) : (
                    sortedValueByLocation.map((item, idx) => (
                      <Table.Tr key={idx}>
                        <Table.Td>{item.location_name}</Table.Td>
                        <Table.Td>{item.total_items}</Table.Td>
                        <Table.Td>{item.total_stock}</Table.Td>
                        <Table.Td>${item.total_value.toFixed(2)}</Table.Td>
                      </Table.Tr>
                    ))
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Paper>
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
};

export default InventoryReportPage;
