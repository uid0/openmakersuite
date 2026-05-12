/**
 * Create / edit a PowerBreaker. Mounted at:
 *   /facilities/electrical/panels/:panelId/breakers/new
 *   /facilities/electrical/breakers/:id/edit
 *
 * In create-mode the ``panelId`` from the URL pre-fills the parent
 * panel selector so the staff member never has to pick it manually.
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
  PowerBreakerWritable,
  PowerPanelDetail,
} from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const POLE_OPTIONS = [
  { value: '1', label: '1 pole' },
  { value: '2', label: '2 pole' },
  { value: '3', label: '3 pole' },
];

const PHASE_OPTIONS: Array<{ value: PowerBreakerWritable['phase']; label: string }> = [
  { value: 'A', label: 'Phase A' },
  { value: 'B', label: 'Phase B' },
  { value: 'C', label: 'Phase C' },
  { value: 'AB', label: 'Phases A + B' },
  { value: 'BC', label: 'Phases B + C' },
  { value: 'AC', label: 'Phases A + C' },
  { value: 'ABC', label: 'Phases A + B + C' },
];

const STATUS_OPTIONS: Array<{ value: PowerBreakerWritable['status']; label: string }> = [
  { value: 'active', label: 'Active' },
  { value: 'spare', label: 'Spare' },
  { value: 'locked_out', label: 'Locked out' },
];

const PowerBreakerFormPage: React.FC = () => {
  const { id, panelId } = useParams<{ id?: string; panelId?: string }>();
  const navigate = useNavigate();
  const isEditMode = !!id;

  const [panels, setPanels] = useState<PowerPanelDetail[]>([]);
  const [form, setForm] = useState<Partial<PowerBreakerWritable>>({
    panel: panelId ? Number(panelId) : undefined,
    position: '',
    pole_count: 1,
    amperage: 20,
    phase: 'A',
    status: 'active',
    label: '',
    notes: '',
    needs_review: false,
  });
  const [loading, setLoading] = useState(isEditMode);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    electricalTopologyAPI
      .listPanels()
      .then((response) => setPanels(response.data.results))
      .catch((err) => console.warn('Failed to load panels', err));
  }, []);

  const loadBreaker = useCallback(async () => {
    if (!id) return;
    try {
      setLoading(true);
      const response = await electricalTopologyAPI.getBreaker(id);
      const b = response.data;
      setForm({
        panel: b.panel,
        position: b.position,
        pole_count: b.pole_count,
        amperage: b.amperage,
        phase: b.phase,
        status: b.status,
        label: b.label,
        notes: b.notes,
        needs_review: b.needs_review,
      });
      setError(null);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to load breaker.'));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    if (isEditMode) loadBreaker();
  }, [isEditMode, loadBreaker]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.panel || !form.position || !form.amperage) {
      setError('Panel, position, and amperage are required.');
      return;
    }
    setError(null);
    setSaving(true);
    try {
      if (isEditMode && id) {
        const response = await electricalTopologyAPI.updateBreaker(id, form);
        navigate(`/facilities/electrical/panels/${response.data.panel}`);
      } else {
        const response = await electricalTopologyAPI.createBreaker(form);
        navigate(`/facilities/electrical/panels/${response.data.panel}`);
      }
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to save breaker.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <WorkspacePage
      testId="power-breaker-form-page"
      hero={{
        eyebrow: 'Electrical · Breaker',
        title: isEditMode ? 'Edit breaker' : 'New breaker',
        description: 'Breakers live in a panel; circuits hang off of breakers.',
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
          <Text c="dimmed">Loading breaker…</Text>
        </Paper>
      ) : (
        <Paper withBorder p="md">
          <form onSubmit={handleSubmit}>
            <Stack gap="md">
              <Select
                label="Panel"
                required
                data={panels.map((p) => ({ value: String(p.id), label: `${p.name} @ ${p.location_name}` }))}
                value={form.panel != null ? String(form.panel) : null}
                onChange={(value) => setForm({ ...form, panel: value ? Number(value) : undefined })}
                searchable
              />
              <Group grow>
                <TextInput
                  label="Position"
                  required
                  value={form.position ?? ''}
                  onChange={(e) => setForm({ ...form, position: e.target.value })}
                  placeholder="e.g. 12 or 14/16 for tandem"
                />
                <Select
                  label="Pole count"
                  data={POLE_OPTIONS}
                  value={String(form.pole_count ?? 1)}
                  onChange={(value) =>
                    setForm({ ...form, pole_count: (Number(value) || 1) as 1 | 2 | 3 })
                  }
                />
              </Group>
              <Group grow>
                <NumberInput
                  label="Amperage"
                  required
                  value={form.amperage ?? 20}
                  onChange={(value) =>
                    setForm({ ...form, amperage: typeof value === 'number' ? value : 20 })
                  }
                  min={1}
                  suffix=" A"
                />
                <Select
                  label="Phase"
                  data={PHASE_OPTIONS}
                  value={form.phase ?? 'A'}
                  onChange={(value) =>
                    setForm({ ...form, phase: (value as PowerBreakerWritable['phase']) ?? 'A' })
                  }
                />
              </Group>
              <Select
                label="Status"
                data={STATUS_OPTIONS}
                value={form.status ?? 'active'}
                onChange={(value) =>
                  setForm({ ...form, status: (value as PowerBreakerWritable['status']) ?? 'active' })
                }
              />
              <TextInput
                label="Label"
                value={form.label ?? ''}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                placeholder="e.g. Wood shop dust collector"
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
                  {isEditMode ? 'Save changes' : 'Create breaker'}
                </Button>
              </Group>
            </Stack>
          </form>
        </Paper>
      )}
    </WorkspacePage>
  );
};

export default PowerBreakerFormPage;
