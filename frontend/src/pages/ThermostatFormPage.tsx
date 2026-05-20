/**
 * Thermostat Form Page (oms-gzycmj) — create or edit a thermostat record.
 *
 * Mounted at:
 *   /facilities/climate/thermostats/new
 *   /facilities/climate/thermostats/:id/edit
 *
 * The "Controls room" / "Mounted in" split mirrors LightSwitchFormPage:
 * thermostats are commonly mounted in hallways but condition adjacent
 * rooms, so the two location FKs are kept distinct.
 *
 * The ``controlled_asset`` field drives the kill-breaker preview that
 * shows up on the safety sign. We surface the resolved chain inline so
 * the operator can sanity-check before saving.
 */
import {
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Paper,
  Select,
  Stack,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { IconAlertCircle, IconBolt } from '@tabler/icons-react';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';

import WorkspacePage from '../components/landing/WorkspacePage';
import {
  assetsAPI,
  climateAPI,
  electricalSafetyAPI,
  inventoryAPI,
  ThermostatWritable,
} from '../services/api';
import { Asset, Location } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface FormState extends ThermostatWritable {}

const initialState: FormState = {
  label: '',
  location: 0,
  controls_location: null,
  controlled_asset: null,
  manufacturer: '',
  model: '',
  notes: '',
};

interface KillBreakerPreview {
  panel: string;
  position: string;
}

const ThermostatFormPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const isEdit = Boolean(id);

  const [form, setForm] = useState<FormState>(initialState);
  const [locations, setLocations] = useState<Location[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [killPreview, setKillPreview] = useState<KillBreakerPreview[] | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const locationOptions = useMemo(
    () => locations.map((l) => ({ value: String(l.id), label: l.name })),
    [locations],
  );

  const assetOptions = useMemo(
    () =>
      assets.map((a) => ({
        value: String(a.id),
        label: a.asset_tag ? `${a.name} (${a.asset_tag})` : a.name,
      })),
    [assets],
  );

  useEffect(() => {
    inventoryAPI
      .listLocations()
      .then((response) => setLocations(response.data.results))
      .catch((err) => console.warn('Failed to load locations', err));
    assetsAPI
      .listAssets()
      .then((response) => setAssets(response.data.results))
      .catch((err) => console.warn('Failed to load assets', err));
  }, []);

  useEffect(() => {
    // Seed `location` from ?location=… so the "Add thermostat" CTA from a
    // Location detail page lands with the right room pre-filled.
    if (!isEdit) {
      const seedLocation = searchParams.get('location');
      if (seedLocation) {
        const loc = Number(seedLocation);
        if (!Number.isNaN(loc) && loc > 0) {
          setForm((s) => ({ ...s, location: loc, controls_location: loc }));
        }
      }
    }
  }, [isEdit, searchParams]);

  useEffect(() => {
    if (!isEdit || !id) return;
    setLoading(true);
    climateAPI
      .getThermostat(id)
      .then((response) => {
        const t = response.data;
        setForm({
          label: t.label,
          location: t.location,
          controls_location: t.controls_location,
          controlled_asset: t.controlled_asset,
          manufacturer: t.manufacturer,
          model: t.model,
          notes: t.notes,
        });
      })
      .catch((err) => setError(extractErrorMessage(err, 'Failed to load thermostat')))
      .finally(() => setLoading(false));
  }, [id, isEdit]);

  const fetchPreview = useCallback(async (assetId: string | null) => {
    if (!assetId) {
      setKillPreview(null);
      return;
    }
    setPreviewLoading(true);
    try {
      const response = await electricalSafetyAPI.getAssetPowerChain(assetId);
      const chain = response.data.chain || [];
      // Power-chain returns [Panel, Breaker, (Circuit), (Disconnect)].
      const panelHop = chain.find((h) => h.kind === 'panel');
      const breakerHop = chain.find((h) => h.kind === 'breaker');
      if (panelHop && breakerHop) {
        setKillPreview([
          {
            panel: String(panelHop.label),
            position: String(breakerHop.label),
          },
        ]);
      } else {
        setKillPreview([]);
      }
    } catch (err) {
      setKillPreview([]);
    } finally {
      setPreviewLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPreview(form.controlled_asset);
  }, [form.controlled_asset, fetchPreview]);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!form.location) {
      setError('Mounted location is required.');
      return;
    }
    if (!form.label.trim()) {
      setError('Label is required.');
      return;
    }
    setSaving(true);
    try {
      const payload: ThermostatWritable = {
        label: form.label.trim(),
        location: form.location,
        controls_location: form.controls_location,
        controlled_asset: form.controlled_asset,
        manufacturer: form.manufacturer,
        model: form.model,
        notes: form.notes,
      };
      if (isEdit && id) {
        await climateAPI.updateThermostat(id, payload);
      } else {
        await climateAPI.createThermostat(payload);
      }
      // Return to the conditioned room's detail so the operator can see
      // the thermostat appear in the safety sheet workflow immediately.
      const target = form.controls_location || form.location;
      navigate(`/inventory/locations/${target}`);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to save thermostat'));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="thermostat-form-page"
        hero={{
          eyebrow: 'Facilities · Climate',
          title: 'Thermostat',
          description: 'Loading…',
        }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading thermostat…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      testId="thermostat-form-page"
      hero={{
        eyebrow: 'Facilities · Climate',
        title: isEdit ? 'Edit thermostat' : 'New thermostat',
        description: isEdit
          ? 'Update the device label, location, or controlled asset.'
          : 'Register a thermostat so it appears on its room’s safety sign.',
      }}
    >
      {error && (
        <Alert icon={<IconAlertCircle size={16} />} color="red" title="Error">
          {error}
        </Alert>
      )}
      <form onSubmit={handleSubmit}>
        <Paper p="md" withBorder>
          <Stack>
            <TextInput
              label="Label"
              placeholder="Wood shop north"
              value={form.label}
              onChange={(e) => setForm((s) => ({ ...s, label: e.target.value }))}
              required
            />
            <Select
              label="Mounted at (where the device hangs)"
              placeholder="Select location"
              data={locationOptions}
              value={form.location ? String(form.location) : null}
              onChange={(value) =>
                setForm((s) => ({
                  ...s,
                  location: value ? Number(value) : 0,
                  // If the operator hasn't picked a separate conditioned room
                  // yet, keep it pinned to wherever the device is mounted.
                  controls_location: s.controls_location ?? (value ? Number(value) : null),
                }))
              }
              required
              searchable
            />
            <Select
              label="Conditions room"
              description="Defaults to the mounting location. Choose a different room if the thermostat hangs in a hallway but heats the shop."
              placeholder="Select room"
              data={locationOptions}
              value={form.controls_location ? String(form.controls_location) : null}
              onChange={(value) =>
                setForm((s) => ({ ...s, controls_location: value ? Number(value) : null }))
              }
              clearable
              searchable
            />
            <Select
              label="Controlled asset"
              description="The HVAC / RTU asset this thermostat commands. Picks the breakers that show up on the safety sign."
              placeholder="Search assets"
              data={assetOptions}
              value={form.controlled_asset}
              onChange={(value) => setForm((s) => ({ ...s, controlled_asset: value }))}
              clearable
              searchable
            />
            <Paper
              withBorder
              p="sm"
              data-testid="kill-breaker-preview"
              bg={killPreview && killPreview.length > 0 ? 'gray.0' : undefined}
            >
              <Group gap="xs" align="center">
                <IconBolt size={16} />
                <Text size="sm" fw={600}>
                  Kill-breaker preview
                </Text>
                {previewLoading && <Loader size="xs" />}
              </Group>
              {!form.controlled_asset && (
                <Text size="xs" c="dimmed" mt={6}>
                  Select a controlled asset to preview the breakers that will appear on this room’s
                  safety sign.
                </Text>
              )}
              {form.controlled_asset && killPreview && killPreview.length === 0 && (
                <Text size="xs" c="orange" mt={6}>
                  No breaker is recorded for this asset yet. Set one on the asset to populate the
                  safety sign.
                </Text>
              )}
              {killPreview && killPreview.length > 0 && (
                <Group gap="xs" mt={6}>
                  {killPreview.map((b) => (
                    <Badge key={`${b.panel}-${b.position}`} variant="light" color="red">
                      {b.panel} · {b.position}
                    </Badge>
                  ))}
                </Group>
              )}
            </Paper>
            <Group grow>
              <TextInput
                label="Manufacturer"
                placeholder="Honeywell"
                value={form.manufacturer}
                onChange={(e) => setForm((s) => ({ ...s, manufacturer: e.target.value }))}
              />
              <TextInput
                label="Model"
                placeholder="T6 Pro"
                value={form.model}
                onChange={(e) => setForm((s) => ({ ...s, model: e.target.value }))}
              />
            </Group>
            <Textarea
              label="Notes"
              value={form.notes}
              onChange={(e) => setForm((s) => ({ ...s, notes: e.target.value }))}
              rows={3}
            />
          </Stack>
          <Group justify="flex-end" mt="xl">
            <Button variant="subtle" onClick={() => navigate(-1)} type="button">
              Cancel
            </Button>
            <Button type="submit" loading={saving}>
              {isEdit ? 'Save changes' : 'Create thermostat'}
            </Button>
          </Group>
        </Paper>
      </form>
    </WorkspacePage>
  );
};

export default ThermostatFormPage;
