/**
 * Asset Report Page
 * Reports for assets by status, maintenance due, and utilization
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
  AssetAssetsByStatus,
  AssetMaintenanceDue,
  AssetUtilization,
} from '../types';
import { exportAssetReportToCSV } from '../utils/csvExport';

type SortField = string;
type SortDirection = 'asc' | 'desc';

const AssetReportPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState<string>('assets_by_status');
  const [loading, setLoading] = useState(false);
  const [assetsByStatus, setAssetsByStatus] = useState<AssetAssetsByStatus[]>([]);
  const [maintenanceDue, setMaintenanceDue] = useState<AssetMaintenanceDue[]>([]);
  const [utilization, setUtilization] = useState<AssetUtilization[]>([]);
  const [utilizationDays, setUtilizationDays] = useState<number>(30);
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
    }
  }, [activeTab, utilizationDays]);

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

  const loadUtilization = async () => {
    try {
      setLoading(true);
      const response = await reportsAPI.getAssetUtilization({ days: utilizationDays });
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

  const handleExport = () => {
    if (activeTab === 'assets_by_status') {
      exportAssetReportToCSV(assetsByStatus, 'assets_by_status');
    } else if (activeTab === 'maintenance_due') {
      exportAssetReportToCSV(maintenanceDue, 'maintenance_due');
    } else if (activeTab === 'utilization') {
      exportAssetReportToCSV(utilization, 'utilization');
    }
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
              <NumberInput
                label="Time Period (days)"
                value={utilizationDays}
                onChange={(value) => setUtilizationDays(Number(value) || 30)}
                min={1}
                max={365}
                style={{ width: 200 }}
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
      </Tabs>
    </Stack>
  );
};

export default AssetReportPage;
