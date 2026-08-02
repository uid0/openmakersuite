/**
 * Storage-slot console — the physical racking project storage is handed
 * out from.
 *
 * A slot is `<rack><level><position>` (1A1, 1B3, 2A2) and carries a
 * permanent AprilTag plus a printed card whose QR sends a member to the
 * kiosk with that slot pre-selected. This page is the warden's side of
 * that loop: see what's on the rack and who's in it, add racking, and
 * print the cards.
 *
 * Two create paths on purpose, because the two real jobs differ:
 *   - "Generate rack" bulk-creates a whole rack from a per-level spec
 *     (idempotent — re-running after adding a level reports the existing
 *     slots as skipped), for racking a new aisle;
 *   - "Add slot" creates one, for the odd shelf that doesn't fit the grid.
 *
 * Printing mirrors the index-card UX: pick slots (or a whole rack), get
 * the Avery 5388 sheet as a download. Per-row preview shows one card,
 * plus the kiosk URL and marker ID it encodes.
 *
 * Permissions: the whole /project-storage/slots/ viewset is
 * IsStorageAdminOrStaff, so a member of the "Storage Admin" Django group
 * can run the racking without being platform-wide staff.
 */
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  NumberInput,
  Paper,
  SegmentedControl,
  Stack,
  Switch,
  Table,
  Text,
  Textarea,
  TextInput,
  Tooltip,
} from '@mantine/core';
import {
  IconAlertCircle,
  IconEye,
  IconPlus,
  IconPrinter,
  IconRefresh,
  IconStack2,
  IconTrash,
} from '@tabler/icons-react';
import axios from 'axios';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import WorkspacePage from '../components/landing/WorkspacePage';
import { storageSlotsAPI } from '../services/api';
import { GenerateRackResult, StorageSlot, StorageSlotCardPreview } from '../types';
import { confirmDelete, showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

type OccupancyFilter = 'all' | 'free' | 'occupied';
type SortKey = 'code' | 'level' | 'position' | 'occupancy' | 'tag';

// DRF's stock PageNumberPagination is configured without a
// page_size_query_param, so the server page size is fixed at 50 no matter
// what we ask for — a 12-level rack is three pages. Walk `next` instead,
// with a cap so a runaway response can't spin forever.
const MAX_PAGES = 20;

/**
 * Pull the message out of a *failed* card download.
 *
 * With `responseType: 'blob'` axios hands back the error body as a Blob
 * too, so extractErrorMessage would only ever see an opaque object. The
 * card endpoints answer 404 with JSON (`detail` plus `missing_ids` or
 * `no_slots_matched`), which is exactly what the warden needs to read.
 */
const readBlobError = async (err: unknown, fallback: string): Promise<string> => {
  if (axios.isAxiosError(err) && err.response?.data instanceof Blob) {
    try {
      const parsed = JSON.parse(await err.response.data.text());
      return extractErrorMessage({ data: parsed }, fallback);
    } catch {
      return fallback;
    }
  }
  return extractErrorMessage(err, fallback);
};

const downloadPdf = (data: BlobPart, filename: string): void => {
  const url = window.URL.createObjectURL(new Blob([data], { type: 'application/pdf' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.URL.revokeObjectURL(url);
};

// ---------------------------------------------------------------------------
// Generate-rack form
// ---------------------------------------------------------------------------

interface LevelRow {
  level: string;
  positions: number | '';
  requiresPalletJack: boolean;
}

const nextLevelLetter = (rows: LevelRow[]): string => {
  const used = rows.map((r) => r.level.toUpperCase()).filter(Boolean);
  for (let i = 0; i < 26; i += 1) {
    const letter = String.fromCharCode(65 + i);
    if (!used.includes(letter)) return letter;
  }
  return '';
};

interface GenerateRackModalProps {
  opened: boolean;
  onClose: () => void;
  onGenerated: (result: GenerateRackResult) => void;
}

const GenerateRackModal: React.FC<GenerateRackModalProps> = ({
  opened,
  onClose,
  onGenerated,
}) => {
  const [rack, setRack] = useState<number | ''>(1);
  const [rows, setRows] = useState<LevelRow[]>([
    { level: 'A', positions: 12, requiresPalletJack: false },
  ]);
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const patchRow = (index: number, patch: Partial<LevelRow>) =>
    setRows((current) =>
      current.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );

  const valid =
    rack !== '' &&
    Number(rack) >= 1 &&
    rows.length > 0 &&
    rows.every((r) => /^[A-Za-z]$/.test(r.level) && r.positions !== '' && Number(r.positions) >= 1);

  const submit = async () => {
    if (!valid) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await storageSlotsAPI.generateRack({
        rack: Number(rack),
        // Upper-cased here as well as server-side so the summary we show
        // back names the same levels the backend created.
        levels: rows.map((row) => ({
          level: row.level.toUpperCase(),
          positions: Number(row.positions),
          requires_pallet_jack: row.requiresPalletJack,
        })),
        notes: notes.trim(),
      });
      onGenerated(res.data);
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not generate that rack.'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Generate a rack"
      centered
      size="lg"
    >
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          Creates every slot on the rack from a per-level spec. Levels run
          A (ground) upward; mark the ones that need a pallet jack to reach.
          Re-running is safe — slots that already exist are left alone.
        </Text>

        {error && (
          <Alert color="red" variant="light" icon={<IconAlertCircle size={18} />} data-testid="generate-error">
            {error}
          </Alert>
        )}

        <NumberInput
          label="Rack number"
          description="The leading digits of the code — 1 in 1A1."
          value={rack}
          onChange={(v) => setRack(v === '' ? '' : Number(v))}
          min={1}
          allowDecimal={false}
          allowNegative={false}
          required
          data-testid="generate-rack-number"
        />

        <Stack gap="xs">
          <Text size="sm" fw={600}>
            Levels
          </Text>
          {rows.map((row, index) => (
            <Group key={index} align="flex-end" wrap="nowrap" gap="sm">
              <TextInput
                label="Level"
                w={90}
                maxLength={1}
                value={row.level}
                onChange={(e) =>
                  patchRow(index, { level: e.currentTarget.value.toUpperCase() })
                }
                data-testid={`generate-level-${index}`}
              />
              <NumberInput
                label="Positions"
                w={130}
                value={row.positions}
                onChange={(v) => patchRow(index, { positions: v === '' ? '' : Number(v) })}
                min={1}
                max={100}
                allowDecimal={false}
                allowNegative={false}
                data-testid={`generate-positions-${index}`}
              />
              <Checkbox
                label="Pallet jack"
                checked={row.requiresPalletJack}
                onChange={(e) =>
                  patchRow(index, { requiresPalletJack: e.currentTarget.checked })
                }
                data-testid={`generate-jack-${index}`}
                mb={8}
              />
              <Button
                variant="subtle"
                color="red"
                size="compact-sm"
                mb={4}
                disabled={rows.length === 1}
                onClick={() => setRows((c) => c.filter((_, i) => i !== index))}
                data-testid={`generate-remove-level-${index}`}
              >
                Remove
              </Button>
            </Group>
          ))}
          <Group>
            <Button
              variant="light"
              size="compact-sm"
              leftSection={<IconPlus size={14} />}
              onClick={() =>
                setRows((c) => [
                  ...c,
                  { level: nextLevelLetter(c), positions: 12, requiresPalletJack: false },
                ])
              }
              data-testid="generate-add-level"
            >
              Add level
            </Button>
          </Group>
        </Stack>

        <Textarea
          label="Notes"
          description="Optional — copied onto every slot this run creates."
          value={notes}
          onChange={(e) => setNotes(e.currentTarget.value)}
          autosize
          minRows={2}
          data-testid="generate-notes"
        />

        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={!valid || submitting}
            leftSection={submitting ? <Loader size="xs" color="white" /> : <IconStack2 size={16} />}
            data-testid="generate-submit"
          >
            Generate rack
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
};

// ---------------------------------------------------------------------------
// Single-slot form
// ---------------------------------------------------------------------------

interface AddSlotModalProps {
  opened: boolean;
  onClose: () => void;
  onCreated: (slot: StorageSlot) => void;
}

const AddSlotModal: React.FC<AddSlotModalProps> = ({ opened, onClose, onCreated }) => {
  const [rack, setRack] = useState<number | ''>(1);
  const [level, setLevel] = useState('A');
  const [position, setPosition] = useState<number | ''>(1);
  const [requiresPalletJack, setRequiresPalletJack] = useState(false);
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const valid =
    rack !== '' && Number(rack) >= 1 && /^[A-Za-z]$/.test(level) && position !== '' && Number(position) >= 1;

  // The code is derived server-side from the components; showing it here
  // is just so the warden can confirm they're about to create 1A1 and not
  // 1A11 before they commit to a permanent marker.
  const previewCode = valid ? `${Number(rack)}${level.toUpperCase()}${Number(position)}` : '—';

  const submit = async () => {
    if (!valid) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await storageSlotsAPI.create({
        rack: Number(rack),
        level: level.toUpperCase(),
        position: Number(position),
        requires_pallet_jack: requiresPalletJack,
        notes: notes.trim(),
      });
      onCreated(res.data);
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not create that slot.'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Add a slot" centered>
      <Stack gap="md">
        {error && (
          <Alert color="red" variant="light" icon={<IconAlertCircle size={18} />} data-testid="add-slot-error">
            {error}
          </Alert>
        )}

        <Group grow align="flex-start">
          <NumberInput
            label="Rack"
            value={rack}
            onChange={(v) => setRack(v === '' ? '' : Number(v))}
            min={1}
            allowDecimal={false}
            allowNegative={false}
            required
            data-testid="add-slot-rack"
          />
          <TextInput
            label="Level"
            maxLength={1}
            value={level}
            onChange={(e) => setLevel(e.currentTarget.value.toUpperCase())}
            required
            data-testid="add-slot-level"
          />
          <NumberInput
            label="Position"
            value={position}
            onChange={(v) => setPosition(v === '' ? '' : Number(v))}
            min={1}
            allowDecimal={false}
            allowNegative={false}
            required
            data-testid="add-slot-position"
          />
        </Group>

        <Text size="sm" c="dimmed">
          Code:{' '}
          <Text span ff="monospace" fw={700} data-testid="add-slot-code-preview">
            {previewCode}
          </Text>
        </Text>

        <Checkbox
          label="Needs a pallet jack to reach"
          checked={requiresPalletJack}
          onChange={(e) => setRequiresPalletJack(e.currentTarget.checked)}
          data-testid="add-slot-jack"
        />

        <Textarea
          label="Notes"
          value={notes}
          onChange={(e) => setNotes(e.currentTarget.value)}
          autosize
          minRows={2}
          data-testid="add-slot-notes"
        />

        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={!valid || submitting}
            leftSection={submitting ? <Loader size="xs" color="white" /> : <IconPlus size={16} />}
            data-testid="add-slot-submit"
          >
            Add slot
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
};

// ---------------------------------------------------------------------------
// Card preview
// ---------------------------------------------------------------------------

const SlotCardPreviewModal: React.FC<{ slot: StorageSlot | null; onClose: () => void }> = ({
  slot,
  onClose,
}) => {
  const [preview, setPreview] = useState<StorageSlotCardPreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!slot) {
      setPreview(null);
      setError(null);
      return;
    }
    let cancelled = false;
    storageSlotsAPI
      .cardPreview(slot.code)
      .then((res) => {
        if (!cancelled) setPreview(res?.data ?? null);
      })
      .catch((err) => {
        if (!cancelled) setError(extractErrorMessage(err, 'Could not render that card.'));
      });
    return () => {
      cancelled = true;
    };
  }, [slot]);

  return (
    <Modal
      opened={slot !== null}
      onClose={onClose}
      title={slot ? `Card — ${slot.code}` : 'Card preview'}
      centered
      size="lg"
    >
      <Stack gap="sm">
        {error && (
          <Alert color="red" variant="light" data-testid="card-preview-error">
            {error}
          </Alert>
        )}
        {!error && !preview && (
          <Group justify="center" p="xl">
            <Loader />
          </Group>
        )}
        {preview && (
          <>
            <iframe
              // The endpoint hands back a base64 PDF precisely so the
              // preview needs no second round trip for the bytes.
              src={`data:application/pdf;base64,${preview.preview}`}
              title={`Storage slot card ${preview.code}`}
              style={{ width: '100%', height: 420, border: '1px solid #dee2e6' }}
              data-testid="card-preview-frame"
            />
            <Text size="xs" c="dimmed">
              QR sends the member to{' '}
              <Anchor href={preview.kiosk_url} size="xs" target="_blank" rel="noreferrer">
                {preview.kiosk_url}
              </Anchor>
            </Text>
            <Text size="xs" c="dimmed">
              AprilTag marker:{' '}
              {preview.april_tag_id === null ? 'none allocated' : `#${preview.april_tag_id}`}
            </Text>
            <Group justify="flex-end">
              <Button
                variant="light"
                leftSection={<IconPrinter size={16} />}
                onClick={() =>
                  downloadPdf(
                    Uint8Array.from(atob(preview.preview), (c) => c.charCodeAt(0)),
                    preview.filename,
                  )
                }
                data-testid="card-preview-download"
              >
                Download card
              </Button>
            </Group>
          </>
        )}
      </Stack>
    </Modal>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const StorageSlotsPage: React.FC = () => {
  const [slots, setSlots] = useState<StorageSlot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [rackFilter, setRackFilter] = useState<number | ''>('');
  const [levelFilter, setLevelFilter] = useState('');
  const [occupancy, setOccupancy] = useState<OccupancyFilter>('all');
  const [palletJackOnly, setPalletJackOnly] = useState(false);
  const [includeRetired, setIncludeRetired] = useState(false);

  const [sortKey, setSortKey] = useState<SortKey>('code');
  const [sortDesc, setSortDesc] = useState(false);

  const [selected, setSelected] = useState<number[]>([]);
  const [printing, setPrinting] = useState(false);

  const [generateOpen, setGenerateOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [previewSlot, setPreviewSlot] = useState<StorageSlot | null>(null);
  const [lastGenerated, setLastGenerated] = useState<GenerateRackResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = {
        ...(rackFilter !== '' ? { rack: Number(rackFilter) } : {}),
        ...(levelFilter.trim() ? { level: levelFilter.trim().toUpperCase() } : {}),
        ...(occupancy === 'all'
          ? {}
          : { occupied: (occupancy === 'occupied' ? 'true' : 'false') as 'true' | 'false' }),
        ...(palletJackOnly ? { requires_pallet_jack: 'true' as const } : {}),
        ...(includeRetired ? {} : { is_active: 'true' as const }),
      };
      const all: StorageSlot[] = [];
      for (let page = 1; page <= MAX_PAGES; page += 1) {
        // Sequential on purpose — we don't know the page count until a
        // response says `next` is null.
        const res = await storageSlotsAPI.list({ ...params, page });
        all.push(...(res?.data?.results ?? []));
        if (!res?.data?.next) break;
      }
      setSlots(all);
      // Drop selections that just filtered out of view — printing a card
      // for a slot the warden can no longer see would be a surprise.
      const visible = new Set(all.map((s) => s.id));
      setSelected((current) => current.filter((id) => visible.has(id)));
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load storage slots.'));
    } finally {
      setLoading(false);
    }
  }, [rackFilter, levelFilter, occupancy, palletJackOnly, includeRetired]);

  useEffect(() => {
    load();
  }, [load]);

  // Rack is the physical unit — a sheet of cards goes up one rack — so
  // rows always group by rack and the sort orders within it.
  const racks = useMemo(() => {
    const compare = (a: StorageSlot, b: StorageSlot): number => {
      switch (sortKey) {
        case 'level':
          return a.level.localeCompare(b.level) || a.position - b.position;
        case 'position':
          return a.position - b.position || a.level.localeCompare(b.level);
        case 'occupancy':
          return (
            Number(a.is_occupied) - Number(b.is_occupied) ||
            a.level.localeCompare(b.level) ||
            a.position - b.position
          );
        case 'tag':
          // Untagged slots sort last either way — "no marker yet" is not a
          // low tag ID, and burying them under a descending sort would hide
          // exactly the rows that need a card run.
          return (
            (a.april_tag_id ?? Number.MAX_SAFE_INTEGER) -
            (b.april_tag_id ?? Number.MAX_SAFE_INTEGER)
          );
        default:
          return a.level.localeCompare(b.level) || a.position - b.position;
      }
    };
    const byRack = new Map<number, StorageSlot[]>();
    slots.forEach((slot) => {
      const bucket = byRack.get(slot.rack);
      if (bucket) bucket.push(slot);
      else byRack.set(slot.rack, [slot]);
    });
    return [...byRack.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([rack, rackSlots]) => ({
        rack,
        slots: [...rackSlots].sort((a, b) => (sortDesc ? -compare(a, b) : compare(a, b))),
        free: rackSlots.filter((s) => !s.is_occupied).length,
      }));
  }, [slots, sortKey, sortDesc]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) setSortDesc((d) => !d);
    else {
      setSortKey(key);
      setSortDesc(false);
    }
  };

  const sortIndicator = (key: SortKey) => (sortKey === key ? (sortDesc ? ' ▾' : ' ▴') : '');

  const toggleSlot = (id: number) =>
    setSelected((current) =>
      current.includes(id) ? current.filter((s) => s !== id) : [...current, id],
    );

  const toggleRack = (rackSlots: StorageSlot[]) => {
    const ids = rackSlots.map((s) => s.id);
    const allSelected = ids.every((id) => selected.includes(id));
    setSelected((current) =>
      allSelected
        ? current.filter((id) => !ids.includes(id))
        : [...current, ...ids.filter((id) => !current.includes(id))],
    );
  };

  const printSelected = async () => {
    if (selected.length === 0) return;
    setPrinting(true);
    try {
      const res = await storageSlotsAPI.printCards({ slot_ids: selected });
      downloadPdf(res.data, 'storage_slot_cards.pdf');
    } catch (err) {
      showError(await readBlobError(err, 'Could not print those cards.'));
    } finally {
      setPrinting(false);
    }
  };

  const printRack = async (rack: number) => {
    setPrinting(true);
    // Match what's on screen: the level filter narrows the sheet and
    // retired slots ride along only when they're showing.
    const payload: { rack: number; level?: string; include_inactive?: boolean } = { rack };
    if (levelFilter.trim()) payload.level = levelFilter.trim().toUpperCase();
    if (includeRetired) payload.include_inactive = true;
    try {
      const res = await storageSlotsAPI.printCards(payload);
      downloadPdf(res.data, `storage_slot_cards_rack${rack}.pdf`);
    } catch (err) {
      showError(await readBlobError(err, `Could not print rack ${rack}.`));
    } finally {
      setPrinting(false);
    }
  };

  const setActive = async (slot: StorageSlot, isActive: boolean) => {
    try {
      await storageSlotsAPI.update(slot.code, { is_active: isActive });
      load();
    } catch (err) {
      showError(
        extractErrorMessage(
          err,
          `Could not ${isActive ? 'return' : 'retire'} slot ${slot.code}.`,
        ),
      );
    }
  };

  const removeSlot = (slot: StorageSlot) => {
    confirmDelete(
      `Delete slot ${slot.code}? Its AprilTag goes back to the pool and the code stops meaning ` +
        'this place. To take it out of service without losing the marker, retire it instead.',
      async () => {
        try {
          await storageSlotsAPI.remove(slot.code);
          showSuccess(`Slot ${slot.code} deleted.`);
          load();
        } catch (err) {
          // 409 when a live stint still holds the slot — the backend's
          // message already says to resolve that stint first.
          showError(extractErrorMessage(err, `Could not delete slot ${slot.code}.`));
        }
      },
    );
  };

  const totalFree = slots.filter((s) => !s.is_occupied).length;

  return (
    <WorkspacePage
      testId="storage-slots-page"
      hero={{
        eyebrow: 'Facilities',
        title: 'Storage slots',
        description:
          'The physical racking behind project storage. Add or generate slots, see who is ' +
          'in each one, and print the cards that send a member to the kiosk with the slot ' +
          'already chosen.',
        action: (
          <Group gap="sm">
            <Button component={Link} to="/facilities/project-storage/overview" variant="light">
              Rack overview
            </Button>
            <Button component={Link} to="/facilities/project-storage/queue" variant="light">
              Storage queue
            </Button>
          </Group>
        ),
      }}
    >
      <Stack gap="md">
        <Paper p="md" withBorder>
          <Stack gap="sm">
            <Group justify="space-between" wrap="wrap">
              <Group gap="sm" wrap="wrap">
                <NumberInput
                  label="Rack"
                  placeholder="all"
                  w={110}
                  value={rackFilter}
                  onChange={(v) => setRackFilter(v === '' ? '' : Number(v))}
                  min={1}
                  allowDecimal={false}
                  allowNegative={false}
                  data-testid="filter-rack"
                />
                <TextInput
                  label="Level"
                  placeholder="all"
                  w={90}
                  maxLength={1}
                  value={levelFilter}
                  onChange={(e) => setLevelFilter(e.currentTarget.value.toUpperCase())}
                  data-testid="filter-level"
                />
                <Stack gap={4}>
                  <Text size="sm" fw={500}>
                    Occupancy
                  </Text>
                  <SegmentedControl
                    value={occupancy}
                    onChange={(v) => setOccupancy(v as OccupancyFilter)}
                    data={[
                      { label: 'All', value: 'all' },
                      { label: 'Free', value: 'free' },
                      { label: 'Occupied', value: 'occupied' },
                    ]}
                    data-testid="filter-occupancy"
                  />
                </Stack>
                <Stack gap={6} justify="flex-end">
                  <Switch
                    label="Pallet jack only"
                    checked={palletJackOnly}
                    onChange={(e) => setPalletJackOnly(e.currentTarget.checked)}
                    data-testid="filter-pallet-jack"
                  />
                  <Switch
                    label="Show retired"
                    checked={includeRetired}
                    onChange={(e) => setIncludeRetired(e.currentTarget.checked)}
                    data-testid="filter-retired"
                  />
                </Stack>
              </Group>

              <Group gap="sm">
                <Button
                  variant="light"
                  leftSection={<IconRefresh size={16} />}
                  onClick={load}
                  data-testid="refresh-slots"
                >
                  Refresh
                </Button>
                <Button
                  leftSection={<IconStack2 size={16} />}
                  onClick={() => setGenerateOpen(true)}
                  data-testid="open-generate-rack"
                >
                  Generate rack
                </Button>
                <Button
                  variant="light"
                  leftSection={<IconPlus size={16} />}
                  onClick={() => setAddOpen(true)}
                  data-testid="open-add-slot"
                >
                  Add slot
                </Button>
              </Group>
            </Group>

            <Group gap="sm" wrap="wrap">
              <Badge variant="light">{slots.length} slots</Badge>
              <Badge color="teal" variant="light">
                {totalFree} free
              </Badge>
              <Badge color="orange" variant="light">
                {slots.length - totalFree} occupied
              </Badge>
              <Button
                size="compact-sm"
                variant="filled"
                leftSection={<IconPrinter size={14} />}
                disabled={selected.length === 0 || printing}
                onClick={printSelected}
                data-testid="print-selected"
              >
                Print {selected.length} selected
              </Button>
              {selected.length > 0 && (
                <Button
                  size="compact-sm"
                  variant="subtle"
                  onClick={() => setSelected([])}
                  data-testid="clear-selection"
                >
                  Clear
                </Button>
              )}
            </Group>
          </Stack>
        </Paper>

        {lastGenerated && (
          <Alert
            color="green"
            variant="light"
            withCloseButton
            onClose={() => setLastGenerated(null)}
            title={`Rack ${lastGenerated.rack} generated`}
            data-testid="generate-summary"
          >
            <Text size="sm">
              Created {lastGenerated.created_count}
              {lastGenerated.skipped_count > 0 &&
                ` · ${lastGenerated.skipped_count} already existed`}
              .
            </Text>
            {lastGenerated.without_tag.length > 0 && (
              <Text size="sm" c="orange.8" mt={4}>
                No AprilTag left for {lastGenerated.without_tag.join(', ')} — those slots work
                by code but can&apos;t be scanned until the family has IDs free.
              </Text>
            )}
          </Alert>
        )}

        {error && (
          <Alert color="red" variant="light" data-testid="slots-error">
            {error}
          </Alert>
        )}

        {loading ? (
          <Group justify="center" p="xl">
            <Loader />
          </Group>
        ) : slots.length === 0 ? (
          <Paper p="xl" withBorder>
            <Text c="dimmed" ta="center" data-testid="slots-empty">
              No slots match these filters. Use “Generate rack” to lay out a rack, or “Add
              slot” for a one-off.
            </Text>
          </Paper>
        ) : (
          <Paper withBorder>
            <Table.ScrollContainer minWidth={900}>
              <Table highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th w={40} />
                    <Table.Th
                      onClick={() => toggleSort('code')}
                      style={{ cursor: 'pointer' }}
                      data-testid="sort-code"
                    >
                      Code{sortIndicator('code')}
                    </Table.Th>
                    <Table.Th
                      onClick={() => toggleSort('level')}
                      style={{ cursor: 'pointer' }}
                      data-testid="sort-level"
                    >
                      Level{sortIndicator('level')}
                    </Table.Th>
                    <Table.Th
                      onClick={() => toggleSort('position')}
                      style={{ cursor: 'pointer' }}
                      data-testid="sort-position"
                    >
                      Position{sortIndicator('position')}
                    </Table.Th>
                    <Table.Th
                      onClick={() => toggleSort('occupancy')}
                      style={{ cursor: 'pointer' }}
                      data-testid="sort-occupancy"
                    >
                      Occupancy{sortIndicator('occupancy')}
                    </Table.Th>
                    <Table.Th
                      onClick={() => toggleSort('tag')}
                      style={{ cursor: 'pointer' }}
                      data-testid="sort-tag"
                    >
                      AprilTag{sortIndicator('tag')}
                    </Table.Th>
                    <Table.Th>Flags</Table.Th>
                    <Table.Th>Actions</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {racks.map((group) => (
                    <React.Fragment key={`rack-${group.rack}`}>
                      <Table.Tr bg="gray.1" data-testid={`rack-header-${group.rack}`}>
                        <Table.Td>
                          <Checkbox
                            aria-label={`Select every slot on rack ${group.rack}`}
                            checked={group.slots.every((s) => selected.includes(s.id))}
                            indeterminate={
                              group.slots.some((s) => selected.includes(s.id)) &&
                              !group.slots.every((s) => selected.includes(s.id))
                            }
                            onChange={() => toggleRack(group.slots)}
                            data-testid={`select-rack-${group.rack}`}
                          />
                        </Table.Td>
                        <Table.Td colSpan={7}>
                          <Group gap="sm">
                            <Text fw={700}>Rack {group.rack}</Text>
                            <Badge variant="light" color="teal">
                              {group.free} free
                            </Badge>
                            <Badge variant="light">{group.slots.length} slots</Badge>
                            <Button
                              size="compact-xs"
                              variant="subtle"
                              leftSection={<IconPrinter size={14} />}
                              disabled={printing}
                              onClick={() => printRack(group.rack)}
                              data-testid={`print-rack-${group.rack}`}
                            >
                              Print rack
                            </Button>
                          </Group>
                        </Table.Td>
                      </Table.Tr>

                      {group.slots.map((slot) => (
                        <Table.Tr key={slot.id} data-testid={`slot-row-${slot.code}`}>
                          <Table.Td>
                            <Checkbox
                              aria-label={`Select slot ${slot.code}`}
                              checked={selected.includes(slot.id)}
                              onChange={() => toggleSlot(slot.id)}
                              data-testid={`select-slot-${slot.code}`}
                            />
                          </Table.Td>
                          <Table.Td>
                            <Text ff="monospace" fw={600}>
                              {slot.code}
                            </Text>
                          </Table.Td>
                          <Table.Td>{slot.level}</Table.Td>
                          <Table.Td>{slot.position}</Table.Td>
                          <Table.Td>
                            {slot.current_stint ? (
                              <Stack gap={0}>
                                <Badge color="orange" variant="light">
                                  Occupied
                                </Badge>
                                <Anchor
                                  component={Link}
                                  to={`/facilities/project-storage/${encodeURIComponent(
                                    slot.current_stint.stint_id,
                                  )}`}
                                  size="xs"
                                  data-testid={`occupant-${slot.code}`}
                                >
                                  {slot.current_stint.display_name || slot.current_stint.username}
                                </Anchor>
                                {slot.current_stint.project_title && (
                                  <Text size="xs" c="dimmed">
                                    {slot.current_stint.project_title}
                                  </Text>
                                )}
                              </Stack>
                            ) : (
                              <Badge color="teal" variant="light">
                                Free
                              </Badge>
                            )}
                          </Table.Td>
                          <Table.Td>
                            {slot.april_tag_id === null ? (
                              <Tooltip label="No marker allocated — the card prints without one.">
                                <Text size="sm" c="dimmed">
                                  —
                                </Text>
                              </Tooltip>
                            ) : (
                              <Text size="sm" ff="monospace">
                                #{slot.april_tag_id}
                              </Text>
                            )}
                          </Table.Td>
                          <Table.Td>
                            <Group gap={4} wrap="wrap">
                              {slot.requires_pallet_jack && (
                                <Badge size="sm" color="grape" variant="light">
                                  Pallet jack
                                </Badge>
                              )}
                              {!slot.is_active && (
                                <Badge size="sm" color="gray" variant="filled">
                                  Retired
                                </Badge>
                              )}
                              {slot.owning_group_name && (
                                <Badge size="sm" color="blue" variant="light">
                                  {slot.owning_group_name}
                                </Badge>
                              )}
                            </Group>
                          </Table.Td>
                          <Table.Td>
                            <Group gap={4} wrap="nowrap">
                              <Button
                                size="compact-xs"
                                variant="subtle"
                                leftSection={<IconEye size={14} />}
                                onClick={() => setPreviewSlot(slot)}
                                data-testid={`preview-card-${slot.code}`}
                              >
                                Card
                              </Button>
                              <Button
                                size="compact-xs"
                                variant="subtle"
                                color={slot.is_active ? 'gray' : 'teal'}
                                onClick={() => setActive(slot, !slot.is_active)}
                                data-testid={`toggle-active-${slot.code}`}
                              >
                                {slot.is_active ? 'Retire' : 'Return'}
                              </Button>
                              <Button
                                size="compact-xs"
                                variant="subtle"
                                color="red"
                                leftSection={<IconTrash size={14} />}
                                onClick={() => removeSlot(slot)}
                                data-testid={`delete-slot-${slot.code}`}
                              >
                                Delete
                              </Button>
                            </Group>
                          </Table.Td>
                        </Table.Tr>
                      ))}
                    </React.Fragment>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Paper>
        )}
      </Stack>

      <GenerateRackModal
        opened={generateOpen}
        onClose={() => setGenerateOpen(false)}
        onGenerated={(result) => {
          setLastGenerated(result);
          load();
        }}
      />
      <AddSlotModal
        opened={addOpen}
        onClose={() => setAddOpen(false)}
        onCreated={(slot) => {
          showSuccess(`Slot ${slot.code} created.`);
          load();
        }}
      />
      <SlotCardPreviewModal slot={previewSlot} onClose={() => setPreviewSlot(null)} />
    </WorkspacePage>
  );
};

export default StorageSlotsPage;
