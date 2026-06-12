/**
 * Storage Vision review queue (AC-20, AC-21, AC-22, AC-23, AC-24, AC-25).
 *
 * Single-screen review surface at /facilities/storage-vision/review.
 *
 * - List with status / area / item / classification filters (AC-20),
 *   defaulting to ``status=pending`` because that's the operator's
 *   morning queue.
 * - Optional ``?capture=N`` deep link from the slice-8 capture page
 *   that scopes the list to one capture's observations and labels
 *   the hero so the operator knows what they're looking at.
 * - Per-row approve / reject with the slice-5 API. Approve writes
 *   StockReconciliation + ReorderRequest where applicable (AC-21,
 *   AC-22); reject requires a reason (AC-24). Repeating either on
 *   an already-resolved row returns 409 (AC-23) and surfaces in the
 *   row's status badge.
 * - Bulk approve: selectable checkbox per row, master checkbox in
 *   the header, "Approve selected" with an optional shared reason.
 *   Skipped rows with per-id reasons render in an inline alert
 *   (AC-25).
 * - AC-29 non-staff gate at both the route and the sidebar.
 */
import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Checkbox,
  Code,
  Group,
  Image,
  LoadingOverlay,
  Modal,
  Select,
  Stack,
  Table,
  Text,
  Textarea,
  Title,
  Tooltip,
} from '@mantine/core';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, Navigate, useSearchParams } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import {
  storageVisionAPI,
  VisionArea,
  VisionObservation,
  VisionObservationAction,
  VisionObservationClass,
  VisionObservationStatus,
} from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const unwrap = <T,>(data: { results: T[] } | T[]): T[] =>
  Array.isArray(data) ? data : data.results;

const STATUS_OPTIONS: { value: VisionObservationStatus; label: string }[] = [
  { value: 'pending', label: 'Pending' },
  { value: 'approved', label: 'Approved' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'superseded', label: 'Superseded' },
];

const CLASS_OPTIONS: { value: VisionObservationClass; label: string }[] = [
  { value: 'empty', label: 'Empty' },
  { value: 'low', label: 'Low' },
  { value: 'full', label: 'Full' },
  { value: 'unknown', label: 'Unknown' },
];

const ACTION_OPTIONS: { value: VisionObservationAction; label: string }[] = [
  { value: 'reconcile_empty', label: 'Reconcile (zero stock)' },
  { value: 'review_only', label: 'Review only' },
];

const statusColor = (s: VisionObservationStatus): string => {
  switch (s) {
    case 'pending':
      return 'gray';
    case 'approved':
      return 'green';
    case 'rejected':
      return 'red';
    case 'superseded':
      return 'yellow';
  }
};

const classificationColor = (c: VisionObservationClass): string => {
  switch (c) {
    case 'empty':
      return 'orange';
    case 'low':
      return 'yellow';
    case 'full':
      return 'green';
    case 'unknown':
      return 'gray';
  }
};

const formatAge = (seconds: number): string => {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
};

interface ResolutionMessage {
  kind: 'success' | 'info' | 'warning' | 'error';
  text: string;
}

const StorageVisionReviewPage: React.FC = () => {
  const isStaff =
    typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' &&
    localStorage.getItem('is_superuser') === 'true';
  const isLogistics =
    typeof window !== 'undefined' &&
    (localStorage.getItem('is_logistics') === 'true' ||
      (localStorage.getItem('groups') || '').includes('Logistics'));
  const isAllowed = isStaff || isSuperuser || isLogistics;

  const [searchParams, setSearchParams] = useSearchParams();
  const captureParam = searchParams.get('capture');
  const captureId = captureParam ? Number(captureParam) : null;

  const [observations, setObservations] = useState<VisionObservation[]>([]);
  const [areas, setAreas] = useState<VisionArea[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState<VisionObservationStatus>('pending');
  const [areaFilter, setAreaFilter] = useState<number | null>(null);
  const [classFilter, setClassFilter] =
    useState<VisionObservationClass | null>(null);
  const [actionFilter, setActionFilter] =
    useState<VisionObservationAction | null>(null);

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busyId, setBusyId] = useState<number | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  const [rejectModal, setRejectModal] = useState<{
    obs: VisionObservation | null;
    reason: string;
  }>({ obs: null, reason: '' });
  const [bulkReason, setBulkReason] = useState<string>('');
  const [resolution, setResolution] = useState<ResolutionMessage | null>(null);
  const [bulkSkipped, setBulkSkipped] = useState<
    Array<{ id: number; reason: string }>
  >([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const obsRes = await storageVisionAPI.listObservations({
        status: statusFilter,
        ...(areaFilter != null ? { area: areaFilter } : {}),
        ...(classFilter ? { classification: classFilter } : {}),
        ...(actionFilter ? { suggested_action: actionFilter } : {}),
      });
      let rows = unwrap(obsRes.data);
      if (captureId != null) {
        rows = rows.filter((o) => o.capture === captureId);
      }
      setObservations(rows);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load observations.'));
    } finally {
      setLoading(false);
    }
  }, [statusFilter, areaFilter, classFilter, actionFilter, captureId]);

  const loadAreas = useCallback(async () => {
    try {
      const res = await storageVisionAPI.listAreas();
      setAreas(unwrap(res.data));
    } catch {
      // Areas filter is non-blocking; the queue still renders.
    }
  }, []);

  useEffect(() => {
    if (!isAllowed) return;
    void load();
  }, [isAllowed, load]);

  useEffect(() => {
    if (!isAllowed) return;
    void loadAreas();
  }, [isAllowed, loadAreas]);

  const areaOptions = useMemo(
    () => areas.map((a) => ({ value: String(a.id), label: a.name })),
    [areas],
  );

  const allSelectable = useMemo(
    () =>
      observations.filter(
        (o) =>
          o.status === 'pending' &&
          o.suggested_action === 'reconcile_empty',
      ),
    [observations],
  );

  if (!isAllowed) {
    return <Navigate to="/" replace />;
  }

  const allSelected =
    allSelectable.length > 0 &&
    allSelectable.every((o) => selected.has(o.id));

  const toggleAll = () => {
    if (allSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(allSelectable.map((o) => o.id)));
    }
  };

  const toggleOne = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const clearCaptureFilter = () => {
    searchParams.delete('capture');
    setSearchParams(searchParams);
  };

  const handleApprove = async (obs: VisionObservation) => {
    setBusyId(obs.id);
    setResolution(null);
    try {
      const res = await storageVisionAPI.approveObservation(obs.id);
      // Optimistic + authoritative update from server.
      setObservations((prev) =>
        prev.map((o) =>
          o.id === obs.id ? { ...o, ...res.data, status: 'approved' } : o,
        ),
      );
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(obs.id);
        return next;
      });
      const parts: string[] = [`Approved observation #${obs.id}`];
      if (res.data.reconciliation_id != null) {
        parts.push(`reconciliation #${res.data.reconciliation_id}`);
      }
      if (res.data.reorder_created) {
        parts.push('reorder created');
      }
      setResolution({ kind: 'success', text: parts.join(' · ') });
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 409) {
        setResolution({
          kind: 'warning',
          text: `Observation #${obs.id} was already resolved (AC-23). Refresh the queue to see the latest state.`,
        });
      } else {
        setResolution({
          kind: 'error',
          text: extractErrorMessage(err, 'Approve failed.'),
        });
      }
    } finally {
      setBusyId(null);
    }
  };

  const handleReject = async () => {
    const obs = rejectModal.obs;
    if (obs == null) return;
    const reason = rejectModal.reason.trim();
    if (!reason) return;
    setBusyId(obs.id);
    try {
      const res = await storageVisionAPI.rejectObservation(obs.id, reason);
      setObservations((prev) =>
        prev.map((o) =>
          o.id === obs.id ? { ...o, ...res.data, status: 'rejected' } : o,
        ),
      );
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(obs.id);
        return next;
      });
      setRejectModal({ obs: null, reason: '' });
      setResolution({
        kind: 'info',
        text: `Rejected observation #${obs.id} (no inventory mutation).`,
      });
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 409) {
        setResolution({
          kind: 'warning',
          text: `Observation #${obs.id} was already resolved (AC-23).`,
        });
        setRejectModal({ obs: null, reason: '' });
      } else {
        setResolution({
          kind: 'error',
          text: extractErrorMessage(err, 'Reject failed.'),
        });
      }
    } finally {
      setBusyId(null);
    }
  };

  const handleBulkApprove = async () => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    setBulkBusy(true);
    setResolution(null);
    setBulkSkipped([]);
    try {
      const res = await storageVisionAPI.bulkApprove(
        ids,
        bulkReason.trim() || undefined,
      );
      const approvedIds = new Set(res.data.approved.map((a) => a.id));
      setObservations((prev) =>
        prev.map((o) =>
          approvedIds.has(o.id) ? { ...o, status: 'approved' } : o,
        ),
      );
      setSelected(new Set());
      setBulkReason('');
      const reorderCount = res.data.approved.filter((a) => a.reorder_created)
        .length;
      const parts = [
        `Approved ${res.data.counts.approved} of ${res.data.counts.requested}`,
      ];
      if (reorderCount) parts.push(`${reorderCount} reorder(s) created`);
      if (res.data.counts.skipped) parts.push(`${res.data.counts.skipped} skipped`);
      setResolution({ kind: 'success', text: parts.join(' · ') });
      setBulkSkipped(res.data.skipped);
    } catch (err) {
      setResolution({
        kind: 'error',
        text: extractErrorMessage(err, 'Bulk approve failed.'),
      });
    } finally {
      setBulkBusy(false);
    }
  };

  return (
    <WorkspacePage
      hero={{
        eyebrow: 'Facilities',
        title: 'Storage vision review',
        subtitle:
          captureId != null
            ? `Showing observations from capture #${captureId}.`
            : 'Pending findings the marker detector produced. Approve to zero the stock and trigger a reorder; reject to discard.',
        action: (
          <Group gap="sm">
            {captureId != null && (
              <Button
                variant="default"
                onClick={clearCaptureFilter}
                data-testid="clear-capture-filter"
              >
                Clear capture filter
              </Button>
            )}
            <Button
              variant="default"
              component={Link}
              to="/facilities/storage-vision/capture"
            >
              New capture
            </Button>
          </Group>
        ),
      }}
      testId="storage-vision-review-page"
    >
      <Box pos="relative">
        <LoadingOverlay visible={loading} />

        {error && (
          <Alert
            color="red"
            mb="md"
            onClose={() => setError(null)}
            withCloseButton
          >
            {error}
          </Alert>
        )}

        {resolution && (
          <Alert
            color={
              resolution.kind === 'success'
                ? 'green'
                : resolution.kind === 'warning'
                  ? 'yellow'
                  : resolution.kind === 'error'
                    ? 'red'
                    : 'blue'
            }
            mb="md"
            onClose={() => setResolution(null)}
            withCloseButton
            data-testid="review-resolution-alert"
          >
            {resolution.text}
          </Alert>
        )}

        {bulkSkipped.length > 0 && (
          <Alert
            color="yellow"
            mb="md"
            onClose={() => setBulkSkipped([])}
            withCloseButton
            data-testid="bulk-skipped-alert"
          >
            <Text fw={600} mb={4}>
              Skipped {bulkSkipped.length} observation(s):
            </Text>
            <Stack gap={2}>
              {bulkSkipped.map((s) => (
                <Text key={s.id} size="sm">
                  #{s.id} — {s.reason}
                </Text>
              ))}
            </Stack>
          </Alert>
        )}

        <Card withBorder mb="md" padding="md">
          <Group align="flex-end" gap="md">
            <Select
              label="Status"
              data={STATUS_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
              value={statusFilter}
              onChange={(v) =>
                v && setStatusFilter(v as VisionObservationStatus)
              }
              data-testid="filter-status"
            />
            <Select
              label="Area"
              data={areaOptions}
              value={areaFilter != null ? String(areaFilter) : null}
              onChange={(v) => setAreaFilter(v ? Number(v) : null)}
              clearable
              searchable
              data-testid="filter-area"
            />
            <Select
              label="Classification"
              data={CLASS_OPTIONS}
              value={classFilter}
              onChange={(v) =>
                setClassFilter((v as VisionObservationClass) || null)
              }
              clearable
              data-testid="filter-classification"
            />
            <Select
              label="Suggested action"
              data={ACTION_OPTIONS}
              value={actionFilter}
              onChange={(v) =>
                setActionFilter((v as VisionObservationAction) || null)
              }
              clearable
              data-testid="filter-action"
            />
          </Group>
        </Card>

        {selected.size > 0 && (
          <Card withBorder mb="md" padding="md">
            <Group align="flex-end" gap="md">
              <Textarea
                label={`Bulk reason (optional, applies to ${selected.size} observation(s))`}
                value={bulkReason}
                onChange={(e) => setBulkReason(e.currentTarget.value)}
                autosize
                minRows={1}
                style={{ flex: 1 }}
              />
              <Button
                onClick={handleBulkApprove}
                loading={bulkBusy}
                disabled={bulkBusy}
                data-testid="bulk-approve-button"
              >
                Approve selected ({selected.size})
              </Button>
              <Button
                variant="default"
                onClick={() => setSelected(new Set())}
              >
                Clear selection
              </Button>
            </Group>
          </Card>
        )}

        {observations.length === 0 ? (
          <Text c="dimmed">No observations match the current filter.</Text>
        ) : (
          <Table data-testid="observations-table">
            <Table.Thead>
              <Table.Tr>
                <Table.Th style={{ width: 32 }}>
                  <Checkbox
                    checked={allSelected}
                    indeterminate={
                      !allSelected &&
                      allSelectable.some((o) => selected.has(o.id))
                    }
                    onChange={toggleAll}
                    aria-label="Select all"
                    data-testid="select-all"
                  />
                </Table.Th>
                <Table.Th>Evidence</Table.Th>
                <Table.Th>Slot / item</Table.Th>
                <Table.Th>Classification</Table.Th>
                <Table.Th>Confidence</Table.Th>
                <Table.Th>Suggested</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Age</Table.Th>
                <Table.Th>Actions</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {observations.map((obs) => {
                const isSelectable =
                  obs.status === 'pending' &&
                  obs.suggested_action === 'reconcile_empty';
                return (
                  <Table.Tr key={obs.id}>
                    <Table.Td>
                      <Checkbox
                        checked={selected.has(obs.id)}
                        onChange={() => toggleOne(obs.id)}
                        disabled={!isSelectable}
                        aria-label={`Select observation ${obs.id}`}
                        data-testid={`select-${obs.id}`}
                      />
                    </Table.Td>
                    <Table.Td>
                      {obs.evidence_crop ? (
                        <Image
                          src={obs.evidence_crop}
                          alt={`evidence ${obs.id}`}
                          w={80}
                          h={80}
                          fit="cover"
                          radius="sm"
                          data-testid={`evidence-${obs.id}`}
                        />
                      ) : (
                        <Text size="xs" c="dimmed">
                          no crop
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Stack gap={2}>
                        <Code>{obs.slot_marker_code}</Code>
                        <Text size="sm">{obs.item_name}</Text>
                        <Text size="xs" c="dimmed">
                          {obs.area_name}
                          {obs.duplicate_count > 0 && (
                            <Tooltip
                              label={`Re-detected ${obs.duplicate_count} time(s) before the operator resolved this row`}
                            >
                              <Badge color="blue" ml="xs">
                                ×{obs.duplicate_count + 1}
                              </Badge>
                            </Tooltip>
                          )}
                        </Text>
                      </Stack>
                    </Table.Td>
                    <Table.Td>
                      <Badge color={classificationColor(obs.classification)}>
                        {obs.classification}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      {Number(obs.confidence).toFixed(2)}
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="light">
                        {obs.suggested_action === 'reconcile_empty'
                          ? 'reconcile'
                          : 'review only'}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Badge color={statusColor(obs.status)}>
                        {obs.status}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{formatAge(obs.age_seconds)}</Table.Td>
                    <Table.Td>
                      <Group gap="xs">
                        <Button
                          size="xs"
                          onClick={() => handleApprove(obs)}
                          loading={busyId === obs.id}
                          disabled={obs.status !== 'pending'}
                          data-testid={`approve-${obs.id}`}
                        >
                          Approve
                        </Button>
                        <Button
                          size="xs"
                          color="red"
                          variant="light"
                          onClick={() =>
                            setRejectModal({ obs, reason: '' })
                          }
                          disabled={
                            obs.status !== 'pending' || busyId === obs.id
                          }
                          data-testid={`reject-${obs.id}`}
                        >
                          Reject
                        </Button>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        )}
      </Box>

      <Modal
        opened={rejectModal.obs != null}
        onClose={() => setRejectModal({ obs: null, reason: '' })}
        title={
          rejectModal.obs
            ? `Reject observation #${rejectModal.obs.id}`
            : 'Reject'
        }
        data-testid="reject-modal"
      >
        <Stack>
          {rejectModal.obs && (
            <Text size="sm" c="dimmed">
              {rejectModal.obs.item_name} ·{' '}
              <Code>{rejectModal.obs.slot_marker_code}</Code> ·{' '}
              {rejectModal.obs.area_name}
            </Text>
          )}
          <Textarea
            label="Reason"
            value={rejectModal.reason}
            onChange={(e) =>
              setRejectModal((prev) => ({
                ...prev,
                reason: e.currentTarget.value,
              }))
            }
            required
            autosize
            minRows={3}
            description="A short note for the audit trail. Required."
            data-testid="reject-reason"
          />
          <Group justify="flex-end">
            <Button
              variant="default"
              onClick={() => setRejectModal({ obs: null, reason: '' })}
            >
              Cancel
            </Button>
            <Button
              color="red"
              disabled={!rejectModal.reason.trim() || busyId != null}
              loading={busyId === rejectModal.obs?.id}
              onClick={handleReject}
              data-testid="reject-confirm"
            >
              Reject
            </Button>
          </Group>
        </Stack>
      </Modal>
    </WorkspacePage>
  );
};

export default StorageVisionReviewPage;
