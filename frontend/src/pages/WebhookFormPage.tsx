/**
 * Webhook Form Page
 * Create/Edit form for webhooks
 */
import { zodResolver } from '@hookform/resolvers/zod';
import {
    Alert,
    Button,
    Group,
    Paper,
    PasswordInput,
    Stack,
    Switch,
    Text,
    Textarea,
    Title,
} from '@mantine/core';
import { IconAlertCircle } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useNavigate, useParams } from 'react-router-dom';
import { FormInput } from '../components/forms/FormInput';
import { FormLayout } from '../components/forms/FormLayout';
import { FormSelect } from '../components/forms/FormSelect';
import { FormTextarea } from '../components/forms/FormTextarea';
import { webhooksAPI } from '../services/api';
import '../styles/WebhookFormPage.css';
import { WebhookFormData, webhookSchema } from '../utils/formSchemas';

const EVENT_TYPE_OPTIONS = [
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

const WebhookFormPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isEditMode = !!id;

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [headersError, setHeadersError] = useState<string | null>(null);

  const {
    control,
    handleSubmit,
    formState: { errors },
    reset,
    watch,
  } = useForm<WebhookFormData>({
    resolver: zodResolver(webhookSchema),
    defaultValues: {
      name: '',
      description: '',
      url: '',
      event_type: 'reorder_request_created',
      is_active: true,
      secret: '',
      headers: '',
    },
  });

  const headersValue = watch('headers');

  useEffect(() => {
    if (isEditMode) {
      loadWebhook();
    }
  }, [id, isEditMode]);

  const loadWebhook = async () => {
    if (!id) return;

    try {
      setLoading(true);
      const response = await webhooksAPI.getWebhook(id);
      const webhook = response.data;

      // Parse headers if they exist
      let headersString = '';
      if (webhook.headers && Object.keys(webhook.headers).length > 0) {
        try {
          headersString = JSON.stringify(webhook.headers, null, 2);
        } catch (e) {
          headersString = '';
        }
      }

      reset({
        name: webhook.name,
        description: webhook.description || '',
        url: webhook.url,
        event_type: webhook.event_type,
        is_active: webhook.is_active,
        secret: '', // Don't populate secret for security
        headers: headersString,
      });
    } catch (err: any) {
      console.error('Error loading webhook:', err);
      setError(err.response?.data?.detail || 'Failed to load webhook. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const validateHeaders = (headersStr: string): Record<string, string> | null => {
    if (!headersStr || headersStr.trim() === '') {
      return null;
    }

    try {
      const parsed = JSON.parse(headersStr);
      if (typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Headers must be a JSON object');
      }

      // Validate all values are strings
      for (const [key, value] of Object.entries(parsed)) {
        if (typeof value !== 'string') {
          throw new Error(`Header value for "${key}" must be a string`);
        }
      }

      return parsed;
    } catch (e: any) {
      setHeadersError(e.message || 'Invalid JSON format');
      return null;
    }
  };

  const onSubmit = async (data: WebhookFormData) => {
    try {
      setSaving(true);
      setError(null);
      setHeadersError(null);

      // Validate and parse headers
      let headers: Record<string, string> | undefined;
      if (data.headers && data.headers.trim()) {
        const parsed = validateHeaders(data.headers);
        if (parsed === null && headersError) {
          return; // Validation failed
        }
        headers = parsed || undefined;
      }

      const payload: any = {
        name: data.name,
        description: data.description || undefined,
        url: data.url,
        event_type: data.event_type,
        is_active: data.is_active,
      };

      if (data.secret && data.secret.trim()) {
        payload.secret = data.secret;
      }

      if (headers) {
        payload.headers = headers;
      }

      if (isEditMode && id) {
        await webhooksAPI.updateWebhook(id, payload);
      } else {
        await webhooksAPI.createWebhook(payload);
      }

      navigate('/settings/webhooks');
    } catch (err: any) {
      console.error('Error saving webhook:', err);
      setError(
        err.response?.data?.detail ||
          err.response?.data?.message ||
          'Failed to save webhook. Please try again.'
      );
    } finally {
      setSaving(false);
    }
  };

  // Validate headers on change
  useEffect(() => {
    if (headersValue && headersValue.trim()) {
      validateHeaders(headersValue);
    } else {
      setHeadersError(null);
    }
  }, [headersValue]);

  if (loading) {
    return (
      <Stack gap="md">
        <Text>Loading webhook...</Text>
      </Stack>
    );
  }

  return (
    <Stack gap="md" className="webhook-form-page">
      <Title order={2}>{isEditMode ? 'Edit Webhook' : 'Create New Webhook'}</Title>

      {error && (
        <Alert icon={<IconAlertCircle size={16} />} title="Error" color="red">
          {error}
        </Alert>
      )}

      <form onSubmit={handleSubmit(onSubmit)}>
        <Paper p="md" withBorder>
          <FormLayout
            sections={[
              {
                title: 'Basic Information',
                children: (
                  <>
                    <FormInput
                      name="name"
                      control={control}
                      label="Name"
                      placeholder="Webhook name (e.g., Discord Notifications)"
                      required
                      error={errors.name?.message}
                    />

                    <FormTextarea
                      name="description"
                      control={control}
                      label="Description"
                      placeholder="Optional description of what this webhook does"
                      error={errors.description?.message}
                    />

                    <FormSelect
                      name="event_type"
                      control={control}
                      label="Event Type"
                      placeholder="Select event type"
                      required
                      data={EVENT_TYPE_OPTIONS}
                      error={errors.event_type?.message}
                    />

                    <FormInput
                      name="url"
                      control={control}
                      label="Webhook URL"
                      placeholder="https://example.com/webhook"
                      required
                      error={errors.url?.message}
                    />
                  </>
                ),
              },
              {
                title: 'Configuration',
                children: (
                  <>
                    <Controller
                      name="is_active"
                      control={control}
                      render={({ field }) => (
                        <Switch
                          label="Active"
                          description="Enable or disable this webhook"
                          checked={field.value}
                          onChange={field.onChange}
                        />
                      )}
                    />

                    <Controller
                      name="secret"
                      control={control}
                      render={({ field, fieldState: { error } }) => (
                        <PasswordInput
                          label="Secret Key (Optional)"
                          description="HMAC secret for signature verification. Leave empty to disable."
                          placeholder="Enter secret key"
                          value={field.value || ''}
                          onChange={field.onChange}
                          error={error?.message}
                        />
                      )}
                    />

                    <Controller
                      name="headers"
                      control={control}
                      render={({ field, fieldState: { error } }) => (
                        <div>
                          <Textarea
                            label="Custom Headers (Optional)"
                            description='JSON object with custom HTTP headers to send with webhook (e.g., {"Authorization": "Bearer token"})'
                            placeholder='{"Authorization": "Bearer token", "X-Custom-Header": "value"}'
                            minRows={4}
                            value={field.value || ''}
                            onChange={(e) => {
                              field.onChange(e);
                              setHeadersError(null);
                            }}
                            error={headersError || error?.message}
                          />
                          {headersValue && headersValue.trim() && !headersError && (
                            <Text size="xs" c="green" mt={4}>
                              Valid JSON
                            </Text>
                          )}
                        </div>
                      )}
                    />
                  </>
                ),
              },
            ]}
          />

          <Group justify="flex-end" mt="xl">
            <Button variant="subtle" onClick={() => navigate('/settings/webhooks')}>
              Cancel
            </Button>
            <Button type="submit" loading={saving}>
              {isEditMode ? 'Save Changes' : 'Create Webhook'}
            </Button>
          </Group>
        </Paper>
      </form>
    </Stack>
  );
};

export default WebhookFormPage;
