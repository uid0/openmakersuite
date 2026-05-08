/**
 * Webhook List Page
 * Display and manage webhooks with filtering and actions
 */
import {
    ActionIcon,
    Badge,
    Button,
    Group,
    Paper,
    Select,
    Stack,
    Table,
    Text,
    TextInput,
} from '@mantine/core';
import { IconEdit, IconExternalLink, IconEye, IconSearch, IconTestPipe, IconTrash } from '@tabler/icons-react';
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { webhooksAPI } from '../services/api';
import '../styles/WebhookListPage.css';
import { Webhook } from '../types';
import { confirmDelete, showError } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const EVENT_TYPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: 'All Event Types' },
  { value: 'reorder_request_created', label: 'Reorder Request Created' },
  { value: 'reorder_request_approved', label: 'Reorder Request Approved' },
  { value: 'reorder_request_ordered', label: 'Reorder Request Ordered' },
  { value: 'reorder_request_received', label: 'Reorder Request Received' },
  { value: 'item_low_stock', label: 'Item Low Stock' },
  { value: 'purchase_order_created', label: 'Purchase Order Created' },
  { value: 'delivery_received', label: 'Delivery Received' },
  { value: 'fixture_refill_requested', label: 'Fixture Refill Requested' },
  { value: 'location_checkin', label: 'Location Check-in' },
  { value: 'location_feedback', label: 'Location Feedback' },
  { value: 'security_report', label: 'Security Report' },
];

const WebhookListPage: React.FC = () => {
  const navigate = useNavigate();
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedEventType, setSelectedEventType] = useState<string | null>(null);
  const [selectedStatus, setSelectedStatus] = useState<string | null>(null);

  useEffect(() => {
    loadWebhooks();
  }, []);

  const loadWebhooks = async () => {
    try {
      setLoading(true);
      const response = await webhooksAPI.listWebhooks();
      setWebhooks(response.data.results || []);
    } catch (err) {
      console.error('Error loading webhooks:', err);
    } finally {
      setLoading(false);
    }
  };

  const filteredWebhooks = useMemo(() => {
    let filtered = [...webhooks];

    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      filtered = filtered.filter(
        (webhook) =>
          webhook.name.toLowerCase().includes(term) ||
          webhook.url.toLowerCase().includes(term) ||
          (webhook.description && webhook.description.toLowerCase().includes(term))
      );
    }

    if (selectedEventType) {
      filtered = filtered.filter((webhook) => webhook.event_type === selectedEventType);
    }

    if (selectedStatus !== null) {
      const isActive = selectedStatus === 'active';
      filtered = filtered.filter((webhook) => webhook.is_active === isActive);
    }

    return filtered;
  }, [webhooks, searchTerm, selectedEventType, selectedStatus]);

  const handleDelete = (id: number, name: string) => {
    confirmDelete(`Are you sure you want to delete webhook "${name}"?`, async () => {
      try {
        await webhooksAPI.deleteWebhook(id);
        await loadWebhooks();
      } catch (err: any) {
        console.error('Error deleting webhook:', err);
        showError(extractErrorMessage(err, 'Failed to delete webhook'));
      }
    });
  };

  const getSuccessRateColor = (rate: number | null) => {
    if (rate === null) return 'gray';
    if (rate >= 95) return 'green';
    if (rate >= 80) return 'yellow';
    return 'red';
  };

  const formatDate = (dateString: string | null) => {
    if (!dateString) return 'Never';
    const date = new Date(dateString);
    return date.toLocaleString();
  };

  if (loading) {
    return (
      <Stack gap="md">
        <Text>Loading webhooks...</Text>
      </Stack>
    );
  }

  return (
    <div className="webhook-list-page">
      <Stack gap="md">
        <Group justify="space-between">
          <div>
            <Text size="xl" fw={500}>
              Webhooks
            </Text>
            <Text size="sm" c="dimmed">
              {filteredWebhooks.length} of {webhooks.length} webhooks
            </Text>
          </div>
          <Button onClick={() => navigate('/settings/webhooks/new')}>
            Create Webhook
          </Button>
        </Group>

        {/* Filters and Search */}
        <Paper p="md" withBorder>
          <Stack gap="md">
            <Group grow>
              <TextInput
                placeholder="Search by name, URL, or description..."
                leftSection={<IconSearch size={16} />}
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
              <Select
                placeholder="All Event Types"
                data={EVENT_TYPE_OPTIONS}
                value={selectedEventType || ''}
                onChange={(value) => setSelectedEventType(value || null)}
                clearable
              />
              <Select
                placeholder="All Statuses"
                data={[
                  { value: '', label: 'All Statuses' },
                  { value: 'active', label: 'Active' },
                  { value: 'inactive', label: 'Inactive' },
                ]}
                value={selectedStatus || ''}
                onChange={(value) => setSelectedStatus(value || null)}
                clearable
              />
            </Group>
          </Stack>
        </Paper>

        {/* Webhooks Table */}
        <Paper withBorder>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Event Type</Table.Th>
                <Table.Th>URL</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Success Rate</Table.Th>
                <Table.Th>Total Triggers</Table.Th>
                <Table.Th>Last Triggered</Table.Th>
                <Table.Th>Actions</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredWebhooks.length === 0 ? (
                <Table.Tr>
                  <Table.Td colSpan={8} style={{ textAlign: 'center', padding: '2rem' }}>
                    <Text c="dimmed">No webhooks found</Text>
                  </Table.Td>
                </Table.Tr>
              ) : (
                filteredWebhooks.map((webhook) => (
                  <Table.Tr key={webhook.id}>
                    <Table.Td>
                      <Text fw={500}>{webhook.name}</Text>
                      {webhook.description && (
                        <Text size="xs" c="dimmed" truncate style={{ maxWidth: 200 }}>
                          {webhook.description}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="light">{webhook.event_type_display}</Badge>
                    </Table.Td>
                    <Table.Td>
                      <Group gap="xs">
                        <Text size="sm" c="blue" truncate style={{ maxWidth: 250 }}>
                          {webhook.url}
                        </Text>
                        <ActionIcon
                          size="sm"
                          variant="subtle"
                          onClick={() => window.open(webhook.url, '_blank')}
                        >
                          <IconExternalLink size={16} />
                        </ActionIcon>
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      {webhook.is_active ? (
                        <Badge color="green">Active</Badge>
                      ) : (
                        <Badge color="gray" variant="light">
                          Inactive
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {webhook.success_rate !== null ? (
                        <Badge color={getSuccessRateColor(webhook.success_rate)}>
                          {webhook.success_rate.toFixed(1)}%
                        </Badge>
                      ) : (
                        <Text size="sm" c="dimmed">
                          No data
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{webhook.total_triggers}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs" c="dimmed">
                        {formatDate(webhook.last_triggered_at)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Group gap="xs">
                        <ActionIcon
                          variant="subtle"
                          onClick={() => navigate(`/settings/webhooks/${webhook.id}`)}
                          title="View Details"
                        >
                          <IconEye size={16} />
                        </ActionIcon>
                        <ActionIcon
                          variant="subtle"
                          onClick={() => navigate(`/settings/webhooks/${webhook.id}/edit`)}
                          title="Edit"
                        >
                          <IconEdit size={16} />
                        </ActionIcon>
                        <ActionIcon
                          variant="subtle"
                          color="blue"
                          onClick={() => navigate(`/settings/webhooks/${webhook.id}?test=true`)}
                          title="Test"
                        >
                          <IconTestPipe size={16} />
                        </ActionIcon>
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          onClick={() => handleDelete(webhook.id, webhook.name)}
                          title="Delete"
                        >
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))
              )}
            </Table.Tbody>
          </Table>
        </Paper>
      </Stack>
    </div>
  );
};

export default WebhookListPage;
