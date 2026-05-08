/**
 * Light Switch Form Page (oms-a5f) — create/edit a light switch.
 *
 * The "controls location" relationship is highlighted because makerspace
 * switches frequently sit in one location and operate the lights of another.
 */
import {
  Alert,
  Button,
  Group,
  Paper,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import { IconAlertCircle } from '@tabler/icons-react';
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { electricalCircuitsAPI, inventoryAPI } from '../services/api';
import { Breaker, Location } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface FormState {
  location: number | null;
  identifier: string;
  controls_location: number | null;
  breaker: number | null;
  description: string;
  notes: string;
  is_active: boolean;
}

const initialState: FormState = {
  location: null,
  identifier: '',
  controls_location: null,
  breaker: null,
  description: '',
  notes: '',
  is_active: true,
};

const LightSwitchFormPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isEdit = Boolean(id);

  const [form, setForm] = useState<FormState>(initialState);
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
      .getLightSwitch(id)
      .then((response) => {
        const sw = response.data;
        setForm({
          location: sw.location,
          identifier: sw.identifier,
          controls_location: sw.controls_location,
          breaker: sw.breaker,
          description: sw.description,
          notes: sw.notes,
          is_active: sw.is_active,
        });
      })
      .catch((err) => {
        setError(extractErrorMessage(err, 'Failed to load light switch'));
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
        controls_location: form.controls_location,
        breaker: form.breaker,
        description: form.description,
        notes: form.notes,
        is_active: form.is_active,
      };
      if (isEdit && id) {
        await electricalCircuitsAPI.updateLightSwitch(id, payload);
      } else {
        await electricalCircuitsAPI.createLightSwitch(payload);
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
        'Failed to save light switch';
      setError(detail);
    } finally {
      setSaving(false);
    }
  };

  const sameLocation =
    form.controls_location !== null && form.location === form.controls_location;

  if (loading) return <div>Loading light switch…</div>;

  return (
    <Stack gap="md">
      <Title order={2}>{isEdit ? 'Edit light switch' : 'New light switch'}</Title>
      {error && (
        <Alert icon={<IconAlertCircle size={16} />} color="red" title="Error">
          {error}
        </Alert>
      )}
      <form onSubmit={handleSubmit}>
        <Paper p="md" withBorder>
          <Stack>
            <Select
              label="Mounted at (where the switch lives)"
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
              placeholder="door-east"
              value={form.identifier}
              onChange={(e) => setForm((s) => ({ ...s, identifier: e.target.value }))}
              required
            />
            <Select
              label="Controls lights at"
              description="Leave blank if the switch operates the lights of its own location"
              placeholder="Optional — select location whose lights this switch controls"
              data={locationOptions}
              value={form.controls_location ? String(form.controls_location) : null}
              onChange={(value) =>
                setForm((s) => ({
                  ...s,
                  controls_location: value ? Number(value) : null,
                }))
              }
              clearable
              searchable
            />
            {sameLocation && (
              <Text c="dimmed" size="xs">
                Tip: leave this blank if the switch controls the lights of the same
                location it&apos;s mounted in.
              </Text>
            )}
            <Select
              label="Breaker (feeds this lighting circuit)"
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
              {isEdit ? 'Save changes' : 'Create light switch'}
            </Button>
          </Group>
        </Paper>
      </form>
    </Stack>
  );
};

export default LightSwitchFormPage;
