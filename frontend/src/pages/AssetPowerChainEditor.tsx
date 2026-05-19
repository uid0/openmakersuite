/**
 * Inline editor for an asset's power chain — replaces the prior "use the
 * Django admin" instruction with a form-driven flow.
 *
 * Two tabs, mutually exclusive ways an asset takes power:
 *   Cordset   — PowerPort + PowerCable → PowerOutlet (the original surface).
 *   Hardwired — HardwiredConnection → Disconnect → PowerCircuit (PR 3/5).
 *
 * Staff-gated: the form only renders for `is_staff`. The backend enforces
 * the same gate; this is a UX hide, not a security boundary.
 */
import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Checkbox,
  Group,
  MultiSelect,
  NativeSelect,
  NumberInput,
  Paper,
  Select,
  Stack,
  Tabs,
  Text,
  Textarea,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import {
  IconBolt,
  IconPlugConnected,
  IconTrash,
  IconUnlink,
} from '@tabler/icons-react';
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import {
  DISCONNECT_TYPE_OPTIONS,
  Disconnect,
  DisconnectType,
  HardwiredConnection,
  HardwiredConnectionWritable,
  LOTODevice,
  PowerCableDetail,
  PowerCircuitDetail,
  PowerOutletDetail,
  PowerPortDetail,
  electricalCircuitsAPI,
  electricalTopologyAPI,
  inventoryAPI,
  lotoAPI,
} from '../services/api';
import { Location } from '../types';
import { showError, showSuccess } from '../utils/dialogs';

const NEMA_TYPES: { value: string; label: string }[] = [
  { value: '5-15R', label: '5-15R (standard 120V 15A)' },
  { value: '5-20R', label: '5-20R (120V 20A)' },
  { value: '6-15R', label: '6-15R (240V 15A)' },
  { value: '6-20R', label: '6-20R (240V 20A)' },
  { value: 'L5-30R', label: 'L5-30R (locking 120V 30A)' },
  { value: 'L6-30R', label: 'L6-30R (locking 240V 30A)' },
  { value: 'C13', label: 'C13 (PDU appliance)' },
  { value: 'C19', label: 'C19 (PDU high-current)' },
  { value: 'other', label: 'Other' },
];

const NEW_DISCONNECT_SENTINEL = '__new__';
// Types where the disconnect doesn't have a separate location (it's part
// of the equipment or there is no switch at all). The form hides the
// location picker for these and POSTs `location: null`.
const LOCATIONLESS_TYPES: DisconnectType[] = ['integral', 'none'];

interface Props {
  assetId: string;
  isStaff: boolean;
  onChange: () => void;
}

function unwrap<T>(payload: { results: T[] } | T[]): T[] {
  if (Array.isArray(payload)) return payload;
  return payload?.results ?? [];
}

interface NewDisconnectDraft {
  label: string;
  disconnect_type: DisconnectType | null;
  location_id: string | null;
  amperage: number | '';
  fuse_size: string;
  is_lockable: boolean;
  required_loto_device_ids: string[];
  notes: string;
}

const emptyDisconnectDraft: NewDisconnectDraft = {
  label: '',
  disconnect_type: null,
  location_id: null,
  amperage: '',
  fuse_size: '',
  is_lockable: true,
  required_loto_device_ids: [],
  notes: '',
};

export const AssetPowerChainEditor: React.FC<Props> = ({ assetId, isStaff, onChange }) => {
  const [activeTab, setActiveTab] = useState<'cordset' | 'hardwired'>('cordset');

  // -------- Cordset state (preserves existing behavior verbatim) --------
  const [ports, setPorts] = useState<PowerPortDetail[]>([]);
  const [cables, setCables] = useState<PowerCableDetail[]>([]);
  const [outlets, setOutlets] = useState<PowerOutletDetail[]>([]);
  const [loading, setLoading] = useState(false);

  const [portLabel, setPortLabel] = useState('Main');
  const [portType, setPortType] = useState('5-15R');
  const [portMaxAmps, setPortMaxAmps] = useState<number | ''>('');

  const [cablePortId, setCablePortId] = useState<string | null>(null);
  const [cableOutletId, setCableOutletId] = useState<string | null>(null);
  const [cableLengthFt, setCableLengthFt] = useState<number | ''>('');

  // -------- Hardwired state --------
  const [hardwired, setHardwired] = useState<HardwiredConnection[]>([]);
  const [circuits, setCircuits] = useState<PowerCircuitDetail[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [lotoDevices, setLotoDevices] = useState<LOTODevice[]>([]);
  const [circuitDisconnects, setCircuitDisconnects] = useState<Disconnect[]>([]);

  const [selectedCircuitId, setSelectedCircuitId] = useState<string | null>(null);
  const [selectedDisconnectId, setSelectedDisconnectId] = useState<string | null>(null);
  const [disconnectDraft, setDisconnectDraft] =
    useState<NewDisconnectDraft>(emptyDisconnectDraft);
  const [conductorSize, setConductorSize] = useState('');
  const [conductorLengthFt, setConductorLengthFt] = useState<number | ''>('');
  const [hardwiredNotes, setHardwiredNotes] = useState('');
  const [submittingHardwired, setSubmittingHardwired] = useState(false);

  const isCreatingNewDisconnect = selectedDisconnectId === NEW_DISCONNECT_SENTINEL;

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [portsResp, cablesResp, outletsResp, hwResp] = await Promise.all([
        electricalTopologyAPI.listPorts({ asset: assetId }),
        electricalTopologyAPI.listPowerCables({ asset: assetId }),
        electricalTopologyAPI.listOutlets(),
        electricalCircuitsAPI.listHardwiredConnections({ asset: assetId }),
      ]);
      setPorts(unwrap(portsResp.data));
      setCables(unwrap(cablesResp.data));
      setOutlets(unwrap(outletsResp.data) as PowerOutletDetail[]);
      setHardwired(unwrap(hwResp.data));
    } catch (err: any) {
      showError(err?.response?.data?.error?.message || 'Failed to load power-chain editor');
    } finally {
      setLoading(false);
    }
  }, [assetId]);

  // Pickers and side-data load once per asset. These don't change in
  // response to user actions inside the editor.
  const loadPickers = useCallback(async () => {
    try {
      const [circuitsResp, locationsResp, devicesResp] = await Promise.all([
        electricalTopologyAPI.listCircuits(),
        inventoryAPI.listLocations(),
        lotoAPI.listDevices(),
      ]);
      setCircuits(unwrap(circuitsResp.data));
      setLocations(unwrap(locationsResp.data));
      setLotoDevices(unwrap(devicesResp.data));
    } catch (err: any) {
      showError(err?.response?.data?.error?.message || 'Failed to load picker data');
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    loadPickers();
  }, [loadPickers]);

  // Refetch the disconnect list whenever the operator picks a different
  // circuit so the "existing disconnect" dropdown narrows correctly.
  useEffect(() => {
    if (!selectedCircuitId) {
      setCircuitDisconnects([]);
      return;
    }
    let cancelled = false;
    electricalCircuitsAPI
      .listDisconnects({ circuit: selectedCircuitId })
      .then((resp) => {
        if (!cancelled) setCircuitDisconnects(unwrap(resp.data));
      })
      .catch((err) => {
        if (!cancelled) {
          showError(
            err?.response?.data?.error?.message || 'Failed to load disconnects for circuit',
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedCircuitId]);

  // -------- Cordset handlers (unchanged) --------
  const handleCreatePort = async () => {
    if (!portLabel.trim()) {
      showError('Port label is required');
      return;
    }
    try {
      await electricalTopologyAPI.createPort({
        asset: assetId,
        label: portLabel.trim(),
        port_type: portType,
        max_draw_amps: portMaxAmps === '' ? null : String(portMaxAmps),
      });
      showSuccess(`Port "${portLabel}" added`);
      setPortLabel('Main');
      setPortMaxAmps('');
      refresh();
      onChange();
    } catch (err: any) {
      showError(err?.response?.data?.error?.message || 'Failed to create port');
    }
  };

  const handleDeletePort = async (id: number) => {
    try {
      await electricalTopologyAPI.deletePort(id);
      showSuccess('Port removed');
      refresh();
      onChange();
    } catch (err: any) {
      showError(err?.response?.data?.error?.message || 'Failed to delete port');
    }
  };

  const handleConnect = async () => {
    if (!cablePortId || !cableOutletId) {
      showError('Pick a port and an outlet');
      return;
    }
    try {
      await electricalTopologyAPI.createPowerCable({
        port: Number(cablePortId),
        outlet: Number(cableOutletId),
        length_ft: cableLengthFt === '' ? null : Number(cableLengthFt),
        status: 'connected',
      });
      showSuccess('Connected');
      setCablePortId(null);
      setCableOutletId(null);
      setCableLengthFt('');
      refresh();
      onChange();
    } catch (err: any) {
      showError(err?.response?.data?.error?.message || 'Failed to connect cable');
    }
  };

  const handleDisconnect = async (id: number) => {
    try {
      await electricalTopologyAPI.deletePowerCable(id);
      showSuccess('Disconnected');
      refresh();
      onChange();
    } catch (err: any) {
      showError(err?.response?.data?.error?.message || 'Failed to disconnect');
    }
  };

  // -------- Hardwired handlers --------
  const resetHardwiredForm = () => {
    setSelectedCircuitId(null);
    setSelectedDisconnectId(null);
    setDisconnectDraft(emptyDisconnectDraft);
    setConductorSize('');
    setConductorLengthFt('');
    setHardwiredNotes('');
  };

  const handleSubmitHardwired = async () => {
    if (!selectedCircuitId) {
      showError('Pick a circuit first');
      return;
    }
    if (!selectedDisconnectId) {
      showError('Pick a disconnect (or create a new one)');
      return;
    }

    setSubmittingHardwired(true);
    try {
      let disconnectId: number;
      if (isCreatingNewDisconnect) {
        if (!disconnectDraft.label.trim()) {
          showError('Disconnect label is required');
          setSubmittingHardwired(false);
          return;
        }
        if (!disconnectDraft.disconnect_type) {
          showError('Pick a disconnect type');
          setSubmittingHardwired(false);
          return;
        }
        const dtype = disconnectDraft.disconnect_type;
        if (!LOCATIONLESS_TYPES.includes(dtype) && !disconnectDraft.location_id) {
          showError('Pick a location for this disconnect type');
          setSubmittingHardwired(false);
          return;
        }
        try {
          const created = await electricalCircuitsAPI.createDisconnect({
            circuit: Number(selectedCircuitId),
            label: disconnectDraft.label.trim(),
            disconnect_type: dtype,
            location: LOCATIONLESS_TYPES.includes(dtype)
              ? null
              : disconnectDraft.location_id
              ? Number(disconnectDraft.location_id)
              : null,
            amperage:
              disconnectDraft.amperage === '' ? null : Number(disconnectDraft.amperage),
            fuse_size:
              dtype === 'fused' ? disconnectDraft.fuse_size : '',
            is_lockable: disconnectDraft.is_lockable,
            required_loto_device_ids: disconnectDraft.required_loto_device_ids.map(Number),
            notes: disconnectDraft.notes,
          });
          disconnectId = created.data.id;
        } catch (err: any) {
          showError(
            err?.response?.data?.error?.message ||
              err?.response?.data?.detail ||
              'Failed to create disconnect',
          );
          setSubmittingHardwired(false);
          return;
        }
      } else {
        disconnectId = Number(selectedDisconnectId);
      }

      const body: HardwiredConnectionWritable = {
        asset: assetId,
        disconnect: disconnectId,
        conductor_size: conductorSize,
        conductor_length_ft: conductorLengthFt === '' ? null : Number(conductorLengthFt),
        notes: hardwiredNotes,
      };
      try {
        await electricalCircuitsAPI.createHardwiredConnection(body);
      } catch (err: any) {
        showError(
          err?.response?.data?.error?.message ||
            err?.response?.data?.detail ||
            'Failed to create hardwired connection',
        );
        setSubmittingHardwired(false);
        return;
      }

      showSuccess('Hardwired connection added');
      resetHardwiredForm();
      refresh();
      onChange();
    } finally {
      setSubmittingHardwired(false);
    }
  };

  const handleDeleteHardwired = async (id: number) => {
    try {
      await electricalCircuitsAPI.deleteHardwiredConnection(id);
      showSuccess('Hardwired connection removed');
      refresh();
      onChange();
    } catch (err: any) {
      showError(
        err?.response?.data?.error?.message || 'Failed to remove hardwired connection',
      );
    }
  };

  // -------- Memoized derived data --------
  const outletOptions = useMemo(
    () =>
      outlets.map((o) => ({
        value: String(o.id),
        label: `${o.label} (${o.location_name || 'unknown'})`,
      })),
    [outlets],
  );
  const portOptions = useMemo(
    () =>
      ports.map((p) => ({
        value: String(p.id),
        label: `${p.label} (${p.port_type})`,
      })),
    [ports],
  );
  const circuitOptions = useMemo(
    () =>
      [...circuits]
        .sort((a, b) => {
          const byPanel = (a.panel_name || '').localeCompare(b.panel_name || '');
          if (byPanel !== 0) return byPanel;
          return (a.breaker_label || '').localeCompare(b.breaker_label || '');
        })
        .map((c) => ({
          value: String(c.id),
          label: `${c.panel_name} · ${c.breaker_label}${c.label ? ` · ${c.label}` : ''}`,
        })),
    [circuits],
  );
  const disconnectOptions = useMemo(
    () => [
      ...circuitDisconnects.map((d) => ({
        value: String(d.id),
        label: `${d.label} (${d.disconnect_type})`,
      })),
      { value: NEW_DISCONNECT_SENTINEL, label: '+ Create new disconnect' },
    ],
    [circuitDisconnects],
  );
  const locationOptions = useMemo(
    () =>
      locations.map((l) => ({
        value: String(l.id),
        label: l.name,
      })),
    [locations],
  );
  const lotoDeviceOptions = useMemo(
    () =>
      lotoDevices.map((d) => ({
        value: String(d.id),
        label: `${d.label} (${d.device_type_display})`,
      })),
    [lotoDevices],
  );

  if (!isStaff) {
    return null;
  }

  return (
    <Paper withBorder p="md" radius="md" data-testid="asset-power-chain-editor">
      <Stack gap="md">
        <Title order={4}>Edit power chain</Title>

        <Tabs
          value={activeTab}
          onChange={(v) => setActiveTab((v as 'cordset' | 'hardwired') || 'cordset')}
        >
          <Tabs.List>
            <Tabs.Tab value="cordset" leftSection={<IconPlugConnected size={14} />}>
              Cordset
            </Tabs.Tab>
            <Tabs.Tab value="hardwired" leftSection={<IconBolt size={14} />}>
              Hardwired
            </Tabs.Tab>
          </Tabs.List>

          <Tabs.Panel value="cordset" pt="md">
            <Stack gap="md">
              <Stack gap="xs">
                <Text size="sm" fw={500}>
                  Ports on this asset
                </Text>
                {ports.length === 0 ? (
                  <Text size="sm" c="dimmed">
                    No ports defined. Add one below before wiring it to an outlet.
                  </Text>
                ) : (
                  <Stack gap={4}>
                    {ports.map((p) => (
                      <Group key={p.id} justify="space-between">
                        <Group gap="xs">
                          <IconPlugConnected size={14} />
                          <Text size="sm">{p.label}</Text>
                          <Badge size="xs" variant="light">
                            {p.port_type}
                          </Badge>
                          {p.max_draw_amps && (
                            <Text size="xs" c="dimmed">
                              max {p.max_draw_amps} A
                            </Text>
                          )}
                        </Group>
                        <Tooltip label="Delete port">
                          <ActionIcon
                            variant="subtle"
                            color="red"
                            onClick={() => handleDeletePort(p.id)}
                            aria-label={`Delete port ${p.label}`}
                          >
                            <IconTrash size={14} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
                    ))}
                  </Stack>
                )}
              </Stack>

              <Paper withBorder p="sm" radius="sm" bg="gray.0">
                <Stack gap="xs">
                  <Text size="sm" fw={500}>
                    Add port
                  </Text>
                  <Group grow align="flex-end">
                    <TextInput
                      label="Label"
                      value={portLabel}
                      onChange={(e) => setPortLabel(e.currentTarget.value)}
                      placeholder="Main / PSU 1 / etc."
                    />
                    <Select
                      label="Type"
                      value={portType}
                      onChange={(v) => setPortType(v ?? '5-15R')}
                      data={NEMA_TYPES}
                      searchable
                    />
                    <NumberInput
                      label="Max amps"
                      value={portMaxAmps}
                      onChange={(v) =>
                        setPortMaxAmps(
                          typeof v === 'number' ? v : v === '' ? '' : Number(v),
                        )
                      }
                      placeholder="optional"
                      min={0}
                      decimalScale={2}
                    />
                    <Button onClick={handleCreatePort} disabled={loading}>
                      Add port
                    </Button>
                  </Group>
                </Stack>
              </Paper>

              <Stack gap="xs">
                <Text size="sm" fw={500}>
                  Cables
                </Text>
                {cables.length === 0 ? (
                  <Text size="sm" c="dimmed">
                    No cables connected. Use the form below to wire a port to an outlet.
                  </Text>
                ) : (
                  <Stack gap={4}>
                    {cables.map((c) => (
                      <Group key={c.id} justify="space-between">
                        <Text size="sm">
                          Port <b>{c.port_label || `#${c.port_id}`}</b> → outlet{' '}
                          <b>{c.outlet_label || `#${c.outlet_id}`}</b>
                          {c.length_ft ? ` (${c.length_ft} ft)` : ''}
                          {c.status !== 'connected' ? (
                            <Badge size="xs" ml="xs" color="gray">
                              {c.status}
                            </Badge>
                          ) : null}
                        </Text>
                        <Tooltip label="Disconnect">
                          <ActionIcon
                            variant="subtle"
                            color="red"
                            onClick={() => handleDisconnect(c.id)}
                            aria-label={`Disconnect cable ${c.id}`}
                          >
                            <IconUnlink size={14} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
                    ))}
                  </Stack>
                )}
              </Stack>

              <Paper withBorder p="sm" radius="sm" bg="gray.0">
                <Stack gap="xs">
                  <Text size="sm" fw={500}>
                    Connect port → outlet
                  </Text>
                  <Group grow align="flex-end">
                    <Select
                      label="Port"
                      value={cablePortId}
                      onChange={setCablePortId}
                      data={portOptions}
                      placeholder={ports.length === 0 ? 'Add a port first' : 'select port'}
                      disabled={ports.length === 0}
                      searchable
                    />
                    <Select
                      label="Outlet"
                      value={cableOutletId}
                      onChange={setCableOutletId}
                      data={outletOptions}
                      placeholder="select outlet"
                      searchable
                    />
                    <NumberInput
                      label="Length (ft)"
                      value={cableLengthFt}
                      onChange={(v) =>
                        setCableLengthFt(
                          typeof v === 'number' ? v : v === '' ? '' : Number(v),
                        )
                      }
                      min={0}
                      placeholder="optional"
                    />
                    <Button onClick={handleConnect} disabled={loading || ports.length === 0}>
                      Connect
                    </Button>
                  </Group>
                </Stack>
              </Paper>
            </Stack>
          </Tabs.Panel>

          <Tabs.Panel value="hardwired" pt="md">
            <Stack gap="md">
              <Stack gap="xs">
                <Text size="sm" fw={500}>
                  Hardwired connections
                </Text>
                {hardwired.length === 0 ? (
                  <Text size="sm" c="dimmed">
                    No hardwired feeds recorded. Use the form below to register one
                    against an existing disconnect, or create the disconnect inline.
                  </Text>
                ) : (
                  <Stack gap={4}>
                    {hardwired.map((h) => (
                      <Group
                        key={h.id}
                        justify="space-between"
                        data-testid={`hardwired-row-${h.id}`}
                      >
                        <Group gap="xs">
                          <IconBolt size={14} />
                          <Text size="sm">
                            <b>{h.disconnect_label || `Disconnect #${h.disconnect}`}</b>
                            {h.circuit_label ? ` · ${h.circuit_label}` : ''}
                            {h.conductor_size ? ` · ${h.conductor_size}` : ''}
                            {h.conductor_length_ft != null
                              ? ` · ${h.conductor_length_ft} ft`
                              : ''}
                          </Text>
                          {h.needs_review && (
                            <Badge size="xs" color="yellow">
                              review
                            </Badge>
                          )}
                        </Group>
                        <Tooltip label="Remove hardwired connection">
                          <ActionIcon
                            variant="subtle"
                            color="red"
                            onClick={() => handleDeleteHardwired(h.id)}
                            aria-label={`Remove hardwired connection ${h.id}`}
                          >
                            <IconUnlink size={14} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
                    ))}
                  </Stack>
                )}
              </Stack>

              <Paper withBorder p="sm" radius="sm" bg="gray.0">
                <Stack gap="sm">
                  <Text size="sm" fw={500}>
                    Add hardwired connection
                  </Text>

                  <Select
                    label="Circuit"
                    value={selectedCircuitId}
                    onChange={(v) => {
                      setSelectedCircuitId(v);
                      setSelectedDisconnectId(null);
                    }}
                    data={circuitOptions}
                    placeholder="select circuit"
                    searchable
                  />

                  <Select
                    label="Disconnect"
                    value={selectedDisconnectId}
                    onChange={setSelectedDisconnectId}
                    data={disconnectOptions}
                    placeholder={
                      selectedCircuitId ? 'select disconnect' : 'pick a circuit first'
                    }
                    disabled={!selectedCircuitId}
                    searchable
                  />

                  {isCreatingNewDisconnect && (
                    <Paper
                      withBorder
                      p="sm"
                      radius="sm"
                      bg="white"
                      data-testid="new-disconnect-form"
                    >
                      <Stack gap="xs">
                        <Text size="xs" fw={500} c="dimmed">
                          New disconnect
                        </Text>
                        <TextInput
                          label="Label"
                          value={disconnectDraft.label}
                          onChange={(e) =>
                            setDisconnectDraft({
                              ...disconnectDraft,
                              label: e.currentTarget.value,
                            })
                          }
                          placeholder='e.g., "RTU-2 disconnect"'
                          required
                        />
                        <NativeSelect
                          label="Type"
                          value={disconnectDraft.disconnect_type ?? ''}
                          onChange={(e) => {
                            const raw = e.currentTarget.value;
                            const next = (raw || null) as DisconnectType | null;
                            const locationless = next
                              ? LOCATIONLESS_TYPES.includes(next)
                              : false;
                            setDisconnectDraft({
                              ...disconnectDraft,
                              disconnect_type: next,
                              // Integral / none can't accept a lockout —
                              // default the checkbox off and clear the
                              // separate location.
                              is_lockable: locationless
                                ? false
                                : disconnectDraft.is_lockable,
                              location_id: locationless
                                ? null
                                : disconnectDraft.location_id,
                            });
                          }}
                          data={[{ value: '', label: '— select disconnect type —' }].concat(
                            DISCONNECT_TYPE_OPTIONS.map((o) => ({
                              value: o.value,
                              label: o.label,
                            })),
                          )}
                        />

                        {disconnectDraft.disconnect_type &&
                          !LOCATIONLESS_TYPES.includes(
                            disconnectDraft.disconnect_type,
                          ) && (
                            <Select
                              label="Location"
                              value={disconnectDraft.location_id}
                              onChange={(v) =>
                                setDisconnectDraft({
                                  ...disconnectDraft,
                                  location_id: v,
                                })
                              }
                              data={locationOptions}
                              placeholder="where is the switch mounted?"
                              searchable
                              data-testid="disconnect-location-select"
                            />
                          )}

                        <Group grow align="flex-end">
                          <NumberInput
                            label="Amperage"
                            value={disconnectDraft.amperage}
                            onChange={(v) =>
                              setDisconnectDraft({
                                ...disconnectDraft,
                                amperage:
                                  typeof v === 'number' ? v : v === '' ? '' : Number(v),
                              })
                            }
                            placeholder="optional"
                            min={0}
                          />
                          {disconnectDraft.disconnect_type === 'fused' && (
                            <TextInput
                              label="Fuse size"
                              value={disconnectDraft.fuse_size}
                              onChange={(e) =>
                                setDisconnectDraft({
                                  ...disconnectDraft,
                                  fuse_size: e.currentTarget.value,
                                })
                              }
                              placeholder='e.g., "30A class J"'
                            />
                          )}
                        </Group>

                        <Checkbox
                          label="Accepts a lockout device"
                          checked={disconnectDraft.is_lockable}
                          onChange={(e) =>
                            setDisconnectDraft({
                              ...disconnectDraft,
                              is_lockable: e.currentTarget.checked,
                            })
                          }
                        />

                        <MultiSelect
                          label="Required LOTO devices"
                          data={lotoDeviceOptions}
                          value={disconnectDraft.required_loto_device_ids}
                          onChange={(v) =>
                            setDisconnectDraft({
                              ...disconnectDraft,
                              required_loto_device_ids: v,
                            })
                          }
                          placeholder="optional — preferred over breaker-level LOTO"
                          searchable
                        />

                        <Textarea
                          label="Notes"
                          value={disconnectDraft.notes}
                          onChange={(e) =>
                            setDisconnectDraft({
                              ...disconnectDraft,
                              notes: e.currentTarget.value,
                            })
                          }
                          minRows={2}
                          autosize
                        />
                      </Stack>
                    </Paper>
                  )}

                  <Box>
                    <Text size="xs" fw={500} c="dimmed" mb={4}>
                      Conductor (on the hardwired connection, not the disconnect)
                    </Text>
                    <Group grow align="flex-end">
                      <TextInput
                        label="Conductor size"
                        value={conductorSize}
                        onChange={(e) => setConductorSize(e.currentTarget.value)}
                        placeholder='e.g., "10 AWG"'
                      />
                      <NumberInput
                        label="Run length (ft)"
                        value={conductorLengthFt}
                        onChange={(v) =>
                          setConductorLengthFt(
                            typeof v === 'number' ? v : v === '' ? '' : Number(v),
                          )
                        }
                        min={0}
                        placeholder="optional"
                      />
                    </Group>
                    <Textarea
                      label="Notes"
                      value={hardwiredNotes}
                      onChange={(e) => setHardwiredNotes(e.currentTarget.value)}
                      minRows={1}
                      autosize
                      mt="xs"
                    />
                  </Box>

                  <Group justify="flex-end">
                    <Button
                      onClick={handleSubmitHardwired}
                      disabled={submittingHardwired || !selectedCircuitId || !selectedDisconnectId}
                      loading={submittingHardwired}
                    >
                      Add hardwired connection
                    </Button>
                  </Group>
                </Stack>
              </Paper>
            </Stack>
          </Tabs.Panel>
        </Tabs>
      </Stack>
    </Paper>
  );
};

export default AssetPowerChainEditor;
