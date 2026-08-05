/**
 * AssetBoundDevicesCard
 *
 * "Bound devices" for one inventory Asset (op-rmic) — the quick-assign surface
 * for ForgeKey hardware that previously existed only in the Django admin:
 *
 *   - ``AssetDevice`` rows (relay / meter, keyed by a free-text ``role``), and
 *   - the asset's ``IndicatorBinding`` rows (status lights),
 *
 * each with a detach, plus one "Attach device" flow: pick a device, pick what
 * to attach it as, done. "Indicator light" writes an ``IndicatorBinding`` (a
 * different table — device-unique, indicator-type only, and the bind pushes the
 * light's initial state server-side); every other choice writes an
 * ``AssetDevice``.
 *
 * ``is_primary`` is set at attach time (defaulting to checked for the asset's
 * first control device) because it decides which device answers for the asset —
 * see ``_primary_device_for_asset`` / ``_has_offline_primary_device`` on the
 * backend. Re-pointing it afterwards stays an admin job.
 *
 * Both writes are database-only, so nothing here is gated on the device-control
 * service being up.
 */
import { Badge, Button, Card, Checkbox, Group, Select, Stack, Text } from '@mantine/core';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ForgeKeyAssetDevice,
  ForgeKeyDevice,
  ForgeKeyDeviceType,
  ForgeKeyIndicatorBinding,
  forgekeyAPI,
} from '../services/api';
import { confirmAction, showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface Props {
  assetId: string;
}

// The `role` column is free text on the backend; these are the conventional
// values (see AssetDevice.role's help_text). INDICATOR_ROLE is not a role at
// all — it routes the attach to the IndicatorBinding endpoint instead.
const INDICATOR_ROLE = 'indicator';

const ATTACH_OPTIONS = [
  { value: 'power_control', label: 'Power control (relay)' },
  { value: 'metering', label: 'Power metering' },
  { value: INDICATOR_ROLE, label: 'Indicator light' },
];

const ROLE_LABELS: Record<string, string> = {
  power_control: 'Power control',
  metering: 'Metering',
};

// Default name the indicator DeviceType ships with; the fallback when the
// device-types lookup can't resolve the stable ``indicator`` code.
const INDICATOR_TYPE_NAME = 'Indicator/Status Light';

// Runaway guard on the fleet walk below — 20 pages is 1000 devices.
const MAX_DEVICE_PAGES = 20;

function unwrap<T>(data: { results?: T[] } | T[]): T[] {
  if (Array.isArray(data)) return data;
  return data.results ?? [];
}

/** Every device, not just the first page — a picker that silently drops the
 *  device you're looking for is worse than a slightly slower one. */
async function fetchAllDevices(): Promise<ForgeKeyDevice[]> {
  const all: ForgeKeyDevice[] = [];
  for (let page = 1; page <= MAX_DEVICE_PAGES; page += 1) {
    // Sequential by necessity: whether there is a page N+1 is only known from N.
    const { data } = await forgekeyAPI.listDevices({ page });
    all.push(...unwrap<ForgeKeyDevice>(data));
    if (Array.isArray(data) || !data.next) break;
  }
  return all;
}

function roleLabel(role: string): string {
  if (!role) return 'Unspecified role';
  return ROLE_LABELS[role] ?? role.replace(/_/g, ' ');
}

function deviceLabel(device: ForgeKeyDevice): string {
  return device.name ? `${device.name} (${device.mac_address})` : device.mac_address;
}

export default function AssetBoundDevicesCard({ assetId }: Props) {
  const [assetDevices, setAssetDevices] = useState<ForgeKeyAssetDevice[]>([]);
  const [indicators, setIndicators] = useState<ForgeKeyIndicatorBinding[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  // Attach flow.
  const [attachOpen, setAttachOpen] = useState(false);
  const [devices, setDevices] = useState<ForgeKeyDevice[]>([]);
  const [indicatorTypeId, setIndicatorTypeId] = useState<number | null>(null);
  const [attachAs, setAttachAs] = useState<string>('power_control');
  const [attachDevice, setAttachDevice] = useState<string | null>(null);
  const [attachPrimary, setAttachPrimary] = useState(false);

  const load = useCallback(async () => {
    try {
      const [deviceRes, indicatorRes] = await Promise.all([
        forgekeyAPI.listAssetDevices({ asset: assetId }),
        forgekeyAPI.listIndicatorBindings({ asset: assetId }),
      ]);
      setAssetDevices(unwrap<ForgeKeyAssetDevice>(deviceRes.data));
      setIndicators(unwrap<ForgeKeyIndicatorBinding>(indicatorRes.data));
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to load bound devices.'));
    } finally {
      setLoading(false);
    }
  }, [assetId]);

  useEffect(() => {
    load();
  }, [load]);

  // The fleet is only needed once someone opens the picker.
  useEffect(() => {
    if (!attachOpen) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const [fleet, typeRes] = await Promise.all([
          fetchAllDevices(),
          forgekeyAPI.listDeviceTypes(),
        ]);
        if (cancelled) return;
        setDevices(fleet);
        const types = unwrap<ForgeKeyDeviceType>(typeRes.data);
        setIndicatorTypeId(types.find((t) => t.code === INDICATOR_ROLE)?.id ?? null);
      } catch (err) {
        if (!cancelled) showError(extractErrorMessage(err, 'Failed to load devices.'));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [attachOpen]);

  const openAttach = () => {
    // First control device on an asset is the primary one by default.
    setAttachPrimary(assetDevices.length === 0);
    setAttachOpen(true);
  };

  const resetAttach = () => {
    setAttachOpen(false);
    setAttachDevice(null);
    setAttachAs('power_control');
    setAttachPrimary(false);
  };

  const attachingIndicator = attachAs === INDICATOR_ROLE;

  const deviceOptions = useMemo(() => {
    const bound = new Set<string>([
      ...assetDevices.map((d) => d.device),
      ...indicators.map((b) => b.device),
    ]);
    return devices
      .filter((d) => d.is_active && !bound.has(d.id))
      .filter(
        (d) =>
          !attachingIndicator ||
          (indicatorTypeId != null
            ? d.device_type === indicatorTypeId
            : d.device_type_name === INDICATOR_TYPE_NAME),
      )
      .map((d) => ({ value: d.id, label: deviceLabel(d) }));
  }, [devices, assetDevices, indicators, attachingIndicator, indicatorTypeId]);

  const run = async (key: string, fn: () => Promise<unknown>, success: string) => {
    setBusy(key);
    try {
      await fn();
      showSuccess(success);
      await load();
    } catch (err) {
      showError(extractErrorMessage(err, 'Action failed.'));
    } finally {
      setBusy(null);
    }
  };

  const handleAttach = async () => {
    if (!attachDevice) return;
    setBusy('attach');
    try {
      if (attachingIndicator) {
        await forgekeyAPI.createIndicatorBinding({ device: attachDevice, asset: assetId });
      } else {
        await forgekeyAPI.createAssetDevice({
          asset: assetId,
          device: attachDevice,
          role: attachAs,
          is_primary: attachPrimary,
        });
      }
      showSuccess('Device attached.');
      resetAttach();
      await load();
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to attach device.'));
    } finally {
      setBusy(null);
    }
  };

  const detachAssetDevice = (row: ForgeKeyAssetDevice) =>
    confirmAction(
      'Detach device',
      `Detach ${row.device_name || row.device_mac_address} from this asset?`,
      () =>
        run(
          `detach-${row.id}`,
          () => forgekeyAPI.deleteAssetDevice(row.id),
          'Device detached.',
        ),
      { color: 'red' },
    );

  const detachIndicator = (row: ForgeKeyIndicatorBinding) =>
    confirmAction(
      'Detach indicator',
      `Detach ${row.device_name || row.device_mac_address} from this asset?`,
      () =>
        run(
          `detach-indicator-${row.id}`,
          () => forgekeyAPI.deleteIndicatorBinding(row.id),
          'Indicator detached.',
        ),
      { color: 'red' },
    );

  if (loading) return null;

  const total = assetDevices.length + indicators.length;

  return (
    <Card withBorder radius="md" p="md" data-testid="asset-bound-devices">
      <Text fw={600}>Bound devices ({total})</Text>
      <Text size="xs" c="dimmed" mb="md">
        ForgeKey hardware attached to this asset — relays, meters, and status lights.
      </Text>

      <Stack gap="sm">
        {total === 0 && (
          <Text size="xs" c="dimmed" data-testid="bound-devices-empty">
            No devices bound to this asset.
          </Text>
        )}

        {assetDevices.map((row) => (
          <Group key={`ad-${row.id}`} justify="space-between" wrap="nowrap">
            <div>
              <Group gap="xs">
                <Text size="sm">{row.device_name || row.device_mac_address}</Text>
                <Badge variant="light" color="blue" data-testid={`role-badge-${row.id}`}>
                  {roleLabel(row.role)}
                </Badge>
                {row.is_primary && (
                  <Badge variant="light" color="teal" data-testid={`primary-badge-${row.id}`}>
                    Primary
                  </Badge>
                )}
              </Group>
              <Text size="xs" c="dimmed">
                {row.device_mac_address}
              </Text>
            </div>
            <Button
              size="xs"
              variant="light"
              color="red"
              loading={busy === `detach-${row.id}`}
              onClick={() => detachAssetDevice(row)}
              data-testid={`detach-${row.id}`}
            >
              Detach
            </Button>
          </Group>
        ))}

        {indicators.map((row) => (
          <Group key={`ib-${row.id}`} justify="space-between" wrap="nowrap">
            <div>
              <Group gap="xs">
                <Text size="sm">{row.device_name || row.device_mac_address}</Text>
                <Badge variant="light" color="grape">
                  Indicator
                </Badge>
              </Group>
              <Text size="xs" c="dimmed">
                {row.device_mac_address}
                {row.last_status && ` · ${row.last_status.replace(/_/g, ' ')}`}
              </Text>
            </div>
            <Button
              size="xs"
              variant="light"
              color="red"
              loading={busy === `detach-indicator-${row.id}`}
              onClick={() => detachIndicator(row)}
              data-testid={`detach-indicator-${row.id}`}
            >
              Detach
            </Button>
          </Group>
        ))}

        {!attachOpen ? (
          <Group>
            <Button
              size="xs"
              variant="light"
              onClick={openAttach}
              data-testid="attach-device-open"
            >
              Attach device
            </Button>
          </Group>
        ) : (
          <Stack gap="xs" data-testid="attach-device-panel">
            <Select
              label="Attach as"
              data={ATTACH_OPTIONS}
              value={attachAs}
              onChange={(v) => {
                setAttachAs(v ?? 'power_control');
                // The eligible devices differ per kind — drop a stale pick.
                setAttachDevice(null);
              }}
              data-testid="attach-role-select"
            />
            <Select
              label="Device"
              placeholder={
                attachingIndicator ? 'Pick an indicator device…' : 'Pick a device…'
              }
              searchable
              data={deviceOptions}
              value={attachDevice}
              onChange={setAttachDevice}
              nothingFoundMessage="No available devices"
              data-testid="attach-device-select"
            />
            {!attachingIndicator && (
              <Checkbox
                label="Primary device for this asset"
                checked={attachPrimary}
                onChange={(e) => setAttachPrimary(e.currentTarget.checked)}
                data-testid="attach-primary"
              />
            )}
            <Group gap="xs">
              <Button
                size="xs"
                loading={busy === 'attach'}
                disabled={!attachDevice}
                onClick={handleAttach}
                data-testid="attach-submit"
              >
                Attach
              </Button>
              <Button size="xs" variant="subtle" onClick={resetAttach}>
                Cancel
              </Button>
            </Group>
          </Stack>
        )}
      </Stack>
    </Card>
  );
}
