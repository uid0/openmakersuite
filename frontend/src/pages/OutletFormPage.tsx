/**
 * Outlet Form Page (oms-a5f) — create/edit an electrical outlet with photo.
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
  OUTLET_TYPE_OPTIONS,
} from '../services/api';
import { Breaker, Location, OutletType } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface FormState {
  location: number | null;
  identifier: string;
  breaker: number | null;
  outlet_type: OutletType;
  description: string;
  plugged_in_notes: string;
  is_active: boolean;
}

const initialState: FormState = {
  location: null,
  identifier: '',
  breaker: null,
  outlet_type: 'standard',
  description: '',
  plugged_in_notes: '',
  is_active: true,
};

const OutletFormPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isEdit = Boolean(id);

  const [form, setForm] = useState<FormState>(initialState);
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [existingPhoto, setExistingPhoto] = useState<string | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [breakers, setBreakers] = useState<Breaker[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const locationOptions = useMemo(
    () => locations.map((l) => ({ value: String(l.id), label: l.name })),
    [locations],
  );
  const breakerOptions = useMemo(
    () =>
      breakers.map((b) => ({
        value: String(b.id),
        label: `${b.panel} / ${b.breaker_number} (${b.amperage}A) — ${b.location_name}`,
      })),
    [breakers],
  );

  useEffect(() => {
    inventoryAPI
      .listLocations()
      .then((response) => setLocations(response.data.results))
      .catch((err) => console.error('Failed to load locations', err));
    electricalCircuitsAPI
      .listBreakers({ is_active: true })
      .then((response) => setBreakers(response.data.results))
      .catch((err) => console.error('Failed to load breakers', err));
  }, []);

  useEffect(() => {
    if (!isEdit || !id) return;
    setLoading(true);
    electricalCircuitsAPI
      .getOutlet(id)
      .then((response) => {
        const o = response.data;
        setForm({
          location: o.location,
          identifier: o.identifier,
          breaker: o.breaker,
          outlet_type: o.outlet_type,
          description: o.description,
          plugged_in_notes: o.plugged_in_notes,
          is_active: o.is_active,
        });
        setExistingPhoto(o.photo);
      })
      .catch((err) => {
        setError(extractErrorMessage(err, 'Failed to load outlet'));
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
      const payload = {
        location: form.location,
        identifier: form.identifier.trim(),
        breaker: form.breaker,
        outlet_type: form.outlet_type,
        description: form.description,
        plugged_in_notes: form.plugged_in_notes,
        is_active: form.is_active,
        ...(photoFile ? { photo: photoFile } : {}),
      };
      if (isEdit && id) {
        await electricalCircuitsAPI.updateOutlet(id, payload);
      } else {
        await electricalCircuitsAPI.createOutlet(payload);
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
        'Failed to save outlet';
      setError(detail);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div>Loading outlet…</div>;

  return (
    <Stack gap="md">
      <Title order={2}>{isEdit ? 'Edit outlet' : 'New outlet'}</Title>
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
              placeholder="NW-bench-1"
              value={form.identifier}
              onChange={(e) => setForm((s) => ({ ...s, identifier: e.target.value }))}
              required
            />
            <Select
              label="Outlet type"
              data={OUTLET_TYPE_OPTIONS}
              value={form.outlet_type}
              onChange={(value) =>
                setForm((s) => ({ ...s, outlet_type: (value as OutletType) || 'standard' }))
              }
            />
            <Select
              label="Breaker (feeds this outlet)"
              placeholder="Optional — select breaker"
              data={breakerOptions}
              value={form.breaker ? String(form.breaker) : null}
              onChange={(value) =>
                setForm((s) => ({ ...s, breaker: value ? Number(value) : null }))
              }
              clearable
              searchable
            />
            <TextInput
              label="Description"
              value={form.description}
              onChange={(e) => setForm((s) => ({ ...s, description: e.target.value }))}
            />
            <Textarea
              label="What's plugged in"
              description="Approximate description of currently connected equipment"
              value={form.plugged_in_notes}
              onChange={(e) =>
                setForm((s) => ({ ...s, plugged_in_notes: e.target.value }))
              }
              rows={3}
            />
            <FileInput
              label="Photo"
              placeholder="Upload outlet photo"
              accept="image/*"
              leftSection={<IconPhoto size={16} />}
              value={photoFile}
              onChange={setPhotoFile}
              clearable
            />
            {existingPhoto && !photoFile && (
              <Image
                src={existingPhoto}
                alt="Existing outlet photo"
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
              {isEdit ? 'Save changes' : 'Create outlet'}
            </Button>
          </Group>
        </Paper>
      </form>
    </Stack>
  );
};

export default OutletFormPage;
