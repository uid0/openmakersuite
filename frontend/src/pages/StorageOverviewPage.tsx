/**
 * Storage overview — the rack board you pull up on a phone while standing
 * in the aisle.
 *
 * One tile per slot, laid out the way the steel is: a row per level with
 * the high shelves first (Z overhead, A at your feet) and a column per
 * position. The letter says what kind of storage holds it — P for a
 * member's project, C/L/E for a committee, logistics, or class holding —
 * and the colour says whether something is wrong.
 *
 * Only Project storage is ever coloured, and the server decides: yellow
 * when a stint is expiring soon, red once it has expired (that one needs
 * to move to purgatory). A committee slot has been the committee's for two
 * years and will be tomorrow, so painting it would drown out the one late
 * member project this screen exists to surface. A healthy rack is a wall of
 * plain tiles and the exceptions jump out.
 *
 * Tapping a tile opens the slot: who is in it, and the staff intake for the
 * non-Project types — assign a free slot to a committee / logistics / class,
 * or release one that is held. Those three are *not* self-service; a member
 * claims their own slot at the kiosk, and staff hand out everything else.
 *
 * Permissions: the overview and the assign/release actions are all
 * IsStorageAdminOrStaff, so the storage warden runs this without being
 * platform-wide staff. Like the sibling slots console there is no
 * client-side role gate — the backend is the gate, and hiding the actions
 * from a group-only warden would take the screen away from the person who
 * uses it most.
 */
import {
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Group,
  Loader,
  Modal,
  Paper,
  SegmentedControl,
  Select,
  Stack,
  Text,
  Textarea,
  TextInput,
  Tooltip,
} from '@mantine/core';
import { IconAlertCircle, IconRefresh } from '@tabler/icons-react';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import WorkspacePage from '../components/landing/WorkspacePage';
import {
  sigAPI,
  storageAssignmentsAPI,
  storageOverviewAPI,
  storageSlotsAPI,
} from '../services/api';
import {
  SIG,
  StorageAssignmentType,
  StorageOverview,
  StorageOverviewCell,
  StorageOverviewRack,
  StorageSlot,
  StorageTypeLetter,
} from '../types';
import { confirmAction, showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

/** What each letter means, for the legend and the slot detail. */
const TYPE_LABELS: Record<StorageTypeLetter, string> = {
  P: 'Project',
  C: 'Committee',
  L: 'Logistics',
  E: 'Class',
};

const ASSIGNABLE_TYPES: { value: StorageAssignmentType; label: string; letter: string }[] = [
  { value: 'committee', label: 'Committee', letter: 'C' },
  { value: 'logistics', label: 'Logistics', letter: 'L' },
  { value: 'class', label: 'Class', letter: 'E' },
];

/**
 * Tile colours. The server sends `yellow`/`red`; everything else is either
 * plainly occupied or blank, and the difference between those two is what
 * the whole screen is read for, so they stay visually quiet.
 */
const CELL_BACKGROUND: Record<string, string> = {
  yellow: 'var(--mantine-color-yellow-4)',
  red: 'var(--mantine-color-red-6)',
  occupied: 'var(--mantine-color-gray-3)',
  empty: 'transparent',
  retired: 'var(--mantine-color-gray-5)',
};

const CELL_FOREGROUND: Record<string, string> = {
  yellow: 'var(--mantine-color-black)',
  red: 'var(--mantine-color-white)',
  occupied: 'var(--mantine-color-black)',
  empty: 'var(--mantine-color-gray-6)',
  retired: 'var(--mantine-color-gray-7)',
};

/** Which colour bucket a cell paints in — the one place that decides. */
const cellTone = (cell: StorageOverviewCell): string => {
  if (cell.color) return cell.color;
  if (cell.type) return 'occupied';
  // An empty slot that is out of service is not the same as a free one:
  // handing it out would send a member to a shelf that isn't there any more.
  return cell.is_active ? 'empty' : 'retired';
};

const statusLabel = (cell: StorageOverviewCell): string => {
  if (cell.status === 'empty') return cell.is_active ? 'Free' : 'Out of service';
  if (cell.status === 'occupied') return 'Occupied';
  // Stint statuses arrive snake_cased: expiring_soon → "Expiring soon".
  const spaced = cell.status.replace(/_/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
};

// ---------------------------------------------------------------------------
// Grid
// ---------------------------------------------------------------------------

interface RackGridProps {
  rack: StorageOverviewRack;
  onPick: (cell: StorageOverviewCell) => void;
}

const RackGrid: React.FC<RackGridProps> = ({ rack, onPick }) => {
  const positions = useMemo(
    () => Array.from({ length: rack.max_position }, (_, i) => i + 1),
    [rack.max_position],
  );

  // A fixed track per position so every level lines up even where the
  // racking has holes — the payload is dense and 1-indexed precisely so
  // this doesn't have to re-derive which columns exist.
  const gridStyle: React.CSSProperties = {
    display: 'grid',
    gridTemplateColumns: `2rem repeat(${rack.max_position}, minmax(2rem, 1fr))`,
    gap: 4,
    minWidth: rack.max_position * 40 + 32,
  };

  return (
    <Paper withBorder p="sm" data-testid={`rack-grid-${rack.rack}`}>
      <Group justify="space-between" mb="xs">
        <Text fw={700}>Rack {rack.rack}</Text>
        <Text size="xs" c="dimmed">
          {rack.levels.length} levels · {rack.max_position} positions
        </Text>
      </Group>

      {/* Wide racks scroll sideways rather than squeezing the tiles below
          thumb size on a phone. */}
      <Box style={{ overflowX: 'auto' }}>
        <Box style={gridStyle}>
          <Box />
          {positions.map((position) => (
            <Text key={`head-${position}`} size="xs" c="dimmed" ta="center">
              {position}
            </Text>
          ))}

          {rack.rows.map((row) => (
            <React.Fragment key={`${rack.rack}-${row.level}`}>
              <Text size="sm" fw={600} ta="center" data-testid={`level-label-${rack.rack}${row.level}`}>
                {row.level}
              </Text>
              {row.cells.map((cell, index) =>
                cell === null ? (
                  // A hole in the racking. Not a slot, so not tappable —
                  // and visibly not the same thing as a free slot.
                  <Box
                    key={`${rack.rack}-${row.level}-gap-${index + 1}`}
                    data-testid={`cell-gap-${rack.rack}${row.level}${index + 1}`}
                    style={{ minHeight: 34 }}
                  />
                ) : (
                  <Tooltip
                    key={cell.code}
                    label={`${cell.code} — ${statusLabel(cell)}${
                      cell.occupant ? ` · ${cell.occupant}` : ''
                    }`}
                    withArrow
                  >
                    <Box
                      component="button"
                      type="button"
                      onClick={() => onPick(cell)}
                      data-testid={`cell-${cell.code}`}
                      data-tone={cellTone(cell)}
                      data-type={cell.type ?? ''}
                      aria-label={`Slot ${cell.code} — ${statusLabel(cell)}${
                        cell.occupant ? `, ${cell.occupant}` : ''
                      }`}
                      style={{
                        minHeight: 34,
                        cursor: 'pointer',
                        borderRadius: 4,
                        border: '1px solid var(--mantine-color-gray-4)',
                        borderStyle: cell.is_active ? 'solid' : 'dashed',
                        background: CELL_BACKGROUND[cellTone(cell)],
                        color: CELL_FOREGROUND[cellTone(cell)],
                        fontWeight: 700,
                        fontFamily: 'monospace',
                        fontSize: 14,
                      }}
                    >
                      {cell.type ?? ''}
                    </Box>
                  </Tooltip>
                ),
              )}
            </React.Fragment>
          ))}
        </Box>
      </Box>
    </Paper>
  );
};

// ---------------------------------------------------------------------------
// Legend
// ---------------------------------------------------------------------------

const swatch = (background: string, border = 'var(--mantine-color-gray-4)') => (
  <Box
    style={{
      width: 16,
      height: 16,
      borderRadius: 3,
      border: `1px solid ${border}`,
      background,
    }}
  />
);

const Legend: React.FC = () => (
  <Paper withBorder p="sm" data-testid="overview-legend">
    <Group gap="lg" wrap="wrap">
      <Group gap={6}>
        <Text size="sm" fw={600}>
          P
        </Text>
        <Text size="sm" c="dimmed">
          Project
        </Text>
      </Group>
      <Group gap={6}>
        <Text size="sm" fw={600}>
          C
        </Text>
        <Text size="sm" c="dimmed">
          Committee
        </Text>
      </Group>
      <Group gap={6}>
        <Text size="sm" fw={600}>
          L
        </Text>
        <Text size="sm" c="dimmed">
          Logistics
        </Text>
      </Group>
      <Group gap={6}>
        <Text size="sm" fw={600}>
          E
        </Text>
        <Text size="sm" c="dimmed">
          Class
        </Text>
      </Group>
      <Group gap={6}>
        {swatch(CELL_BACKGROUND.yellow)}
        <Text size="sm" c="dimmed">
          Expiring soon
        </Text>
      </Group>
      <Group gap={6}>
        {swatch(CELL_BACKGROUND.red)}
        <Text size="sm" c="dimmed">
          Expired — move to purgatory
        </Text>
      </Group>
      <Group gap={6}>
        {swatch(CELL_BACKGROUND.occupied)}
        <Text size="sm" c="dimmed">
          In use
        </Text>
      </Group>
      <Group gap={6}>
        {swatch(CELL_BACKGROUND.empty)}
        <Text size="sm" c="dimmed">
          Free
        </Text>
      </Group>
      <Group gap={6}>
        {swatch(CELL_BACKGROUND.retired)}
        <Text size="sm" c="dimmed">
          Out of service
        </Text>
      </Group>
    </Group>
  </Paper>
);

// ---------------------------------------------------------------------------
// Slot detail + the C/L/E staff intake
// ---------------------------------------------------------------------------

interface SlotDetailModalProps {
  cell: StorageOverviewCell | null;
  onClose: () => void;
  onChanged: () => void;
}

const SlotDetailModal: React.FC<SlotDetailModalProps> = ({ cell, onClose, onChanged }) => {
  const [slot, setSlot] = useState<StorageSlot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [storageType, setStorageType] = useState<StorageAssignmentType>('committee');
  const [owningGroup, setOwningGroup] = useState<string | null>(null);
  const [occupantLabel, setOccupantLabel] = useState('');
  const [notes, setNotes] = useState('');
  const [sigs, setSigs] = useState<SIG[]>([]);

  // The grid cell carries what to paint; the slot itself carries what to do
  // with it — the stint's permalink and the assignment id release needs.
  useEffect(() => {
    if (!cell) {
      setSlot(null);
      setError(null);
      return;
    }
    setStorageType('committee');
    setOwningGroup(null);
    setOccupantLabel('');
    setNotes('');
    setLoading(true);
    setError(null);
    let cancelled = false;
    storageSlotsAPI
      .get(cell.code)
      .then((res) => {
        if (!cancelled) setSlot(res?.data ?? null);
      })
      .catch((err) => {
        if (!cancelled) setError(extractErrorMessage(err, `Could not load slot ${cell.code}.`));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [cell]);

  const assignment = slot?.current_assignment ?? null;
  const stint = slot?.current_stint ?? null;
  const canAssign = slot !== null && !slot.is_occupied && slot.is_active;

  // Committees are auth groups, which is what the SIG endpoint lists. Only
  // fetched once a free slot is open — most taps land on an occupied tile,
  // and that one never shows the picker.
  useEffect(() => {
    if (!canAssign) return;
    let cancelled = false;
    sigAPI
      .listMySIGs()
      .then((res) => {
        if (!cancelled) setSigs(res?.data?.results ?? []);
      })
      .catch(() => {
        if (!cancelled) setSigs([]);
      });
    return () => {
      cancelled = true;
    };
  }, [canAssign]);

  // Mirrors the server rule: a committee holding that names neither the
  // group nor a label is just a blocked slot, and the grid would have
  // nothing to show for it.
  const assignValid =
    storageType !== 'committee' || owningGroup !== null || occupantLabel.trim() !== '';

  const submitAssign = async () => {
    if (!cell || !assignValid) return;
    setSubmitting(true);
    setError(null);
    try {
      await storageAssignmentsAPI.assign({
        slot: cell.code,
        storage_type: storageType,
        owning_group: storageType === 'committee' && owningGroup ? Number(owningGroup) : null,
        occupant_label: occupantLabel.trim(),
        notes: notes.trim(),
      });
      showSuccess(`Slot ${cell.code} assigned.`);
      onChanged();
      onClose();
    } catch (err) {
      // 409 when something already holds it — the backend's message names
      // which kind and what to do about it.
      setError(extractErrorMessage(err, `Could not assign slot ${cell.code}.`));
    } finally {
      setSubmitting(false);
    }
  };

  const submitRelease = () => {
    if (!assignment || !cell) return;
    confirmAction(
      `Release slot ${cell.code}`,
      `Hand ${cell.code} back from ${assignment.occupant_display}? The slot becomes free to ` +
        'give out again, and the record of who had it is kept.',
      async () => {
        setSubmitting(true);
        try {
          await storageAssignmentsAPI.release(assignment.id);
          showSuccess(`Slot ${cell.code} released.`);
          onChanged();
          onClose();
        } catch (err) {
          showError(extractErrorMessage(err, `Could not release slot ${cell.code}.`));
        } finally {
          setSubmitting(false);
        }
      },
      { labels: { confirm: 'Release', cancel: 'Cancel' }, color: 'red' },
    );
  };

  return (
    <Modal
      opened={cell !== null}
      onClose={onClose}
      title={cell ? `Slot ${cell.code}` : 'Slot'}
      centered
      data-testid="slot-detail-modal"
    >
      <Stack gap="md">
        {error && (
          <Alert color="red" variant="light" icon={<IconAlertCircle size={18} />} data-testid="slot-detail-error">
            {error}
          </Alert>
        )}

        {loading && (
          <Group justify="center" p="md">
            <Loader size="sm" />
          </Group>
        )}

        {cell && (
          <Group gap="sm" wrap="wrap">
            <Badge variant="light" data-testid="slot-detail-status">
              {statusLabel(cell)}
            </Badge>
            {cell.type && (
              <Badge variant="light" color="blue" data-testid="slot-detail-type">
                {TYPE_LABELS[cell.type]}
              </Badge>
            )}
            {slot?.requires_pallet_jack && (
              <Badge variant="light" color="grape">
                Pallet jack
              </Badge>
            )}
            {slot && !slot.is_active && (
              <Badge variant="filled" color="gray">
                Retired
              </Badge>
            )}
          </Group>
        )}

        {stint && (
          <Stack gap={2} data-testid="slot-detail-stint">
            <Text size="sm">{stint.display_name || stint.username}</Text>
            {stint.project_title && (
              <Text size="sm" c="dimmed">
                {stint.project_title}
              </Text>
            )}
            <Anchor
              component={Link}
              to={`/facilities/project-storage/${encodeURIComponent(stint.stint_id)}`}
              size="sm"
              data-testid="slot-detail-stint-link"
            >
              Open the stint ({stint.stint_id})
            </Anchor>
          </Stack>
        )}

        {assignment && (
          <Stack gap="xs" data-testid="slot-detail-assignment">
            <Text size="sm">
              {assignment.occupant_display} — {TYPE_LABELS[assignment.type_letter]}
            </Text>
            <Text size="xs" c="dimmed">
              Held since {new Date(assignment.assigned_at).toLocaleDateString()}
            </Text>
            <Group>
              <Button
                color="red"
                variant="light"
                disabled={submitting}
                onClick={submitRelease}
                data-testid="slot-release"
              >
                Release slot
              </Button>
            </Group>
          </Stack>
        )}

        {slot && slot.is_occupied && !stint && !assignment && (
          <Text size="sm" c="dimmed">
            {cell?.occupant}
          </Text>
        )}

        {slot && !slot.is_occupied && !slot.is_active && (
          <Text size="sm" c="dimmed" data-testid="slot-detail-retired">
            This slot is out of service. Return it on the{' '}
            <Anchor component={Link} to="/facilities/project-storage/slots" size="sm">
              slots console
            </Anchor>{' '}
            before assigning it.
          </Text>
        )}

        {canAssign && (
          <Stack gap="sm" data-testid="slot-assign-form">
            <Text size="sm" fw={600}>
              Assign this slot
            </Text>
            <Text size="xs" c="dimmed">
              Committee, logistics and class storage are handed out by staff — members claim
              their own slot at the kiosk.
            </Text>

            <SegmentedControl
              value={storageType}
              onChange={(value) => setStorageType(value as StorageAssignmentType)}
              data={ASSIGNABLE_TYPES.map((type) => ({
                value: type.value,
                label: `${type.letter} · ${type.label}`,
              }))}
              data-testid="assign-type"
            />

            {storageType === 'committee' && (
              <Select
                label="Committee"
                placeholder="Pick a committee"
                data={sigs.map((sig) => ({ value: String(sig.id), label: sig.name }))}
                value={owningGroup}
                onChange={setOwningGroup}
                searchable
                clearable
                nothingFoundMessage="No committees"
                data-testid="assign-group"
              />
            )}

            <TextInput
              label={storageType === 'committee' ? 'Occupant label (optional)' : 'Occupant'}
              description={
                storageType === 'committee'
                  ? 'Only needed when the committee is not in the list.'
                  : 'Who this is for — a crew, an instructor, a class.'
              }
              value={occupantLabel}
              onChange={(event) => setOccupantLabel(event.currentTarget.value)}
              data-testid="assign-label"
            />

            <Textarea
              label="Notes"
              value={notes}
              onChange={(event) => setNotes(event.currentTarget.value)}
              autosize
              minRows={2}
              data-testid="assign-notes"
            />

            <Group justify="flex-end">
              <Button variant="default" onClick={onClose}>
                Cancel
              </Button>
              <Button
                onClick={submitAssign}
                disabled={!assignValid || submitting}
                data-testid="assign-submit"
              >
                Assign slot
              </Button>
            </Group>
          </Stack>
        )}
      </Stack>
    </Modal>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const StorageOverviewPage: React.FC = () => {
  const [overview, setOverview] = useState<StorageOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rackFilter, setRackFilter] = useState<string>('all');
  // Remembered across loads: narrowing to one rack means the response no
  // longer mentions the others, and the selector must not lose them.
  const [knownRacks, setKnownRacks] = useState<number[]>([]);
  const [picked, setPicked] = useState<StorageOverviewCell | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await storageOverviewAPI.get(
        rackFilter === 'all' ? undefined : { rack: Number(rackFilter) },
      );
      const data = res?.data ?? null;
      setOverview(data);
      const racks = data?.racks?.map((rack) => rack.rack) ?? [];
      setKnownRacks((current) => [...new Set([...current, ...racks])].sort((a, b) => a - b));
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load the storage overview.'));
    } finally {
      setLoading(false);
    }
  }, [rackFilter]);

  useEffect(() => {
    load();
  }, [load]);

  // Memoised so the counts below don't recompute on every render just
  // because the fallback [] is a fresh array each time.
  const racks = useMemo(() => overview?.racks ?? [], [overview]);

  // The counts worth having on a phone: how many tiles are shouting, and
  // how much room is left.
  const totals = useMemo(() => {
    let attention = 0;
    let occupied = 0;
    let free = 0;
    racks.forEach((rack) =>
      rack.rows.forEach((row) =>
        row.cells.forEach((cell) => {
          if (!cell) return;
          if (cell.color) attention += 1;
          if (cell.type) occupied += 1;
          else if (cell.is_active) free += 1;
        }),
      ),
    );
    return { attention, occupied, free };
  }, [racks]);

  return (
    <WorkspacePage
      testId="storage-overview-page"
      hero={{
        eyebrow: 'Facilities',
        title: 'Storage overview',
        description:
          'Every rack at a glance — P for a member project, C/L/E for committee, logistics ' +
          'and class storage. Coloured tiles are the ones to go fix: yellow is expiring, red ' +
          'has expired and needs to move to purgatory. Tap a tile to open the slot.',
        action: (
          <Group gap="sm">
            <Button component={Link} to="/facilities/project-storage/slots" variant="light">
              Storage slots
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
          <Group justify="space-between" wrap="wrap" gap="sm">
            <Group gap="sm" wrap="wrap">
              <SegmentedControl
                value={rackFilter}
                onChange={setRackFilter}
                data={[
                  { label: 'All racks', value: 'all' },
                  ...knownRacks.map((rack) => ({ label: `Rack ${rack}`, value: String(rack) })),
                ]}
                data-testid="rack-filter"
              />
              <Button
                variant="light"
                leftSection={<IconRefresh size={16} />}
                onClick={load}
                data-testid="refresh-overview"
              >
                Refresh
              </Button>
            </Group>

            <Group gap="sm" wrap="wrap">
              <Badge color={totals.attention > 0 ? 'red' : 'gray'} variant="light" data-testid="count-attention">
                {totals.attention} need attention
              </Badge>
              <Badge variant="light" data-testid="count-occupied">
                {totals.occupied} in use
              </Badge>
              <Badge color="teal" variant="light" data-testid="count-free">
                {totals.free} free
              </Badge>
            </Group>
          </Group>
        </Paper>

        <Legend />

        {error && (
          <Alert color="red" variant="light" data-testid="overview-error">
            {error}
          </Alert>
        )}

        {loading ? (
          <Group justify="center" p="xl">
            <Loader />
          </Group>
        ) : racks.length === 0 ? (
          <Paper p="xl" withBorder>
            <Text c="dimmed" ta="center" data-testid="overview-empty">
              No racking to show yet. Lay out a rack on the{' '}
              <Anchor component={Link} to="/facilities/project-storage/slots">
                slots console
              </Anchor>
              .
            </Text>
          </Paper>
        ) : (
          // Stacked vertically: on a phone one rack fills the screen, and
          // scrolling past the healthy ones is how you find the coloured tile.
          racks.map((rack) => <RackGrid key={rack.rack} rack={rack} onPick={setPicked} />)
        )}

        {overview?.generated_at && (
          <Text size="xs" c="dimmed" data-testid="overview-generated">
            Generated {new Date(overview.generated_at).toLocaleString()}
          </Text>
        )}
      </Stack>

      <SlotDetailModal cell={picked} onClose={() => setPicked(null)} onChanged={load} />
    </WorkspacePage>
  );
};

export default StorageOverviewPage;
