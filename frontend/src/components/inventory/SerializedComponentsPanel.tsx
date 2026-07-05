/**
 * SerializedComponentsPanel — per-unit "instances" view for a serialized
 * InventoryItem (#818). Lists every physical unit with its lifecycle status,
 * exposes the legal lifecycle actions (receive / install / remove / consume /
 * retire / dispose) driven by the backend's `available_actions`, lets staff
 * record newly-received units by serial number, and expands a per-unit
 * ComponentUsageEvent history.
 *
 * Writes are gated to staff / SIG admins server-side; this panel surfaces the
 * action buttons to everyone and lets the API be the source of truth (a 403
 * is shown as an inline error) — matching AssetReservationsAndOOSSection.
 */
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Collapse,
  Group,
  Loader,
  Modal,
  Paper,
  Select,
  Stack,
  Table,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import { IconChevronDown, IconChevronRight, IconPlus } from '@tabler/icons-react';
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import {
  assetsAPI,
  ComponentUsageEvent,
  SerializedComponent,
  SerializedComponentAction,
  serializedComponentsAPI,
  SerializedTrackingMode,
} from '../../services/api';
import { Asset } from '../../types';
import { extractErrorMessage } from '../../utils/extractErrorMessage';
import {
  serializedActionColor,
  serializedActionLabel,
  serializedStatusColor,
} from '../../utils/serializedComponents';

interface Props {
  itemId: string;
  /** Owning item's tracking mode, shown as context in the header. */
  trackingMode?: SerializedTrackingMode;
  /**
   * Whether the owning item is flagged `is_serialized`. When false the panel
   * shows a discoverable "turn this on" call-to-action instead of a dead,
   * empty units view — a user who came here to add a serial number gets a
   * clear next step rather than no input field (op-qff). Defaults to true so
   * existing serialized callers are unaffected.
   */
  isSerialized?: boolean;
  /** Invoked by the "Enable serial tracking" CTA shown when not serialized. */
  onEnableTracking?: () => void;
}

const fmtDateTime = (iso: string | null): string =>
  iso
    ? new Date(iso).toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—';

const SerializedComponentsPanel: React.FC<Props> = ({
  itemId,
  trackingMode,
  isSerialized = true,
  onEnableTracking,
}) => {
  const [units, setUnits] = useState<SerializedComponent[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Per-unit usage history (loaded on expand).
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [historyByUnit, setHistoryByUnit] = useState<Record<string, ComponentUsageEvent[]>>({});
  const [historyLoadingId, setHistoryLoadingId] = useState<string | null>(null);

  // Add-unit form.
  const [showAdd, setShowAdd] = useState(false);
  const [newSerial, setNewSerial] = useState('');
  const [newLot, setNewLot] = useState('');
  const [adding, setAdding] = useState(false);

  // Install modal.
  const [installUnit, setInstallUnit] = useState<SerializedComponent | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [assetsLoading, setAssetsLoading] = useState(false);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);

  // Dispose modal.
  const [disposeUnit, setDisposeUnit] = useState<SerializedComponent | null>(null);
  const [disposalReason, setDisposalReason] = useState('');

  const load = useCallback(async () => {
    // A non-serialized item has no units to fetch — skip the request and let
    // the panel render its "enable serial tracking" CTA instead.
    if (!isSerialized) {
      setUnits([]);
      setTotal(0);
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      setError(null);
      const res = await serializedComponentsAPI.list({ item: itemId });
      const results = res?.data?.results ?? [];
      setUnits(results);
      setTotal(res?.data?.count ?? results.length);
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not load serialized units.'));
    } finally {
      setLoading(false);
    }
  }, [itemId, isSerialized]);

  useEffect(() => {
    load();
  }, [load]);

  // Replace one unit in place after a lifecycle action mutates it.
  const replaceUnit = useCallback((updated: SerializedComponent) => {
    setUnits((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
  }, []);

  const refreshHistory = useCallback(async (unitId: string) => {
    setHistoryLoadingId(unitId);
    try {
      const res = await serializedComponentsAPI.listUsageEvents({ component: unitId });
      setHistoryByUnit((prev) => ({ ...prev, [unitId]: res?.data?.results ?? [] }));
    } catch {
      setHistoryByUnit((prev) => ({ ...prev, [unitId]: [] }));
    } finally {
      setHistoryLoadingId(null);
    }
  }, []);

  const toggleHistory = useCallback(
    (unitId: string) => {
      if (expandedId === unitId) {
        setExpandedId(null);
        return;
      }
      setExpandedId(unitId);
      if (!historyByUnit[unitId]) {
        refreshHistory(unitId);
      }
    },
    [expandedId, historyByUnit, refreshHistory],
  );

  const runSimpleAction = useCallback(
    async (unit: SerializedComponent, action: Exclude<SerializedComponentAction, 'install' | 'dispose'>) => {
      setBusyId(unit.id);
      setError(null);
      try {
        const res = await serializedComponentsAPI[action](unit.id);
        if (res?.data) replaceUnit(res.data);
        if (expandedId === unit.id) {
          refreshHistory(unit.id);
        }
      } catch (err) {
        setError(extractErrorMessage(err, `Could not ${action} ${unit.serial_number}.`));
      } finally {
        setBusyId(null);
      }
    },
    [expandedId, refreshHistory, replaceUnit],
  );

  const openInstall = useCallback(
    async (unit: SerializedComponent) => {
      setInstallUnit(unit);
      setSelectedAssetId(null);
      if (assets.length === 0) {
        setAssetsLoading(true);
        try {
          const res = await assetsAPI.listAssets({ page_size: 500, is_active: true });
          setAssets(res.data.results ?? []);
        } catch {
          setAssets([]);
        } finally {
          setAssetsLoading(false);
        }
      }
    },
    [assets.length],
  );

  const submitInstall = useCallback(async () => {
    if (!installUnit || !selectedAssetId) return;
    setBusyId(installUnit.id);
    setError(null);
    try {
      const res = await serializedComponentsAPI.install(installUnit.id, { asset: selectedAssetId });
      if (res?.data) replaceUnit(res.data);
      if (expandedId === installUnit.id) refreshHistory(installUnit.id);
      setInstallUnit(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not install unit.'));
    } finally {
      setBusyId(null);
    }
  }, [installUnit, selectedAssetId, expandedId, refreshHistory, replaceUnit]);

  const submitDispose = useCallback(async () => {
    if (!disposeUnit || !disposalReason.trim()) return;
    setBusyId(disposeUnit.id);
    setError(null);
    try {
      const res = await serializedComponentsAPI.dispose(disposeUnit.id, {
        disposal_reason: disposalReason.trim(),
      });
      if (res?.data) replaceUnit(res.data);
      if (expandedId === disposeUnit.id) refreshHistory(disposeUnit.id);
      setDisposeUnit(null);
      setDisposalReason('');
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not dispose unit.'));
    } finally {
      setBusyId(null);
    }
  }, [disposeUnit, disposalReason, expandedId, refreshHistory, replaceUnit]);

  const handleAction = useCallback(
    (unit: SerializedComponent, action: SerializedComponentAction) => {
      if (action === 'install') {
        openInstall(unit);
      } else if (action === 'dispose') {
        setDisposeUnit(unit);
        setDisposalReason('');
      } else {
        runSimpleAction(unit, action);
      }
    },
    [openInstall, runSimpleAction],
  );

  const submitAdd = useCallback(async () => {
    const serial = newSerial.trim();
    if (!serial) return;
    setAdding(true);
    setError(null);
    try {
      const res = await serializedComponentsAPI.create({
        item: itemId,
        serial_number: serial,
        lot: newLot.trim() || undefined,
      });
      if (res?.data) {
        setUnits((prev) => [res.data, ...prev]);
        setTotal((prev) => prev + 1);
      }
      setNewSerial('');
      setNewLot('');
      setShowAdd(false);
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not add unit.'));
    } finally {
      setAdding(false);
    }
  }, [itemId, newSerial, newLot]);

  const assetOptions = useMemo(
    () =>
      assets.map((a) => ({
        value: a.id,
        label: a.asset_tag ? `${a.name} (${a.asset_tag})` : a.name,
      })),
    [assets],
  );

  const truncated = total > units.length;

  // The item isn't tracked by serial number: don't strand the user in front of
  // an empty panel with no way to add a unit. Explain the state and offer a
  // direct path to turn serial tracking on (op-qff).
  if (!isSerialized) {
    return (
      <Paper withBorder p="md" data-testid="serialized-components-panel">
        <Group gap="xs" mb="sm">
          <Title order={4}>Serialized units</Title>
        </Group>
        <Alert color="blue" variant="light" data-testid="serialized-not-tracked">
          This item isn&apos;t tracked by serial number yet, so there are no units
          to add. Turn on serial tracking to record individual units by serial
          number and follow their lifecycle (receive, install, consume, retire,
          dispose).
        </Alert>
        {onEnableTracking && (
          <Button
            mt="sm"
            leftSection={<IconPlus size={14} />}
            onClick={onEnableTracking}
            data-testid="serialized-enable-tracking"
          >
            Enable serial tracking
          </Button>
        )}
      </Paper>
    );
  }

  return (
    <Paper withBorder p="md" data-testid="serialized-components-panel">
      <Group justify="space-between" mb="sm">
        <Group gap="xs">
          <Title order={4}>Serialized units</Title>
          <Badge variant="light">{total}</Badge>
          {trackingMode && (
            <Badge variant="outline" color="gray" tt="capitalize">
              {trackingMode}
            </Badge>
          )}
        </Group>
        <Button
          size="xs"
          variant="light"
          leftSection={<IconPlus size={14} />}
          onClick={() => setShowAdd((v) => !v)}
          data-testid="serialized-add-toggle"
        >
          {showAdd ? 'Cancel' : 'Add unit'}
        </Button>
      </Group>

      {error && (
        <Alert color="red" mb="sm" onClose={() => setError(null)} withCloseButton>
          {error}
        </Alert>
      )}

      <Collapse in={showAdd}>
        <Paper withBorder p="sm" mb="sm" bg="var(--mantine-color-gray-0)">
          <Group align="flex-end" gap="sm">
            <TextInput
              label="Serial number"
              placeholder="SN-000123"
              value={newSerial}
              onChange={(e) => setNewSerial(e.currentTarget.value)}
              data-testid="serialized-add-serial"
              style={{ flex: 1 }}
            />
            <TextInput
              label="Lot (optional)"
              placeholder="Batch / lot"
              value={newLot}
              onChange={(e) => setNewLot(e.currentTarget.value)}
              data-testid="serialized-add-lot"
              style={{ flex: 1 }}
            />
            <Button
              onClick={submitAdd}
              loading={adding}
              disabled={!newSerial.trim()}
              data-testid="serialized-add-submit"
            >
              Add
            </Button>
          </Group>
        </Paper>
      </Collapse>

      {loading ? (
        <Group justify="center" py="lg">
          <Loader size="sm" />
        </Group>
      ) : units.length === 0 ? (
        <Text c="dimmed">No serialized units recorded for this item yet.</Text>
      ) : (
        <Table highlightOnHover verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={40} />
              <Table.Th>Serial</Table.Th>
              <Table.Th>Lot</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Installed in</Table.Th>
              <Table.Th>Actions</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {units.map((unit) => {
              const isExpanded = expandedId === unit.id;
              const history = historyByUnit[unit.id];
              return (
                <React.Fragment key={unit.id}>
                  <Table.Tr data-testid={`serialized-unit-${unit.id}`}>
                    <Table.Td>
                      <ActionIcon
                        variant="subtle"
                        size="sm"
                        onClick={() => toggleHistory(unit.id)}
                        aria-label={isExpanded ? 'Hide history' : 'Show history'}
                        aria-expanded={isExpanded}
                      >
                        {isExpanded ? (
                          <IconChevronDown size={16} />
                        ) : (
                          <IconChevronRight size={16} />
                        )}
                      </ActionIcon>
                    </Table.Td>
                    <Table.Td>
                      <Text fw={500}>{unit.serial_number}</Text>
                    </Table.Td>
                    <Table.Td>{unit.lot || '—'}</Table.Td>
                    <Table.Td>
                      <Badge color={serializedStatusColor(unit.status)} variant="light">
                        {unit.status_display}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{unit.installed_in_asset_name || '—'}</Table.Td>
                    <Table.Td>
                      <Group gap={4}>
                        {unit.available_actions.length === 0 ? (
                          <Text size="xs" c="dimmed">
                            No actions
                          </Text>
                        ) : (
                          unit.available_actions.map((action) => (
                            <Button
                              key={action}
                              size="compact-xs"
                              variant="light"
                              color={serializedActionColor(action)}
                              loading={busyId === unit.id}
                              onClick={() => handleAction(unit, action)}
                              data-testid={`serialized-action-${action}-${unit.id}`}
                            >
                              {serializedActionLabel(action)}
                            </Button>
                          ))
                        )}
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                  <Table.Tr>
                    <Table.Td colSpan={6} p={0} style={{ border: isExpanded ? undefined : 'none' }}>
                      <Collapse in={isExpanded}>
                        <div style={{ padding: '0.5rem 1rem 1rem 3rem' }}>
                          {historyLoadingId === unit.id ? (
                            <Group gap="xs">
                              <Loader size="xs" />
                              <Text size="sm" c="dimmed">
                                Loading history…
                              </Text>
                            </Group>
                          ) : history && history.length > 0 ? (
                            <Table withRowBorders={false} verticalSpacing={4} fz="sm">
                              <Table.Thead>
                                <Table.Tr>
                                  <Table.Th>When</Table.Th>
                                  <Table.Th>Action</Table.Th>
                                  <Table.Th>Asset</Table.Th>
                                  <Table.Th>By</Table.Th>
                                  <Table.Th>Notes</Table.Th>
                                </Table.Tr>
                              </Table.Thead>
                              <Table.Tbody>
                                {history.map((ev) => (
                                  <Table.Tr key={ev.id}>
                                    <Table.Td>{fmtDateTime(ev.at)}</Table.Td>
                                    <Table.Td>{ev.action_display}</Table.Td>
                                    <Table.Td>{ev.asset_name || '—'}</Table.Td>
                                    <Table.Td>{ev.actor_username || '—'}</Table.Td>
                                    <Table.Td>{ev.notes || '—'}</Table.Td>
                                  </Table.Tr>
                                ))}
                              </Table.Tbody>
                            </Table>
                          ) : (
                            <Text size="sm" c="dimmed">
                              No history recorded.
                            </Text>
                          )}
                        </div>
                      </Collapse>
                    </Table.Td>
                  </Table.Tr>
                </React.Fragment>
              );
            })}
          </Table.Tbody>
        </Table>
      )}

      {truncated && (
        <Text size="xs" c="dimmed" mt="xs">
          Showing the first {units.length} of {total} units.
        </Text>
      )}

      {/* Install modal — pick the target asset. */}
      <Modal
        opened={installUnit !== null}
        onClose={() => setInstallUnit(null)}
        title={`Install ${installUnit?.serial_number ?? ''}`}
        centered
      >
        <Stack>
          <Select
            label="Install into asset"
            placeholder={assetsLoading ? 'Loading assets…' : 'Select an asset'}
            data={assetOptions}
            value={selectedAssetId}
            onChange={setSelectedAssetId}
            searchable
            disabled={assetsLoading}
            nothingFoundMessage="No assets found"
            data-testid="serialized-install-asset"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setInstallUnit(null)}>
              Cancel
            </Button>
            <Button
              onClick={submitInstall}
              disabled={!selectedAssetId}
              loading={busyId === installUnit?.id}
              data-testid="serialized-install-submit"
            >
              Install
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Dispose modal — a reason is required by the backend. */}
      <Modal
        opened={disposeUnit !== null}
        onClose={() => setDisposeUnit(null)}
        title={`Dispose ${disposeUnit?.serial_number ?? ''}`}
        centered
      >
        <Stack>
          <Textarea
            label="Disposal reason"
            placeholder="Why is this unit being disposed?"
            value={disposalReason}
            onChange={(e) => setDisposalReason(e.currentTarget.value)}
            minRows={3}
            required
            data-testid="serialized-dispose-reason"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setDisposeUnit(null)}>
              Cancel
            </Button>
            <Button
              color="red"
              onClick={submitDispose}
              disabled={!disposalReason.trim()}
              loading={busyId === disposeUnit?.id}
              data-testid="serialized-dispose-submit"
            >
              Dispose
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Paper>
  );
};

export default SerializedComponentsPanel;
