/**
 * Supplier Detail Page
 * Comprehensive detail view with tabs for overview, items, lead time analytics, order history, and price trends
 */
import {
    ActionIcon,
    Badge,
    Button,
    Card,
    Group,
    Paper,
    Stack,
    Table,
    Tabs,
    Text,
    Title,
} from '@mantine/core';
import { IconEdit, IconExternalLink } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import LeadTimeChart from '../components/LeadTimeChart';
import WorkspacePage from '../components/landing/WorkspacePage';
import PriceTrendChart from '../components/PriceTrendChart';
import { inventoryAPI } from '../services/api';
import { ItemSupplier, SupplierDetail } from '../types';

const SupplierDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [supplier, setSupplier] = useState<SupplierDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<string | null>('overview');

  useEffect(() => {
    if (id) {
      loadSupplier();
    }
  }, [id]);

  const loadSupplier = async () => {
    if (!id) return;

    try {
      setLoading(true);
      const response = await inventoryAPI.getSupplier(id);
      setSupplier(response.data);
    } catch (err) {
      console.error('Error loading supplier details:', err);
    } finally {
      setLoading(false);
    }
  };

  const getTypeBadgeColor = (type: string) => {
    switch (type) {
      case 'local':
        return 'blue';
      case 'online':
        return 'green';
      case 'national':
        return 'orange';
      default:
        return 'gray';
    }
  };

  const getTypeLabel = (type: string) => {
    switch (type) {
      case 'local':
        return 'Local';
      case 'online':
        return 'Online';
      case 'national':
        return 'National';
      default:
        return type;
    }
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="supplier-detail-page"
        hero={{ eyebrow: 'Inventory · Supplier', title: 'Supplier', description: 'Loading…' }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading supplier…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  if (!supplier) {
    return (
      <WorkspacePage
        testId="supplier-detail-page"
        hero={{ eyebrow: 'Inventory · Supplier', title: 'Supplier', description: 'Not found.' }}
      >
        <Paper withBorder p="md">
          <Text>Supplier not found.</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      testId="supplier-detail-page"
      hero={{
        eyebrow: `Inventory · ${getTypeLabel(supplier.supplier_type)} supplier`,
        title: supplier.name,
        description: supplier.notes ? supplier.notes.split('\n')[0] : undefined,
        action: (
          <Button
            leftSection={<IconEdit size={16} />}
            onClick={() => navigate(`/inventory/suppliers/${id}/edit`)}
          >
            Edit
          </Button>
        ),
      }}
    >
      {/* Status Badges */}
      <Group>
        <Badge color={getTypeBadgeColor(supplier.supplier_type)}>
          {getTypeLabel(supplier.supplier_type)}
        </Badge>
        {supplier.tax_free_paperwork_filed ? (
          <Badge color="green">Tax-Free</Badge>
        ) : (
          <Badge color="gray" variant="light">
            Tax-Free Not Filed
          </Badge>
        )}
      </Group>

      {/* Tabs */}
      <Tabs value={activeTab} onChange={setActiveTab}>
        <Tabs.List>
          <Tabs.Tab value="overview">Overview</Tabs.Tab>
          <Tabs.Tab value="items">Items</Tabs.Tab>
          <Tabs.Tab value="lead-time">Lead Time Analytics</Tabs.Tab>
          <Tabs.Tab value="orders">Order History</Tabs.Tab>
          <Tabs.Tab value="price-trends">Price Trends</Tabs.Tab>
        </Tabs.List>

        {/* Overview Tab */}
        <Tabs.Panel value="overview" pt="md">
          <Stack gap="md">
            <Group align="flex-start" grow>
              {/* Basic Information */}
              <Card withBorder p="md">
                <Stack gap="md">
                  <Title order={4}>Basic Information</Title>
                  <div>
                    <Text size="sm" c="dimmed">
                      Name
                    </Text>
                    <Text fw={500}>{supplier.name}</Text>
                  </div>
                  <div>
                    <Text size="sm" c="dimmed">
                      Type
                    </Text>
                    <Badge color={getTypeBadgeColor(supplier.supplier_type)}>
                      {getTypeLabel(supplier.supplier_type)}
                    </Badge>
                  </div>
                  {supplier.website && (
                    <div>
                      <Text size="sm" c="dimmed">
                        Website
                      </Text>
                      <Group gap="xs">
                        <Text
                          size="sm"
                          c="blue"
                          style={{ cursor: 'pointer' }}
                          onClick={() => window.open(supplier.website, '_blank')}
                        >
                          {supplier.website}
                        </Text>
                        <ActionIcon
                          size="sm"
                          variant="subtle"
                          onClick={() => window.open(supplier.website, '_blank')}
                        >
                          <IconExternalLink size={14} />
                        </ActionIcon>
                      </Group>
                    </div>
                  )}
                  {supplier.account_number && (
                    <div>
                      <Text size="sm" c="dimmed">
                        Account Number
                      </Text>
                      <Text fw={500}>{supplier.account_number}</Text>
                    </div>
                  )}
                  <div>
                    <Text size="sm" c="dimmed">
                      Tax-Free Status
                    </Text>
                    {supplier.tax_free_paperwork_filed ? (
                      <Badge color="green">Tax-Free Paperwork Filed</Badge>
                    ) : (
                      <Badge color="gray" variant="light">
                        Not Filed
                      </Badge>
                    )}
                  </div>
                </Stack>
              </Card>

              {/* Statistics */}
              <Card withBorder p="md">
                <Stack gap="md">
                  <Title order={4}>Statistics</Title>
                  <div>
                    <Text size="sm" c="dimmed">
                      Items Supplied
                    </Text>
                    <Text size="xl" fw={700}>
                      {supplier.item_count || 0}
                    </Text>
                  </div>
                  <div>
                    <Text size="sm" c="dimmed">
                      Purchase Orders
                    </Text>
                    <Text size="xl" fw={700}>
                      {supplier.purchase_order_count || 0}
                    </Text>
                  </div>
                  <div>
                    <Text size="sm" c="dimmed">
                      Total Spent
                    </Text>
                    <Text size="xl" fw={700}>
                      ${supplier.total_spent || '0.00'}
                    </Text>
                  </div>
                </Stack>
              </Card>
            </Group>

            {/* Notes */}
            {supplier.notes && (
              <Card withBorder p="md">
                <Stack gap="md">
                  <Title order={4}>Notes</Title>
                  <Text size="sm">{supplier.notes}</Text>
                </Stack>
              </Card>
            )}
          </Stack>
        </Tabs.Panel>

        {/* Items Tab */}
        <Tabs.Panel value="items" pt="md">
          <Card withBorder p="md">
            <Title order={4} mb="md">
              Items Supplied ({supplier.items?.length || 0})
            </Title>
            {supplier.items && supplier.items.length > 0 ? (
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Item Name</Table.Th>
                    <Table.Th>SKU</Table.Th>
                    <Table.Th>Unit Cost</Table.Th>
                    <Table.Th>Package Cost</Table.Th>
                    <Table.Th>Lead Time (days)</Table.Th>
                    <Table.Th>Primary</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {supplier.items.map((itemSupplier: ItemSupplier) => (
                    <Table.Tr
                      key={itemSupplier.id}
                      style={{ cursor: 'pointer' }}
                      onClick={() => navigate(`/inventory/items/${itemSupplier.item}`)}
                    >
                      <Table.Td>
                        <Text fw={500}>{itemSupplier.item_name}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm" c="dimmed">
                          {itemSupplier.supplier_sku}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        {itemSupplier.unit_cost ? (
                          <Text>${itemSupplier.unit_cost}</Text>
                        ) : (
                          <Text c="dimmed">-</Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        {itemSupplier.package_cost ? (
                          <Text>${itemSupplier.package_cost}</Text>
                        ) : (
                          <Text c="dimmed">-</Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Text>{itemSupplier.average_lead_time} days</Text>
                      </Table.Td>
                      <Table.Td>
                        {itemSupplier.is_primary ? (
                          <Badge color="blue">Primary</Badge>
                        ) : (
                          <Text c="dimmed">-</Text>
                        )}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            ) : (
              <Text c="dimmed">No items supplied by this supplier.</Text>
            )}
          </Card>
        </Tabs.Panel>

        {/* Lead Time Analytics Tab */}
        <Tabs.Panel value="lead-time" pt="md">
          {supplier.lead_time_analytics ? (
            <LeadTimeChart analytics={supplier.lead_time_analytics} />
          ) : (
            <Card withBorder p="md">
              <Text c="dimmed">No lead time data available.</Text>
            </Card>
          )}
        </Tabs.Panel>

        {/* Order History Tab */}
        <Tabs.Panel value="orders" pt="md">
          <Card withBorder p="md">
            <Title order={4} mb="md">
              Purchase Orders ({supplier.purchase_orders?.length || 0})
            </Title>
            {supplier.purchase_orders && supplier.purchase_orders.length > 0 ? (
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>PO Number</Table.Th>
                    <Table.Th>Order Date</Table.Th>
                    <Table.Th>Status</Table.Th>
                    <Table.Th>Items</Table.Th>
                    <Table.Th>Estimated Total</Table.Th>
                    <Table.Th>Actual Total</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {supplier.purchase_orders.map((order: any) => (
                    <Table.Tr
                      key={order.id}
                      style={{ cursor: 'pointer' }}
                      onClick={() => navigate(`/purchasing/orders/${order.id}`)}
                    >
                      <Table.Td>
                        <Text fw={500}>{order.po_number || `PO-${order.id}`}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm">
                          {new Date(order.order_date).toLocaleDateString()}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Badge>{order.status}</Badge>
                      </Table.Td>
                      <Table.Td>
                        <Text>{order.items?.length || 0}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text>${order.estimated_total || '0.00'}</Text>
                      </Table.Td>
                      <Table.Td>
                        {order.actual_total ? (
                          <Text>${order.actual_total}</Text>
                        ) : (
                          <Text c="dimmed">-</Text>
                        )}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            ) : (
              <Text c="dimmed">No purchase orders found.</Text>
            )}
          </Card>
        </Tabs.Panel>

        {/* Price Trends Tab */}
        <Tabs.Panel value="price-trends" pt="md">
          {supplier.price_trends ? (
            <PriceTrendChart priceTrends={supplier.price_trends} />
          ) : (
            <Card withBorder p="md">
              <Text c="dimmed">No price trend data available.</Text>
            </Card>
          )}
        </Tabs.Panel>
      </Tabs>
    </WorkspacePage>
  );
};

export default SupplierDetailPage;
