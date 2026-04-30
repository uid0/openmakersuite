/**
 * Network Drop Form Page (oms-a5f) — create/edit a network jack/drop with photo.
 */
import {
  Alert,
  Button,
  FileInput,
  Group,
  Image,
  Paper,
  Select,
  Stack,
  Switch,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import { IconAlertCircle, IconPhoto } from '@tabler/icons-react';
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  electricalCircuitsAPI,
  inventoryAPI,
  NETWORK_DROP_TYPE_OPTIONS,
} from '../services/api';
import { Location, NetworkDropType } from '../types';

interface FormState {
  location: number | null;
  identifier: string;
  drop_type: NetworkDropType;
  patch_panel: string;
  patch_port: string;
  mac_address: string;
  ip_address: string;
  description: string;
  notes: string;
  is_active: boolean;
}

const initialState: FormState = {
  location: null,
  identifier: '',
  drop_type: 'data',
  patch_panel: '',
  patch_port: '',
  mac_address: '',
  ip_address: '',
  description: '',
  notes: '',
  is_active: true,
};

const NetworkDropFormPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isEdit = Boolean(id);

  const [form, setForm] = useState<FormState>(initialState);
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [existingPhoto, setExistingPhoto] = useState<string | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const locationOptions = useMemo(
    () => locations.map((l) => ({ value: String(l.id), label: l.name })),
    [locations],
  );

  useEffect(() => {
    inventoryAPI
      .listLocations()
      .then((response) => setLocations(response.data.results))
      .catch((err) => console.error('Failed to load locations', err));
  }, []);

  useEffect(() => {
    if (!isEdit || !id) return;
    setLoading(true);
    electricalCircuitsAPI
      .getNetworkDrop(id)
      .then((response) => {
        const d = response.data;
        setForm({
          location: d.location,
          identifier: d.identifier,
          drop_type: d.drop_type,
          patch_panel: d.patch_panel,
          patch_port: d.patch_port,
          mac_address: d.mac_address,
          ip_address: d.ip_address || '',
          description: d.description,
          notes: d.notes,
          is_active: d.is_active,
        });
        setExistingPhoto(d.photo);
      })
      .catch((err) => {
        setError(err.response?.data?.detail || 'Failed to load network drop');
      })
      .finally(() => setLoading(false));
  }, [id, isEdit]);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!form.location) {
      setError('Location is required.');
      return;
    }
    if (!form.identifier.trim()) {
      setError('Identifier is required.');
      return;
    }
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        location: form.location,
        identifier: form.identifier.trim(),
        drop_type: form.drop_type,
        patch_panel: form.patch_panel,
        patch_port: form.patch_port,
        mac_address: form.mac_address,
        description: form.description,
        notes: form.notes,
        is_active: form.is_active,
      };
      if (form.ip_address.trim()) {
        payload.ip_address = form.ip_address.trim();
      } else if (isEdit) {
        payload.ip_address = null;
      }
      if (photoFile) {
        payload.photo = photoFile;
      }
      if (isEdit && id) {
        await electricalCircuitsAPI.updateNetworkDrop(id, payload as any);
      } else {
        await electricalCircuitsAPI.createNetworkDrop(payload as any);
      }
      navigate('/facilities/electrical');
    } catch (err: any) {
      const data = err.response?.data;
      const detail =
        data?.detail ||
        (typeof data === 'object' && data
          ? Object.entries(data)
              .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(', ') : value}`)
              .join('; ')
          : null) ||
        'Failed to save network drop';
      setError(detail);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div>Loading network drop…</div>;

  return (
    <Stack gap="md">
      <Title order={2}>{isEdit ? 'Edit network drop' : 'New network drop'}</Title>
      {error && (
        <Alert icon={<IconAlertCircle size={16} />} color="red" title="Error">
          {error}
        </Alert>
      )}
      <form onSubmit={handleSubmit}>
        <Paper p="md" withBorder>
          <Stack>
            <Select
              label="Location"
              placeholder="Select location"
              data={locationOptions}
              value={form.location ? String(form.location) : null}
              onChange={(value) =>
                setForm((s) => ({ ...s, location: value ? Number(value) : null }))
              }
              required
              searchable
            />
            <TextInput
              label="Identifier"
              placeholder="wallplate-3A"
              value={form.identifier}
              onChange={(e) =>
                setForm((s) => ({ ...s, identifier: e.target.value }))
              }
              required
            />
            <Select
              label="Drop type"
              data={NETWORK_DROP_TYPE_OPTIONS}
              value={form.drop_type}
              onChange={(value) =>
                setForm((s) => ({
                  ...s,
                  drop_type: (value as NetworkDropType) || 'data',
                }))
              }
            />
            <Group grow>
              <TextInput
                label="Patch panel"
                placeholder="IDF-1 patch panel B"
                value={form.patch_panel}
                onChange={(e) =>
                  setForm((s) => ({ ...s, patch_panel: e.target.value }))
                }
              />
              <TextInput
                label="Patch port"
                placeholder="24"
                value={form.patch_port}
                onChange={(e) =>
                  setForm((s) => ({ ...s, patch_port: e.target.value }))
                }
              />
            </Group>
            <Group grow>
              <TextInput
                label="MAC address"
                placeholder="aa:bb:cc:dd:ee:ff"
                value={form.mac_address}
                onChange={(e) =>
                  setForm((s) => ({ ...s, mac_address: e.target.value }))
                }
              />
              <TextInput
                label="IP address"
                placeholder="10.0.0.42"
                value={form.ip_address}
                onChange={(e) =>
                  setForm((s) => ({ ...s, ip_address: e.target.value }))
                }
              />
            </Group>
            <TextInput
              label="Description"
              value={form.description}
              onChange={(e) =>
                setForm((s) => ({ ...s, description: e.target.value }))
              }
            />
            <Textarea
              label="Notes"
              value={form.notes}
              onChange={(e) => setForm((s) => ({ ...s, notes: e.target.value }))}
              rows={3}
            />
            <FileInput
              label="Photo"
              placeholder="Upload drop photo"
              accept="image/*"
              leftSection={<IconPhoto size={16} />}
              value={photoFile}
              onChange={setPhotoFile}
              clearable
            />
            {existingPhoto && !photoFile && (
              <Image
                src={existingPhoto}
                alt="Existing network drop photo"
                radius="md"
                h={200}
                fit="contain"
              />
            )}
            <Switch
              label="Active"
              checked={form.is_active}
              onChange={(e) =>
                setForm((s) => ({ ...s, is_active: e.currentTarget.checked }))
              }
            />
          </Stack>
          <Group justify="flex-end" mt="xl">
            <Button variant="subtle" onClick={() => navigate(-1)} type="button">
              Cancel
            </Button>
            <Button type="submit" loading={saving}>
              {isEdit ? 'Save changes' : 'Create network drop'}
            </Button>
          </Group>
        </Paper>
      </form>
    </Stack>
  );
};

export default NetworkDropFormPage;
