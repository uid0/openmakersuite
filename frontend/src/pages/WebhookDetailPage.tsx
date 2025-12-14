/**
 * Webhook Detail Page
 * View webhook details, statistics, and test delivery
 */
import {
    ActionIcon,
    Alert,
    Badge,
    Button,
    Card,
    Code,
    Group,
    Paper,
    Stack,
    Table,
    Text,
    Title,
} from '@mantine/core';
import { IconAlertCircle, IconCheck, IconEdit, IconTestPipe, IconTrash, IconX } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { webhooksAPI } from '../services/api';
import '../styles/WebhookDetailPage.css';
import { WebHook, WebHookTestResult } from '../types';

const WebhookDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [webhook, setWebhook] = useState<WebHook | null>(null);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<WebHookTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (id) {
      loadWebhook();
    }
  }, [id]);

  const loadWebhook = async () => {
    if (!id) return;

    try {
      setLoading(true);
      const response = await webhooksAPI.getWebhook(id);
      setWebhook(response.data);
    } catch (err: any) {
      console.error('Error loading webhook:', err);
      setError(err.response?.data?.detail || 'Failed to load webhook');
    } finally {
      setLoading(false);
    }
  };

  const handleTest = async () => {
    if (!id) return;

    try {
      setTesting(true);
      setTestResult(null);
      const response = await webhooksAPI.testWebhook(id);
      setTestResult(response.data);
      // Reload webhook to update statistics
      loadWebhook();
    } catch (err: any) {
      console.error('Error testing webhook:', err);
      setError(err.response?.data?.detail || 'Failed to test webhook');
    } finally {
      setTesting(false);
    }
  };

  const handleDelete = async () => {
    if (!id || !webhook) return;

    if (!window.confirm(`Are you sure you want to delete webhook "${webhook.name}"?`)) {
      return;
    }

    try {
      await webhooksAPI.deleteWebhook(id);
      navigate('/settings/webhooks');
    } catch (err: any) {
      console.error('Error deleting webhook:', err);
      alert(err.response?.data?.detail || 'Failed to delete webhook');
    }
  };

  const getStatusBadge = (isActive: boolean) => {
    return (
      <Badge color={isActive ? 'green' : 'gray'} variant="light" size="lg">
        {isActive ? 'Active' : 'Inactive'}
      </Badge>
    );
  };

  const getSuccessRateBadge = (successRate: number | null) => {
    if (successRate === null) {
      return (
        <Badge color="gray" variant="light" size="lg">
          No data
        </Badge>
      );
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
      <Badge color={color} variant="light" size="lg">
        {successRate.toFixed(1)}%
      </Badge>
    );
  };

  if (loading) {
    return (
      <Paper p="md">
        <Text>Loading webhook...</Text>
      </Paper>
    );
  }

  if (error && !webhook) {
    return (
      <Paper p="md">
        <Alert icon={<IconAlertCircle size={16} />} title="Error" color="red">
          {error}
        </Alert>
      </Paper>
    );
  }

  if (!webhook) {
    return (
      <Paper p="md">
        <Text>Webhook not found</Text>
      </Paper>
    );
  }

  return (
    <Stack gap="md" className="webhook-detail-page">
      {/* Header */}
      <Group justify="space-between">
        <div>
          <Title order={2}>{webhook.name}</Title>
          <Text c="dimmed" size="sm" mt={4}>
            {webhook.event_type_display}
          </Text>
        </div>
        <Group>
          {getStatusBadge(webhook.is_active)}
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
            onClick={handleDelete}
            title="Delete"
          >
            <IconTrash size={16} />
          </ActionIcon>
        </Group>
      </Group>

      {/* Configuration */}
      <Paper p="md" withBorder>
        <Title order={3} mb="md">
          Configuration
        </Title>
        <Table>
          <Table.Tbody>
            <Table.Tr>
              <Table.Td style={{ fontWeight: 500, width: '200px' }}>Name</Table.Td>
              <Table.Td>{webhook.name}</Table.Td>
            </Table.Tr>
            {webhook.description && (
              <Table.Tr>
                <Table.Td style={{ fontWeight: 500 }}>Description</Table.Td>
                <Table.Td>{webhook.description}</Table.Td>
              </Table.Tr>
            )}
            <Table.Tr>
              <Table.Td style={{ fontWeight: 500 }}>Event Type</Table.Td>
              <Table.Td>
                <Badge variant="light">{webhook.event_type_display}</Badge>
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td style={{ fontWeight: 500 }}>URL</Table.Td>
              <Table.Td>
                <Code>{webhook.url}</Code>
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td style={{ fontWeight: 500 }}>Status</Table.Td>
              <Table.Td>{getStatusBadge(webhook.is_active)}</Table.Td>
            </Table.Tr>
            {webhook.headers && Object.keys(webhook.headers).length > 0 && (
              <Table.Tr>
                <Table.Td style={{ fontWeight: 500 }}>Custom Headers</Table.Td>
                <Table.Td>
                  <Code block>{JSON.stringify(webhook.headers, null, 2)}</Code>
                </Table.Td>
              </Table.Tr>
            )}
            <Table.Tr>
              <Table.Td style={{ fontWeight: 500 }}>Created</Table.Td>
              <Table.Td>{new Date(webhook.created_at).toLocaleString()}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td style={{ fontWeight: 500 }}>Last Updated</Table.Td>
              <Table.Td>{new Date(webhook.updated_at).toLocaleString()}</Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
      </Paper>

      {/* Statistics */}
      <Paper p="md" withBorder>
        <Title order={3} mb="md">
          Delivery Statistics
        </Title>
        <div className="statistics-grid">
          <Card withBorder p="md">
            <Text size="sm" c="dimmed" mb={4}>
              Success Rate
            </Text>
            {getSuccessRateBadge(webhook.success_rate)}
          </Card>
          <Card withBorder p="md">
            <Text size="sm" c="dimmed" mb={4}>
              Total Triggers
            </Text>
            <Text size="xl" fw={700}>
              {webhook.total_triggers}
            </Text>
          </Card>
          <Card withBorder p="md">
            <Text size="sm" c="dimmed" mb={4}>
              Successful
            </Text>
            <Text size="xl" fw={700} c="green">
              {webhook.success_count}
            </Text>
          </Card>
          <Card withBorder p="md">
            <Text size="sm" c="dimmed" mb={4}>
              Failed
            </Text>
            <Text size="xl" fw={700} c="red">
              {webhook.failure_count}
            </Text>
          </Card>
        </div>

        <Table mt="md">
          <Table.Tbody>
            <Table.Tr>
              <Table.Td style={{ fontWeight: 500, width: '200px' }}>Last Triggered</Table.Td>
              <Table.Td>
                {webhook.last_triggered_at ? (
                  new Date(webhook.last_triggered_at).toLocaleString()
                ) : (
                  <Text c="dimmed">Never</Text>
                )}
              </Table.Td>
            </Table.Tr>
            {webhook.last_error && (
              <Table.Tr>
                <Table.Td style={{ fontWeight: 500 }}>Last Error</Table.Td>
                <Table.Td>
                  <Alert icon={<IconAlertCircle size={16} />} color="red" variant="light">
                    <Code block>{webhook.last_error}</Code>
                  </Alert>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Paper>

      {/* Test Webhook */}
      <Paper p="md" withBorder>
        <Group justify="space-between" mb="md">
          <Title order={3}>Test Delivery</Title>
          <Button
            leftSection={<IconTestPipe size={16} />}
            onClick={handleTest}
            loading={testing}
          >
            Test Webhook
          </Button>
        </Group>
        <Text size="sm" c="dimmed" mb="md">
          Send a test payload to verify the webhook is working correctly.
        </Text>

        {testResult && (
          <Alert
            icon={testResult.success ? <IconCheck size={16} /> : <IconX size={16} />}
            title={testResult.success ? 'Test Successful' : 'Test Failed'}
            color={testResult.success ? 'green' : 'red'}
            mt="md"
          >
            <Stack gap="xs">
              {testResult.status_code && (
                <Text size="sm">
                  <strong>Status Code:</strong> {testResult.status_code}
                </Text>
              )}
              {testResult.response_time_ms && (
                <Text size="sm">
                  <strong>Response Time:</strong> {testResult.response_time_ms}ms
                </Text>
              )}
              {testResult.error_message && (
                <Text size="sm">
                  <strong>Error:</strong> {testResult.error_message}
                </Text>
              )}
              {testResult.response_body && (
                <div>
                  <Text size="sm" fw={500} mb={4}>
                    Response Body:
                  </Text>
                  <Code block>{testResult.response_body}</Code>
                </div>
              )}
              <Text size="xs" c="dimmed">
                Tested at: {new Date(testResult.tested_at).toLocaleString()}
              </Text>
            </Stack>
          </Alert>
        )}
      </Paper>

      {/* Delivery Logs Note */}
      <Paper p="md" withBorder>
        <Alert icon={<IconAlertCircle size={16} />} title="Delivery Logs" color="blue" variant="light">
          <Text size="sm">
            Detailed per-delivery logs are not currently available. The statistics above show aggregate
            information about webhook deliveries. For detailed logging, a backend enhancement would be
            required to track individual delivery attempts.
          </Text>
        </Alert>
      </Paper>
    </Stack>
  );
};

export default WebhookDetailPage;
