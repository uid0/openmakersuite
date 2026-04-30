/**
 * Breaker Form Page (oms-a5f) — create/edit a circuit breaker.
 */
import {
  Alert,
  Button,
  Group,
  NumberInput,
  Paper,
  Select,
  Stack,
  Switch,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import { IconAlertCircle } from '@tabler/icons-react';
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { electricalCircuitsAPI, inventoryAPI } from '../services/api';
import { Location } from '../types';

interface FormState {
  location: number | null;
  panel: string;
  breaker_number: string;
  amperage: number | '';
  voltage: number | '';
  poles: number | '';
  description: string;
  notes: string;
  is_active: boolean;
}

const initialState: FormState = {
  location: null,
  panel: '',
  breaker_number: '',
  amperage: 20,
  voltage: 120,
  poles: 1,
  description: '',
  notes: '',
  is_active: true,
};

const BreakerFormPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isEdit = Boolean(id);

  const [form, setForm] = useState<FormState>(initialState);
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
      .getBreaker(id)
      .then((response) => {
        const b = response.data;
        setForm({
          location: b.location,
          panel: b.panel,
          breaker_number: b.breaker_number,
          amperage: b.amperage,
          voltage: b.voltage,
          poles: b.poles,
          description: b.description,
          notes: b.notes,
          is_active: b.is_active,
        });
      })
      .catch((err) => {
        setError(err.response?.data?.detail || 'Failed to load breaker');
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
    if (!form.panel.trim() || !form.breaker_number.trim()) {
      setError('Panel and breaker number are required.');
      return;
    }
    setSaving(true);
    try {
      const payload = {
        location: form.location,
        panel: form.panel.trim(),
        breaker_number: form.breaker_number.trim(),
        amperage: Number(form.amperage),
        voltage: Number(form.voltage),
        poles: Number(form.poles),
        description: form.description,
        notes: form.notes,
        is_active: form.is_active,
      };
      if (isEdit && id) {
        await electricalCircuitsAPI.updateBreaker(id, payload);
      } else {
        await electricalCircuitsAPI.createBreaker(payload);
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
        'Failed to save breaker';
      setError(detail);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div>Loading breaker…</div>;
  }

  return (
    <Stack gap="md">
      <Title order={2}>{isEdit ? 'Edit breaker' : 'New breaker'}</Title>
      {error && (
        <Alert icon={<IconAlertCircle size={16} />} color="red" title="Error">
          {error}
        </Alert>
      )}
      <form onSubmit={handleSubmit}>
        <Paper p="md" withBorder>
          <Stack>
            <Select
              label="Location (panel cabinet)"
              placeholder="Select panel location"
              data={locationOptions}
              value={form.location ? String(form.location) : null}
              onChange={(value) =>
                setForm((s) => ({ ...s, location: value ? Number(value) : null }))
              }
              required
              searchable
            />
            <Group grow>
              <TextInput
                label="Panel"
                placeholder="Panel A"
                value={form.panel}
                onChange={(e) => setForm((s) => ({ ...s, panel: e.target.value }))}
                required
              />
              <TextInput
                label="Breaker #"
                placeholder="12"
                value={form.breaker_number}
                onChange={(e) => setForm((s) => ({ ...s, breaker_number: e.target.value }))}
                required
              />
            </Group>
            <Group grow>
              <NumberInput
                label="Amperage"
                value={form.amperage}
                onChange={(value) =>
                  setForm((s) => ({ ...s, amperage: value === '' ? '' : Number(value) }))
                }
                min={1}
                required
              />
              <NumberInput
                label="Voltage"
                value={form.voltage}
                onChange={(value) =>
                  setForm((s) => ({ ...s, voltage: value === '' ? '' : Number(value) }))
                }
                min={1}
              />
              <NumberInput
                label="Poles"
                value={form.poles}
                onChange={(value) =>
                  setForm((s) => ({ ...s, poles: value === '' ? '' : Number(value) }))
                }
                min={1}
                max={3}
              />
            </Group>
            <TextInput
              label="Description"
              placeholder="Wood shop dust collector"
              value={form.description}
              onChange={(e) => setForm((s) => ({ ...s, description: e.target.value }))}
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
              onChange={(e) => setForm((s) => ({ ...s, is_active: e.currentTarget.checked }))}
            />
          </Stack>
          <Group justify="flex-end" mt="xl">
            <Button variant="subtle" onClick={() => navigate(-1)} type="button">
              Cancel
            </Button>
            <Button type="submit" loading={saving}>
              {isEdit ? 'Save changes' : 'Create breaker'}
            </Button>
          </Group>
        </Paper>
      </form>
    </Stack>
  );
};

export default BreakerFormPage;
