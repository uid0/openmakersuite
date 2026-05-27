/**
 * Inventory Item Detail Page
 * Comprehensive detail view with tabs for overview, stock history, reorder history, usage logs, and linked assets
 */
import {
    ActionIcon,
    Badge,
    Button,
    Card,
    Group,
    Image,
    Paper,
    Stack,
    Table,
    Tabs,
    Text,
    Title,
} from '@mantine/core';
import { IconEdit, IconQrcode } from '@tabler/icons-react';
import { QRCodeSVG } from 'qrcode.react';
import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import NFPADiamond from '../components/NFPADiamond';
import StockHistoryChart from '../components/StockHistoryChart';
import { assetsAPI, inventoryAPI, reorderAPI } from '../services/api';
import { Asset, InventoryItem, ReorderRequest, UsageLog } from '../types';
import { showError } from '../utils/dialogs';

const InventoryItemDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [item, setItem] = useState<InventoryItem | null>(null);
  const [usageLogs, setUsageLogs] = useState<UsageLog[]>([]);
  const [reorderHistory, setReorderHistory] = useState<ReorderRequest[]>([]);
  const [linkedAssets, setLinkedAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<string | null>('overview');

  useEffect(() => {
    if (id) {
      loadData();
    }
  }, [id]);

  const loadData = async () => {
    if (!id) return;

    setLoading(true);
    // Use allSettled so the page still renders the item when a sibling
    // call (usage logs, reorder history, linked assets) fails. Previously
    // any one of these rejecting would short-circuit the whole try-block
    // and show "Item not found" even though the item exists — uid0 hit
    // this on items with no linked-assets filter match where assetsAPI
    // returned a 400.
    const [itemRes, usageLogsRes, reorderRes, assetsRes] = await Promise.allSettled([
      inventoryAPI.getItem(id),
      inventoryAPI.getUsageLogs(id),
      reorderAPI.listRequests({ status: undefined }),
      assetsAPI.listAssets({ inventory_item: id }),
    ]);

    if (itemRes.status === 'fulfilled') {
      setItem(itemRes.value.data);
    } else {
      console.error('Error loading item:', itemRes.reason);
    }

    if (usageLogsRes.status === 'fulfilled') {
      setUsageLogs(usageLogsRes.value.data.results || []);
    } else {
      console.error('Error loading usage logs:', usageLogsRes.reason);
    }

    if (reorderRes.status === 'fulfilled') {
      const allRequests = reorderRes.value.data.results || [];
      setReorderHistory(allRequests.filter((req) => req.item === id));
    } else {
      console.error('Error loading reorder requests:', reorderRes.reason);
    }

    if (assetsRes.status === 'fulfilled') {
      setLinkedAssets(assetsRes.value.data.results || []);
    } else {
      console.error('Error loading linked assets:', assetsRes.reason);
    }

    setLoading(false);
  };

  const handleGenerateQR = async () => {
    if (!id) return;

    try {
      await inventoryAPI.generateQR(id);
      await loadData(); // Reload to get updated QR code
    } catch (err) {
      console.error('Error generating QR code:', err);
      showError('Failed to generate QR code. Please try again.');
    }
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="inventory-item-detail-page"
        hero={{ eyebrow: 'Inventory · Item', title: 'Item', description: 'Loading…' }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading item…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  if (!item) {
    return (
      <WorkspacePage
        testId="inventory-item-detail-page"
        hero={{ eyebrow: 'Inventory · Item', title: 'Item', description: 'Not found.' }}
      >
        <Paper withBorder p="md">
          <Text>Item not found.</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      testId="inventory-item-detail-page"
      hero={{
        eyebrow: `Inventory · SKU ${item.sku}`,
        title: item.name,
        description: item.description ? item.description.split('\n')[0] : undefined,
        action: (
          <Group gap="sm">
            <Button
              variant="default"
              leftSection={<IconQrcode size={16} />}
              onClick={handleGenerateQR}
            >
              Generate QR
            </Button>
            <Button
              leftSection={<IconEdit size={16} />}
              onClick={() => navigate(`/inventory/items/${id}/edit`)}
            >
              Edit
            </Button>
          </Group>
        ),
      }}
    >
      {/* Status Badges */}
      <Group>
        {item.needs_reorder && <Badge color="red">Low Stock</Badge>}
        {item.has_pending_reorder && <Badge color="blue">Reorder Pending</Badge>}
        {!item.is_active && <Badge color="gray">Inactive</Badge>}
        {item.is_hazardous && <Badge color="orange">Hazardous Material</Badge>}
      </Group>

      {/* Tabs */}
      <Tabs value={activeTab} onChange={setActiveTab}>
        <Tabs.List>
          <Tabs.Tab value="overview">Overview</Tabs.Tab>
          <Tabs.Tab value="stock-history">Stock History</Tabs.Tab>
          <Tabs.Tab value="reorder-history">Reorder History</Tabs.Tab>
          <Tabs.Tab value="usage-logs">Usage Logs</Tabs.Tab>
          <Tabs.Tab value="linked-assets">Linked Assets</Tabs.Tab>
        </Tabs.List>

        {/* Overview Tab */}
        <Tabs.Panel value="overview" pt="md">
          <Stack gap="md">
            <Group align="flex-start" grow>
              {/* Image and Basic Info */}
              <Card withBorder p="md">
                <Stack gap="md">
                  {item.thumbnail && (
                    <Image src={item.thumbnail} alt={item.name} height={200} fit="contain" />
                  )}
                  <div>
                    <Text size="sm" fw={500} mb="xs">
                      Description
                    </Text>
                    <Text size="sm">{item.description || 'No description provided'}</Text>
                  </div>
                </Stack>
              </Card>

              {/* Stock Information */}
              <Card withBorder p="md">
                <Stack gap="md">
                  <Title order={4}>Stock Information</Title>
                  <Group justify="space-between">
                    <Text size="sm">Current Stock:</Text>
                    <Text size="sm" fw={600} c={item.needs_reorder ? 'red' : undefined}>
                      {item.current_stock} {item.use_case_based_reorder ? 'units' : 'units'}
                    </Text>
                  </Group>
                  {item.use_case_based_reorder && (
                    <Group justify="space-between">
                      <Text size="sm">Current Cases:</Text>
                      <Text size="sm" fw={600}>
                        {item.current_cases.toFixed(1)} cases
                      </Text>
                    </Group>
                  )}
                  <Group justify="space-between">
                    <Text size="sm">Minimum Stock:</Text>
                    <Text size="sm">
                      {item.use_case_based_reorder ? `${item.minimum_cases} cases` : `${item.minimum_stock} units`}
                    </Text>
                  </Group>
                  <Group justify="space-between">
                    <Text size="sm">Reorder Quantity:</Text>
                    <Text size="sm">
                      {item.use_case_based_reorder ? `${item.reorder_cases} cases` : `${item.reorder_quantity} units`}
                    </Text>
                  </Group>
                  {item.unit_cost && (
                    <Group justify="space-between">
                      <Text size="sm">Unit Cost:</Text>
                      <Text size="sm" fw={600}>
                        ${parseFloat(item.unit_cost).toFixed(2)}
                      </Text>
                    </Group>
                  )}
                </Stack>
              </Card>
            </Group>

            <Group align="flex-start" grow>
              {/* Category & Location */}
              <Card withBorder p="md">
                <Stack gap="md">
                  <Title order={4}>Organization</Title>
                  <div>
                    <Text size="sm" fw={500} mb="xs">
                      Category
                    </Text>
                    <Text size="sm">{item.category_name || 'Uncategorized'}</Text>
                  </div>
                  <div>
                    <Text size="sm" fw={500} mb="xs">
                      Location
                    </Text>
                    <Text size="sm">{item.location || 'No location specified'}</Text>
                  </div>
                  {item.supplier_name && (
                    <div>
                      <Text size="sm" fw={500} mb="xs">
                        Primary Supplier
                      </Text>
                      <Text size="sm">{item.supplier_name}</Text>
                    </div>
                  )}
                </Stack>
              </Card>

              {/* Hazmat Information */}
              {item.is_hazardous && (
                <Card withBorder p="md">
                  <Stack gap="md">
                    <Title order={4}>Hazardous Materials</Title>
                    <div>
                      <Text size="sm" fw={500} mb="xs">
                        Compliance Status
                      </Text>
                      <Badge color={item.hazmat_compliance_status === 'Complete' ? 'green' : 'orange'}>
                        {item.hazmat_compliance_status}
                      </Badge>
                    </div>
                    {item.msds_url && (
                      <div>
                        <Text size="sm" fw={500} mb="xs">
                          MSDS/SDS
                        </Text>
                        <Text size="sm">
                          <a href={item.msds_url} target="_blank" rel="noopener noreferrer">
                            View MSDS
                          </a>
                        </Text>
                      </div>
                    )}
                    <div>
                      <Text size="sm" fw={500} mb="xs">
                        NFPA Fire Diamond
                      </Text>
                      <NFPADiamond
                        health={item.nfpa_health_hazard}
                        flammability={item.nfpa_fire_hazard}
                        instability={item.nfpa_instability_hazard}
                        special={item.nfpa_special_hazards}
                      />
                    </div>
                  </Stack>
                </Card>
              )}
            </Group>

            {/* QR Code */}
            {item.qr_code && (
              <Card withBorder p="md">
                <Stack gap="md" align="center">
                  <Title order={4}>QR Code</Title>
                  <QRCodeSVG value={`${window.location.origin}/inventory/scan/${item.id}`} size={200} />
                  <Text size="xs" c="dimmed">
                    Scan to view item details
                  </Text>
                </Stack>
              </Card>
            )}

            {/* Notes */}
            {item.notes && (
              <Card withBorder p="md">
                <Stack gap="md">
                  <Title order={4}>Notes</Title>
                  <Text size="sm">{item.notes}</Text>
                </Stack>
              </Card>
            )}
          </Stack>
        </Tabs.Panel>

        {/* Stock History Tab */}
        <Tabs.Panel value="stock-history" pt="md">
          <StockHistoryChart
            usageLogs={usageLogs}
            currentStock={item.current_stock}
            minimumStock={item.minimum_stock}
          />
        </Tabs.Panel>

        {/* Reorder History Tab */}
        <Tabs.Panel value="reorder-history" pt="md">
          <Card withBorder p="md">
            <Title order={4} mb="md">
              Reorder History
            </Title>
            {reorderHistory.length === 0 ? (
              <Text c="dimmed">No reorder history available.</Text>
            ) : (
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Date</Table.Th>
                    <Table.Th>Quantity</Table.Th>
                    <Table.Th>Status</Table.Th>
                    <Table.Th>Requested By</Table.Th>
                    <Table.Th>Notes</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {reorderHistory.map((req) => (
                    <Table.Tr key={req.id}>
                      <Table.Td>{new Date(req.requested_at).toLocaleDateString()}</Table.Td>
                      <Table.Td>{req.quantity}</Table.Td>
                      <Table.Td>
                        <Badge color={req.status === 'received' ? 'green' : req.status === 'ordered' ? 'blue' : 'yellow'}>
                          {req.status}
                        </Badge>
                      </Table.Td>
                      <Table.Td>{req.requested_by}</Table.Td>
                      <Table.Td>{req.request_notes || '-'}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
          </Card>
        </Tabs.Panel>

        {/* Usage Logs Tab */}
        <Tabs.Panel value="usage-logs" pt="md">
          <Card withBorder p="md">
            <Title order={4} mb="md">
              Usage Logs
            </Title>
            {usageLogs.length === 0 ? (
              <Text c="dimmed">No usage logs available.</Text>
            ) : (
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Date</Table.Th>
                    <Table.Th>Quantity Used</Table.Th>
                    <Table.Th>Notes</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {usageLogs.map((log) => (
                    <Table.Tr key={log.id}>
                      <Table.Td>{new Date(log.usage_date).toLocaleDateString()}</Table.Td>
                      <Table.Td>{log.quantity_used}</Table.Td>
                      <Table.Td>{log.notes || '-'}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
          </Card>
        </Tabs.Panel>

        {/* Linked Assets Tab */}
        <Tabs.Panel value="linked-assets" pt="md">
          <Card withBorder p="md">
            <Title order={4} mb="md">
              Linked Assets
            </Title>
            {linkedAssets.length === 0 ? (
              <Text c="dimmed">No assets linked to this inventory item.</Text>
            ) : (
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Asset Name</Table.Th>
                    <Table.Th>Asset Tag</Table.Th>
                    <Table.Th>Status</Table.Th>
                    <Table.Th>Location</Table.Th>
                    <Table.Th>Actions</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {linkedAssets.map((asset) => (
                    <Table.Tr key={asset.id}>
                      <Table.Td>{asset.name}</Table.Td>
                      <Table.Td>{asset.asset_tag || '-'}</Table.Td>
                      <Table.Td>
                        <Badge color={asset.status === 'active' ? 'green' : 'gray'}>{asset.status}</Badge>
                      </Table.Td>
                      <Table.Td>{asset.location_name || '-'}</Table.Td>
                      <Table.Td>
                        <ActionIcon
                          variant="subtle"
                          onClick={() => navigate(`/inventory/scan/asset/${asset.id}`)}
                        >
                          View
                        </ActionIcon>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
          </Card>
        </Tabs.Panel>
      </Tabs>
    </WorkspacePage>
  );
};

export default InventoryItemDetailPage;
