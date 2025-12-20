/**
 * Asset Table View Component
 * Displays assets in a table format with sorting, filtering, and pagination
 */
import {
  ActionIcon,
  Badge,
  Group,
  Loader,
  Pagination,
  Paper,
  Stack,
  Table,
  Text,
  Tooltip,
} from '@mantine/core';
import { IconDownload, IconSortAscending, IconSortDescending } from '@tabler/icons-react';
import React, { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Asset } from '../types';
import { exportAssetsToCSV } from '../utils/csvExport';

interface AssetTableViewProps {
  assets: Asset[];
  loading: boolean;
  totalCount?: number;
  currentPage?: number;
  pageSize?: number;
  onPageChange?: (page: number) => void;
  sortField: string | null;
  sortDirection: 'asc' | 'desc';
  onSort: (field: string) => void;
  serverMode: boolean;
  onExport?: () => Promise<Asset[]> | Asset[]; // Optional function to fetch all assets for export
}

type SortableField = 
  | 'name'
  | 'asset_tag'
  | 'serial_number'
  | 'status'
  | 'location_name'
  | 'category_name'
  | 'display_manufacturer'
  | 'date_received'
  | 'age_in_days'
  | 'inventory_item_name'
  | 'owning_group_name'
  | 'operational_status'
  | 'is_active';

// Map table field names to backend field names for sorting
const mapTableFieldToBackend = (field: SortableField): string => {
  const fieldMap: Record<SortableField, string> = {
    'name': 'name',
    'asset_tag': 'asset_tag',
    'serial_number': 'serial_number',
    'status': 'status',
    'location_name': 'location__name',
    'category_name': 'category__name',
    'display_manufacturer': 'manufacturer__name',
    'date_received': 'date_received',
    'age_in_days': 'date_received', // Age is calculated from date_received
    'inventory_item_name': 'inventory_item__name',
    'owning_group_name': 'owning_group__name',
    'operational_status': 'operational_status',
    'is_active': 'is_active',
  };
  return fieldMap[field] || field;
};

const AssetTableView: React.FC<AssetTableViewProps> = ({
  assets,
  loading,
  totalCount,
  currentPage = 1,
  pageSize = 50,
  onPageChange,
  sortField,
  sortDirection,
  onSort,
  serverMode,
  onExport,
}) => {
  const navigate = useNavigate();
  const [exporting, setExporting] = React.useState(false);

  const getStatusBadgeColor = (status: string) => {
    switch (status) {
      case 'active': return 'green';
      case 'maintenance': return 'yellow';
      case 'retired': return 'gray';
      case 'lost': return 'red';
      case 'donated_out': return 'blue';
      default: return 'gray';
    }
  };

  const getStatusLabel = (status: string) => {
    switch (status) {
      case 'active': return 'Active';
      case 'maintenance': return 'Maintenance';
      case 'retired': return 'Retired';
      case 'lost': return 'Lost';
      case 'donated_out': return 'Donated';
      default: return status;
    }
  };

  const getOperationalStatusBadgeColor = (status: string) => {
    switch (status) {
      case 'available': return 'green';
      case 'reserved': return 'blue';
      case 'needs_maintenance': return 'yellow';
      case 'disabled': return 'red';
      default: return 'gray';
    }
  };

  const formatAge = (ageInDays: number | undefined) => {
    if (ageInDays === undefined || ageInDays === null) return 'N/A';
    const years = Math.floor(ageInDays / 365);
    const days = ageInDays % 365;
    if (years > 0) {
      return `${years}y ${days}d`;
    }
    return `${days}d`;
  };

  const formatDate = (dateString: string | null | undefined) => {
    if (!dateString) return 'N/A';
    try {
      return new Date(dateString).toLocaleDateString();
    } catch {
      return 'N/A';
    }
  };

  const getSortIcon = (field: SortableField) => {
    if (sortField !== field) return null;
    return sortDirection === 'asc' ? (
      <IconSortAscending size={16} />
    ) : (
      <IconSortDescending size={16} />
    );
  };

  const handleRowClick = (assetId: string) => {
    navigate(`/assets/${assetId}`);
  };

  const handleSort = (field: SortableField) => {
    onSort(field);
  };

  const handleExport = async () => {
    try {
      setExporting(true);
      let assetsToExport: Asset[] = [];
      
      if (onExport) {
        // Use the provided export function to get all assets
        const result = await onExport();
        assetsToExport = Array.isArray(result) ? result : [];
      } else {
        // Fallback: export current page only
        assetsToExport = assets;
      }
      
      exportAssetsToCSV(assetsToExport);
    } catch (err) {
      console.error('Error exporting assets:', err);
    } finally {
      setExporting(false);
    }
  };

  const totalPages = serverMode && totalCount ? Math.ceil(totalCount / pageSize) : 1;

  if (loading && assets.length === 0) {
    return (
      <Paper withBorder p="xl">
        <Stack align="center" gap="md">
          <Loader size="lg" />
          <Text>Loading assets...</Text>
        </Stack>
      </Paper>
    );
  }

  if (assets.length === 0) {
    return (
      <Paper withBorder p="xl">
        <Text ta="center" c="dimmed">No assets found.</Text>
      </Paper>
    );
  }

  return (
    <Stack gap="md">
      <Group justify="space-between" align="center">
        <Text size="sm" c="dimmed">
          {serverMode 
            ? `Showing page ${currentPage} of ${totalPages} (${totalCount} total assets)`
            : `Showing ${assets.length} asset${assets.length !== 1 ? 's' : ''}`
          }
        </Text>
        <Tooltip label="Export to CSV">
          <ActionIcon
            variant="light"
            onClick={handleExport}
            loading={exporting}
            disabled={assets.length === 0}
          >
            <IconDownload size={18} />
          </ActionIcon>
        </Tooltip>
      </Group>
      <Paper withBorder>
        <Table.ScrollContainer minWidth={1400}>
          <Table highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th
                  style={{ cursor: 'pointer' }}
                  onClick={() => handleSort('name')}
                >
                  <Group gap="xs">
                    Name
                    {getSortIcon('name')}
                  </Group>
                </Table.Th>
                <Table.Th
                  style={{ cursor: 'pointer' }}
                  onClick={() => handleSort('asset_tag')}
                >
                  <Group gap="xs">
                    Asset Tag
                    {getSortIcon('asset_tag')}
                  </Group>
                </Table.Th>
                <Table.Th
                  style={{ cursor: 'pointer' }}
                  onClick={() => handleSort('serial_number')}
                >
                  <Group gap="xs">
                    Serial Number
                    {getSortIcon('serial_number')}
                  </Group>
                </Table.Th>
                <Table.Th
                  style={{ cursor: 'pointer' }}
                  onClick={() => handleSort('status')}
                >
                  <Group gap="xs">
                    Status
                    {getSortIcon('status')}
                  </Group>
                </Table.Th>
                <Table.Th
                  style={{ cursor: 'pointer' }}
                  onClick={() => handleSort('location_name')}
                >
                  <Group gap="xs">
                    Location
                    {getSortIcon('location_name')}
                  </Group>
                </Table.Th>
                <Table.Th
                  style={{ cursor: 'pointer' }}
                  onClick={() => handleSort('category_name')}
                >
                  <Group gap="xs">
                    Category
                    {getSortIcon('category_name')}
                  </Group>
                </Table.Th>
                <Table.Th
                  style={{ cursor: 'pointer' }}
                  onClick={() => handleSort('display_manufacturer')}
                >
                  <Group gap="xs">
                    Manufacturer
                    {getSortIcon('display_manufacturer')}
                  </Group>
                </Table.Th>
                <Table.Th
                  style={{ cursor: 'pointer' }}
                  onClick={() => handleSort('age_in_days')}
                >
                  <Group gap="xs">
                    Age
                    {getSortIcon('age_in_days')}
                  </Group>
                </Table.Th>
                <Table.Th
                  style={{ cursor: 'pointer' }}
                  onClick={() => handleSort('date_received')}
                >
                  <Group gap="xs">
                    Date Received
                    {getSortIcon('date_received')}
                  </Group>
                </Table.Th>
                <Table.Th
                  style={{ cursor: 'pointer' }}
                  onClick={() => handleSort('inventory_item_name')}
                >
                  <Group gap="xs">
                    Inventory Item
                    {getSortIcon('inventory_item_name')}
                  </Group>
                </Table.Th>
                <Table.Th
                  style={{ cursor: 'pointer' }}
                  onClick={() => handleSort('owning_group_name')}
                >
                  <Group gap="xs">
                    Owning Group
                    {getSortIcon('owning_group_name')}
                  </Group>
                </Table.Th>
                <Table.Th>
                  Operational Status
                </Table.Th>
                <Table.Th
                  style={{ cursor: 'pointer' }}
                  onClick={() => handleSort('is_active')}
                >
                  <Group gap="xs">
                    Is Active
                    {getSortIcon('is_active')}
                  </Group>
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {assets.map((asset) => (
                <Table.Tr
                  key={asset.id}
                  style={{ cursor: 'pointer' }}
                  onClick={() => handleRowClick(asset.id)}
                >
                  <Table.Td>{asset.name}</Table.Td>
                  <Table.Td>{asset.asset_tag || 'N/A'}</Table.Td>
                  <Table.Td>{asset.serial_number || 'N/A'}</Table.Td>
                  <Table.Td>
                    <Badge color={getStatusBadgeColor(asset.status)}>
                      {getStatusLabel(asset.status)}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{asset.location_name || 'N/A'}</Table.Td>
                  <Table.Td>{asset.category_name || 'N/A'}</Table.Td>
                  <Table.Td>{asset.display_manufacturer || 'N/A'}</Table.Td>
                  <Table.Td>{formatAge(asset.age_in_days)}</Table.Td>
                  <Table.Td>{formatDate(asset.date_received)}</Table.Td>
                  <Table.Td>{asset.inventory_item_name || 'N/A'}</Table.Td>
                  <Table.Td>{asset.owning_group_name || 'N/A'}</Table.Td>
                  <Table.Td>
                    <Badge color={getOperationalStatusBadgeColor(asset.operational_status)}>
                      {asset.operational_status}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{asset.is_active ? 'Yes' : 'No'}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>

      {serverMode && totalCount && totalCount > pageSize && (
        <Group justify="center">
          <Pagination
            value={currentPage}
            onChange={onPageChange}
            total={totalPages}
            siblings={1}
            boundaries={1}
          />
        </Group>
      )}
    </Stack>
  );
};

export default AssetTableView;

