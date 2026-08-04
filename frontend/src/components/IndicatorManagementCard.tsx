/**
 * IndicatorManagementCard
 *
 * Device-detail panel for a ForgeKey *indicator* device (epic ga-72l). Lets an
 * admin:
 *   - bind the light to an asset XOR a room (and unbind),
 *   - see the derived / last-pushed light state and re-sync it ("Sync now"),
 *   - send an explicit color/brightness/pattern preview ("Test light"),
 *   - read the color/pattern legend.
 *
 * For non-indicator devices it renders a greyed "not applicable" stub (op-3u4)
 * rather than nothing, so the device-detail page can mount it unconditionally
 * and staff can see the section exists but does not apply to this device.
 */
import {
  Badge,
  Button,
  Card,
  Group,
  NumberInput,
  SegmentedControl,
  Select,
  Stack,
  Text,
} from '@mantine/core';
import { useCallback, useEffect, useMemo, useState } from 'react';
import DeviceSectionGate from './DeviceSectionGate';
import IndicatorStatusLegend from './IndicatorStatusLegend';
import IndicatorSwatch from './IndicatorSwatch';
import ServiceUnavailableNotice, {
  DEVICE_CONTROL_UNAVAILABLE,
} from './ServiceUnavailableNotice';
import { useServiceStatus } from '../hooks/useServiceStatus';
import {
  ForgeKeyDevice,
  ForgeKeyDeviceType,
  ForgeKeyIndicatorBinding,
  ForgeKeyIndicatorSyncResponse,
  ForgeKeyIndicatorTestResponse,
  assetsAPI,
  forgekeyAPI,
  inventoryAPI,
} from '../services/api';
import { Asset, Location } from '../types';
import { confirmAction, showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';
import { isBlinkPattern, statusLabel } from '../utils/indicatorPresentation';

interface Props {
  device: ForgeKeyDevice;
  onChanged?: () => void;
}

// Default name the indicator DeviceType ships with; used as a fallback when the
// device-types lookup can't resolve the stable ``indicator`` code.
const INDICATOR_TYPE_NAME = 'Indicator/Status Light';

const STATUS_BADGE_COLORS: Record<string, string> = {
  available: 'green',
  in_use: 'teal',
  unavailable: 'red',
  locked_out: 'dark',
  classroom: 'grape',
};

const TEST_COLORS = ['green', 'red', 'purple', 'blue', 'yellow', 'white'];
const TEST_BRIGHTNESS = ['low', 'high'];
const TEST_PATTERNS = ['solid', 'blink', 'slow_blink', 'breathe', 'off'];

function unwrap<T>(data: { results?: T[] } | T[]): T[] {
  if (Array.isArray(data)) return data;
  return data.results ?? [];
}

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1).replace(/_/g, ' ');
}

export default function IndicatorManagementCard({ device, onChanged }: Props) {
  const { isDegraded } = useServiceStatus();
  const deviceControlDown = isDegraded('device_control');
  const [loading, setLoading] = useState(true);
  const [isIndicator, setIsIndicator] = useState(false);
  const [binding, setBinding] = useState<ForgeKeyIndicatorBinding | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const [targetKind, setTargetKind] = useState<'asset' | 'room'>('asset');
  const [targetAsset, setTargetAsset] = useState<string | null>(null);
  const [targetRoom, setTargetRoom] = useState<string | null>(null);

  const [testColor, setTestColor] = useState<string>('green');
  const [testBrightness, setTestBrightness] = useState<string>('high');
  const [testPattern, setTestPattern] = useState<string>('solid');
  const [testPeriod, setTestPeriod] = useState<number | string>(1500);

  const [syncResult, setSyncResult] = useState<ForgeKeyIndicatorSyncResponse | null>(null);
  const [testResult, setTestResult] = useState<ForgeKeyIndicatorTestResponse | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const typesRes = await forgekeyAPI.listDeviceTypes();
      const types = unwrap<ForgeKeyDeviceType>(typesRes.data);
      const indicatorType = types.find((t) => t.code === 'indicator');
      const indicator =
        (indicatorType != null && device.device_type === indicatorType.id) ||
        device.device_type_name === INDICATOR_TYPE_NAME;
      setIsIndicator(indicator);
      if (!indicator) {
        setBinding(null);
        return;
      }
      const bindRes = await forgekeyAPI.listIndicatorBindings({ device: device.id });
      const existing = unwrap<ForgeKeyIndicatorBinding>(bindRes.data)[0] ?? null;
      setBinding(existing);
      if (!existing) {
        const [assetRes, locRes] = await Promise.all([
          assetsAPI.listAssets({ page_size: 200, ordering: 'name' }),
          inventoryAPI.listLocations(),
        ]);
        setAssets(assetRes.data.results ?? []);
        setLocations(unwrap<Location>(locRes.data));
      }
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to load indicator settings.'));
    } finally {
      setLoading(false);
    }
  }, [device.id, device.device_type, device.device_type_name]);

  useEffect(() => {
    load();
  }, [load]);

  const reload = useCallback(async () => {
    await load();
    onChanged?.();
  }, [load, onChanged]);

  const assetOptions = useMemo(
    () =>
      assets.map((a) => ({
        value: a.id,
        label: a.asset_tag ? `${a.name} (${a.asset_tag})` : a.name,
      })),
    [assets],
  );
  const roomOptions = useMemo(
    () => locations.map((l) => ({ value: String(l.id), label: l.name })),
    [locations],
  );

  const onBind = async () => {
    const body =
      targetKind === 'asset'
        ? targetAsset && { device: device.id, asset: targetAsset }
        : targetRoom && { device: device.id, location: Number(targetRoom) };
    if (!body) {
      showError('Pick an asset or a room to bind to.');
      return;
    }
    setBusy('bind');
    try {
      await forgekeyAPI.createIndicatorBinding(body);
      showSuccess('Indicator bound.');
      setTargetAsset(null);
      setTargetRoom(null);
      await reload();
    } catch (err) {
      showError(extractErrorMessage(err, 'Bind failed.'));
    } finally {
      setBusy(null);
    }
  };

  const onSync = async () => {
    if (!binding) return;
    setBusy('sync');
    try {
      const res = await forgekeyAPI.indicatorSync(binding.id);
      setSyncResult(res.data);
      showSuccess('Indicator re-synced.');
      await reload();
    } catch (err) {
      showError(extractErrorMessage(err, 'Sync failed.'));
    } finally {
      setBusy(null);
    }
  };

  const onUnbind = () => {
    if (!binding) return;
    const label = binding.asset_name ?? binding.location_name ?? 'its target';
    confirmAction(
      'Unbind indicator',
      `Unbind this light from ${label}?`,
      async () => {
        setBusy('unbind');
        try {
          await forgekeyAPI.deleteIndicatorBinding(binding.id);
          showSuccess('Indicator unbound.');
          setSyncResult(null);
          await reload();
        } catch (err) {
          showError(extractErrorMessage(err, 'Unbind failed.'));
        } finally {
          setBusy(null);
        }
      },
      { color: 'red' },
    );
  };

  const onTest = async () => {
    setBusy('test');
    try {
      const body: {
        color?: string;
        brightness?: string;
        pattern?: string;
        period_ms?: number;
      } = { brightness: testBrightness, pattern: testPattern };
      if (testPattern !== 'off') body.color = testColor;
      if (isBlinkPattern(testPattern) && testPeriod) body.period_ms = Number(testPeriod);
      const res = await forgekeyAPI.indicatorTest(device.id, body);
      setTestResult(res.data);
      showSuccess('Test command sent.');
    } catch (err) {
      showError(extractErrorMessage(err, 'Test failed.'));
    } finally {
      setBusy(null);
    }
  };

  if (loading) return null;
  if (!isIndicator) {
    return (
      <DeviceSectionGate relevant="no" testId="indicator-not-applicable">
        <Card withBorder radius="md" p="md">
          <Text fw={600}>Indicator light</Text>
          <Text size="xs" c="dimmed">
            Bind a status light to an asset or room, preview it, and re-sync its state.
          </Text>
        </Card>
      </DeviceSectionGate>
    );
  }

  const previewPresentation = {
    color: testPattern === 'off' ? null : testColor,
    brightness: testBrightness,
    pattern: testPattern,
  };

  return (
    <Card withBorder radius="md" p="md" data-testid="indicator-management">
      <Text fw={600}>Indicator light</Text>
      <Text size="xs" c="dimmed" mb="md">
        Bind this light to an asset or room, preview it, and re-sync its state.
      </Text>

      <Stack gap="lg">
        {binding ? (
          <div data-testid="indicator-binding">
            <Group gap="xs" mb="xs">
              <Text size="sm" fw={500}>
                Bound to
              </Text>
              <Badge variant="light" color={binding.asset ? 'blue' : 'grape'}>
                {binding.asset ? 'Asset' : 'Room'}
              </Badge>
              <Text size="sm" data-testid="indicator-target-name">
                {binding.asset_name ?? binding.location_name ?? '—'}
              </Text>
            </Group>

            <Group gap="xs" mb="xs">
              <Text size="sm" fw={500}>
                Current light
              </Text>
              {binding.last_status ? (
                <>
                  <IndicatorSwatch
                    status={binding.last_status}
                    testId="indicator-current-swatch"
                  />
                  <Badge
                    color={STATUS_BADGE_COLORS[binding.last_status] ?? 'gray'}
                    data-testid="indicator-current-status"
                  >
                    {statusLabel(binding.last_status)}
                  </Badge>
                </>
              ) : (
                <Text size="sm" c="dimmed" data-testid="indicator-current-status">
                  Not yet synced
                </Text>
              )}
              {binding.last_synced_at && (
                <Text size="xs" c="dimmed">
                  last synced {new Date(binding.last_synced_at).toLocaleString()}
                </Text>
              )}
            </Group>

            {syncResult && (
              <Text size="xs" c="dimmed" mb="xs" data-testid="indicator-sync-result">
                Synced: {statusLabel(syncResult.status)}
                {syncResult.command_id ? ` · command ${syncResult.command_id}` : ''}
              </Text>
            )}

            <Group gap="xs">
              <Button
                size="xs"
                variant="light"
                loading={busy === 'sync'}
                disabled={deviceControlDown}
                onClick={onSync}
                data-testid="indicator-sync-btn"
              >
                Sync now
              </Button>
              <Button
                size="xs"
                variant="light"
                color="red"
                loading={busy === 'unbind'}
                onClick={onUnbind}
                data-testid="indicator-unbind-btn"
              >
                Unbind
              </Button>
            </Group>
            {/* "Sync now" re-pushes the light state over MQTT; unbinding is a
                database change and stays available. */}
            <ServiceUnavailableNotice
              service="device_control"
              message={DEVICE_CONTROL_UNAVAILABLE}
              testId="indicator-sync-device-control-notice"
            />
          </div>
        ) : (
          <div data-testid="indicator-bind-form">
            <Text size="sm" fw={500} mb="xs">
              Not bound — bind this light to a target
            </Text>
            <Stack gap="xs">
              <SegmentedControl
                value={targetKind}
                onChange={(v) => setTargetKind(v as 'asset' | 'room')}
                data={[
                  { label: 'Asset', value: 'asset' },
                  { label: 'Room', value: 'room' },
                ]}
                data-testid="indicator-target-kind"
              />
              {targetKind === 'asset' ? (
                <Select
                  label="Asset"
                  placeholder="Pick an asset"
                  searchable
                  data={assetOptions}
                  value={targetAsset}
                  onChange={setTargetAsset}
                  aria-label="Asset"
                  data-testid="indicator-asset-select"
                />
              ) : (
                <Select
                  label="Room"
                  placeholder="Pick a room"
                  searchable
                  data={roomOptions}
                  value={targetRoom}
                  onChange={setTargetRoom}
                  aria-label="Room"
                  data-testid="indicator-room-select"
                />
              )}
              <Group>
                <Button
                  size="xs"
                  loading={busy === 'bind'}
                  onClick={onBind}
                  data-testid="indicator-bind-btn"
                >
                  Bind
                </Button>
              </Group>
            </Stack>
          </div>
        )}

        <div data-testid="indicator-test">
          <Text size="sm" fw={500} mb="xs">
            Test light
          </Text>
          <Group align="flex-end" gap="sm" wrap="wrap">
            <Select
              label="Color"
              data={TEST_COLORS.map((c) => ({ value: c, label: titleCase(c) }))}
              value={testColor}
              onChange={(v) => setTestColor(v ?? 'green')}
              w={120}
              aria-label="Color"
              data-testid="indicator-test-color"
            />
            <Select
              label="Brightness"
              data={TEST_BRIGHTNESS.map((b) => ({ value: b, label: titleCase(b) }))}
              value={testBrightness}
              onChange={(v) => setTestBrightness(v ?? 'high')}
              w={120}
              aria-label="Brightness"
              data-testid="indicator-test-brightness"
            />
            <Select
              label="Pattern"
              data={TEST_PATTERNS.map((p) => ({ value: p, label: titleCase(p) }))}
              value={testPattern}
              onChange={(v) => setTestPattern(v ?? 'solid')}
              w={130}
              aria-label="Pattern"
              data-testid="indicator-test-pattern"
            />
            {isBlinkPattern(testPattern) && (
              <NumberInput
                label="Period (ms)"
                min={1}
                max={60000}
                value={testPeriod}
                onChange={setTestPeriod}
                w={120}
                aria-label="Period (ms)"
                data-testid="indicator-test-period"
              />
            )}
            <Group gap="xs" pb={4}>
              <Text size="xs" c="dimmed">
                Preview
              </Text>
              <IndicatorSwatch
                presentation={previewPresentation}
                size={20}
                testId="indicator-test-preview"
              />
            </Group>
          </Group>
          <Button
            size="xs"
            mt="sm"
            variant="light"
            loading={busy === 'test'}
            disabled={deviceControlDown}
            onClick={onTest}
            data-testid="indicator-test-btn"
          >
            Send test
          </Button>
          {/* A test push is a device command over MQTT, so it goes with the
              broker. */}
          <ServiceUnavailableNotice
            service="device_control"
            message={DEVICE_CONTROL_UNAVAILABLE}
            testId="indicator-device-control-notice"
          />
          {testResult && (
            <Text size="xs" c="dimmed" mt="xs" data-testid="indicator-test-result">
              Sent {testResult.payload.pattern ?? ''} · command {testResult.command_id}
            </Text>
          )}
        </div>

        <IndicatorStatusLegend />
      </Stack>
    </Card>
  );
}
