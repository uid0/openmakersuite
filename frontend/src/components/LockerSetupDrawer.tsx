/**
 * LockerSetupDrawer
 *
 * Create / edit a locker and bind ESP32 devices to it by role — the web
 * equivalent of the Django-admin setup that locker provisioning used to
 * require. Mutations are manager-gated server-side (staff / logistics / SIG
 * admin); this drawer just surfaces the form + device bindings.
 *
 * Create mode flips to edit mode on save so device binding becomes available
 * immediately against the freshly-created locker.
 */
import {
  Badge,
  Button,
  Divider,
  Drawer,
  Group,
  MultiSelect,
  NumberInput,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useCallback, useEffect, useState } from 'react';
import {
  assetsAPI,
  forgekeyAPI,
  inventoryAPI,
  lockersAPI,
  sigAPI,
  type ForgeKeyLocker,
  type ForgeKeyLockerDevice,
} from '../services/api';
import { showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

type Option = { value: string; label: string };

const ROLE_OPTIONS: Option[] = [
  { value: 'latch', label: 'Latch controller' },
  { value: 'reed_switch', label: 'Door reed switch' },
  { value: 'ir_break', label: 'Inventory IR break sensor' },
  { value: 'keypad', label: 'OTP keypad' },
  { value: 'led_strip', label: 'WS2818 LED strip controller' },
  { value: 'mortise_key', label: 'Mortise key (admin override) sensor' },
];

const POWER_OPTIONS: Option[] = [
  { value: 'poe', label: 'Power over Ethernet' },
  { value: 'usb', label: 'USB-C / barrel jack' },
  { value: 'ac_outlet', label: 'AC mains outlet' },
  { value: 'battery', label: 'Battery only' },
  { value: 'unpowered', label: 'Unpowered (mechanical only)' },
];

const asArray = <T,>(data: { results?: T[] } | T[]): T[] =>
  Array.isArray(data) ? data : (data.results ?? []);

const toOptions = (rows: Array<{ id: number | string; name: string }>): Option[] =>
  rows.map((r) => ({ value: String(r.id), label: r.name }));

interface Props {
  opened: boolean;
  onClose: () => void;
  /** The locker to edit, or null to create a new one. */
  locker: ForgeKeyLocker | null;
  /** Called after any successful mutation so the parent list can reload. */
  onSaved: () => void;
}

export default function LockerSetupDrawer({ opened, onClose, locker, onSaved }: Props) {
  const [current, setCurrent] = useState<ForgeKeyLocker | null>(locker);
  const editing = current !== null;

  const [name, setName] = useState('');
  const [locationId, setLocationId] = useState<string | null>(null);
  const [sigId, setSigId] = useState<string | null>(null);
  const [assetId, setAssetId] = useState<string | null>(null);
  const [powerSource, setPowerSource] = useState('poe');
  const [ledCount, setLedCount] = useState(0);
  const [highTrust, setHighTrust] = useState(false);
  const [isActive, setIsActive] = useState(true);
  const [certIds, setCertIds] = useState<string[]>([]);
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);

  const [locations, setLocations] = useState<Option[]>([]);
  const [sigs, setSigs] = useState<Option[]>([]);
  const [assets, setAssets] = useState<Option[]>([]);
  const [devices, setDevices] = useState<Option[]>([]);
  const [certs, setCerts] = useState<Option[]>([]);

  const [boundDevices, setBoundDevices] = useState<ForgeKeyLockerDevice[]>([]);
  const [newDeviceId, setNewDeviceId] = useState<string | null>(null);
  const [newRole, setNewRole] = useState<string | null>(null);
  const [newPrimary, setNewPrimary] = useState(false);
  const [bindingBusy, setBindingBusy] = useState(false);

  // (Re)seed the form whenever the drawer opens for a given locker.
  useEffect(() => {
    if (!opened) {
      return;
    }
    setCurrent(locker);
    if (locker) {
      setName(locker.name);
      setLocationId(locker.location != null ? String(locker.location) : null);
      setSigId(locker.owning_sig != null ? String(locker.owning_sig) : null);
      setAssetId(locker.current_asset);
      setPowerSource(locker.power_source || 'poe');
      setLedCount(locker.led_count);
      setHighTrust(locker.is_high_trust);
      setIsActive(locker.is_active);
      setCertIds((locker.required_certifications || []).map(String));
      setDescription(locker.description || '');
      setBoundDevices(locker.devices || []);
    } else {
      setName('');
      setLocationId(null);
      setSigId(null);
      setAssetId(null);
      setPowerSource('poe');
      setLedCount(0);
      setHighTrust(false);
      setIsActive(true);
      setCertIds([]);
      setDescription('');
      setBoundDevices([]);
    }
    setNewDeviceId(null);
    setNewRole(null);
    setNewPrimary(false);
  }, [opened, locker]);

  // Load the picker option lists on open.
  useEffect(() => {
    if (!opened) {
      return;
    }
    let alive = true;
    (async () => {
      try {
        const [loc, sg, as, dev, ct] = await Promise.all([
          inventoryAPI.listLocations(),
          sigAPI.listMySIGs(),
          assetsAPI.listAssets(),
          forgekeyAPI.listDevices(),
          lockersAPI.listAvailableCertifications(),
        ]);
        if (!alive) {
          return;
        }
        setLocations(toOptions(asArray(loc.data)));
        setSigs(toOptions(sg.data.results ?? []));
        setAssets(toOptions(as.data.results ?? []));
        setDevices(
          asArray(dev.data).map((d) => ({
            value: String(d.id),
            label: d.name || d.mac_address,
          })),
        );
        setCerts(toOptions(ct.data));
      } catch (err) {
        showError(extractErrorMessage(err, 'Failed to load setup options.'));
      }
    })();
    return () => {
      alive = false;
    };
  }, [opened]);

  const reloadDevices = useCallback(async (id: string) => {
    const fresh = await lockersAPI.getLocker(id);
    setBoundDevices(fresh.data.devices || []);
  }, []);

  const handleSave = useCallback(async () => {
    if (!name.trim() || !locationId || !sigId) {
      showError('Name, location, and owning SIG are required.');
      return;
    }
    setSaving(true);
    const payload = {
      name: name.trim(),
      location: Number(locationId),
      owning_sig: Number(sigId),
      description,
      power_source: powerSource,
      current_asset: assetId,
      is_high_trust: highTrust,
      led_count: ledCount,
      required_certifications: certIds.map(Number),
      is_active: isActive,
    };
    try {
      if (current) {
        const res = await lockersAPI.updateLocker(current.id, payload);
        setCurrent(res.data);
        setBoundDevices(res.data.devices || []);
        showSuccess('Locker updated.');
      } else {
        const res = await lockersAPI.createLocker(payload);
        // Flip to edit mode so device binding becomes available.
        setCurrent(res.data);
        setBoundDevices(res.data.devices || []);
        showSuccess('Locker created — you can now bind devices.');
      }
      onSaved();
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to save locker.'));
    } finally {
      setSaving(false);
    }
  }, [
    name,
    locationId,
    sigId,
    description,
    powerSource,
    assetId,
    highTrust,
    ledCount,
    certIds,
    isActive,
    current,
    onSaved,
  ]);

  const handleAddDevice = useCallback(async () => {
    if (!current || !newDeviceId || !newRole) {
      showError('Pick a device and a role.');
      return;
    }
    setBindingBusy(true);
    try {
      await lockersAPI.addLockerDevice(current.id, {
        device: newDeviceId,
        role: newRole,
        is_primary: newPrimary,
      });
      // Primary demotion happens server-side, so re-read the bindings.
      await reloadDevices(current.id);
      setNewDeviceId(null);
      setNewRole(null);
      setNewPrimary(false);
      showSuccess('Device bound.');
      onSaved();
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to bind device.'));
    } finally {
      setBindingBusy(false);
    }
  }, [current, newDeviceId, newRole, newPrimary, reloadDevices, onSaved]);

  const handleRemoveDevice = useCallback(
    async (assignmentId: number) => {
      if (!current) {
        return;
      }
      setBindingBusy(true);
      try {
        await lockersAPI.removeLockerDevice(current.id, assignmentId);
        setBoundDevices((prev) => prev.filter((d) => d.id !== assignmentId));
        showSuccess('Device unbound.');
        onSaved();
      } catch (err) {
        showError(extractErrorMessage(err, 'Failed to unbind device.'));
      } finally {
        setBindingBusy(false);
      }
    },
    [current, onSaved],
  );

  return (
    <Drawer
      opened={opened}
      onClose={onClose}
      position="right"
      size="lg"
      title={editing ? `Locker setup — ${current?.name}` : 'New locker'}
    >
      <Stack gap="sm">
        <TextInput
          label="Name"
          required
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          data-testid="locker-name"
        />
        <Select
          label="Location"
          required
          searchable
          data={locations}
          value={locationId}
          onChange={setLocationId}
        />
        <Select
          label="Owning SIG"
          required
          searchable
          data={sigs}
          value={sigId}
          onChange={setSigId}
        />
        <Select
          label="Current asset"
          description="Asset stored inside (optional)"
          searchable
          clearable
          data={assets}
          value={assetId}
          onChange={setAssetId}
        />
        <Select
          label="Power source"
          data={POWER_OPTIONS}
          value={powerSource}
          onChange={(v) => setPowerSource(v || 'poe')}
        />
        <NumberInput
          label="LED count"
          description="WS2818 LEDs inside (0 = none)"
          min={0}
          value={ledCount}
          onChange={(v) => setLedCount(typeof v === 'number' ? v : 0)}
        />
        <MultiSelect
          label="Required certifications"
          description="Certs a member must hold for self-serve access"
          searchable
          clearable
          data={certs}
          value={certIds}
          onChange={setCertIds}
        />
        <Group>
          <Switch
            label="High trust"
            checked={highTrust}
            onChange={(e) => setHighTrust(e.currentTarget.checked)}
          />
          <Switch
            label="Active"
            checked={isActive}
            onChange={(e) => setIsActive(e.currentTarget.checked)}
          />
        </Group>
        <Textarea
          label="Description"
          autosize
          minRows={2}
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
        />
        <Button onClick={handleSave} loading={saving} data-testid="save-locker">
          {editing ? 'Save changes' : 'Create locker'}
        </Button>

        {editing && (
          <>
            <Divider label="Bound devices" labelPosition="center" />
            {boundDevices.length === 0 ? (
              <Text size="sm" c="dimmed">
                No devices bound yet.
              </Text>
            ) : (
              <Stack gap={6}>
                {boundDevices.map((d) => (
                  <Group key={d.id} justify="space-between" wrap="nowrap">
                    <div>
                      <Group gap={6}>
                        <Text size="sm">{d.role_display}</Text>
                        {d.is_primary && (
                          <Badge size="xs" color="blue">
                            primary
                          </Badge>
                        )}
                      </Group>
                      <Text size="xs" c="dimmed">
                        {d.device_mac}
                      </Text>
                    </div>
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      color="red"
                      loading={bindingBusy}
                      onClick={() => handleRemoveDevice(d.id)}
                      data-testid={`remove-device-${d.id}`}
                    >
                      Remove
                    </Button>
                  </Group>
                ))}
              </Stack>
            )}
            <Group align="flex-end" gap="xs" wrap="nowrap">
              <Select
                label="Device"
                placeholder="ESP32 device"
                searchable
                data={devices}
                value={newDeviceId}
                onChange={setNewDeviceId}
                style={{ flex: 1 }}
              />
              <Select
                label="Role"
                placeholder="Role"
                data={ROLE_OPTIONS}
                value={newRole}
                onChange={setNewRole}
                style={{ flex: 1 }}
              />
              <Switch
                label="Primary"
                checked={newPrimary}
                onChange={(e) => setNewPrimary(e.currentTarget.checked)}
              />
              <Button onClick={handleAddDevice} loading={bindingBusy} data-testid="add-device">
                Bind
              </Button>
            </Group>
          </>
        )}
      </Stack>
    </Drawer>
  );
}
