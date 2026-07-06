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
    Stack,
    Switch,
    Text,
    Title,
} from '@mantine/core';
import { IconAlertCircle } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Controller, Resolver, useForm } from 'react-hook-form';
import { useNavigate, useParams } from 'react-router-dom';
import { FormInput } from '../components/forms/FormInput';
import { FormLayout } from '../components/forms/FormLayout';
import { FormSelect } from '../components/forms/FormSelect';
import { FormTextarea } from '../components/forms/FormTextarea';
import WorkspacePage from '../components/landing/WorkspacePage';
import { webhooksAPI } from '../services/api';
import '../styles/WebhookFormPage.css';
import { WebhookFormData, webhookSchema } from '../utils/formSchemas';
import { extractErrorMessage } from '../utils/extractErrorMessage';

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

  const {
    control,
    handleSubmit,
    formState: { errors },
    reset,
    watch,
  } = useForm<WebhookFormData>({
    // v5 resolvers infer the schema's Zod *input* type (fields with `.default()`
    // are optional pre-parse); this form seeds all fields via defaultValues, so
    // its field values are the *output* shape — assert against WebhookFormData.
    resolver: zodResolver(webhookSchema) as Resolver<WebhookFormData>,
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
      const response = await webhooksAPI.getWebhook(Number(id));
      const webhook = response.data;

      reset({
        name: webhook.name,
        description: webhook.description || '',
        url: webhook.url,
        event_type: webhook.event_type,
        is_active: webhook.is_active,
        secret: '', // Don't populate secret for security
        headers: webhook.headers ? JSON.stringify(webhook.headers, null, 2) : '',
      });
    } catch (err: any) {
      console.error('Error loading webhook:', err);
      setError(extractErrorMessage(err, 'Failed to load webhook. Please try again.'));
    } finally {
      setLoading(false);
    }
  };

  const onSubmit = async (data: WebhookFormData) => {
    try {
      setSaving(true);
      setError(null);

      // Transform headers from string to object
      const submitData: any = {
        ...data,
        headers: data.headers && typeof data.headers === 'string' && data.headers.trim()
          ? JSON.parse(data.headers)
          : {},
      };

      // Don't send empty secret
      if (!submitData.secret || submitData.secret.trim() === '') {
        delete submitData.secret;
      }

      if (isEditMode && id) {
        await webhooksAPI.updateWebhook(Number(id), submitData);
      } else {
        await webhooksAPI.createWebhook(submitData);
      }

      navigate('/settings/webhooks');
    } catch (err: any) {
      console.error('Error saving webhook:', err);
      setError(
        extractErrorMessage(err, '') ||
          err.response?.data?.message ||
          'Failed to save webhook. Please try again.'
      );
    } finally {
      setSaving(false);
    }
  };

  const validateJson = (value: string): boolean => {
    if (!value || value.trim() === '') return true;
    try {
      JSON.parse(value);
      return true;
    } catch {
      return false;
    }
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="webhook-form-page"
        hero={{ eyebrow: 'Settings · Webhook', title: 'Webhook', description: 'Loading…' }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading webhook…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      testId="webhook-form-page"
      hero={{
        eyebrow: 'Settings · Webhook',
        title: isEditMode ? 'Edit webhook' : 'New webhook',
        description: isEditMode
          ? 'Update target URL, secret, and event subscriptions.'
          : 'Subscribe an external system to OMS events with a signed payload.',
      }}
    >
      <div className="webhook-form-page">
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
                        placeholder="Webhook name"
                        required
                        error={errors.name?.message}
                      />

                      <FormTextarea
                        name="description"
                        control={control}
                        label="Description"
                        placeholder="Optional description of what this webhook does"
                        error={errors.description?.message}
                        rows={3}
                      />

                      <FormInput
                        name="url"
                        control={control}
                        label="Webhook URL"
                        placeholder="https://example.com/webhook"
                        required
                        error={errors.url?.message}
                        description="The endpoint URL where webhook notifications will be sent"
                      />

                      <FormSelect
                        name="event_type"
                        control={control}
                        label="Event Type"
                        placeholder="Select event type"
                        required
                        data={EVENT_TYPE_OPTIONS}
                        error={errors.event_type?.message}
                        description="The type of event that will trigger this webhook"
                      />

                      <Controller
                        name="is_active"
                        control={control}
                        render={({ field }) => (
                          <Switch
                            label="Active"
                            description="Enable or disable this webhook without deleting it"
                            checked={field.value}
                            onChange={field.onChange}
                          />
                        )}
                      />
                    </>
                  ),
                },
                {
                  title: 'Security & Configuration',
                  children: (
                    <>
                      <FormInput
                        name="secret"
                        control={control}
                        label="Secret Key (Optional)"
                        placeholder="Leave empty to keep current secret"
                        type="password"
                        error={errors.secret?.message}
                        description="Secret key for HMAC signature verification. Leave empty when editing to keep the existing secret."
                      />

                      <Controller
                        name="headers"
                        control={control}
                        render={({ field, fieldState: { error } }) => (
                          <FormTextarea
                            {...field}
                            name="headers"
                            control={control}
                            label="Custom Headers (Optional)"
                            placeholder='{"Authorization": "Bearer token", "X-Custom-Header": "value"}'
                            error={error?.message || (headersValue && !validateJson(headersValue) ? 'Invalid JSON format' : undefined)}
                            description="Custom HTTP headers to send with webhook requests (JSON format)"
                            rows={6}
                          />
                        )}
                      />
                    </>
                  ),
                },
              ]}
            />

            <Group justify="flex-end" mt="xl">
              <Button variant="subtle" onClick={() => navigate(-1)}>
                Cancel
              </Button>
              <Button type="submit" loading={saving}>
                {isEditMode ? 'Save Changes' : 'Create Webhook'}
              </Button>
            </Group>
          </Paper>
        </form>
      </div>
    </WorkspacePage>
  );
};

export default WebhookFormPage;
