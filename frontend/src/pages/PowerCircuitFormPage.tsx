/**
 * Create / edit a PowerCircuit. Mounted at:
 *   /facilities/electrical/breakers/:breakerId/circuits/new
 *   /facilities/electrical/circuits/:id/edit
 *
 * In create-mode the ``breakerId`` from the URL pre-fills the parent
 * breaker selector. ``max_load_amps`` defaults to 80% of the breaker
 * amperage server-side when left blank (NEC continuous-load rule), so
 * the field is optional.
 */
import {
  Alert,
  Button,
  Group,
  NumberInput,
  Paper,
  Select,
  Stack,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { IconAlertCircle } from '@tabler/icons-react';
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import WorkspacePage from '../components/landing/WorkspacePage';
import {
  electricalTopologyAPI,
  PowerBreakerDetail,
  PowerCircuitWritable,
} from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const PowerCircuitFormPage: React.FC = () => {
  const { id, breakerId } = useParams<{ id?: string; breakerId?: string }>();
  const navigate = useNavigate();
  const isEditMode = !!id;

  const [breakers, setBreakers] = useState<PowerBreakerDetail[]>([]);
  const [panelId, setPanelId] = useState<number | null>(null);
  const [form, setForm] = useState<Partial<PowerCircuitWritable>>({
    breaker: breakerId ? Number(breakerId) : undefined,
    label: '',
    conductor_size: '',
    conductor_length_ft: null,
    max_load_amps: null,
    notes: '',
    needs_review: false,
  });
  const [loading, setLoading] = useState(isEditMode);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    electricalTopologyAPI
      .listBreakers()
      .then((response) => setBreakers(response.data.results))
      .catch((err) => console.warn('Failed to load breakers', err));
  }, []);

  const loadCircuit = useCallback(async () => {
    if (!id) return;
    try {
      setLoading(true);
      const response = await electricalTopologyAPI.getCircuit(id);
      const c = response.data;
      setPanelId(c.panel_id);
      setForm({
        breaker: c.breaker,
        label: c.label,
        conductor_size: c.conductor_size,
        conductor_length_ft: c.conductor_length_ft,
        max_load_amps: c.max_load_amps,
        notes: c.notes,
        needs_review: c.needs_review,
      });
      setError(null);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to load circuit.'));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    if (isEditMode) loadCircuit();
  }, [isEditMode, loadCircuit]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.breaker) {
      setError('Breaker is required.');
      return;
    }
    setError(null);
    setSaving(true);
    try {
      let destPanelId: number | null = panelId;
      if (isEditMode && id) {
        const response = await electricalTopologyAPI.updateCircuit(id, form);
        destPanelId = response.data.panel_id;
      } else {
        const response = await electricalTopologyAPI.createCircuit(form);
        destPanelId = response.data.panel_id;
      }
      if (destPanelId != null) {
        navigate(`/facilities/electrical/panels/${destPanelId}`);
      } else {
        navigate(-1);
      }
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to save circuit.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <WorkspacePage
      testId="power-circuit-form-page"
      hero={{
        eyebrow: 'Electrical · Circuit',
        title: isEditMode ? 'Edit circuit' : 'New circuit',
        description:
          'Circuits live on a breaker. Outlets land on circuits. Leave max-load blank to default to 80% of breaker amperage.',
        action: (
          <Button variant="default" onClick={() => navigate(-1)}>
            Cancel
          </Button>
        ),
      }}
    >
      {error && (
        <Alert color="red" icon={<IconAlertCircle size={16} />}>
          {error}
        </Alert>
      )}

      {loading ? (
        <Paper withBorder p="md">
          <Text c="dimmed">Loading circuit…</Text>
        </Paper>
      ) : (
        <Paper withBorder p="md">
          <form onSubmit={handleSubmit}>
            <Stack gap="md">
              <Select
                label="Breaker"
                required
                data={breakers.map((b) => ({
                  value: String(b.id),
                  label: `${b.panel_name} · pos ${b.position} (${b.amperage}A)`,
                }))}
                value={form.breaker != null ? String(form.breaker) : null}
                onChange={(value) =>
                  setForm({ ...form, breaker: value ? Number(value) : undefined })
                }
                searchable
              />
              <TextInput
                label="Label"
                value={form.label ?? ''}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                placeholder="e.g. Bench row 1"
              />
              <Group grow>
                <TextInput
                  label="Conductor size"
                  value={form.conductor_size ?? ''}
                  onChange={(e) => setForm({ ...form, conductor_size: e.target.value })}
                  placeholder="e.g. 12 AWG"
                />
                <NumberInput
                  label="Conductor length"
                  value={form.conductor_length_ft ?? ''}
                  onChange={(value) =>
                    setForm({
                      ...form,
                      conductor_length_ft: typeof value === 'number' ? value : null,
                    })
                  }
                  min={1}
                  suffix=" ft"
                />
              </Group>
              <NumberInput
                label="Max load (continuous)"
                description="Leave blank for NEC default (80% of breaker amperage)."
                value={form.max_load_amps ?? ''}
                onChange={(value) =>
                  setForm({
                    ...form,
                    max_load_amps: typeof value === 'number' ? value : null,
                  })
                }
                min={1}
                suffix=" A"
              />
              <Textarea
                label="Notes"
                value={form.notes ?? ''}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
                autosize
                minRows={2}
              />
              <Group justify="flex-end">
                <Button type="button" variant="default" onClick={() => navigate(-1)}>
                  Cancel
                </Button>
                <Button type="submit" loading={saving}>
                  {isEditMode ? 'Save changes' : 'Create circuit'}
                </Button>
              </Group>
            </Stack>
          </form>
        </Paper>
      )}
    </WorkspacePage>
  );
};

export default PowerCircuitFormPage;
