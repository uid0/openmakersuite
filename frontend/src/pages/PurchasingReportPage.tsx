/**
 * Purchasing Report Page
 * Reports for spend by supplier, spend by category, lead time analysis, and price trends
 */
import {
  Button,
  Group,
  Paper,
  Stack,
  Table,
  Tabs,
  Text,
  Title,
} from '@mantine/core';
import { DatePickerInput, DatesRangeValue } from '@mantine/dates';
import { IconDownload } from '@tabler/icons-react';
import dayjs from 'dayjs';
import React, { useEffect, useMemo, useState } from 'react';
import { reportsAPI } from '../services/api';
import {
  PurchasingLeadTimeAnalysis,
  PurchasingPriceTrends,
  PurchasingSpendByCategory,
  PurchasingSpendBySupplier,
} from '../types';
import { exportPurchasingReportToCSV } from '../utils/csvExport';

type SortField = string;
type SortDirection = 'asc' | 'desc';

/**
 * A price from a report, or an em dash where the server recorded none.
 *
 * `null` and `0` are different answers here (op-9m2v): the server used to send
 * `0` for a price nobody recorded, for a supplier that charges nothing, and for
 * an item with no price history at all. A `$0.00` in this column now means the
 * supplier is free, so it must not also be what an absence looks like.
 */
const money = (amount: number | null) => (amount === null ? '—' : `$${amount.toFixed(2)}`);

// Default to last 6 months
const getDefaultDateRange = (): DatesRangeValue => {
  const endDate = new Date();
  const startDate = dayjs().subtract(6, 'month').toDate();
  return [startDate, endDate];
};

const PurchasingReportPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState<string>('spend_by_supplier');
  const [loading, setLoading] = useState(false);
  const [spendBySupplier, setSpendBySupplier] = useState<PurchasingSpendBySupplier[]>([]);
  const [spendByCategory, setSpendByCategory] = useState<PurchasingSpendByCategory[]>([]);
  const [leadTimeAnalysis, setLeadTimeAnalysis] = useState<PurchasingLeadTimeAnalysis[]>([]);
  const [priceTrends, setPriceTrends] = useState<PurchasingPriceTrends[]>([]);
  const [dateRange, setDateRange] = useState<DatesRangeValue>(getDefaultDateRange);
  const [sortField, setSortField] = useState<SortField>('');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');

  useEffect(() => {
    loadSpendBySupplier();
  }, []);

  useEffect(() => {
    if (activeTab === 'spend_by_category') {
      loadSpendByCategory();
    } else if (activeTab === 'lead_time_analysis') {
      loadLeadTimeAnalysis();
    } else if (activeTab === 'price_trends') {
      loadPriceTrends();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, dateRange]);

  const loadSpendBySupplier = async () => {
    try {
      setLoading(true);
      const response = await reportsAPI.getPurchasingSpendBySupplier();
      setSpendBySupplier(response.data);
    } catch (err) {
      console.error('Error loading spend by supplier:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadSpendByCategory = async () => {
    try {
      setLoading(true);
      const response = await reportsAPI.getPurchasingSpendByCategory();
      setSpendByCategory(response.data);
    } catch (err) {
      console.error('Error loading spend by category:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadLeadTimeAnalysis = async () => {
    if (!dateRange[0] || !dateRange[1]) return;
    try {
      setLoading(true);
      const response = await reportsAPI.getPurchasingLeadTimeAnalysis({
        start_date: dayjs(dateRange[0]).format('YYYY-MM-DD'),
        end_date: dayjs(dateRange[1]).format('YYYY-MM-DD'),
      });
      setLeadTimeAnalysis(response.data);
    } catch (err) {
      console.error('Error loading lead time analysis:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadPriceTrends = async () => {
    if (!dateRange[0] || !dateRange[1]) return;
    try {
      setLoading(true);
      const response = await reportsAPI.getPurchasingPriceTrends({
        start_date: dayjs(dateRange[0]).format('YYYY-MM-DD'),
        end_date: dayjs(dateRange[1]).format('YYYY-MM-DD'),
      });
      setPriceTrends(response.data);
    } catch (err) {
      console.error('Error loading price trends:', err);
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

  /**
   * Sort rows by the active column, with the unknowns last in BOTH directions.
   *
   * The three cost columns are nullable (op-9m2v), and JavaScript coerces
   * `null` to `0` in a relational comparison — so a price nobody recorded used
   * to sort in among the cheapest, indistinguishable from a supplier that
   * genuinely charges nothing. An unknown price must not be COMPARED as a real
   * number any more than it may be shown as one. A real `0` still sorts as the
   * cheapest real price.
   */
  const sortData = <T extends Record<string, any>>(data: T[]): T[] => {
    const sorted = [...data];
    if (!sortField) return sorted;
    sorted.sort((a, b) => {
      let aVal: any = a[sortField];
      let bVal: any = b[sortField];
      const aMissing = aVal === null || aVal === undefined;
      const bMissing = bVal === null || bVal === undefined;
      if (aMissing || bMissing) {
        if (aMissing && bMissing) return 0;
        return aMissing ? 1 : -1;
      }
      if (typeof aVal === 'string') {
        aVal = aVal.toLowerCase();
        bVal = typeof bVal === 'string' ? bVal.toLowerCase() : bVal;
      }
      if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
      if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
      return 0;
    });
    return sorted;
  };

  const sortedSpendBySupplier = useMemo(() => sortData(spendBySupplier), [spendBySupplier, sortField, sortDirection]);
  const sortedSpendByCategory = useMemo(() => sortData(spendByCategory), [spendByCategory, sortField, sortDirection]);
  const sortedLeadTimeAnalysis = useMemo(() => sortData(leadTimeAnalysis), [leadTimeAnalysis, sortField, sortDirection]);
  const sortedPriceTrends = useMemo(() => sortData(priceTrends), [priceTrends, sortField, sortDirection]);

  const handleExport = () => {
    if (activeTab === 'spend_by_supplier') {
      exportPurchasingReportToCSV(spendBySupplier, 'spend_by_supplier');
    } else if (activeTab === 'spend_by_category') {
      exportPurchasingReportToCSV(spendByCategory, 'spend_by_category');
    } else if (activeTab === 'lead_time_analysis') {
      exportPurchasingReportToCSV(leadTimeAnalysis, 'lead_time_analysis');
    } else if (activeTab === 'price_trends') {
      exportPurchasingReportToCSV(priceTrends, 'price_trends');
    }
  };

  return (
    <Stack gap="md" p="md">
      <Group justify="space-between">
        <Title order={2}>Purchasing Reports</Title>
        <Button
          leftSection={<IconDownload size={16} />}
          onClick={handleExport}
          disabled={loading}
        >
          Export CSV
        </Button>
      </Group>

      <Tabs value={activeTab} onChange={(value) => setActiveTab(value || 'spend_by_supplier')}>
        <Tabs.List>
          <Tabs.Tab value="spend_by_supplier">Spend by Supplier</Tabs.Tab>
          <Tabs.Tab value="spend_by_category">Spend by Category</Tabs.Tab>
          <Tabs.Tab value="lead_time_analysis">Lead Time Analysis</Tabs.Tab>
          <Tabs.Tab value="price_trends">Price Trends</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="spend_by_supplier" pt="md">
          <Paper withBorder>
            <Table.ScrollContainer minWidth={800}>
              <Table highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('supplier_name')}>
                      Supplier
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('total_orders')}>
                      Total Orders
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('total_spend')}>
                      Total Spend
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('avg_order_value')}>
                      Avg Order Value
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
                  ) : sortedSpendBySupplier.length === 0 ? (
                    <Table.Tr>
                      <Table.Td colSpan={4} style={{ textAlign: 'center' }}>
                        <Text>No data available</Text>
                      </Table.Td>
                    </Table.Tr>
                  ) : (
                    sortedSpendBySupplier.map((item, idx) => (
                      <Table.Tr key={idx}>
                        <Table.Td>{item.supplier_name}</Table.Td>
                        <Table.Td>{item.total_orders}</Table.Td>
                        <Table.Td>${item.total_spend.toFixed(2)}</Table.Td>
                        <Table.Td>${item.avg_order_value.toFixed(2)}</Table.Td>
                      </Table.Tr>
                    ))
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="spend_by_category" pt="md">
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
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('total_quantity')}>
                      Total Quantity
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('total_spend')}>
                      Total Spend
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
                  ) : sortedSpendByCategory.length === 0 ? (
                    <Table.Tr>
                      <Table.Td colSpan={4} style={{ textAlign: 'center' }}>
                        <Text>No data available</Text>
                      </Table.Td>
                    </Table.Tr>
                  ) : (
                    sortedSpendByCategory.map((item, idx) => (
                      <Table.Tr key={idx}>
                        <Table.Td>{item.category_name}</Table.Td>
                        <Table.Td>{item.total_items}</Table.Td>
                        <Table.Td>{item.total_quantity}</Table.Td>
                        <Table.Td>${item.total_spend.toFixed(2)}</Table.Td>
                      </Table.Tr>
                    ))
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="lead_time_analysis" pt="md">
          <Stack gap="md">
            <Group>
              <DatePickerInput
                type="range"
                label="Date Range"
                placeholder="Select date range"
                value={dateRange}
                onChange={setDateRange}
                allowSingleDateInRange
                clearable
                maxDate={new Date()}
                style={{ minWidth: 280 }}
              />
            </Group>
            <Paper withBorder>
              <Table.ScrollContainer minWidth={1000}>
                <Table highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('supplier_name')}>
                        Supplier
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('item_name')}>
                        Item Name
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('total_orders')}>
                        Total Orders
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('avg_estimated_lead_time')}>
                        Avg Quoted Lead Time (days)
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('avg_actual_lead_time')}>
                        Avg Actual Lead Time (days)
                      </Table.Th>
                      {/*
                        Both of these are measured against the supplier link's
                        standing quoted lead time, never against the delivery
                        dates confirmed on the orders — a supplier can sit at a
                        low rate here having hit every date it agreed to. The
                        headers say the yardstick so nobody chases a vendor for
                        the wrong broken promise.
                      */}
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('avg_variance')}>
                        Avg Variance vs. Quoted (days)
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('on_time_rate')}>
                        Within Quoted Lead Time (%)
                      </Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {loading ? (
                      <Table.Tr>
                        <Table.Td colSpan={7} style={{ textAlign: 'center' }}>
                          <Text>Loading...</Text>
                        </Table.Td>
                      </Table.Tr>
                    ) : sortedLeadTimeAnalysis.length === 0 ? (
                      <Table.Tr>
                        <Table.Td colSpan={7} style={{ textAlign: 'center' }}>
                          <Text>No data available</Text>
                        </Table.Td>
                      </Table.Tr>
                    ) : (
                      sortedLeadTimeAnalysis.map((item, idx) => (
                        <Table.Tr key={idx}>
                          <Table.Td>{item.supplier_name}</Table.Td>
                          <Table.Td>{item.item_name}</Table.Td>
                          <Table.Td>{item.total_orders}</Table.Td>
                          <Table.Td>{item.avg_estimated_lead_time}</Table.Td>
                          <Table.Td>{item.avg_actual_lead_time}</Table.Td>
                          <Table.Td>{item.avg_variance}</Table.Td>
                          <Table.Td>{item.on_time_rate.toFixed(1)}%</Table.Td>
                        </Table.Tr>
                      ))
                    )}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            </Paper>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="price_trends" pt="md">
          <Stack gap="md">
            <Group>
              <DatePickerInput
                type="range"
                label="Date Range"
                placeholder="Select date range"
                value={dateRange}
                onChange={setDateRange}
                allowSingleDateInRange
                clearable
                maxDate={new Date()}
                style={{ minWidth: 280 }}
              />
            </Group>
            <Paper withBorder>
              <Table.ScrollContainer minWidth={1000}>
                <Table highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('item_name')}>
                        Item Name
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('supplier_name')}>
                        Supplier
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('price_changes')}>
                        Price Changes
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('min_unit_cost')}>
                        Min Unit Cost
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('max_unit_cost')}>
                        Max Unit Cost
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('latest_unit_cost')}>
                        Latest Unit Cost
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('price_change_percentage')}>
                        Price Change %
                      </Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {loading ? (
                      <Table.Tr>
                        <Table.Td colSpan={7} style={{ textAlign: 'center' }}>
                          <Text>Loading...</Text>
                        </Table.Td>
                      </Table.Tr>
                    ) : sortedPriceTrends.length === 0 ? (
                      <Table.Tr>
                        <Table.Td colSpan={7} style={{ textAlign: 'center' }}>
                          <Text>No data available</Text>
                        </Table.Td>
                      </Table.Tr>
                    ) : (
                      sortedPriceTrends.map((item, idx) => (
                        <Table.Tr key={idx}>
                          <Table.Td>{item.item_name}</Table.Td>
                          <Table.Td>{item.supplier_name}</Table.Td>
                          <Table.Td>{item.price_changes}</Table.Td>
                          <Table.Td>{money(item.min_unit_cost)}</Table.Td>
                          <Table.Td>{money(item.max_unit_cost)}</Table.Td>
                          <Table.Td>{money(item.latest_unit_cost)}</Table.Td>
                          <Table.Td>
                            {item.price_change_percentage !== null
                              ? `${item.price_change_percentage > 0 ? '+' : ''}${item.price_change_percentage.toFixed(2)}%`
                              : 'N/A'}
                          </Table.Td>
                        </Table.Tr>
                      ))
                    )}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            </Paper>
          </Stack>
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
};

export default PurchasingReportPage;
