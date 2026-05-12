/**
 * Create / edit a PowerPanel — top of the power-topology hierarchy.
 * Mounted at:
 *   /facilities/electrical/panels/new
 *   /facilities/electrical/panels/:id/edit
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
  inventoryAPI,
  PowerPanelPhase,
  PowerPanelWritable,
} from '../services/api';
import { Location } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const PHASE_OPTIONS: Array<{ value: PowerPanelPhase; label: string }> = [
  { value: 'single', label: 'Single phase' },
  { value: 'split', label: 'Split phase 120/240V' },
  { value: 'three', label: 'Three phase' },
];

const PowerPanelFormPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isEditMode = !!id;

  const [locations, setLocations] = useState<Location[]>([]);
  const [form, setForm] = useState<Partial<PowerPanelWritable>>({
    name: '',
    phase_configuration: 'split',
    voltage: 240,
    main_breaker_amperage: null,
    manufacturer: '',
    model: '',
    install_date: null,
    notes: '',
    needs_review: false,
  });
  const [loading, setLoading] = useState(isEditMode);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    inventoryAPI
      .listLocations()
      .then((response) => setLocations(response.data.results))
      .catch((err) => console.warn('Failed to load locations', err));
  }, []);

  const loadPanel = useCallback(async () => {
    if (!id) return;
    try {
      setLoading(true);
      const response = await electricalTopologyAPI.getPanel(id);
      const panel = response.data;
      setForm({
        location: panel.location,
        name: panel.name,
        phase_configuration: panel.phase_configuration,
        voltage: panel.voltage,
        main_breaker_amperage: panel.main_breaker_amperage,
        manufacturer: panel.manufacturer,
        model: panel.model,
        install_date: panel.install_date,
        notes: panel.notes,
        needs_review: panel.needs_review,
      });
      setError(null);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to load panel.'));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    if (isEditMode) loadPanel();
  }, [isEditMode, loadPanel]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name || !form.location) {
      setError('Name and location are required.');
      return;
    }
    setError(null);
    setSaving(true);
    try {
      if (isEditMode && id) {
        await electricalTopologyAPI.updatePanel(id, form);
        navigate(`/facilities/electrical/panels/${id}`);
      } else {
        const response = await electricalTopologyAPI.createPanel(form);
        navigate(`/facilities/electrical/panels/${response.data.id}`);
      }
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to save panel.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <WorkspacePage
      testId="power-panel-form-page"
      hero={{
        eyebrow: 'Electrical · Topology',
        title: isEditMode ? 'Edit power panel' : 'New power panel',
        description: 'Load centers anchor the topology — everything else hangs off them.',
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
          <Text c="dimmed">Loading panel…</Text>
        </Paper>
      ) : (
        <Paper withBorder p="md">
          <form onSubmit={handleSubmit}>
            <Stack gap="md">
              <TextInput
                label="Name"
                required
                value={form.name ?? ''}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="e.g. Sewing Panel A"
              />
              <Select
                label="Location"
                required
                data={locations.map((loc) => ({ value: String(loc.id), label: loc.name }))}
                value={form.location != null ? String(form.location) : null}
                onChange={(value) =>
                  setForm({ ...form, location: value ? Number(value) : undefined })
                }
                searchable
                placeholder="Where the panel is mounted"
              />
              <Select
                label="Phase configuration"
                data={PHASE_OPTIONS}
                value={form.phase_configuration ?? 'split'}
                onChange={(value) =>
                  setForm({ ...form, phase_configuration: (value as PowerPanelPhase) ?? 'split' })
                }
              />
              <Group grow>
                <NumberInput
                  label="Voltage"
                  value={form.voltage ?? 240}
                  onChange={(value) =>
                    setForm({ ...form, voltage: typeof value === 'number' ? value : 240 })
                  }
                  min={1}
                  suffix=" V"
                />
                <NumberInput
                  label="Main breaker amperage"
                  description="Leave blank if no main / sub-fed"
                  value={form.main_breaker_amperage ?? ''}
                  onChange={(value) =>
                    setForm({
                      ...form,
                      main_breaker_amperage: typeof value === 'number' ? value : null,
                    })
                  }
                  min={1}
                  suffix=" A"
                />
              </Group>
              <Group grow>
                <TextInput
                  label="Manufacturer"
                  value={form.manufacturer ?? ''}
                  onChange={(e) => setForm({ ...form, manufacturer: e.target.value })}
                />
                <TextInput
                  label="Model"
                  value={form.model ?? ''}
                  onChange={(e) => setForm({ ...form, model: e.target.value })}
                />
              </Group>
              <TextInput
                label="Install date"
                type="date"
                value={form.install_date ?? ''}
                onChange={(e) => setForm({ ...form, install_date: e.target.value || null })}
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
                  {isEditMode ? 'Save changes' : 'Create panel'}
                </Button>
              </Group>
            </Stack>
          </form>
        </Paper>
      )}
    </WorkspacePage>
  );
};

export default PowerPanelFormPage;
