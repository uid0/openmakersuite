import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Card,
  Checkbox,
  Container,
  Divider,
  FileButton,
  Group,
  Image,
  Loader,
  Modal,
  NumberInput,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TextInput,
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
  IconPlayerPause,
  IconPlayerPlay,
  IconPlus,
  IconReceipt,
  IconRobot,
  IconScan,
  IconShoppingCart,
  IconStopwatch,
  IconTag,
  IconTool,
  IconTrash,
  IconUpload,
} from '@tabler/icons-react';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { inventoryAPI, maintenanceAPI, workOrderAPI } from '../services/api';
import {
  WorkOrder,
  WorkOrderAdHocMaterialInput,
  WorkOrderMaterialUsage,
  WorkOrderStatus,
} from '../types';
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

// ── Elapsed timer (op-m3so) ─────────────────────────────────────────────────
// The server owns the total: `elapsed_seconds` already includes whatever
// segment is currently running. These helpers only *display* it — they never
// accumulate into a value that gets sent back.

/** `MM:SS`, or `H:MM:SS` once a job passes the hour mark. */
export const formatElapsed = (totalSeconds: number): string => {
  const total = Math.max(0, Math.floor(totalSeconds || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const mm = String(minutes).padStart(2, '0');
  const ss = String(seconds).padStart(2, '0');
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
};

/** "18m / est 30m" — the comparison the timer exists to produce. */
export const formatElapsedSummary = (
  totalSeconds: number,
  estimateMinutes?: number | null,
): string => {
  const minutes = Math.round(Math.max(0, totalSeconds || 0) / 60);
  return estimateMinutes ? `${minutes}m / est ${estimateMinutes}m` : `${minutes}m on job`;
};

// ── Material cost (op-768w) ─────────────────────────────────────────────────
// A line's real spend is `quantity_used × unit_cost`, and the *work order's*
// actual is the server-owned sum of that over the lines actually used. The
// estimate it gets measured against lives on the PM template
// (`MaintenanceMaterial.estimated_cost_per_unit`), so a corrective work order —
// which has no template — has an actual and nothing to compare it against,
// exactly like the stopwatch has no `estimated_time_minutes`.

/** `$12.50`, or an em dash when nothing was priced. */
export const formatMoney = (value: string | number | null | undefined): string => {
  if (value === null || value === undefined || value === '') return '—';
  const num = Number(value);
  return Number.isFinite(num) ? `$${num.toFixed(2)}` : '—';
};

/** The number a Decimal-as-string field carries, or 0 when it carries nothing. */
const toNumber = (value: string | number | null | undefined): number => {
  const num = Number(value ?? 0);
  return Number.isFinite(num) ? num : 0;
};

/**
 * What the used lines actually cost, for a payload without the server total.
 *
 * Mirrors `WorkOrder.actual_material_cost`: planned-but-unused costs nothing,
 * and an unpriced used line contributes 0 rather than voiding the total.
 */
const sumActualMaterialCost = (lines: WorkOrderMaterialUsage[]): number =>
  lines.reduce((total, mu) => (mu.was_used ? total + toNumber(mu.actual_cost) : total), 0);

/** True when two money/quantity drafts mean the same thing (`''`/null = unset). */
const sameAmount = (a: string | number | null | undefined, b: string | number | null | undefined) => {
  const left = a === '' || a === null || a === undefined ? null : Number(a);
  const right = b === '' || b === null || b === undefined ? null : Number(b);
  if (left === null || right === null) return left === right;
  return Math.abs(left - right) < 1e-9;
};

/**
 * Tick a local display forward from the server's total while the clock runs.
 *
 * Re-anchors on every fresh server value, so a reload or another device's
 * pause always wins over the local count. The interval is cleared when the
 * clock stops or the row unmounts — a leaked one fails the suite.
 */
const useTickingSeconds = (serverSeconds: number, running: boolean): number => {
  const [seconds, setSeconds] = useState(serverSeconds);
  useEffect(() => {
    setSeconds(serverSeconds);
    if (!running) return undefined;
    const id = setInterval(() => setSeconds((prev) => prev + 1), 1000);
    return () => clearInterval(id);
  }, [serverSeconds, running]);
  return seconds;
};

/** The work-order stopwatch: running clock, actual-vs-estimate, start/pause. */
const WorkOrderTimer: React.FC<{
  elapsedSeconds: number;
  isTiming: boolean;
  estimateMinutes?: number | null;
  completed: boolean;
  busy: boolean;
  onToggle: (action: 'start' | 'pause') => void;
}> = ({ elapsedSeconds, isTiming, estimateMinutes, completed, busy, onToggle }) => {
  const seconds = useTickingSeconds(elapsedSeconds, isTiming);
  return (
    <Group justify="space-between" wrap="nowrap" mt="md" data-testid="wo-timer">
      <Group gap="xs" wrap="nowrap">
        <IconStopwatch size={20} color={isTiming ? '#2f9e44' : undefined} />
        <Box>
          <Text
            fw={700}
            size="xl"
            ff="monospace"
            c={isTiming ? 'green' : undefined}
            data-testid="wo-timer-clock"
          >
            {formatElapsed(seconds)}
          </Text>
          <Text size="xs" c="dimmed" data-testid="wo-timer-summary">
            {formatElapsedSummary(seconds, estimateMinutes)}
          </Text>
        </Box>
      </Group>
      <Tooltip
        label="The total was recorded when this work order was completed."
        disabled={!completed}
      >
        <Box>
          <Button
            variant={isTiming ? 'filled' : 'light'}
            color={isTiming ? 'orange' : 'green'}
            leftSection={isTiming ? <IconPlayerPause size={16} /> : <IconPlayerPlay size={16} />}
            onClick={() => onToggle(isTiming ? 'pause' : 'start')}
            loading={busy}
            disabled={completed}
          >
            {isTiming ? 'Pause' : 'Start'}
          </Button>
        </Box>
      </Tooltip>
    </Group>
  );
};

/**
 * One step's stopwatch. Only one step runs at a time server-side, so starting
 * this one stops whichever other step was running.
 */
const StepTimer: React.FC<{
  stepTitle: string;
  elapsedSeconds: number;
  isTiming: boolean;
  locked: boolean;
  busy: boolean;
  onToggle: (action: 'start' | 'pause') => void;
}> = ({ stepTitle, elapsedSeconds, isTiming, locked, busy, onToggle }) => {
  const seconds = useTickingSeconds(elapsedSeconds, isTiming);
  // A finished step keeps its number on show but loses the control — the clock
  // stopped when the box was ticked, and restarting it would misreport the step.
  if (locked && seconds <= 0) return null;
  return (
    <Group gap={6} wrap="nowrap">
      {!locked && (
        <ActionIcon
          variant={isTiming ? 'filled' : 'light'}
          color={isTiming ? 'orange' : 'green'}
          size="md"
          loading={busy}
          onClick={() => onToggle(isTiming ? 'pause' : 'start')}
          aria-label={`${isTiming ? 'Pause' : 'Start'} timer for ${stepTitle}`}
        >
          {isTiming ? <IconPlayerPause size={16} /> : <IconPlayerPlay size={16} />}
        </ActionIcon>
      )}
      {(seconds > 0 || isTiming) && (
        <Text size="xs" ff="monospace" c={isTiming ? 'green' : 'dimmed'}>
          {formatElapsed(seconds)}
        </Text>
      )}
    </Group>
  );
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

// op-syov: a step's reference photo — the instructional shot set on the PM
// template ("what this should look like"). Same click-to-zoom lightbox as the
// OMR crops, but the image is a plain media URL, so no blob fetch is needed.
const StepReferencePhoto: React.FC<{ url: string; stepTitle: string }> = ({ url, stepTitle }) => {
  const [zoomed, setZoomed] = useState(false);
  return (
    <>
      <UnstyledButton
        onClick={() => setZoomed(true)}
        aria-label={`Zoom reference photo for ${stepTitle}`}
        style={{ flexShrink: 0, lineHeight: 0 }}
      >
        <Image
          src={url}
          alt={`Reference photo for ${stepTitle}`}
          w={72}
          h={54}
          fit="cover"
          radius="sm"
          style={{ border: '1px solid #dee2e6', backgroundColor: '#fff', cursor: 'zoom-in' }}
        />
      </UnstyledButton>
      <Modal
        opened={zoomed}
        onClose={() => setZoomed(false)}
        size="lg"
        title={`Reference photo — ${stepTitle}`}
        centered
      >
        <Image src={url} alt={`Reference photo for ${stepTitle} (enlarged)`} fit="contain" />
      </Modal>
    </>
  );
};

// op-syov: the per-step evidence picker. Owns its own `resetRef` so re-picking
// the same file — the natural thing to do after a failed upload — fires
// onChange again instead of silently doing nothing.
const StepEvidenceUpload: React.FC<{
  stepTitle: string;
  uploading: boolean;
  onPick: (file: File) => Promise<void>;
}> = ({ stepTitle, uploading, onPick }) => {
  const resetRef = useRef<() => void>(null);

  const handleChange = async (file: File | null) => {
    if (!file) return;
    try {
      await onPick(file);
    } finally {
      resetRef.current?.();
    }
  };

  return (
    <FileButton resetRef={resetRef} onChange={handleChange} accept="image/*">
      {(props) => (
        <Button
          {...props}
          size="compact-xs"
          variant="subtle"
          mt="xs"
          leftSection={<IconCamera size={14} />}
          loading={uploading}
          aria-label={`Add photo to ${stepTitle}`}
        >
          Add photo
        </Button>
      )}
    </FileButton>
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
  // Per-row "quantity used" edits, keyed by material-usage id. Seeded from the
  // row's quantity_used and sent when the material is checked off as used.
  const [materialQty, setMaterialQty] = useState<Record<string, number | string>>({});
  // op-768w: the same idea for the real price paid per unit. Both drafts ride
  // the toggle and commit on blur, so an already-checked line can still be
  // priced — the backend freezes both once the stock decrement lands.
  const [materialCost, setMaterialCost] = useState<Record<string, number | string>>({});
  const [removingMaterial, setRemovingMaterial] = useState<string | null>(null);
  // op-768w: the add-material form. `receipt` mode is the out-of-pocket buy —
  // a line that records a spend and moves no stock.
  const [addMaterialOpen, setAddMaterialOpen] = useState(false);
  const [addMode, setAddMode] = useState<'material' | 'receipt'>('material');
  const [newMaterialName, setNewMaterialName] = useState('');
  const [newMaterialQty, setNewMaterialQty] = useState<number | string>(1);
  const [newMaterialUnit, setNewMaterialUnit] = useState('');
  const [newMaterialCost, setNewMaterialCost] = useState<number | string>('');
  const [newMaterialItem, setNewMaterialItem] = useState<string | null>(null);
  const [newReceiptWhere, setNewReceiptWhere] = useState('');
  const [newReceiptFile, setNewReceiptFile] = useState<File | null>(null);
  const [savingMaterial, setSavingMaterial] = useState(false);
  // Stock picker for an ad-hoc line: searched server-side, since a shop's item
  // list is far longer than a dropdown should ever hold.
  const [itemSearch, setItemSearch] = useState('');
  const [itemOptions, setItemOptions] = useState<{ value: string; label: string }[]>([]);
  const [itemCosts, setItemCosts] = useState<Record<string, string | null>>({});
  const resetReceiptRef = useRef<() => void>(null);
  // Estimated cost per unit from the PM template, keyed by MaintenanceMaterial
  // id — the only thing the "actual vs estimated" comparison can be measured
  // against, and it does not ride the work-order payload.
  const [estimatedUnitCosts, setEstimatedUnitCosts] = useState<Record<string, string>>({});
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  // Which step's evidence photo is currently uploading (null = none).
  const [uploadingStepPhoto, setUploadingStepPhoto] = useState<string | null>(null);
  const [notes, setNotes] = useState('');
  const [savingNotes, setSavingNotes] = useState(false);
  const [togglingLoto, setTogglingLoto] = useState<string | null>(null);
  const [lotoNote, setLotoNote] = useState('');
  const [savingLotoNote, setSavingLotoNote] = useState(false);
  const resetPhotoRef = useRef<() => void>(null);
  // op-pzae: which reference document has its revision history expanded.
  const [openRevisions, setOpenRevisions] = useState<Record<string, boolean>>({});
  // op-m3so: in-flight timer calls (WO-level, and which step).
  const [timerBusy, setTimerBusy] = useState(false);
  const [timingStep, setTimingStep] = useState<string | null>(null);

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
      setLotoNote(res.data.loto_completion_note || '');
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

  // op-768w: pull the PM template's per-unit estimates so the materials total
  // can say actual *against* estimate. One fetch per template; a corrective
  // work order has none, and a failure just drops the comparison — the actual
  // spend stands on its own.
  const maintenanceItemId = workOrder?.maintenance_item ?? null;
  useEffect(() => {
    if (!maintenanceItemId) {
      setEstimatedUnitCosts({});
      return undefined;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await maintenanceAPI.getItem(maintenanceItemId);
        if (cancelled) return;
        const costs: Record<string, string> = {};
        (res?.data?.materials ?? []).forEach((mat) => {
          costs[mat.id] = mat.estimated_cost_per_unit;
        });
        setEstimatedUnitCosts(costs);
      } catch {
        // No estimate to compare against; the actual is still worth showing.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [maintenanceItemId]);

  // Stock picker options for the add-material form. Searched server-side and
  // only while the form is open, so the page itself never pays for it.
  useEffect(() => {
    if (!addMaterialOpen) return undefined;
    let cancelled = false;
    const handle = setTimeout(async () => {
      try {
        const res = await inventoryAPI.listItems({
          search: itemSearch || undefined,
          page_size: 25,
        });
        if (cancelled) return;
        const items = res?.data?.results ?? [];
        setItemOptions(items.map((item) => ({ value: item.id, label: item.name })));
        setItemCosts(
          items.reduce<Record<string, string | null>>((acc, item) => {
            acc[item.id] = item.unit_cost;
            return acc;
          }, {}),
        );
      } catch {
        // Picking stock is optional — an ad-hoc line works without it.
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [addMaterialOpen, itemSearch]);

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

  // op-m3so: the work-order stopwatch. The response is the refreshed work
  // order, so a start/pause needs no second round trip.
  const handleTimer = async (action: 'start' | 'pause') => {
    if (!workOrder) return;
    setTimerBusy(true);
    try {
      const res = await workOrderAPI.timer(workOrder.id, action);
      if (res?.data) {
        setWorkOrder(res.data);
      } else {
        await loadWorkOrder();
      }
    } catch {
      notifications.show({
        title: 'Error',
        message: `Failed to ${action} the timer.`,
        color: 'red',
      });
    } finally {
      setTimerBusy(false);
    }
  };

  const handleStepTimer = async (taskCompletionId: string, action: 'start' | 'pause') => {
    if (!workOrder) return;
    setTimingStep(taskCompletionId);
    try {
      await workOrderAPI.taskTimer(workOrder.id, taskCompletionId, action);
      // Starting a step pauses whichever other step was running, so reload the
      // whole checklist rather than patching this one row.
      await loadWorkOrder();
    } catch {
      notifications.show({
        title: 'Error',
        message: 'Failed to update the step timer.',
        color: 'red',
      });
    } finally {
      setTimingStep(null);
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

  const handleToggleMaterial = async (
    materialUsageId: string,
    wasUsed: boolean,
    quantityUsed?: number | string,
    unitCost?: number | string | null
  ) => {
    if (!workOrder) return;
    setTogglingMaterial(materialUsageId);
    try {
      await workOrderAPI.toggleMaterial(
        workOrder.id,
        materialUsageId,
        wasUsed,
        quantityUsed,
        unitCost
      );
      // The server now holds what the drafts were proposing — drop them so the
      // row reads from the payload again (a PO re-mirror can move either one).
      setMaterialQty((prev) => {
        const { [materialUsageId]: _dropped, ...rest } = prev;
        return rest;
      });
      setMaterialCost((prev) => {
        const { [materialUsageId]: _dropped, ...rest } = prev;
        return rest;
      });
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

  /**
   * Send a quantity/cost edit that no checkbox click is going to carry.
   *
   * The toggle is the only write endpoint for these two fields, so an
   * already-marked line (an out-of-pocket buy is marked used and moves no
   * stock) would otherwise have no way to record what it cost. Sends the
   * line's current `was_used` unchanged; a no-op edit sends nothing.
   */
  const handleCommitMaterialEdits = async (mu: WorkOrderMaterialUsage) => {
    const qtyDraft = materialQty[mu.id];
    const costDraft = materialCost[mu.id];
    const qtyChanged = qtyDraft !== undefined && !sameAmount(qtyDraft, mu.quantity_used);
    const costChanged = costDraft !== undefined && !sameAmount(costDraft, mu.unit_cost);
    if (!qtyChanged && !costChanged) return;
    await handleToggleMaterial(
      mu.id,
      mu.was_used,
      qtyChanged ? qtyDraft : undefined,
      // '' is a real answer — "I don't know what this cost" clears the price.
      costChanged ? (costDraft === '' ? null : costDraft) : undefined
    );
  };

  const handleRemoveMaterial = async (mu: WorkOrderMaterialUsage) => {
    if (!workOrder) return;
    setRemovingMaterial(mu.id);
    try {
      await workOrderAPI.removeMaterial(workOrder.id, mu.id);
      await loadWorkOrder();
      notifications.show({
        title: 'Material removed',
        message: `${mu.material_name} removed from this work order.`,
        color: 'green',
        icon: <IconCheck size={16} />,
      });
    } catch (err: unknown) {
      notifications.show({
        title: 'Error',
        message: extractErrorMessage(err, 'Failed to remove material.'),
        color: 'red',
      });
    } finally {
      setRemovingMaterial(null);
    }
  };

  const resetAddMaterialForm = () => {
    setAddMode('material');
    setNewMaterialName('');
    setNewMaterialQty(1);
    setNewMaterialUnit('');
    setNewMaterialCost('');
    setNewMaterialItem(null);
    setNewReceiptWhere('');
    setNewReceiptFile(null);
    setItemSearch('');
    resetReceiptRef.current?.();
  };

  /** The line an out-of-pocket receipt becomes: one unit, priced at the total. */
  const receiptMaterialName = newReceiptWhere.trim()
    ? `Misc supplies — ${newReceiptWhere.trim()}`
    : 'Misc supplies';

  const handleAddMaterial = async () => {
    if (!workOrder) return;
    const isReceipt = addMode === 'receipt';
    const materialName = isReceipt ? receiptMaterialName : newMaterialName.trim();
    if (!materialName) return;

    const payload: WorkOrderAdHocMaterialInput = {
      material_name: materialName,
      quantity_used: isReceipt ? 1 : (newMaterialQty === '' ? 1 : newMaterialQty),
    };
    if (!isReceipt && newMaterialUnit.trim()) payload.unit = newMaterialUnit.trim();
    if (newMaterialCost !== '' && newMaterialCost !== null) payload.unit_cost = newMaterialCost;
    // A receipt buy is out of pocket by definition — it moves no stock.
    if (!isReceipt && newMaterialItem) payload.inventory_item = newMaterialItem;

    setSavingMaterial(true);
    try {
      if (newReceiptFile) {
        // Multipart only when a receipt rides along, mirroring the photo client.
        const formData = new FormData();
        Object.entries(payload).forEach(([key, value]) => {
          if (value !== undefined && value !== null) formData.append(key, String(value));
        });
        formData.append('receipt_image', newReceiptFile);
        await workOrderAPI.addMaterial(workOrder.id, formData);
      } else {
        await workOrderAPI.addMaterial(workOrder.id, payload);
      }
      setAddMaterialOpen(false);
      resetAddMaterialForm();
      await loadWorkOrder();
      notifications.show({
        title: 'Material added',
        message: `${materialName} added to this work order.`,
        color: 'green',
        icon: <IconCheck size={16} />,
      });
    } catch (err: unknown) {
      notifications.show({
        title: 'Error',
        message: extractErrorMessage(err, 'Failed to add material.'),
        color: 'red',
      });
    } finally {
      setSavingMaterial(false);
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

  const handleToggleLoto = async (lotoCompletionId: string, isCompleted: boolean) => {
    if (!workOrder) return;
    setTogglingLoto(lotoCompletionId);
    try {
      await workOrderAPI.completeLoto(workOrder.id, lotoCompletionId, { is_completed: isCompleted });
      await loadWorkOrder();
    } catch {
      notifications.show({
        title: 'Error',
        message: 'Failed to update lockout/tagout.',
        color: 'red',
      });
    } finally {
      setTogglingLoto(null);
    }
  };

  const handleSaveLotoNote = async () => {
    if (!workOrder) return;
    setSavingLotoNote(true);
    try {
      const res = await workOrderAPI.updateWorkOrder(workOrder.id, {
        loto_completion_note: lotoNote,
      });
      setWorkOrder(res.data);
      notifications.show({
        title: 'LOTO note saved',
        message: 'Lockout/tagout completion note updated.',
        color: 'green',
        icon: <IconCheck size={16} />,
      });
    } catch {
      notifications.show({
        title: 'Error',
        message: 'Failed to save LOTO note.',
        color: 'red',
      });
    } finally {
      setSavingLotoNote(false);
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

  // op-syov: evidence — the photo a tech takes *while doing a step*. Same
  // add_photo endpoint as the work-order gallery, with the step id attached so
  // it files itself under that step instead of the general pile.
  const handleStepPhotoUpload = async (taskCompletionId: string, file: File) => {
    if (!workOrder) return;
    setUploadingStepPhoto(taskCompletionId);
    try {
      const formData = new FormData();
      formData.append('image', file);
      formData.append('work_order', workOrder.id);
      formData.append('task_completion', taskCompletionId);
      await workOrderAPI.addPhoto(workOrder.id, formData);
      await loadWorkOrder();
      notifications.show({
        title: 'Photo Uploaded',
        message: 'Photo added to this step.',
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
      setUploadingStepPhoto(null);
    }
  };

  // op-768w: actual against estimate, the pair the materials card exists to
  // produce. The actual is the server's; the estimate is the template's
  // per-unit price applied to the quantities *this* work order planned, so a
  // later edit to the template does not rewrite what this job estimated.
  const materialCosts = useMemo(() => {
    const lines = workOrder?.material_usage ?? [];
    const actual =
      workOrder?.actual_material_cost != null
        ? toNumber(workOrder.actual_material_cost)
        : sumActualMaterialCost(lines);
    let estimated = 0;
    let hasEstimate = false;
    lines.forEach((mu) => {
      const perUnit = mu.material ? estimatedUnitCosts[mu.material] : undefined;
      if (perUnit === undefined) return;
      hasEstimate = true;
      estimated += toNumber(mu.quantity_planned) * toNumber(perUnit);
    });
    return { actual, estimated, hasEstimate };
  }, [workOrder, estimatedUnitCosts]);

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
  // Defensive default: the list endpoint's WorkOrder shape omits loto_completions
  // (detail-only), so never assume the array is present.
  const lotoCompletions = workOrder.loto_completions ?? [];
  const completedLoto = lotoCompletions.filter((l) => l.is_completed).length;
  const totalLoto = lotoCompletions.length;
  // Already ordered required-first by the serializer; older payloads omit it.
  const tools = workOrder.tools ?? [];
  // op-pzae: detail-only, and absent from payloads printed before it shipped.
  const referenceDocuments = workOrder.reference_documents?.documents ?? [];
  const referenceLinks = workOrder.reference_documents?.links ?? [];

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

        {/* op-m3so: time on job, right at the top where a tech will reach for
            it. The clock ticks locally but the total is the server's — closing
            the work order records it against the template's estimate. */}
        <WorkOrderTimer
          elapsedSeconds={workOrder.elapsed_seconds ?? 0}
          isTiming={workOrder.is_timing ?? false}
          estimateMinutes={workOrder.estimated_time_minutes}
          completed={workOrder.status === 'completed'}
          busy={timerBusy}
          onToggle={handleTimer}
        />
      </Card>

      {/* op-67q5: what to grab, up front — directly above the electrical /
          lockout cards, mirroring the printed work order's running order so
          paper and screen read the same way. Reference only (no checkboxes):
          nothing here is scanned back off the paper form. */}
      <Card withBorder p="md" radius="md" mb="md" mt="md">
        <Group mb="sm" gap="xs">
          <IconTool size={18} />
          <Title order={5}>Tools Required</Title>
        </Group>
        {tools.length > 0 ? (
          <Stack gap="xs">
            {tools.map((tool) => (
              <Group key={tool.id} gap="xs" wrap="nowrap" align="flex-start">
                <Text size="sm" fw={500} style={{ flex: 1, minWidth: 0 }}>
                  {tool.name}
                  {tool.quantity > 1 && (
                    <Text span size="sm" c="dimmed">
                      {' '}
                      ×{tool.quantity}
                    </Text>
                  )}
                  {tool.location_hint && (
                    <Text size="xs" c="dimmed">
                      {tool.location_hint}
                    </Text>
                  )}
                  {tool.notes && (
                    <Text size="xs" c="dimmed">
                      {tool.notes}
                    </Text>
                  )}
                </Text>
                {tool.is_required && (
                  <Badge color="orange" variant="light" size="sm">
                    Required
                  </Badge>
                )}
              </Group>
            ))}
          </Stack>
        ) : (
          <Text size="sm" c="dimmed">
            No tools specified.
          </Text>
        )}
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
        ) : totalLoto === 0 ? (
          <Text size="sm" c="dimmed">No LOTO required for this asset.</Text>
        ) : null}

        {/* Structured per-energy-source lockout checklist. These are the boxes
            printed on the paper work order (loto_<id>) and read back by scan;
            the tech can also tick them here. Visible whenever the asset has
            recorded energy sources, independent of the free-text lockout_type. */}
        {totalLoto > 0 && (
          <Box mt={workOrder.loto?.is_required ? 'md' : 0}>
            <Group justify="space-between" mb={6}>
              <Text size="xs" c="dimmed" fw={600}>
                Energy sources to isolate
              </Text>
              <Text size="xs" c={completedLoto === totalLoto ? 'green' : 'dimmed'} fw={600}>
                {completedLoto}/{totalLoto}
              </Text>
            </Group>
            <Stack gap="xs">
              {lotoCompletions.map((lc) => (
                <Box
                  key={lc.id}
                  p="sm"
                  style={{
                    borderRadius: 8,
                    backgroundColor: lc.is_completed ? '#f0fff4' : '#fff4e0',
                    border: `1px solid ${lc.is_completed ? '#69db7c' : '#ffd8a8'}`,
                    opacity: togglingLoto === lc.id ? 0.6 : 1,
                  }}
                >
                  <Group gap="md" align="flex-start" wrap="nowrap">
                    <Checkbox
                      checked={lc.is_completed}
                      onChange={(e) => handleToggleLoto(lc.id, e.currentTarget.checked)}
                      disabled={togglingLoto === lc.id}
                      size="lg"
                      mt={2}
                    />
                    <Box style={{ flex: 1 }}>
                      <Text
                        fw={lc.is_completed ? 400 : 600}
                        size="sm"
                        td={lc.is_completed ? 'line-through' : 'none'}
                        c={lc.is_completed ? 'dimmed' : 'inherit'}
                      >
                        {lc.source_label}
                      </Text>
                      {lc.isolation_point && (
                        <Text size="xs" c="dimmed">Isolate at: {lc.isolation_point}</Text>
                      )}
                      {lc.required_devices && (
                        <Text size="xs" c="dimmed">Devices: {lc.required_devices}</Text>
                      )}
                      {lc.is_completed && lc.completed_at && (
                        <Text size="xs" c="green">
                          ✓ {lc.completed_by_name || 'Locked out'} ·{' '}
                          {new Date(lc.completed_at).toLocaleString()}
                        </Text>
                      )}
                    </Box>
                  </Group>
                </Box>
              ))}
            </Stack>
          </Box>
        )}

        {/* Free-text LOTO completion note — the "both": structured boxes above
            plus this free-text half (recorded web-side, not OMR'd). */}
        <Box mt="md">
          <Text size="xs" c="dimmed" fw={600} mb={4}>
            Lockout/tagout completion note
          </Text>
          <Textarea
            value={lotoNote}
            onChange={(e) => setLotoNote(e.currentTarget.value)}
            placeholder="Notes on how the asset was locked out / verified de-energized…"
            autosize
            minRows={2}
            maxRows={6}
            mb="xs"
          />
          <Button
            size="xs"
            variant="light"
            onClick={handleSaveLotoNote}
            loading={savingLotoNote}
            leftSection={<IconCheck size={14} />}
          >
            Save LOTO note
          </Button>
        </Box>
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
                    {/* Evidence the tech attached to this step: "here is what
                        I did". The reference photo alongside the row is the
                        other half: what the step should look like. */}
                    {(tc.evidence_photos ?? []).length > 0 && (
                      <Group gap="xs" wrap="wrap" mt="xs">
                        {(tc.evidence_photos ?? []).map((photo) => (
                          <Image
                            key={photo.id}
                            src={photo.image_url ?? undefined}
                            w={64}
                            h={48}
                            fit="cover"
                            radius="sm"
                            alt={photo.caption || `Photo of ${tc.task_title}`}
                            style={{ cursor: 'pointer', border: '1px solid #dee2e6' }}
                            onClick={() =>
                              photo.image_url && window.open(photo.image_url, '_blank')
                            }
                          />
                        ))}
                      </Group>
                    )}
                    <StepEvidenceUpload
                      stepTitle={tc.task_title}
                      uploading={uploadingStepPhoto === tc.id}
                      onPick={(file) => handleStepPhotoUpload(tc.id, file)}
                    />
                  </Box>
                  <StepTimer
                    stepTitle={tc.task_title}
                    elapsedSeconds={tc.elapsed_seconds ?? 0}
                    isTiming={tc.is_timing ?? false}
                    locked={tc.is_completed || workOrder.status === 'completed'}
                    busy={timingStep === tc.id}
                    onToggle={(action) => handleStepTimer(tc.id, action)}
                  />
                  {tc.task_reference_image_url && (
                    <StepReferencePhoto
                      url={tc.task_reference_image_url}
                      stepTitle={tc.task_title}
                    />
                  )}
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
      <Card withBorder p="md" radius="md" mb="md">
        <Group mb="sm" justify="space-between">
          <Group gap="xs">
            <IconTag size={18} />
            <Title order={5}>Materials</Title>
            {workOrder.material_usage.length > 0 && (
              <Badge color="gray" size="sm">{workOrder.material_usage.length}</Badge>
            )}
          </Group>
          <Button
            size="sm"
            variant="light"
            leftSection={<IconPlus size={16} />}
            onClick={() => {
              resetAddMaterialForm();
              setAddMaterialOpen(true);
            }}
          >
            Add material
          </Button>
        </Group>

        {workOrder.material_usage.length === 0 ? (
          <Text size="sm" c="dimmed" ta="center" py="sm">
            No materials recorded. Add what you used or bought to do this job.
          </Text>
        ) : (
          <Stack gap="xs">
            {workOrder.material_usage.map((mu) => {
              // A PO-sourced line mirrors its purchase order (op-bu80): the PO
              // owns its quantity and price, and a later receipt re-writes
              // both, so the job page shows them rather than offering edits.
              const fromPurchaseOrder = Boolean(mu.purchase_order_item);
              const busy = togglingMaterial === mu.id || removingMaterial === mu.id;
              return (
                <Box
                  key={mu.id}
                  p="sm"
                  style={{
                    borderRadius: 8,
                    backgroundColor: mu.was_used ? '#f0fff4' : '#f8f9fa',
                    border: `1px solid ${mu.was_used ? '#69db7c' : '#dee2e6'}`,
                    opacity: busy ? 0.6 : 1,
                  }}
                >
                  <Group gap="md" wrap="nowrap" justify="space-between" align="flex-start">
                    <Checkbox
                      checked={mu.was_used}
                      onChange={(e) => {
                        const checked = e.currentTarget.checked;
                        handleToggleMaterial(
                          mu.id,
                          checked,
                          checked ? (materialQty[mu.id] ?? mu.quantity_used) : undefined,
                          checked ? materialCost[mu.id] : undefined
                        );
                      }}
                      disabled={busy}
                      size="lg"
                      label={
                        <Box>
                          <Group gap={6}>
                            <Text
                              fw={mu.was_used ? 400 : 600}
                              size="md"
                              td={mu.was_used ? 'line-through' : 'none'}
                              c={mu.was_used ? 'dimmed' : 'inherit'}
                            >
                              {mu.material_name}
                            </Text>
                            {mu.is_ad_hoc && !fromPurchaseOrder && (
                              <Badge size="xs" variant="light" color="blue">added</Badge>
                            )}
                            {fromPurchaseOrder && (
                              <Badge size="xs" variant="light" color="grape">from PO</Badge>
                            )}
                          </Group>
                          <Text size="xs" c="dimmed">
                            Planned: {mu.quantity_planned}
                            {mu.unit ? ` ${mu.unit}` : ''}
                            {mu.inventory_item_name ? ` · stock: ${mu.inventory_item_name}` : ''}
                          </Text>
                          <Group gap={8}>
                            <Text size="xs" c={mu.actual_cost ? 'inherit' : 'dimmed'}>
                              Cost: {formatMoney(mu.actual_cost)}
                              {mu.unit_cost ? ` (${formatMoney(mu.unit_cost)}/unit)` : ''}
                            </Text>
                            {mu.receipt_url && (
                              <Anchor
                                size="xs"
                                href={mu.receipt_url}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                Receipt
                              </Anchor>
                            )}
                          </Group>
                        </Box>
                      }
                    />
                    <Group gap="xs" wrap="nowrap" align="flex-start">
                      {mu.stock_applied ? (
                        <Stack gap={0} align="flex-end">
                          <Text
                            size="xs"
                            c="teal.7"
                            fw={600}
                            style={{ whiteSpace: 'nowrap' }}
                          >
                            −{mu.applied_quantity} from stock
                          </Text>
                          <Text size="xs" c="dimmed">
                            usage logged
                          </Text>
                        </Stack>
                      ) : fromPurchaseOrder ? (
                        <Stack gap={0} align="flex-end">
                          <Text size="xs" fw={600} style={{ whiteSpace: 'nowrap' }}>
                            {mu.quantity_used} received
                          </Text>
                          <Text size="xs" c="dimmed">
                            priced by the PO
                          </Text>
                        </Stack>
                      ) : (
                        <>
                          {/* flexShrink 0 on both: the row is `nowrap`, and a
                              shrunk NumberInput lays its stepper controls over
                              whatever sits next to it — which put the remove
                              button out of reach. */}
                          <NumberInput
                            size="xs"
                            label="Qty used"
                            value={materialQty[mu.id] ?? Number(mu.quantity_used)}
                            onChange={(v) => setMaterialQty((prev) => ({ ...prev, [mu.id]: v }))}
                            onBlur={() => handleCommitMaterialEdits(mu)}
                            min={0}
                            step={1}
                            disabled={busy}
                            style={{ width: 110, flexShrink: 0 }}
                          />
                          <NumberInput
                            size="xs"
                            label="Unit cost"
                            aria-label={`Unit cost for ${mu.material_name}`}
                            value={materialCost[mu.id] ?? (mu.unit_cost ?? '')}
                            onChange={(v) => setMaterialCost((prev) => ({ ...prev, [mu.id]: v }))}
                            onBlur={() => handleCommitMaterialEdits(mu)}
                            min={0}
                            prefix="$"
                            decimalScale={2}
                            // Stepping a price by ±1 is never what anyone wants,
                            // and the controls are what did the overlapping.
                            hideControls
                            disabled={busy}
                            style={{ width: 110, flexShrink: 0 }}
                          />
                        </>
                      )}
                      {mu.is_ad_hoc && !fromPurchaseOrder && (
                        <Tooltip
                          label={
                            mu.stock_applied
                              ? 'Un-mark it as used first to restore the stock'
                              : 'Remove this line'
                          }
                        >
                          {/* A disabled ActionIcon swallows pointer events, so
                              the tooltip needs a wrapper to hang off. */}
                          <Box style={{ flexShrink: 0 }}>
                            <ActionIcon
                              variant="subtle"
                              color="red"
                              mt={20}
                              aria-label={`Remove ${mu.material_name}`}
                              disabled={busy || mu.stock_applied}
                              onClick={() => handleRemoveMaterial(mu)}
                            >
                              <IconTrash size={16} />
                            </ActionIcon>
                          </Box>
                        </Tooltip>
                      )}
                    </Group>
                  </Group>
                </Box>
              );
            })}
          </Stack>
        )}

        {workOrder.material_usage.length > 0 && (
          <>
            <Divider my="sm" />
            <Group justify="space-between" align="flex-end">
              <Box>
                <Text size="xs" c="dimmed">Actual material cost</Text>
                <Text fw={700} size="lg" data-testid="wo-actual-material-cost">
                  {formatMoney(materialCosts.actual)}
                </Text>
              </Box>
              <Box ta="right">
                {materialCosts.hasEstimate ? (
                  <>
                    <Text size="xs" c="dimmed">Estimated</Text>
                    <Text size="sm" data-testid="wo-estimated-material-cost">
                      {formatMoney(materialCosts.estimated)}
                      {' · '}
                      <Text
                        span
                        fw={600}
                        c={materialCosts.actual > materialCosts.estimated ? 'red.7' : 'teal.7'}
                      >
                        {materialCosts.actual > materialCosts.estimated
                          ? `${formatMoney(materialCosts.actual - materialCosts.estimated)} over`
                          : `${formatMoney(materialCosts.estimated - materialCosts.actual)} under`}
                      </Text>
                    </Text>
                  </>
                ) : (
                  <Text size="xs" c="dimmed">No estimate for this job</Text>
                )}
              </Box>
            </Group>
          </>
        )}
      </Card>

      {/* Ordered for this work order (op-bu80) */}
      {workOrder.purchase_order_lines && workOrder.purchase_order_lines.length > 0 && (
        <Card withBorder p="md" radius="md" mb="md">
          <Group mb="sm" gap="xs">
            <IconShoppingCart size={18} />
            <Title order={5}>Ordered for this work order</Title>
            <Badge color="gray" size="sm">{workOrder.purchase_order_lines.length}</Badge>
          </Group>
          <Stack gap="xs">
            {workOrder.purchase_order_lines.map((line) => (
              <Box
                key={line.id}
                p="sm"
                style={{
                  borderRadius: 8,
                  backgroundColor: '#f8f9fa',
                  border: '1px solid #dee2e6',
                }}
              >
                <Group justify="space-between" wrap="nowrap" align="flex-start" gap="sm">
                  <Box style={{ flex: 1, minWidth: 0 }}>
                    <Group gap="xs">
                      <Text fw={600} size="sm">{line.name}</Text>
                      <Badge
                        size="xs"
                        variant="light"
                        color={line.is_fully_received ? 'green' : 'yellow'}
                      >
                        {line.is_fully_received
                          ? 'received'
                          : `${line.quantity_pending} on order`}
                      </Badge>
                    </Group>
                    <Text size="xs" c="dimmed">
                      <Anchor
                        component={Link}
                        to={`/purchasing/orders/${line.purchase_order_id}`}
                        size="xs"
                      >
                        {line.po_number}
                      </Anchor>
                      {line.supplier_name ? ` · ${line.supplier_name}` : ''}
                      {` · ${line.quantity_received}/${line.quantity_ordered} received`}
                    </Text>
                    {!line.is_fully_received && line.expected_delivery_date && (
                      <Text size="xs" c="dimmed">
                        Expected {formatDateOnly(line.expected_delivery_date)}
                      </Text>
                    )}
                  </Box>
                  <Text size="sm" fw={600} style={{ whiteSpace: 'nowrap' }}>
                    {formatMoney(line.unit_cost)}/unit
                  </Text>
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

      {/* op-pzae: manual / revision history / reference links, at the point of
          sign-off — whoever performs and signs the job should be able to reach
          the docs without hunting for them. Reuses the asset's document library
          (the work order stores no links of its own), and mirrors the block the
          printed form puts above its sign-off box: both read one builder. */}
      <Card withBorder p="md" radius="md" mb="md">
        <Group mb="sm" gap="xs">
          <IconFileText size={18} />
          <Title order={5}>Documentation &amp; References</Title>
        </Group>
        {referenceDocuments.length > 0 || referenceLinks.length > 0 ? (
          <Stack gap="sm">
            {referenceDocuments.map((doc) => (
              <Box key={doc.id}>
                <Group gap="xs" wrap="nowrap" align="center">
                  <Box style={{ flex: 1, minWidth: 0 }}>
                    {doc.file_url ? (
                      <Anchor
                        href={doc.file_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        size="sm"
                        fw={500}
                      >
                        {doc.title}
                      </Anchor>
                    ) : (
                      <Text size="sm" fw={500}>
                        {doc.title}
                      </Text>
                    )}
                  </Box>
                  <Badge color="gray" variant="light" size="sm">
                    {doc.category_display}
                  </Badge>
                  <Badge color="blue" variant="light" size="sm">
                    rev {doc.version}
                  </Badge>
                </Group>
                {doc.revisions.length > 0 && (
                  <Box mt={4}>
                    <UnstyledButton
                      onClick={() =>
                        setOpenRevisions((prev) => ({ ...prev, [doc.id]: !prev[doc.id] }))
                      }
                    >
                      <Text size="xs" c="dimmed" td="underline">
                        {openRevisions[doc.id]
                          ? 'Hide revision history'
                          : `Revision history (${doc.revisions.length})`}
                      </Text>
                    </UnstyledButton>
                    {openRevisions[doc.id] && (
                      <Stack gap={2} mt={4} pl="sm">
                        {doc.revisions.map((rev) => (
                          <Text key={rev.id} size="xs" c="dimmed">
                            rev {rev.version}
                            {rev.uploaded_at
                              ? ` · ${new Date(rev.uploaded_at).toLocaleDateString()}`
                              : ''}
                            {rev.file_url && (
                              <>
                                {' · '}
                                <Anchor
                                  href={rev.file_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  size="xs"
                                >
                                  Open
                                </Anchor>
                              </>
                            )}
                          </Text>
                        ))}
                      </Stack>
                    )}
                  </Box>
                )}
              </Box>
            ))}
            {referenceLinks.length > 0 && (
              <Stack gap={2}>
                {referenceLinks.map((link) => (
                  <Anchor
                    key={link.label}
                    href={link.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    size="sm"
                  >
                    {link.label}
                  </Anchor>
                ))}
              </Stack>
            )}
          </Stack>
        ) : (
          <Text size="sm" c="dimmed">
            No linked documents.
          </Text>
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

      {/* op-768w: record a material used or bought to do this job */}
      <Modal
        opened={addMaterialOpen}
        onClose={() => setAddMaterialOpen(false)}
        title="Add material"
        centered
      >
        <Stack gap="sm">
          <SegmentedControl
            value={addMode}
            onChange={(value) => setAddMode(value as 'material' | 'receipt')}
            data={[
              { value: 'material', label: 'Material used' },
              { value: 'receipt', label: 'Out-of-pocket receipt' },
            ]}
            fullWidth
          />

          {addMode === 'material' ? (
            <>
              <TextInput
                label="Material name"
                placeholder="What did you use?"
                value={newMaterialName}
                onChange={(e) => setNewMaterialName(e.currentTarget.value)}
                required
              />
              <Select
                label="Inventory item"
                description="Link it to draw the material from stock when you mark it used. Leave empty for something bought just for this job."
                placeholder="Search inventory…"
                data={itemOptions}
                value={newMaterialItem}
                searchable
                clearable
                nothingFoundMessage="No matching items"
                searchValue={itemSearch}
                onSearchChange={setItemSearch}
                // The server already matched the query (on sku and description
                // too), so re-filtering the results by label would hide hits.
                filter={({ options }) => options}
                onChange={(value) => {
                  setNewMaterialItem(value);
                  // Seed the price from what the item costs today — a default,
                  // not a lock: the tech overrides it when the real one differs.
                  const seeded = value ? itemCosts[value] : null;
                  if (seeded != null && newMaterialCost === '') setNewMaterialCost(seeded);
                }}
              />
              <Group grow>
                <NumberInput
                  label="Quantity"
                  value={newMaterialQty}
                  onChange={setNewMaterialQty}
                  min={0}
                  step={1}
                />
                <TextInput
                  label="Unit"
                  placeholder="ea, ft, qt…"
                  value={newMaterialUnit}
                  onChange={(e) => setNewMaterialUnit(e.currentTarget.value)}
                />
              </Group>
              <NumberInput
                label="Unit cost"
                description="Real price paid per unit. Leave empty if nobody prices it."
                value={newMaterialCost}
                onChange={setNewMaterialCost}
                min={0}
                prefix="$"
                decimalScale={2}
              />
            </>
          ) : (
            <>
              <TextInput
                label="Bought where"
                placeholder="Hardware store"
                value={newReceiptWhere}
                onChange={(e) => setNewReceiptWhere(e.currentTarget.value)}
              />
              <NumberInput
                label="Amount"
                description="What the receipt totals. Recorded as one line at this price."
                value={newMaterialCost}
                onChange={setNewMaterialCost}
                min={0}
                prefix="$"
                decimalScale={2}
              />
              <Text size="xs" c="dimmed">
                Records as “{receiptMaterialName}”. Moves no stock — it is money
                spent, not something drawn from inventory.
              </Text>
            </>
          )}

          <Group gap="xs">
            <FileButton
              resetRef={resetReceiptRef}
              onChange={setNewReceiptFile}
              accept="image/*"
            >
              {(props) => (
                <Button
                  {...props}
                  size="sm"
                  variant="light"
                  leftSection={<IconReceipt size={16} />}
                >
                  {newReceiptFile ? 'Change receipt' : 'Attach receipt'}
                </Button>
              )}
            </FileButton>
            {newReceiptFile && (
              <Text size="xs" c="dimmed" truncate>
                {newReceiptFile.name}
              </Text>
            )}
          </Group>

          <Group justify="flex-end" gap="xs">
            <Button variant="default" onClick={() => setAddMaterialOpen(false)}>
              Cancel
            </Button>
            <Button
              leftSection={<IconPlus size={16} />}
              onClick={handleAddMaterial}
              loading={savingMaterial}
              disabled={addMode === 'material' && !newMaterialName.trim()}
            >
              Add
            </Button>
          </Group>
        </Stack>
      </Modal>
    </WorkspacePage>
  );
};

export default WorkOrderPage;
