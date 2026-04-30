import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Card,
  Checkbox,
  Container,
  FileButton,
  Group,
  Image,
  Loader,
  Select,
  Stack,
  Text,
  Textarea,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconCamera,
  IconCheck,
  IconClipboard,
  IconDownload,
  IconFileText,
  IconPhoto,
  IconTag,
  IconUpload,
} from '@tabler/icons-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { workOrderAPI } from '../services/api';
import { WorkOrder, WorkOrderStatus } from '../types';
import { formatDateOnly } from '../utils/dates';

const STATUS_OPTIONS = [
  { value: 'open', label: 'Open' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'completed', label: 'Completed' },
];

const STATUS_COLORS: Record<WorkOrderStatus, string> = {
  open: 'blue',
  in_progress: 'yellow',
  blocked: 'red',
  completed: 'green',
};

const WorkOrderPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [workOrder, setWorkOrder] = useState<WorkOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [savingStatus, setSavingStatus] = useState(false);
  const [togglingTask, setTogglingTask] = useState<string | null>(null);
  const [togglingMaterial, setTogglingMaterial] = useState<string | null>(null);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [notes, setNotes] = useState('');
  const [savingNotes, setSavingNotes] = useState(false);
  const resetPhotoRef = useRef<() => void>(null);

  const loadWorkOrder = useCallback(async () => {
    if (!id) return;
    try {
      const res = await workOrderAPI.getWorkOrder(id);
      setWorkOrder(res.data);
      setNotes(res.data.notes || '');
    } catch {
      notifications.show({
        title: 'Error',
        message: 'Failed to load work order.',
        color: 'red',
      });
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    loadWorkOrder();
  }, [loadWorkOrder]);

  const handleStatusChange = async (newStatus: string | null) => {
    if (!workOrder || !newStatus) return;
    setSavingStatus(true);
    try {
      const res = await workOrderAPI.updateWorkOrder(workOrder.id, {
        status: newStatus as WorkOrderStatus,
      });
      setWorkOrder(res.data);
      notifications.show({
        title: 'Status Updated',
        message: `Work order status set to ${newStatus.replace('_', ' ')}.`,
        color: 'green',
        icon: <IconCheck size={16} />,
      });
    } catch {
      notifications.show({
        title: 'Error',
        message: 'Failed to update status.',
        color: 'red',
      });
    } finally {
      setSavingStatus(false);
    }
  };

  const handleToggleTask = async (taskCompletionId: string, isCompleted: boolean) => {
    if (!workOrder) return;
    setTogglingTask(taskCompletionId);
    try {
      await workOrderAPI.completeTask(workOrder.id, taskCompletionId, { is_completed: isCompleted });
      await loadWorkOrder();
    } catch {
      notifications.show({
        title: 'Error',
        message: 'Failed to update task.',
        color: 'red',
      });
    } finally {
      setTogglingTask(null);
    }
  };

  const handleToggleMaterial = async (materialUsageId: string, wasUsed: boolean) => {
    if (!workOrder) return;
    setTogglingMaterial(materialUsageId);
    try {
      await workOrderAPI.toggleMaterial(workOrder.id, materialUsageId, wasUsed);
      await loadWorkOrder();
    } catch {
      notifications.show({
        title: 'Error',
        message: 'Failed to update material.',
        color: 'red',
      });
    } finally {
      setTogglingMaterial(null);
    }
  };

  const handleSaveNotes = async () => {
    if (!workOrder) return;
    setSavingNotes(true);
    try {
      const res = await workOrderAPI.updateWorkOrder(workOrder.id, { notes });
      setWorkOrder(res.data);
      notifications.show({
        title: 'Notes Saved',
        message: 'Work order notes updated.',
        color: 'green',
        icon: <IconCheck size={16} />,
      });
    } catch {
      notifications.show({
        title: 'Error',
        message: 'Failed to save notes.',
        color: 'red',
      });
    } finally {
      setSavingNotes(false);
    }
  };

  const handlePhotoUpload = async (file: File | null) => {
    if (!file || !workOrder) return;
    setUploadingPhoto(true);
    try {
      const formData = new FormData();
      formData.append('image', file);
      formData.append('work_order', workOrder.id);
      await workOrderAPI.addPhoto(workOrder.id, formData);
      await loadWorkOrder();
      notifications.show({
        title: 'Photo Uploaded',
        message: 'Photo added to work order.',
        color: 'green',
        icon: <IconCheck size={16} />,
      });
    } catch {
      notifications.show({
        title: 'Error',
        message: 'Failed to upload photo.',
        color: 'red',
      });
    } finally {
      setUploadingPhoto(false);
      resetPhotoRef.current?.();
    }
  };

  if (loading) {
    return (
      <Container py="xl">
        <Group justify="center">
          <Loader />
          <Text c="dimmed">Loading work order…</Text>
        </Group>
      </Container>
    );
  }

  if (!workOrder) {
    return (
      <Container py="xl">
        <Text c="red">Work order not found.</Text>
        <Button mt="sm" variant="default" onClick={() => navigate('/maintenance/dashboard')}>
          Back to Dashboard
        </Button>
      </Container>
    );
  }

  const completedTasks = workOrder.task_completions.filter((t) => t.is_completed).length;
  const totalTasks = workOrder.task_completions.length;
  const allTasksDone = totalTasks > 0 && completedTasks === totalTasks;

  return (
    <Container size="sm" py="md">
      {/* Header */}
      <Card withBorder p="md" radius="md" mb="md">
        <Group justify="space-between" mb="xs" wrap="nowrap">
          <Box style={{ flex: 1, minWidth: 0 }}>
            <Group gap="xs" mb={4}>
              <Text fw={700} size="lg">{workOrder.short_id}</Text>
              <Badge color={STATUS_COLORS[workOrder.status]} size="md">
                {workOrder.status.replace('_', ' ')}
              </Badge>
              {workOrder.is_overdue && (
                <Badge color="red" variant="filled" size="sm">
                  <Group gap={4}>
                    <IconAlertTriangle size={12} />
                    Overdue
                  </Group>
                </Badge>
              )}
            </Group>
            <Text fw={600} size="sm" truncate>{workOrder.maintenance_item_title}</Text>
            <Text size="xs" c="dimmed">
              {workOrder.asset_name}
              {workOrder.asset_tag && ` · ${workOrder.asset_tag}`}
            </Text>
            {workOrder.due_date && (
              <Text size="xs" c="dimmed">
                Due: {formatDateOnly(workOrder.due_date, undefined, '')}
              </Text>
            )}
          </Box>
          <Stack gap="xs">
            <Tooltip label="Print PDF">
              <ActionIcon
                variant="light"
                color="gray"
                size="lg"
                component="a"
                href={workOrderAPI.getPdfUrl(workOrder.id)}
                target="_blank"
                rel="noopener noreferrer"
              >
                <IconFileText size={20} />
              </ActionIcon>
            </Tooltip>
          </Stack>
        </Group>

        {/* Status selector */}
        <Select
          label="Status"
          data={STATUS_OPTIONS}
          value={workOrder.status}
          onChange={handleStatusChange}
          disabled={savingStatus}
          size="md"
          mt="xs"
        />
      </Card>

      {/* Task Steps */}
      {workOrder.task_completions.length > 0 && (
        <Card withBorder p="md" radius="md" mb="md">
          <Group mb="sm" justify="space-between">
            <Group gap="xs">
              <IconClipboard size={18} />
              <Title order={5}>Task Steps</Title>
            </Group>
            <Text size="sm" c={allTasksDone ? 'green' : 'dimmed'} fw={600}>
              {completedTasks}/{totalTasks}
              {allTasksDone && <IconCheck size={14} style={{ marginLeft: 4 }} />}
            </Text>
          </Group>
          <Stack gap="xs">
            {workOrder.task_completions.map((tc) => (
              <Box
                key={tc.id}
                p="sm"
                style={{
                  borderRadius: 8,
                  backgroundColor: tc.is_completed ? '#f0fff4' : '#f8f9fa',
                  border: `1px solid ${tc.is_completed ? '#69db7c' : '#dee2e6'}`,
                  opacity: togglingTask === tc.id ? 0.6 : 1,
                }}
              >
                <Group gap="md" align="flex-start" wrap="nowrap">
                  <Checkbox
                    checked={tc.is_completed}
                    onChange={(e) => handleToggleTask(tc.id, e.currentTarget.checked)}
                    disabled={togglingTask === tc.id}
                    size="lg"
                    mt={2}
                  />
                  <Box style={{ flex: 1 }}>
                    <Group gap="xs">
                      <Text
                        fw={tc.is_completed ? 400 : 600}
                        size="md"
                        td={tc.is_completed ? 'line-through' : 'none'}
                        c={tc.is_completed ? 'dimmed' : 'inherit'}
                      >
                        {tc.task_title}
                      </Text>
                      {tc.is_required && !tc.is_completed && (
                        <Badge color="red" size="xs" variant="light">Required</Badge>
                      )}
                    </Group>
                    {tc.is_completed && tc.completed_at && (
                      <Text size="xs" c="green">
                        ✓ {tc.completed_by_name || 'Done'} · {new Date(tc.completed_at).toLocaleString()}
                      </Text>
                    )}
                    {tc.notes && (
                      <Text size="xs" c="dimmed" mt={2} style={{ whiteSpace: 'pre-wrap' }}>
                        {tc.notes}
                      </Text>
                    )}
                  </Box>
                </Group>
              </Box>
            ))}
          </Stack>
        </Card>
      )}

      {/* Uploaded PDFs */}
      {workOrder.submissions && workOrder.submissions.length > 0 && (
        <Card withBorder p="md" radius="md" mb="md">
          <Group mb="sm" gap="xs">
            <IconUpload size={18} />
            <Title order={5}>Uploaded PDFs</Title>
            <Badge color="gray" size="sm">{workOrder.submissions.length}</Badge>
          </Group>
          <Stack gap="xs">
            {workOrder.submissions.map((sub) => (
              <Box
                key={sub.id}
                p="sm"
                style={{
                  borderRadius: 8,
                  backgroundColor: '#f8f9fa',
                  border: '1px solid #dee2e6',
                }}
              >
                <Group justify="space-between" wrap="nowrap" gap="sm">
                  <Box style={{ flex: 1, minWidth: 0 }}>
                    <Group gap="xs">
                      <Text fw={600} size="sm" truncate>
                        {sub.subject || (sub.source === 'manual' ? 'Manual upload' : 'Email submission')}
                      </Text>
                      <Badge size="xs" variant="light" color={sub.source === 'manual' ? 'blue' : 'grape'}>
                        {sub.source === 'manual' ? 'manual' : 'email'}
                      </Badge>
                      <Badge
                        size="xs"
                        variant="light"
                        color={sub.status === 'applied' ? 'green' : sub.status === 'failed' ? 'red' : 'yellow'}
                      >
                        {sub.status}
                      </Badge>
                    </Group>
                    <Text size="xs" c="dimmed">
                      {sub.submitted_by_name || sub.from_email || '—'} ·{' '}
                      {new Date(sub.received_at).toLocaleString()}
                    </Text>
                    {sub.parse_error && (
                      <Text size="xs" c="red" mt={2}>
                        {sub.parse_error}
                      </Text>
                    )}
                  </Box>
                  {sub.pdf_url && (
                    <Tooltip label="Download PDF">
                      <ActionIcon
                        variant="light"
                        color="blue"
                        size="lg"
                        component="a"
                        href={sub.pdf_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        <IconDownload size={18} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                </Group>
              </Box>
            ))}
          </Stack>
        </Card>
      )}

      {/* Materials */}
      {workOrder.material_usage.length > 0 && (
        <Card withBorder p="md" radius="md" mb="md">
          <Group mb="sm" gap="xs">
            <IconTag size={18} />
            <Title order={5}>Materials</Title>
          </Group>
          <Stack gap="xs">
            {workOrder.material_usage.map((mu) => (
              <Box
                key={mu.id}
                p="sm"
                style={{
                  borderRadius: 8,
                  backgroundColor: mu.was_used ? '#f0fff4' : '#f8f9fa',
                  border: `1px solid ${mu.was_used ? '#69db7c' : '#dee2e6'}`,
                  opacity: togglingMaterial === mu.id ? 0.6 : 1,
                }}
              >
                <Group gap="md" wrap="nowrap">
                  <Checkbox
                    checked={mu.was_used}
                    onChange={(e) => handleToggleMaterial(mu.id, e.currentTarget.checked)}
                    disabled={togglingMaterial === mu.id}
                    size="lg"
                    label={
                      <Box>
                        <Text
                          fw={mu.was_used ? 400 : 600}
                          size="md"
                          td={mu.was_used ? 'line-through' : 'none'}
                          c={mu.was_used ? 'dimmed' : 'inherit'}
                        >
                          {mu.material_name}
                        </Text>
                        <Text size="xs" c="dimmed">
                          {mu.quantity_planned}
                          {mu.unit ? ` ${mu.unit}` : ''}
                        </Text>
                      </Box>
                    }
                  />
                </Group>
              </Box>
            ))}
          </Stack>
        </Card>
      )}

      {/* Notes */}
      <Card withBorder p="md" radius="md" mb="md">
        <Title order={5} mb="sm">Notes</Title>
        <Textarea
          value={notes}
          onChange={(e) => setNotes(e.currentTarget.value)}
          placeholder="Add notes about this work order…"
          autosize
          minRows={3}
          maxRows={8}
          mb="sm"
        />
        <Button
          size="sm"
          onClick={handleSaveNotes}
          loading={savingNotes}
          leftSection={<IconCheck size={16} />}
        >
          Save Notes
        </Button>
      </Card>

      {/* Photos */}
      <Card withBorder p="md" radius="md" mb="md">
        <Group mb="sm" justify="space-between">
          <Group gap="xs">
            <IconPhoto size={18} />
            <Title order={5}>Photos</Title>
            {workOrder.photos.length > 0 && (
              <Badge color="gray" size="sm">{workOrder.photos.length}</Badge>
            )}
          </Group>
          <FileButton
            resetRef={resetPhotoRef}
            onChange={handlePhotoUpload}
            accept="image/*"
          >
            {(props) => (
              <Button
                {...props}
                size="sm"
                variant="light"
                leftSection={<IconCamera size={16} />}
                loading={uploadingPhoto}
              >
                Add Photo
              </Button>
            )}
          </FileButton>
        </Group>

        {workOrder.photos.length === 0 ? (
          <Text size="sm" c="dimmed" ta="center" py="sm">
            No photos yet. Tap "Add Photo" to document the work.
          </Text>
        ) : (
          <Group gap="xs" wrap="wrap">
            {workOrder.photos.map((photo) => (
              <Box key={photo.id} style={{ position: 'relative' }}>
                <Image
                  src={photo.image_url || photo.image}
                  w={120}
                  h={90}
                  fit="cover"
                  radius="sm"
                  alt={photo.caption || 'Work order photo'}
                  style={{ cursor: 'pointer' }}
                  onClick={() => window.open(photo.image_url || photo.image, '_blank')}
                />
                {photo.caption && (
                  <Text size="xs" c="dimmed" ta="center" mt={2} w={120} truncate>
                    {photo.caption}
                  </Text>
                )}
              </Box>
            ))}
          </Group>
        )}
      </Card>

      {/* Completion CTA */}
      {workOrder.status !== 'completed' && allTasksDone && (
        <Card withBorder p="md" radius="md" mb="md" style={{ borderColor: '#51cf66', backgroundColor: '#f0fff4' }}>
          <Group gap="xs" mb="xs">
            <IconCheck size={18} color="green" />
            <Text fw={600} c="green">All tasks completed!</Text>
          </Group>
          <Text size="sm" c="dimmed" mb="sm">
            Mark this work order as completed to finalize it.
          </Text>
          <Button
            color="green"
            leftSection={<IconCheck size={16} />}
            onClick={() => handleStatusChange('completed')}
            loading={savingStatus}
            fullWidth
            size="md"
          >
            Mark as Completed
          </Button>
        </Card>
      )}

      <Button
        variant="default"
        fullWidth
        onClick={() => navigate('/maintenance/dashboard')}
      >
        Back to Dashboard
      </Button>
    </Container>
  );
};

export default WorkOrderPage;
