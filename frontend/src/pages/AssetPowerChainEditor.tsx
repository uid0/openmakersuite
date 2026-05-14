/**
 * Inline editor for an asset's power chain — replaces the prior "use the
 * Django admin" instruction with a form-driven flow.
 *
 * Two affordances:
 *   1. Add a PowerPort to the asset (label, NEMA type, optional max draw).
 *   2. Wire a PowerPort to a PowerOutlet by creating a power Cable, or
 *      tear an existing cable down.
 *
 * Staff-gated: the form only renders for `is_staff`. The backend enforces
 * the same gate; this is a UX hide, not a security boundary.
 */
import {
  ActionIcon,
  Badge,
  Button,
  Group,
  NumberInput,
  Paper,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { IconPlugConnected, IconTrash, IconUnlink } from '@tabler/icons-react';
import React, { useCallback, useEffect, useState } from 'react';

import {
  PowerCableDetail,
  PowerOutletDetail,
  PowerPortDetail,
  electricalTopologyAPI,
} from '../services/api';
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

interface Props {
  assetId: string;
  isStaff: boolean;
  onChange: () => void;
}

function unwrap<T>(payload: { results: T[] } | T[]): T[] {
  if (Array.isArray(payload)) return payload;
  return payload?.results ?? [];
}

export const AssetPowerChainEditor: React.FC<Props> = ({ assetId, isStaff, onChange }) => {
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

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [portsResp, cablesResp, outletsResp] = await Promise.all([
        electricalTopologyAPI.listPorts({ asset: assetId }),
        electricalTopologyAPI.listPowerCables({ asset: assetId }),
        electricalTopologyAPI.listOutlets(),
      ]);
      setPorts(unwrap(portsResp.data));
      setCables(unwrap(cablesResp.data));
      setOutlets(unwrap(outletsResp.data) as PowerOutletDetail[]);
    } catch (err: any) {
      showError(err?.response?.data?.error?.message || 'Failed to load power-chain editor');
    } finally {
      setLoading(false);
    }
  }, [assetId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

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

  if (!isStaff) {
    return null;
  }

  const outletOptions = outlets.map((o) => ({
    value: String(o.id),
    label: `${o.label} (${o.location_name || 'unknown'})`,
  }));
  const portOptions = ports.map((p) => ({
    value: String(p.id),
    label: `${p.label} (${p.port_type})`,
  }));

  return (
    <Paper withBorder p="md" radius="md" data-testid="asset-power-chain-editor">
      <Stack gap="md">
        <Title order={4}>Edit power chain</Title>

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
                  setPortMaxAmps(typeof v === 'number' ? v : v === '' ? '' : Number(v))
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
                  setCableLengthFt(typeof v === 'number' ? v : v === '' ? '' : Number(v))
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
    </Paper>
  );
};

export default AssetPowerChainEditor;
