/**
 * Asset Report Page
 * Reports for assets by status, maintenance due, and utilization
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
  AssetAssetsByStatus,
  AssetMaintenanceDue,
  AssetTco,
  AssetUtilization,
} from '../types';
import { exportAssetReportToCSV } from '../utils/csvExport';

type SortField = string;
type SortDirection = 'asc' | 'desc';

// Default to last 30 days
const getDefaultDateRange = (): DatesRangeValue => {
  const endDate = new Date();
  const startDate = dayjs().subtract(30, 'day').toDate();
  return [startDate, endDate];
};

const AssetReportPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState<string>('assets_by_status');
  const [loading, setLoading] = useState(false);
  const [assetsByStatus, setAssetsByStatus] = useState<AssetAssetsByStatus[]>([]);
  const [maintenanceDue, setMaintenanceDue] = useState<AssetMaintenanceDue[]>([]);
  const [utilization, setUtilization] = useState<AssetUtilization[]>([]);
  const [tcoRows, setTcoRows] = useState<AssetTco[]>([]);
  const [dateRange, setDateRange] = useState<DatesRangeValue>(getDefaultDateRange);
  const [sortField, setSortField] = useState<SortField>('');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');

  useEffect(() => {
    loadAssetsByStatus();
  }, []);

  useEffect(() => {
    if (activeTab === 'maintenance_due') {
      loadMaintenanceDue();
    } else if (activeTab === 'utilization') {
      loadUtilization();
    } else if (activeTab === 'tco') {
      loadTco();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, dateRange]);

  const loadAssetsByStatus = async () => {
    try {
      setLoading(true);
      const response = await reportsAPI.getAssetAssetsByStatus();
      setAssetsByStatus(response.data);
    } catch (err) {
      console.error('Error loading assets by status:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadMaintenanceDue = async () => {
    try {
      setLoading(true);
      const response = await reportsAPI.getAssetMaintenanceDue();
      setMaintenanceDue(response.data);
    } catch (err) {
      console.error('Error loading maintenance due:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadTco = async () => {
    try {
      setLoading(true);
      const response = await reportsAPI.getAssetTco();
      setTcoRows(response.data);
    } catch (err) {
      console.error('Error loading TCO report:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadUtilization = async () => {
    if (!dateRange[0] || !dateRange[1]) return;
    try {
      setLoading(true);
      const response = await reportsAPI.getAssetUtilization({
        start_date: dayjs(dateRange[0]).format('YYYY-MM-DD'),
        end_date: dayjs(dateRange[1]).format('YYYY-MM-DD'),
      });
      setUtilization(response.data);
    } catch (err) {
      console.error('Error loading utilization:', err);
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

  const sortData = <T extends Record<string, any>>(data: T[]): T[] => {
    const sorted = [...data];
    if (!sortField) return sorted;
    sorted.sort((a, b) => {
      let aVal: any = a[sortField];
      let bVal: any = b[sortField];
      if (typeof aVal === 'string') {
        aVal = aVal.toLowerCase();
        bVal = bVal.toLowerCase();
      }
      if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
      if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
      return 0;
    });
    return sorted;
  };

  const sortedAssetsByStatus = useMemo(() => sortData(assetsByStatus), [assetsByStatus, sortField, sortDirection]);
  const sortedMaintenanceDue = useMemo(() => sortData(maintenanceDue), [maintenanceDue, sortField, sortDirection]);
  const sortedUtilization = useMemo(() => sortData(utilization), [utilization, sortField, sortDirection]);
  const sortedTco = useMemo(() => {
    if (!sortField) {
      return [...tcoRows].sort(
        (a, b) => Number(b.total_maintenance_cost_90d) - Number(a.total_maintenance_cost_90d),
      );
    }
    const sorted = [...tcoRows];
    sorted.sort((a, b) => {
      let aVal: any = (a as any)[sortField];
      let bVal: any = (b as any)[sortField];
      const numericFields = [
        'maintenance_days_last_90',
        'scheduled_maintenance_cost',
        'unscheduled_maintenance_cost',
        'repair_cost',
        'tco',
        'preventive_maintenance_cost',
        'vendor_maintenance_cost',
        'total_maintenance_cost_90d',
      ];
      if (numericFields.includes(sortField)) {
        aVal = Number(aVal);
        bVal = Number(bVal);
      } else if (typeof aVal === 'string') {
        aVal = aVal.toLowerCase();
        bVal = (bVal ?? '').toString().toLowerCase();
      }
      if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
      if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
      return 0;
    });
    return sorted;
  }, [tcoRows, sortField, sortDirection]);

  const tcoHighlightThreshold = useMemo(() => {
    if (tcoRows.length === 0) return Infinity;
    const sorted = [...tcoRows].sort(
      (a, b) => Number(b.total_maintenance_cost_90d) - Number(a.total_maintenance_cost_90d),
    );
    return Number(sorted[Math.min(sorted.length - 1, 2)].total_maintenance_cost_90d);
  }, [tcoRows]);

  const handleExport = () => {
    if (activeTab === 'assets_by_status') {
      exportAssetReportToCSV(assetsByStatus, 'assets_by_status');
    } else if (activeTab === 'maintenance_due') {
      exportAssetReportToCSV(maintenanceDue, 'maintenance_due');
    } else if (activeTab === 'utilization') {
      exportAssetReportToCSV(utilization, 'utilization');
    } else if (activeTab === 'tco') {
      exportAssetReportToCSV(tcoRows, 'tco');
    }
  };

  const formatMoney = (value: string | number) => {
    const num = typeof value === 'number' ? value : parseFloat(value || '0');
    return Number.isFinite(num) ? num.toFixed(2) : '0.00';
  };

  const tcoRowColor = (totalValue: string) => {
    const num = Number(totalValue);
    if (!Number.isFinite(num) || num <= 0) return undefined;
    const topTotal = Number(sortedTco[0]?.total_maintenance_cost_90d ?? 0);
    if (num >= tcoHighlightThreshold && num >= topTotal * 0.8) {
      return 'red.0';
    }
    if (num >= tcoHighlightThreshold) return 'orange.0';
    return undefined;
  };

  return (
    <Stack gap="md" p="md">
      <Group justify="space-between">
        <Title order={2}>Asset Reports</Title>
        <Button
          leftSection={<IconDownload size={16} />}
          onClick={handleExport}
          disabled={loading}
        >
          Export CSV
        </Button>
      </Group>

      <Tabs value={activeTab} onChange={(value) => setActiveTab(value || 'assets_by_status')}>
        <Tabs.List>
          <Tabs.Tab value="assets_by_status">Assets by Status</Tabs.Tab>
          <Tabs.Tab value="maintenance_due">Maintenance Due</Tabs.Tab>
          <Tabs.Tab value="utilization">Utilization</Tabs.Tab>
          <Tabs.Tab value="tco">Total Cost of Ownership</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="assets_by_status" pt="md">
          <Paper withBorder>
            <Table.ScrollContainer minWidth={600}>
              <Table highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('status_display')}>
                      Status
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('count')}>
                      Count
                    </Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {loading ? (
                    <Table.Tr>
                      <Table.Td colSpan={2} style={{ textAlign: 'center' }}>
                        <Text>Loading...</Text>
                      </Table.Td>
                    </Table.Tr>
                  ) : sortedAssetsByStatus.length === 0 ? (
                    <Table.Tr>
                      <Table.Td colSpan={2} style={{ textAlign: 'center' }}>
                        <Text>No data available</Text>
                      </Table.Td>
                    </Table.Tr>
                  ) : (
                    sortedAssetsByStatus.map((item, idx) => (
                      <Table.Tr key={idx}>
                        <Table.Td>{item.status_display}</Table.Td>
                        <Table.Td>{item.count}</Table.Td>
                      </Table.Tr>
                    ))
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="maintenance_due" pt="md">
          <Paper withBorder>
            <Table.ScrollContainer minWidth={1200}>
              <Table highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('asset_name')}>
                      Asset Name
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('asset_tag')}>
                      Asset Tag
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('part_name')}>
                      Part Name
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('part_sku')}>
                      Part SKU
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('maintenance_interval_days')}>
                      Interval (days)
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('days_since_replacement')}>
                      Days Since Replacement
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('days_overdue')}>
                      Days Overdue
                    </Table.Th>
                    <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('last_replaced_at')}>
                      Last Replaced At
                    </Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {loading ? (
                    <Table.Tr>
                      <Table.Td colSpan={8} style={{ textAlign: 'center' }}>
                        <Text>Loading...</Text>
                      </Table.Td>
                    </Table.Tr>
                  ) : sortedMaintenanceDue.length === 0 ? (
                    <Table.Tr>
                      <Table.Td colSpan={8} style={{ textAlign: 'center' }}>
                        <Text>No maintenance due</Text>
                      </Table.Td>
                    </Table.Tr>
                  ) : (
                    sortedMaintenanceDue.map((item, idx) => (
                      <Table.Tr key={idx}>
                        <Table.Td>{item.asset_name}</Table.Td>
                        <Table.Td>{item.asset_tag || '-'}</Table.Td>
                        <Table.Td>{item.part_name || 'N/A'}</Table.Td>
                        <Table.Td>{item.part_sku || 'N/A'}</Table.Td>
                        <Table.Td>{item.maintenance_interval_days || 'N/A'}</Table.Td>
                        <Table.Td>{item.days_since_replacement || 'N/A'}</Table.Td>
                        <Table.Td>{item.days_overdue || 0}</Table.Td>
                        <Table.Td>
                          {item.last_replaced_at
                            ? new Date(item.last_replaced_at).toLocaleDateString()
                            : 'N/A'}
                        </Table.Td>
                      </Table.Tr>
                    ))
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="utilization" pt="md">
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
              <Table.ScrollContainer minWidth={800}>
                <Table highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('asset_name')}>
                        Asset Name
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('asset_tag')}>
                        Asset Tag
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('total_sessions')}>
                        Total Sessions
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('total_hours')}>
                        Total Hours
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('avg_hours_per_session')}>
                        Avg Hours per Session
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
                    ) : sortedUtilization.length === 0 ? (
                      <Table.Tr>
                        <Table.Td colSpan={5} style={{ textAlign: 'center' }}>
                          <Text>No utilization data available</Text>
                        </Table.Td>
                      </Table.Tr>
                    ) : (
                      sortedUtilization.map((item, idx) => (
                        <Table.Tr key={idx}>
                          <Table.Td>{item.asset_name}</Table.Td>
                          <Table.Td>{item.asset_tag || '-'}</Table.Td>
                          <Table.Td>{item.total_sessions}</Table.Td>
                          <Table.Td>{item.total_hours.toFixed(2)}</Table.Td>
                          <Table.Td>{item.avg_hours_per_session.toFixed(2)}</Table.Td>
                        </Table.Tr>
                      ))
                    )}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            </Paper>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="tco" pt="md">
          <Stack gap="xs">
            <Text size="sm" c="dimmed">
              Per-asset cost over the last 90 days. Preventive is in-house maintenance
              (Scheduled + Unscheduled); Vendor is third-party WO spend allocated per
              asset. Total = Preventive + Vendor. Top spenders are highlighted so you
              can decide where to invest, retire, or replace. Sort by any column.
            </Text>
            <Paper withBorder>
              <Table.ScrollContainer minWidth={1300}>
                <Table highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('asset_name')}>
                        Asset
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('asset_tag')}>
                        Tag
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('maintenance_days_last_90')}>
                        Days in Maintenance (90d)
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('scheduled_maintenance_cost')}>
                        Scheduled $
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('unscheduled_maintenance_cost')}>
                        Unscheduled $
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('preventive_maintenance_cost')}>
                        Preventive $
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('vendor_maintenance_cost')}>
                        Vendor $
                      </Table.Th>
                      <Table.Th style={{ cursor: 'pointer' }} onClick={() => handleSort('total_maintenance_cost_90d')}>
                        Total (90d)
                      </Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {loading ? (
                      <Table.Tr>
                        <Table.Td colSpan={8} style={{ textAlign: 'center' }}>
                          <Text>Loading...</Text>
                        </Table.Td>
                      </Table.Tr>
                    ) : sortedTco.length === 0 ? (
                      <Table.Tr>
                        <Table.Td colSpan={8} style={{ textAlign: 'center' }}>
                          <Text>No TCO data available</Text>
                        </Table.Td>
                      </Table.Tr>
                    ) : (
                      sortedTco.map((item) => (
                        <Table.Tr
                          key={item.asset_id}
                          bg={tcoRowColor(item.total_maintenance_cost_90d)}
                        >
                          <Table.Td>{item.asset_name}</Table.Td>
                          <Table.Td>{item.asset_tag || '-'}</Table.Td>
                          <Table.Td>{item.maintenance_days_last_90}</Table.Td>
                          <Table.Td>${formatMoney(item.scheduled_maintenance_cost)}</Table.Td>
                          <Table.Td>${formatMoney(item.unscheduled_maintenance_cost)}</Table.Td>
                          <Table.Td>${formatMoney(item.preventive_maintenance_cost)}</Table.Td>
                          <Table.Td>${formatMoney(item.vendor_maintenance_cost)}</Table.Td>
                          <Table.Td>
                            <Text fw={600}>${formatMoney(item.total_maintenance_cost_90d)}</Text>
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

export default AssetReportPage;
