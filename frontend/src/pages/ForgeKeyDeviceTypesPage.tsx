/**
 * ForgeKey Device Types — staff catalog management.
 *
 * The device-type lookup table (name, code, description, active). Reads are
 * open; creating / editing is staff-only. The catalog is seeded, so this is
 * mostly renaming / describing / (de)activating existing types; create covers
 * a newly-added code.
 */
import {
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Modal,
  Paper,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import React, { useCallback, useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { ForgeKeyDeviceType, forgekeyAPI } from '../services/api';
import { showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const asList = <T,>(data: { results?: T[] } | T[]): T[] =>
  Array.isArray(data) ? data : (data.results ?? []);

// Mirrors forgekey.models.DeviceType.TYPE_CHOICES (the code is a fixed enum).
const CODE_OPTIONS = [
  { value: 'indicator', label: 'Indicator/Status Light' },
  { value: 'badge_reader', label: 'Badge Reader' },
  { value: 'epaper_screen', label: 'E-Paper Screen' },
  { value: 'oled_screen', label: 'OLED Screen' },
  { value: 'temperature_sensor', label: 'Temperature Sensor' },
  { value: 'generic_input', label: 'Generic Input' },
  { value: 'generic_output', label: 'Generic Output' },
  { value: 'ac_relay', label: 'AC Relay' },
  { value: 'power_measurement', label: 'Power Measurement' },
  { value: 'people_counter', label: 'People Counter' },
  { value: 'env_sensor', label: 'Environmental Sensor' },
  { value: 'door_counter', label: 'Door Counter' },
  { value: 'locker_latch', label: 'Locker latch controller' },
  { value: 'door_latch', label: 'Door latch controller' },
  { value: 'otp_keypad', label: 'OTP keypad' },
  { value: 'led_strip', label: 'WS2818 LED strip controller' },
  { value: 'reed_switch', label: 'Door reed switch' },
  { value: 'ir_break', label: 'Inventory IR-break sensor' },
  { value: 'mortise_key', label: 'Mortise key (admin override) sensor' },
];

const ForgeKeyDeviceTypesPage: React.FC = () => {
  const isStaff = typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' && localStorage.getItem('is_superuser') === 'true';

  const [types, setTypes] = useState<ForgeKeyDeviceType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [isNew, setIsNew] = useState(false);
  const [editing, setEditing] = useState<ForgeKeyDeviceType | null>(null);
  const [formName, setFormName] = useState('');
  const [formCode, setFormCode] = useState<string | null>(null);
  const [formDescription, setFormDescription] = useState('');
  const [formActive, setFormActive] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await forgekeyAPI.listDeviceTypes();
      setTypes(asList(res.data));
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load device types.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isStaff || isSuperuser) load();
  }, [isStaff, isSuperuser, load]);

  if (!isStaff && !isSuperuser) {
    return <Navigate to="/" replace />;
  }

  const openCreate = () => {
    setIsNew(true);
    setEditing(null);
    setFormName('');
    setFormCode(null);
    setFormDescription('');
    setFormActive(true);
    setModalOpen(true);
  };

  const openEdit = (t: ForgeKeyDeviceType) => {
    setIsNew(false);
    setEditing(t);
    setFormName(t.name);
    setFormCode(t.code);
    setFormDescription(t.description ?? '');
    setFormActive(t.is_active ?? true);
    setModalOpen(true);
  };

  const handleSave = async () => {
    if (!formName.trim() || (isNew && !formCode)) {
      showError('Name (and a code for a new type) are required.');
      return;
    }
    setSaving(true);
    try {
      if (isNew && formCode) {
        await forgekeyAPI.createDeviceType({
          name: formName.trim(),
          code: formCode,
          description: formDescription,
          is_active: formActive,
        });
        showSuccess('Device type created.');
      } else if (editing) {
        await forgekeyAPI.updateDeviceType(editing.id, {
          name: formName.trim(),
          description: formDescription,
          is_active: formActive,
        });
        showSuccess('Device type updated.');
      }
      setModalOpen(false);
      await load();
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to save the device type.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <WorkspacePage
      testId="forgekey-device-types-page"
      hero={{
        eyebrow: 'Facilities · ForgeKey',
        title: 'Device types',
        description: 'The device-type catalog — name, code, and whether a type is in use.',
      }}
    >
      {error && (
        <Alert color="red" variant="light" data-testid="types-error">
          {error}
        </Alert>
      )}

      {loading ? (
        <Group justify="center" p="xl">
          <Loader />
        </Group>
      ) : (
        <Paper withBorder>
          <Group p="sm" justify="space-between">
            <Title order={5}>Device types</Title>
            <Button onClick={openCreate} data-testid="new-type">
              New type
            </Button>
          </Group>
          <Table.ScrollContainer minWidth={640}>
            <Table highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Code</Table.Th>
                  <Table.Th>Description</Table.Th>
                  <Table.Th>Status</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {types.map((t) => (
                  <Table.Tr key={t.id} data-testid={`type-${t.id}`}>
                    <Table.Td>
                      <Text fw={500}>{t.name}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" ff="monospace">
                        {t.code}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c="dimmed" lineClamp={1}>
                        {t.description || '—'}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Badge color={t.is_active === false ? 'gray' : 'green'}>
                        {t.is_active === false ? 'Inactive' : 'Active'}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Button
                        size="xs"
                        variant="light"
                        onClick={() => openEdit(t)}
                        data-testid={`edit-${t.id}`}
                      >
                        Edit
                      </Button>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Paper>
      )}

      <Modal
        opened={modalOpen}
        onClose={() => setModalOpen(false)}
        title={isNew ? 'New device type' : `Edit ${editing?.name ?? ''}`}
      >
        <Stack gap="sm">
          {isNew ? (
            <Select
              label="Code"
              required
              searchable
              data={CODE_OPTIONS}
              value={formCode}
              onChange={setFormCode}
              description="The device-type code (fixed enum)"
            />
          ) : (
            <TextInput label="Code" value={formCode ?? ''} disabled />
          )}
          <TextInput
            label="Name"
            required
            value={formName}
            onChange={(e) => setFormName(e.currentTarget.value)}
            data-testid="type-name"
          />
          <Textarea
            label="Description"
            autosize
            minRows={2}
            value={formDescription}
            onChange={(e) => setFormDescription(e.currentTarget.value)}
          />
          <Switch
            label="Active"
            checked={formActive}
            onChange={(e) => setFormActive(e.currentTarget.checked)}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setModalOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleSave} loading={saving} data-testid="save-type">
              {isNew ? 'Create' : 'Save'}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </WorkspacePage>
  );
};

export default ForgeKeyDeviceTypesPage;
