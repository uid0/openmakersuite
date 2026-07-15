import {
  ActionIcon,
  Alert,
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
  Modal,
  Select,
  Stack,
  Text,
  Textarea,
  Title,
  Tooltip,
  UnstyledButton,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconBolt,
  IconCamera,
  IconCheck,
  IconClipboard,
  IconDownload,
  IconFileText,
  IconLock,
  IconPhoto,
  IconRobot,
  IconScan,
  IconTag,
  IconUpload,
} from '@tabler/icons-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { workOrderAPI } from '../services/api';
import { WorkOrder, WorkOrderStatus } from '../types';
import { formatDateOnly } from '../utils/dates';
import { extractErrorMessage } from '../utils/extractErrorMessage';

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

// OMR bead-2: a small warped crop of one scanned mark so the reviewer can
// eyeball the ink. The crop endpoint is authenticated, so we fetch the PNG as
// a blob (Bearer token) and render it from an object URL rather than a bare
// <img src> (which would send no auth header). op-o6rs: click to zoom.
const OmrMarkCrop: React.FC<{
  workOrderId: string;
  submissionId: string;
  targetId: string;
}> = ({ workOrderId, submissionId, targetId }) => {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [zoomed, setZoomed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    workOrderAPI
      .getMarkCrop(workOrderId, submissionId, targetId)
      .then((res) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(res.data as Blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [workOrderId, submissionId, targetId]);

  if (failed) return null;
  if (!url) return <Loader size="xs" />;
  return (
    <>
      <UnstyledButton
        onClick={() => setZoomed(true)}
        aria-label={`Zoom scanned mark ${targetId}`}
        style={{ flexShrink: 0, lineHeight: 0 }}
      >
        <Image
          src={url}
          alt={`Scanned mark ${targetId}`}
          w={72}
          h={44}
          fit="contain"
          radius="sm"
          style={{ border: '1px solid #dee2e6', backgroundColor: '#fff', cursor: 'zoom-in' }}
        />
      </UnstyledButton>
      <Modal
        opened={zoomed}
        onClose={() => setZoomed(false)}
        size="lg"
        title="Scanned mark"
        centered
      >
        <Image src={url} alt={`Scanned mark ${targetId} (enlarged)`} fit="contain" />
      </Modal>
    </>
  );
};

// op-o6rs: the FULL scanned page, so the reviewer verifies the detected marks
// against the actual paper form. Same authed-blob → object-URL pattern as the
// per-mark crop; click to open a full-size lightbox. The getScanImage call is
// wrapped in Promise.resolve so an auto-mocked api (tests) that returns
// undefined degrades to "no image" instead of throwing.
const OmrScanImage: React.FC<{
  workOrderId: string;
  submissionId: string;
}> = ({ workOrderId, submissionId }) => {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [zoomed, setZoomed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    Promise.resolve(workOrderAPI.getScanImage(workOrderId, submissionId))
      .then((res) => {
        if (cancelled) return;
        const blob = (res as { data?: Blob } | undefined)?.data;
        if (!blob) {
          setFailed(true);
          return;
        }
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [workOrderId, submissionId]);

  if (failed) return null;
  if (!url) return <Loader size="sm" />;
  return (
    <>
      <UnstyledButton
        onClick={() => setZoomed(true)}
        aria-label="Zoom scanned page"
        style={{ width: '100%', lineHeight: 0 }}
      >
        <Image
          src={url}
          alt="Scanned work order page"
          fit="contain"
          radius="sm"
          mah={320}
          style={{ border: '1px solid #dee2e6', backgroundColor: '#fff', cursor: 'zoom-in' }}
        />
      </UnstyledButton>
      <Modal
        opened={zoomed}
        onClose={() => setZoomed(false)}
        size="xl"
        title="Scanned work order page"
        centered
      >
        <Image src={url} alt="Scanned work order page (enlarged)" fit="contain" />
      </Modal>
    </>
  );
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

  // AC-3: validation prompt state.
  const [validationOpen, setValidationOpen] = useState(false);
  const [validationKind, setValidationKind] = useState<'finalize' | 'pdf'>('finalize');
  const [ackElectrical, setAckElectrical] = useState(false);
  const [ackLoto, setAckLoto] = useState(false);
  const [ackRequired, setAckRequired] = useState(false);
  const [validationNotes, setValidationNotes] = useState('');
  const [savingValidation, setSavingValidation] = useState(false);

  // AC-4: per-submission pending-review action state.
  const [pendingActionId, setPendingActionId] = useState<string | null>(null);
  // OMR bead-2: per-row action state (`${submissionId}:${target_id}`).
  const [rowActionKey, setRowActionKey] = useState<string | null>(null);

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
    // AC-3: gate finalization on validation acknowledgement.
    if (newStatus === 'completed' && !workOrder.validation?.is_complete) {
      setValidationKind('finalize');
      setAckElectrical(false);
      setAckLoto(false);
      setAckRequired(false);
      setValidationNotes('');
      setValidationOpen(true);
      return;
    }
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
    } catch (err: unknown) {
      const e = err as { response?: { status?: number; data?: { detail?: string } } };
      if (e.response?.status === 412) {
        // Backend says we still need validation — open the modal.
        setValidationKind('finalize');
        setValidationOpen(true);
      } else {
        notifications.show({
          title: 'Error',
          message: extractErrorMessage(e, 'Failed to update status.'),
          color: 'red',
        });
      }
    } finally {
      setSavingStatus(false);
    }
  };

  const handleSubmitValidation = async () => {
    if (!workOrder) return;
    if (!ackElectrical || !ackLoto || !ackRequired) return;
    setSavingValidation(true);
    try {
      await workOrderAPI.validateChecklist(workOrder.id, {
        electrical_acknowledged: ackElectrical,
        loto_acknowledged: ackLoto,
        required_fields_acknowledged: ackRequired,
        notes: validationNotes,
      });
      setValidationOpen(false);
      await loadWorkOrder();
      notifications.show({
        title: 'Validated',
        message:
          validationKind === 'finalize'
            ? 'Validation recorded. The work order can now be marked completed.'
            : 'Validation recorded. The PDF can now be generated.',
        color: 'green',
        icon: <IconCheck size={16} />,
      });
      if (validationKind === 'finalize') {
        // Re-attempt finalization now that the gate is open.
        await handleStatusChange('completed');
      } else {
        // Open the work order PDF in a new tab now that the gate is open.
        window.open(
          workOrderAPI.getPdfUrl(workOrder.id),
          '_blank',
          'noopener,noreferrer',
        );
      }
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } };
      notifications.show({
        title: 'Validation failed',
        message: extractErrorMessage(e, 'Could not record validation.'),
        color: 'red',
      });
    } finally {
      setSavingValidation(false);
    }
  };

  const handlePrintPdf = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (!workOrder?.validation?.is_complete) {
      e.preventDefault();
      setValidationKind('pdf');
      setAckElectrical(false);
      setAckLoto(false);
      setAckRequired(false);
      setValidationNotes('');
      setValidationOpen(true);
    }
  };

  const handleApplyPending = async (
    submissionId: string,
    body?: { target_ids?: string[]; confirm_complete?: boolean },
  ) => {
    if (!workOrder) return;
    setPendingActionId(submissionId);
    try {
      const res = await workOrderAPI.applyPendingChanges(workOrder.id, submissionId, body);
      await loadWorkOrder();
      notifications.show({
        title: res.data?.work_order_completed ? 'Work order completed' : 'Applied',
        message: res.data?.work_order_completed
          ? 'Marks confirmed and the work order was closed.'
          : 'Auto-detected changes accepted.',
        color: 'green',
        icon: <IconCheck size={16} />,
      });
    } catch {
      notifications.show({
        title: 'Error',
        message: 'Failed to apply pending changes.',
        color: 'red',
      });
    } finally {
      setPendingActionId(null);
    }
  };

  const handleDiscardPending = async (submissionId: string, body?: { target_ids?: string[] }) => {
    if (!workOrder) return;
    setPendingActionId(submissionId);
    try {
      await workOrderAPI.discardPendingChanges(workOrder.id, submissionId, body);
      await loadWorkOrder();
      notifications.show({
        title: 'Discarded',
        message: 'Auto-detected changes rejected.',
        color: 'gray',
      });
    } catch {
      notifications.show({
        title: 'Error',
        message: 'Failed to discard pending changes.',
        color: 'red',
      });
    } finally {
      setPendingActionId(null);
    }
  };

  // OMR bead-2: accept / reject a single scanned mark (per-row review).
  const handleRowAction = async (
    submissionId: string,
    targetId: string,
    action: 'accept' | 'reject',
  ) => {
    if (!workOrder || !targetId) return;
    setRowActionKey(`${submissionId}:${targetId}`);
    try {
      if (action === 'accept') {
        await workOrderAPI.applyPendingChanges(workOrder.id, submissionId, {
          target_ids: [targetId],
        });
      } else {
        await workOrderAPI.discardPendingChanges(workOrder.id, submissionId, {
          target_ids: [targetId],
        });
      }
      await loadWorkOrder();
    } catch {
      notifications.show({ title: 'Error', message: 'Could not update that mark.', color: 'red' });
    } finally {
      setRowActionKey(null);
    }
  };

  // OMR bead-2: the human confirm that advances the WO to Completed. A scan
  // never closes a work order on its own.
  const handleConfirmComplete = (submissionId: string) =>
    handleApplyPending(submissionId, { confirm_complete: true });

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
      <WorkspacePage
        testId="work-order-page"
        hero={{ eyebrow: 'Maintenance · Work order', title: 'Work order', description: 'Loading…' }}
        containerSize="sm"
      >
        <Group justify="center">
          <Loader />
          <Text c="dimmed">Loading work order…</Text>
        </Group>
      </WorkspacePage>
    );
  }

  if (!workOrder) {
    return (
      <WorkspacePage
        testId="work-order-page"
        hero={{
          eyebrow: 'Maintenance · Work order',
          title: 'Work order',
          description: 'Not found.',
          action: (
            <Button variant="default" onClick={() => navigate('/maintenance/dashboard')}>
              Back to dashboard
            </Button>
          ),
        }}
        containerSize="sm"
      >
        <Text c="red">Work order not found.</Text>
      </WorkspacePage>
    );
  }

  const completedTasks = workOrder.task_completions.filter((t) => t.is_completed).length;
  const totalTasks = workOrder.task_completions.length;
  const allTasksDone = totalTasks > 0 && completedTasks === totalTasks;

  return (
    <WorkspacePage
      testId="work-order-page"
      hero={{
        eyebrow: `Maintenance · ${workOrder.short_id}`,
        title: workOrder.maintenance_item_title,
        description: workOrder.asset_name ? `Asset: ${workOrder.asset_name}` : undefined,
      }}
      containerSize="sm"
    >
      {/* Header */}
      <Card withBorder p="md" radius="md">
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
              {(workOrder.pending_review_count ?? 0) > 0 && (
                <Badge color="orange" variant="filled" size="sm" leftSection={<IconScan size={12} />}>
                  {workOrder.pending_review_count} to review
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
          <Tooltip label="Print work order form">
            <ActionIcon
              variant="light"
              color="gray"
              size="lg"
              component="a"
              href={workOrderAPI.getPdfUrl(workOrder.id)}
              target="_blank"
              rel="noopener noreferrer"
              onClick={handlePrintPdf}
              aria-label="Print work order form"
            >
              <IconFileText size={20} />
            </ActionIcon>
          </Tooltip>
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

      {/* AC-1 Electrical info — present even when empty so the section is
          visibly accounted for (per bead AC-1: 'do not omit silently'). */}
      <Card withBorder p="md" radius="md" mb="md">
        <Group mb="sm" gap="xs">
          <IconBolt size={18} />
          <Title order={5}>Electrical</Title>
        </Group>
        {workOrder.electrical && !workOrder.electrical.is_empty ? (
          <Stack gap="xs">
            {workOrder.electrical.rows.length > 0 && (
              <Stack gap={4}>
                {workOrder.electrical.rows.map(([label, value]) => (
                  <Group key={label} gap="xs" wrap="nowrap">
                    <Text size="xs" c="dimmed" fw={600} style={{ minWidth: 140 }}>
                      {label}
                    </Text>
                    <Text size="sm">{value}</Text>
                  </Group>
                ))}
              </Stack>
            )}
            {workOrder.electrical.outlets.length > 0 && (
              <Box>
                <Text size="xs" c="dimmed" fw={600} mb={4}>
                  Outlets at this location
                </Text>
                <Stack gap={2}>
                  {workOrder.electrical.outlets.map((o) => (
                    <Text key={o.id} size="sm">
                      <b>{o.identifier}</b> · {o.outlet_type_display}
                      {o.breaker ? ` · ${o.breaker.label}` : ''}
                    </Text>
                  ))}
                </Stack>
              </Box>
            )}
            {workOrder.electrical.network_drops.length > 0 && (
              <Box>
                <Text size="xs" c="dimmed" fw={600} mb={4}>
                  Network drops at this location
                </Text>
                <Stack gap={2}>
                  {workOrder.electrical.network_drops.map((d) => (
                    <Text key={d.id} size="sm">
                      <b>{d.identifier}</b> · {d.drop_type_display}
                      {d.patch_panel ? ` · ${d.patch_panel}` : ''}
                      {d.patch_port ? ` / port ${d.patch_port}` : ''}
                    </Text>
                  ))}
                </Stack>
              </Box>
            )}
          </Stack>
        ) : (
          <Text size="sm" c="dimmed">
            No electrical circuits associated with this asset's location.
          </Text>
        )}
      </Card>

      {/* AC-2 LOTO — visible even when not required (per bead). */}
      <Card withBorder p="md" radius="md" mb="md">
        <Group mb="sm" gap="xs">
          <IconLock size={18} />
          <Title order={5}>Lockout / Tagout</Title>
        </Group>
        {workOrder.loto?.is_required ? (
          <Stack gap="xs">
            <Group gap="xs">
              <Text size="xs" c="dimmed" fw={600} style={{ minWidth: 140 }}>
                Lockout type
              </Text>
              <Text size="sm">{workOrder.loto.lockout_type}</Text>
            </Group>
            {workOrder.loto.lockout_responsible && (
              <Group gap="xs">
                <Text size="xs" c="dimmed" fw={600} style={{ minWidth: 140 }}>
                  Responsible
                </Text>
                <Text size="sm">{workOrder.loto.lockout_responsible}</Text>
              </Group>
            )}
            {workOrder.loto.lockout_instructions && (
              <Box>
                <Text size="xs" c="dimmed" fw={600} mb={4}>
                  Procedure
                </Text>
                <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                  {workOrder.loto.lockout_instructions}
                </Text>
              </Box>
            )}
          </Stack>
        ) : (
          <Text size="sm" c="dimmed">No LOTO required for this asset.</Text>
        )}
      </Card>

      {/* Pending review — CV-detected (email) or OMR scan marks (bead-2). */}
      {workOrder.submissions.some(
        (s) => (s.pending_changes?.length ?? 0) > 0 || s.status === 'pending_review',
      ) && (
        <Card withBorder p="md" radius="md" mb="md" style={{ borderColor: '#ffd43b' }}>
          <Group mb="sm" gap="xs">
            <IconRobot size={18} />
            <Title order={5}>Detected from paper form (pending review)</Title>
          </Group>
          <Stack gap="md">
            {workOrder.submissions
              .filter((s) => (s.pending_changes?.length ?? 0) > 0 || s.status === 'pending_review')
              .map((sub) => {
                const isScan = sub.source === 'scan';
                return (
                  <Box
                    key={sub.id}
                    p="sm"
                    style={{
                      borderRadius: 8,
                      backgroundColor: '#fff9db',
                      border: '1px solid #ffe066',
                    }}
                  >
                    <Group gap="xs" mb={6}>
                      {isScan && (
                        <Badge size="xs" variant="filled" color="grape" leftSection={<IconScan size={11} />}>
                          Flatbed scan
                        </Badge>
                      )}
                      <Text size="xs" c="dimmed">
                        Submission {sub.subject || sub.id} ·{' '}
                        {new Date(sub.received_at).toLocaleString()}
                      </Text>
                    </Group>

                    {sub.parse_error && (
                      <Alert
                        color="orange"
                        variant="light"
                        mb="sm"
                        icon={<IconAlertTriangle size={16} />}
                      >
                        {sub.parse_error}
                      </Alert>
                    )}

                    {/* op-o6rs: the full scanned page for paper-form verification. */}
                    {isScan && (
                      <Box mb="sm">
                        <Text size="xs" c="dimmed" mb={4}>
                          Scanned form — verify the marks below against the paper (click to zoom):
                        </Text>
                        <OmrScanImage workOrderId={workOrder.id} submissionId={sub.id} />
                      </Box>
                    )}

                    <Stack gap={6} mb="sm">
                      {sub.pending_changes.map((c, idx) => {
                        const rowKey = `${sub.id}:${c.target_id}`;
                        const isMark = c.kind === 'checkbox' || c.kind === 'ink';
                        const rowBusy = rowActionKey === rowKey || pendingActionId === sub.id;
                        return (
                          <Group
                            key={c.target_id || idx}
                            gap="xs"
                            wrap="nowrap"
                            align="center"
                            justify="space-between"
                          >
                            <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
                              {isScan && c.crop_url && c.target_id && (
                                <OmrMarkCrop
                                  workOrderId={workOrder.id}
                                  submissionId={sub.id}
                                  targetId={c.target_id}
                                />
                              )}
                              <Badge
                                size="xs"
                                variant="light"
                                color={c.confidence >= 0.999 ? 'green' : 'yellow'}
                              >
                                {Math.round(c.confidence * 100)}%
                              </Badge>
                              <Box style={{ minWidth: 0 }}>
                                <Text size="sm" truncate>
                                  <b>{c.label || c.kind}</b>
                                </Text>
                                <Text size="xs" c="dimmed">
                                  {isMark
                                    ? `reader: ${c.value ? 'marked' : 'blank'}${
                                        c.auto_applied ? ' · pre-checked' : ''
                                      }`
                                    : typeof c.value === 'string'
                                      ? c.value
                                      : String(c.value)}
                                </Text>
                              </Box>
                            </Group>
                            {isMark && c.target_id && (
                              <Group gap={4} wrap="nowrap">
                                <Tooltip label="Accept this mark">
                                  <ActionIcon
                                    size="sm"
                                    color="green"
                                    variant="light"
                                    loading={rowBusy}
                                    onClick={() => handleRowAction(sub.id, c.target_id!, 'accept')}
                                    aria-label={`Accept ${c.label}`}
                                  >
                                    <IconCheck size={14} />
                                  </ActionIcon>
                                </Tooltip>
                                <Tooltip label="Reject this mark">
                                  <ActionIcon
                                    size="sm"
                                    color="red"
                                    variant="light"
                                    loading={rowBusy}
                                    onClick={() => handleRowAction(sub.id, c.target_id!, 'reject')}
                                    aria-label={`Reject ${c.label}`}
                                  >
                                    <IconAlertTriangle size={14} />
                                  </ActionIcon>
                                </Tooltip>
                              </Group>
                            )}
                          </Group>
                        );
                      })}
                    </Stack>

                    <Group gap="xs">
                      {sub.pending_changes.length > 0 && (
                        <>
                          <Button
                            size="xs"
                            color="green"
                            leftSection={<IconCheck size={14} />}
                            loading={pendingActionId === sub.id}
                            onClick={() => handleApplyPending(sub.id)}
                          >
                            Accept all
                          </Button>
                          <Button
                            size="xs"
                            variant="default"
                            loading={pendingActionId === sub.id}
                            onClick={() => handleDiscardPending(sub.id)}
                          >
                            Reject all
                          </Button>
                        </>
                      )}
                      {isScan && (
                        <Button
                          size="xs"
                          color="blue"
                          variant="filled"
                          leftSection={<IconClipboard size={14} />}
                          loading={pendingActionId === sub.id}
                          onClick={() => handleConfirmComplete(sub.id)}
                        >
                          Confirm &amp; complete work order
                        </Button>
                      )}
                    </Group>
                  </Box>
                );
              })}
          </Stack>
        </Card>
      )}

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

      {/* AC-3 Validation prompt */}
      <Modal
        opened={validationOpen}
        onClose={() => setValidationOpen(false)}
        title={
          validationKind === 'finalize'
            ? 'Validate before finalizing'
            : 'Validate before generating PDF'
        }
        centered
      >
        <Stack gap="sm">
          <Alert color="blue" variant="light">
            Confirm the work order is ready
            {validationKind === 'finalize' ? ' to be marked completed' : ' to print'}.
            All three items must be acknowledged.
          </Alert>
          <Checkbox
            checked={ackElectrical}
            onChange={(e) => setAckElectrical(e.currentTarget.checked)}
            label="Electrical info reviewed and correct"
          />
          <Checkbox
            checked={ackLoto}
            onChange={(e) => setAckLoto(e.currentTarget.checked)}
            label="Lockout/Tagout requirements reviewed and acknowledged"
          />
          <Checkbox
            checked={ackRequired}
            onChange={(e) => setAckRequired(e.currentTarget.checked)}
            label="All required fields are present"
          />
          <Textarea
            value={validationNotes}
            onChange={(e) => setValidationNotes(e.currentTarget.value)}
            placeholder="Optional notes recorded with this validation…"
            autosize
            minRows={2}
            maxRows={5}
          />
          <Group justify="flex-end" gap="xs">
            <Button variant="default" onClick={() => setValidationOpen(false)}>
              Cancel
            </Button>
            <Button
              color="green"
              leftSection={<IconCheck size={16} />}
              onClick={handleSubmitValidation}
              loading={savingValidation}
              disabled={!ackElectrical || !ackLoto || !ackRequired}
            >
              Confirm
            </Button>
          </Group>
        </Stack>
      </Modal>
    </WorkspacePage>
  );
};

export default WorkOrderPage;
