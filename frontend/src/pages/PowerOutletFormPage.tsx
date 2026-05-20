/**
 * Create / edit a PowerOutlet. Mounted at:
 *   /facilities/electrical/circuits/:circuitId/outlets/new
 *   /facilities/electrical/outlets/:id/edit
 *
 * In create-mode the ``circuitId`` from the URL pre-fills the parent
 * circuit selector. ``outlet_type`` carries the NEMA receptacle code.
 */
import {
  Alert,
  Button,
  Group,
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
  PowerCircuitDetail,
  PowerOutletWritable,
} from '../services/api';
import { Location } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

// NEMA receptacle codes — must match NEMA_PORT_TYPE_CHOICES on the
// backend or the create call rejects.
const OUTLET_TYPE_OPTIONS = [
  { value: '5-15R', label: 'NEMA 5-15R (120V 15A)' },
  { value: '5-20R', label: 'NEMA 5-20R (120V 20A)' },
  { value: '6-15R', label: 'NEMA 6-15R (240V 15A)' },
  { value: '6-20R', label: 'NEMA 6-20R (240V 20A)' },
  { value: 'L5-15R', label: 'NEMA L5-15R (120V 15A locking)' },
  { value: 'L5-20R', label: 'NEMA L5-20R (120V 20A locking)' },
  { value: 'L6-20R', label: 'NEMA L6-20R (240V 20A locking)' },
  { value: 'L6-30R', label: 'NEMA L6-30R (240V 30A locking)' },
  { value: '14-30R', label: 'NEMA 14-30R (240V 30A)' },
  { value: '14-50R', label: 'NEMA 14-50R (240V 50A)' },
  { value: 'USB', label: 'USB charging' },
  { value: 'OTHER', label: 'Other' },
];

const STATUS_OPTIONS: Array<{ value: PowerOutletWritable['status']; label: string }> = [
  { value: 'active', label: 'Active' },
  { value: 'inactive', label: 'Inactive' },
  { value: 'capped', label: 'Capped / decommissioned' },
];

const PowerOutletFormPage: React.FC = () => {
  const { id, circuitId } = useParams<{ id?: string; circuitId?: string }>();
  const navigate = useNavigate();
  const isEditMode = !!id;

  const [circuits, setCircuits] = useState<PowerCircuitDetail[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [panelId, setPanelId] = useState<number | null>(null);
  const [form, setForm] = useState<Partial<PowerOutletWritable>>({
    circuit: circuitId ? Number(circuitId) : undefined,
    outlet_type: '5-15R',
    label: '',
    location_description: '',
    status: 'active',
    notes: '',
    needs_review: false,
  });
  const [loading, setLoading] = useState(isEditMode);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      electricalTopologyAPI.listCircuits(),
      inventoryAPI.listLocations(),
    ])
      .then(([circuitsResp, locationsResp]) => {
        setCircuits(circuitsResp.data.results);
        setLocations(locationsResp.data.results);
      })
      .catch((err) => console.warn('Failed to load options', err));
  }, []);

  const loadOutlet = useCallback(async () => {
    if (!id) return;
    try {
      setLoading(true);
      const response = await electricalTopologyAPI.getOutlet(id);
      const o = response.data;
      setForm({
        circuit: o.circuit,
        location: o.location,
        outlet_type: o.outlet_type,
        label: o.label,
        location_description: o.location_description,
        status: o.status,
        notes: o.notes,
        needs_review: o.needs_review,
      });
      // Look up the circuit so we can navigate back to its panel after save.
      const circuit = await electricalTopologyAPI.getCircuit(o.circuit);
      setPanelId(circuit.data.panel_id);
      setError(null);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to load outlet.'));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    if (isEditMode) loadOutlet();
  }, [isEditMode, loadOutlet]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.circuit || !form.location) {
      setError('Circuit and location are required.');
      return;
    }
    setError(null);
    setSaving(true);
    try {
      if (isEditMode && id) {
        await electricalTopologyAPI.updateOutlet(id, form);
      } else {
        const response = await electricalTopologyAPI.createOutlet(form);
        const circuit = await electricalTopologyAPI.getCircuit(response.data.circuit);
        setPanelId(circuit.data.panel_id);
      }
      const destPanelId =
        panelId ?? circuits.find((c) => c.id === form.circuit)?.panel_id ?? null;
      if (destPanelId != null) {
        navigate(`/facilities/electrical/panels/${destPanelId}`);
      } else {
        navigate(-1);
      }
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to save outlet.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <WorkspacePage
      testId="power-outlet-form-page"
      hero={{
        eyebrow: 'Electrical · Outlet',
        title: isEditMode ? 'Edit outlet' : 'New outlet',
        description:
          'Outlets live on a circuit at a physical location. The NEMA code describes the receptacle shape.',
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
          <Text c="dimmed">Loading outlet…</Text>
        </Paper>
      ) : (
        <Paper withBorder p="md">
          <form onSubmit={handleSubmit}>
            <Stack gap="md">
              <Select
                label="Circuit"
                required
                data={circuits.map((c) => ({
                  value: String(c.id),
                  label: `${c.panel_name} · ${c.label || 'Circuit ' + c.id}`,
                }))}
                value={form.circuit != null ? String(form.circuit) : null}
                onChange={(value) =>
                  setForm({ ...form, circuit: value ? Number(value) : undefined })
                }
                searchable
              />
              <Select
                label="Location"
                required
                data={locations.map((l) => ({ value: String(l.id), label: l.name }))}
                value={form.location != null ? String(form.location) : null}
                onChange={(value) =>
                  setForm({ ...form, location: value ? Number(value) : undefined })
                }
                searchable
              />
              <Group grow>
                <Select
                  label="Outlet type (NEMA)"
                  data={OUTLET_TYPE_OPTIONS}
                  value={form.outlet_type ?? '5-15R'}
                  onChange={(value) => setForm({ ...form, outlet_type: value ?? '5-15R' })}
                  searchable
                />
                <Select
                  label="Status"
                  data={STATUS_OPTIONS}
                  value={form.status ?? 'active'}
                  onChange={(value) =>
                    setForm({
                      ...form,
                      status: (value as PowerOutletWritable['status']) ?? 'active',
                    })
                  }
                />
              </Group>
              <TextInput
                label="Label"
                value={form.label ?? ''}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                placeholder="e.g. NW-bench-1"
              />
              <TextInput
                label="Location description"
                value={form.location_description ?? ''}
                onChange={(e) => setForm({ ...form, location_description: e.target.value })}
                placeholder="e.g. east wall, 3 ft from corner"
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
                  {isEditMode ? 'Save changes' : 'Create outlet'}
                </Button>
              </Group>
            </Stack>
          </form>
        </Paper>
      )}
    </WorkspacePage>
  );
};

export default PowerOutletFormPage;
