/**
 * Webhook Detail Page
 * Comprehensive detail view with statistics and test functionality
 */
import {
    ActionIcon,
    Alert,
    Badge,
    Button,
    Card,
    Group,
    Modal,
    Paper,
    Stack,
    Table,
    Tabs,
    Text,
    Title,
} from '@mantine/core';
import { IconEdit, IconExternalLink, IconTestPipe, IconTrash } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { webhooksAPI } from '../services/api';
import '../styles/WebhookDetailPage.css';
import { Webhook, WebhookTestResult } from '../types';
import { confirmDelete, showError } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const WebhookDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const showTestModal = searchParams.get('test') === 'true';

  const [webhook, setWebhook] = useState<Webhook | null>(null);
  const [loading, setLoading] = useState(true);
  const [testResult, setTestResult] = useState<WebhookTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [activeTab, setActiveTab] = useState<string | null>('overview');
  const [testModalOpen, setTestModalOpen] = useState(false);

  useEffect(() => {
    if (id) {
      loadWebhook();
    }
  }, [id]);

  useEffect(() => {
    if (showTestModal) {
      setTestModalOpen(true);
      setSearchParams({});
    }
  }, [showTestModal, setSearchParams]);

  const loadWebhook = async () => {
    if (!id) return;

    try {
      setLoading(true);
      const response = await webhooksAPI.getWebhook(Number(id));
      setWebhook(response.data);
    } catch (err) {
      console.error('Error loading webhook details:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleTest = async () => {
    if (!id) return;

    try {
      setTesting(true);
      setTestResult(null);
      const response = await webhooksAPI.testWebhook(Number(id));
      const initialResult = response.data;
      setTestResult(initialResult);
      setTestModalOpen(true);

      // If we got a task_id, poll for the result
      if (initialResult.task_id && initialResult.task_status === 'PENDING') {
        pollTaskStatus(initialResult.task_id);
      }
    } catch (err: any) {
      console.error('Error testing webhook:', err);
      setTestResult({
        webhook_id: Number(id),
        webhook_name: webhook?.name || '',
        success: false,
        error_message: extractErrorMessage(err, 'Failed to test webhook'),
        tested_at: new Date().toISOString(),
      });
      setTestModalOpen(true);
    } finally {
      setTesting(false);
    }
  };

  const pollTaskStatus = async (taskId: string, maxAttempts: number = 30) => {
    let attempts = 0;
    const pollInterval = 1000; // Poll every second

    const poll = async () => {
      if (attempts >= maxAttempts) {
        setTestResult((prev) => ({
          ...prev!,
          task_status: 'FAILURE',
          success: false,
          error_message: 'Task status check timed out after 30 seconds',
        }));
        return;
      }

      try {
        const response = await webhooksAPI.getTestStatus(taskId);
        const statusResult = response.data;
        setTestResult(statusResult);

        // Continue polling if task is still pending
        if (statusResult.task_status === 'PENDING') {
          attempts++;
          setTimeout(poll, pollInterval);
        }
        // Task completed (success or failure), stop polling
      } catch (err: any) {
        console.error('Error polling task status:', err);
        setTestResult((prev) => ({
          ...prev!,
          task_status: 'FAILURE',
          success: false,
          error_message: `Failed to check task status: ${err.response?.data?.error || err.message}`,
        }));
      }
    };

    // Start polling after a short delay
    setTimeout(poll, pollInterval);
  };

  const handleDelete = () => {
    if (!id || !webhook) return;

    confirmDelete(`Are you sure you want to delete webhook "${webhook.name}"?`, async () => {
      try {
        await webhooksAPI.deleteWebhook(Number(id));
        navigate('/settings/webhooks');
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

  const formatDate = (dateString: string | null | undefined) => {
    if (!dateString) return 'Never';
    const date = new Date(dateString);
    return date.toLocaleString();
  };

  if (loading) {
    return (
      <Paper p="md">
        <Text>Loading...</Text>
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
    <div className="webhook-detail-page">
      <Stack gap="md">
        {/* Header */}
        <Group justify="space-between">
          <div>
            <Title order={2}>{webhook.name}</Title>
            <Text size="sm" c="dimmed">
              {webhook.event_type_display}
            </Text>
          </div>
          <Group>
            <Button
              leftSection={<IconTestPipe size={16} />}
              onClick={handleTest}
              loading={testing}
              variant="light"
            >
              Test Webhook
            </Button>
            <Button
              leftSection={<IconEdit size={16} />}
              onClick={() => navigate(`/settings/webhooks/${id}/edit`)}
            >
              Edit
            </Button>
            <Button
              leftSection={<IconTrash size={16} />}
              color="red"
              variant="light"
              onClick={handleDelete}
            >
              Delete
            </Button>
          </Group>
        </Group>

        {/* Status Badges */}
        <Group>
          {webhook.is_active ? (
            <Badge color="green" size="lg">Active</Badge>
          ) : (
            <Badge color="gray" variant="light" size="lg">Inactive</Badge>
          )}
          {webhook.success_rate !== null && (
            <Badge color={getSuccessRateColor(webhook.success_rate)} size="lg">
              {webhook.success_rate.toFixed(1)}% Success Rate
            </Badge>
          )}
        </Group>

        {/* Tabs */}
        <Tabs value={activeTab} onChange={setActiveTab}>
          <Tabs.List>
            <Tabs.Tab value="overview">Overview</Tabs.Tab>
            <Tabs.Tab value="statistics">Statistics</Tabs.Tab>
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
                      <Text fw={500}>{webhook.name}</Text>
                    </div>
                    {webhook.description && (
                      <div>
                        <Text size="sm" c="dimmed">
                          Description
                        </Text>
                        <Text>{webhook.description}</Text>
                      </div>
                    )}
                    <div>
                      <Text size="sm" c="dimmed">
                        Event Type
                      </Text>
                      <Badge variant="light">{webhook.event_type_display}</Badge>
                    </div>
                    <div>
                      <Text size="sm" c="dimmed">
                        URL
                      </Text>
                      <Group gap="xs">
                        <Text
                          size="sm"
                          c="blue"
                          style={{ cursor: 'pointer', wordBreak: 'break-all' }}
                          onClick={() => window.open(webhook.url, '_blank')}
                        >
                          {webhook.url}
                        </Text>
                        <ActionIcon
                          size="sm"
                          variant="subtle"
                          onClick={() => window.open(webhook.url, '_blank')}
                        >
                          <IconExternalLink size={14} />
                        </ActionIcon>
                      </Group>
                    </div>
                    <div>
                      <Text size="sm" c="dimmed">
                        Status
                      </Text>
                      {webhook.is_active ? (
                        <Badge color="green">Active</Badge>
                      ) : (
                        <Badge color="gray" variant="light">
                          Inactive
                        </Badge>
                      )}
                    </div>
                    {webhook.headers && Object.keys(webhook.headers).length > 0 && (
                      <div>
                        <Text size="sm" c="dimmed">
                          Custom Headers
                        </Text>
                        <Paper p="xs" withBorder style={{ backgroundColor: '#f8f9fa' }}>
                          <Text size="xs" style={{ fontFamily: 'monospace' }}>
                            {JSON.stringify(webhook.headers, null, 2)}
                          </Text>
                        </Paper>
                      </div>
                    )}
                  </Stack>
                </Card>

                {/* Quick Stats */}
                <Card withBorder p="md">
                  <Stack gap="md">
                    <Title order={4}>Quick Statistics</Title>
                    <div>
                      <Text size="sm" c="dimmed">
                        Total Triggers
                      </Text>
                      <Text size="xl" fw={700}>
                        {webhook.total_triggers}
                      </Text>
                    </div>
                    <div>
                      <Text size="sm" c="dimmed">
                        Success Count
                      </Text>
                      <Text size="xl" fw={700} c="green">
                        {webhook.success_count}
                      </Text>
                    </div>
                    <div>
                      <Text size="sm" c="dimmed">
                        Failure Count
                      </Text>
                      <Text size="xl" fw={700} c="red">
                        {webhook.failure_count}
                      </Text>
                    </div>
                    <div>
                      <Text size="sm" c="dimmed">
                        Success Rate
                      </Text>
                      <Text size="xl" fw={700} c={getSuccessRateColor(webhook.success_rate)}>
                        {webhook.success_rate !== null ? `${webhook.success_rate.toFixed(1)}%` : 'N/A'}
                      </Text>
                    </div>
                  </Stack>
                </Card>
              </Group>

              {/* Last Error */}
              {webhook.last_error && (
                <Card withBorder p="md" style={{ borderColor: '#fa5252' }}>
                  <Stack gap="md">
                    <Title order={4} c="red">Last Error</Title>
                    <Text size="sm" style={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
                      {webhook.last_error}
                    </Text>
                    <Text size="xs" c="dimmed">
                      Last triggered: {formatDate(webhook.last_triggered_at)}
                    </Text>
                  </Stack>
                </Card>
              )}
            </Stack>
          </Tabs.Panel>

          {/* Statistics Tab */}
          <Tabs.Panel value="statistics" pt="md">
            <Card withBorder p="md">
              <Stack gap="md">
                <Title order={4}>Delivery Statistics</Title>
                <Text size="sm" c="dimmed">
                  Note: Detailed per-delivery logs are not available. Only aggregate statistics are tracked.
                </Text>
                <Table>
                  <Table.Tbody>
                    <Table.Tr>
                      <Table.Td>
                        <Text fw={500}>Total Triggers</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text>{webhook.total_triggers}</Text>
                      </Table.Td>
                    </Table.Tr>
                    <Table.Tr>
                      <Table.Td>
                        <Text fw={500}>Successful Deliveries</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text c="green">{webhook.success_count}</Text>
                      </Table.Td>
                    </Table.Tr>
                    <Table.Tr>
                      <Table.Td>
                        <Text fw={500}>Failed Deliveries</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text c="red">{webhook.failure_count}</Text>
                      </Table.Td>
                    </Table.Tr>
                    <Table.Tr>
                      <Table.Td>
                        <Text fw={500}>Success Rate</Text>
                      </Table.Td>
                      <Table.Td>
                        <Badge color={getSuccessRateColor(webhook.success_rate)}>
                          {webhook.success_rate !== null ? `${webhook.success_rate.toFixed(2)}%` : 'N/A'}
                        </Badge>
                      </Table.Td>
                    </Table.Tr>
                    <Table.Tr>
                      <Table.Td>
                        <Text fw={500}>Last Triggered</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm">{formatDate(webhook.last_triggered_at)}</Text>
                      </Table.Td>
                    </Table.Tr>
                    {webhook.last_error && (
                      <Table.Tr>
                        <Table.Td>
                          <Text fw={500}>Last Error</Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="sm" c="red" style={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
                            {webhook.last_error}
                          </Text>
                        </Table.Td>
                      </Table.Tr>
                    )}
                  </Table.Tbody>
                </Table>
              </Stack>
            </Card>
          </Tabs.Panel>
        </Tabs>
      </Stack>

      {/* Test Result Modal */}
      <Modal
        opened={testModalOpen}
        onClose={() => setTestModalOpen(false)}
        title="Webhook Test Result"
        size="lg"
      >
        {testResult && (
          <Stack gap="md">
            {testResult.task_status === 'PENDING' ? (
              <Alert color="blue" title="Testing Webhook">
                <Text size="sm">The webhook test is in progress. Please wait...</Text>
              </Alert>
            ) : testResult.success ? (
              <Alert color="green" title="Test Successful">
                <Text size="sm">The webhook was delivered successfully.</Text>
              </Alert>
            ) : (
              <Alert color="red" title="Test Failed">
                <Text size="sm">{testResult.error_message || 'The webhook delivery failed.'}</Text>
              </Alert>
            )}

            <Table>
              <Table.Tbody>
                {testResult.task_status && (
                  <Table.Tr>
                    <Table.Td>
                      <Text fw={500}>Task Status</Text>
                    </Table.Td>
                    <Table.Td>
                      <Badge
                        color={
                          testResult.task_status === 'SUCCESS'
                            ? 'green'
                            : testResult.task_status === 'FAILURE'
                            ? 'red'
                            : 'blue'
                        }
                      >
                        {testResult.task_status}
                      </Badge>
                    </Table.Td>
                  </Table.Tr>
                )}
                <Table.Tr>
                  <Table.Td>
                    <Text fw={500}>Status</Text>
                  </Table.Td>
                  <Table.Td>
                    {testResult.success === null ? (
                      <Badge color="blue">Pending</Badge>
                    ) : (
                      <Badge color={testResult.success ? 'green' : 'red'}>
                        {testResult.success ? 'Success' : 'Failed'}
                      </Badge>
                    )}
                  </Table.Td>
                </Table.Tr>
                {testResult.status_code && (
                  <Table.Tr>
                    <Table.Td>
                      <Text fw={500}>HTTP Status Code</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text>{testResult.status_code}</Text>
                    </Table.Td>
                  </Table.Tr>
                )}
                {testResult.response_time_ms && (
                  <Table.Tr>
                    <Table.Td>
                      <Text fw={500}>Response Time</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text>{testResult.response_time_ms.toFixed(2)} ms</Text>
                    </Table.Td>
                  </Table.Tr>
                )}
                {testResult.error_message && (
                  <Table.Tr>
                    <Table.Td>
                      <Text fw={500}>Error Message</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c="red" style={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
                        {testResult.error_message}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                )}
                {testResult.response_body && (
                  <Table.Tr>
                    <Table.Td colSpan={2}>
                      <Text fw={500} mb="xs">Response Body</Text>
                      <Paper p="xs" withBorder style={{ backgroundColor: '#f8f9fa', maxHeight: 200, overflow: 'auto' }}>
                        <Text size="xs" style={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
                          {testResult.response_body}
                        </Text>
                      </Paper>
                    </Table.Td>
                  </Table.Tr>
                )}
                <Table.Tr>
                  <Table.Td>
                    <Text fw={500}>Tested At</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{formatDate(testResult.tested_at)}</Text>
                  </Table.Td>
                </Table.Tr>
              </Table.Tbody>
            </Table>
          </Stack>
        )}
      </Modal>
    </div>
  );
};

export default WebhookDetailPage;
