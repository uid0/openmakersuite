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
import { IconEdit, IconEye, IconPlus, IconSearch, IconTestPipe, IconTrash } from '@tabler/icons-react';
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { webhooksAPI } from '../services/api';
import '../styles/WebhookListPage.css';
import { WebHook, WebHookEventType } from '../types';

const EVENT_TYPE_OPTIONS: { value: WebHookEventType; label: string }[] = [
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
  const [webhooks, setWebhooks] = useState<WebHook[]>([]);
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
      setWebhooks(response.data.results);
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

    if (selectedStatus) {
      const isActive = selectedStatus === 'active';
      filtered = filtered.filter((webhook) => webhook.is_active === isActive);
    }

    return filtered;
  }, [webhooks, searchTerm, selectedEventType, selectedStatus]);

  const handleDelete = async (id: number, name: string) => {
    if (!window.confirm(`Are you sure you want to delete webhook "${name}"?`)) {
      return;
    }

    try {
      await webhooksAPI.deleteWebhook(id.toString());
      loadWebhooks();
    } catch (err: any) {
      console.error('Error deleting webhook:', err);
      alert(err.response?.data?.detail || 'Failed to delete webhook');
    }
  };

  const handleTest = async (id: number) => {
    try {
      const result = await webhooksAPI.testWebhook(id.toString());
      if (result.data.success) {
        alert(`Test successful! Status: ${result.data.status_code}, Response time: ${result.data.response_time_ms}ms`);
      } else {
        alert(`Test failed: ${result.data.error_message || 'Unknown error'}`);
      }
      loadWebhooks(); // Refresh to update statistics
    } catch (err: any) {
      console.error('Error testing webhook:', err);
      alert(err.response?.data?.detail || 'Failed to test webhook');
    }
  };

  const getStatusBadge = (isActive: boolean) => {
    return (
      <Badge color={isActive ? 'green' : 'gray'} variant="light">
        {isActive ? 'Active' : 'Inactive'}
      </Badge>
    );
  };

  const getSuccessRateBadge = (successRate: number | null) => {
    if (successRate === null) {
      return <Text size="sm" c="dimmed">No data</Text>;
    }

    let color: string;
    if (successRate >= 95) {
      color = 'green';
    } else if (successRate >= 80) {
      color = 'yellow';
    } else {
      color = 'red';
    }

    return (
      <Badge color={color} variant="light">
        {successRate.toFixed(1)}%
      </Badge>
    );
  };

  if (loading) {
    return (
      <Paper p="md">
        <Text>Loading webhooks...</Text>
      </Paper>
    );
  }

  return (
    <Stack gap="md" className="webhook-list-page">
      {/* Header */}
      <Group justify="space-between">
        <div>
          <h1>Webhook Management</h1>
          <Text c="dimmed" size="sm">
            Manage webhook configurations for event notifications
          </Text>
        </div>
        <Button
          leftSection={<IconPlus size={16} />}
          onClick={() => navigate('/settings/webhooks/new')}
        >
          Create Webhook
        </Button>
      </Group>

      {/* Filters */}
      <Paper p="md" withBorder>
        <Group gap="md">
          <TextInput
            placeholder="Search webhooks..."
            leftSection={<IconSearch size={16} />}
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.currentTarget.value)}
            style={{ flex: 1 }}
          />
          <Select
            placeholder="Event Type"
            data={[
              { value: '', label: 'All Event Types' },
              ...EVENT_TYPE_OPTIONS.map((opt) => ({ value: opt.value, label: opt.label })),
            ]}
            value={selectedEventType || ''}
            onChange={(value) => setSelectedEventType(value || null)}
            clearable
            style={{ width: 200 }}
          />
          <Select
            placeholder="Status"
            data={[
              { value: '', label: 'All Statuses' },
              { value: 'active', label: 'Active' },
              { value: 'inactive', label: 'Inactive' },
            ]}
            value={selectedStatus || ''}
            onChange={(value) => setSelectedStatus(value || null)}
            clearable
            style={{ width: 150 }}
          />
        </Group>
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
                    <div>
                      <Text fw={500}>{webhook.name}</Text>
                      {webhook.description && (
                        <Text size="xs" c="dimmed">
                          {webhook.description}
                        </Text>
                      )}
                    </div>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light">{webhook.event_type_display}</Badge>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {webhook.url}
                    </Text>
                  </Table.Td>
                  <Table.Td>{getStatusBadge(webhook.is_active)}</Table.Td>
                  <Table.Td>{getSuccessRateBadge(webhook.success_rate)}</Table.Td>
                  <Table.Td>
                    <Text size="sm">{webhook.total_triggers}</Text>
                  </Table.Td>
                  <Table.Td>
                    {webhook.last_triggered_at ? (
                      <Text size="sm">
                        {new Date(webhook.last_triggered_at).toLocaleString()}
                      </Text>
                    ) : (
                      <Text size="sm" c="dimmed">Never</Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      <ActionIcon
                        variant="light"
                        color="blue"
                        onClick={() => navigate(`/settings/webhooks/${webhook.id}`)}
                        title="View Details"
                      >
                        <IconEye size={16} />
                      </ActionIcon>
                      <ActionIcon
                        variant="light"
                        color="green"
                        onClick={() => handleTest(webhook.id)}
                        title="Test Webhook"
                      >
                        <IconTestPipe size={16} />
                      </ActionIcon>
                      <ActionIcon
                        variant="light"
                        color="orange"
                        onClick={() => navigate(`/settings/webhooks/${webhook.id}/edit`)}
                        title="Edit"
                      >
                        <IconEdit size={16} />
                      </ActionIcon>
                      <ActionIcon
                        variant="light"
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
  );
};

export default WebhookListPage;
