import { zodResolver } from '@hookform/resolvers/zod';
import {
  ActionIcon,
  Alert,
  Button,
  Divider,
  Group,
  NumberInput,
  Paper,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { IconAlertCircle, IconPlus, IconTrash } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Resolver, useForm, useWatch } from 'react-hook-form';
import { useNavigate, useParams } from 'react-router-dom';
import { FormInput } from '../components/forms/FormInput';
import { FormLayout } from '../components/forms/FormLayout';
import { FormNumberInput } from '../components/forms/FormNumberInput';
import { FormTextarea } from '../components/forms/FormTextarea';
import WorkspacePage from '../components/landing/WorkspacePage';
import { maintenanceAPI, maintenanceToolAPI } from '../services/api';
import { MaintenanceMaterial, MaintenanceTool } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';
import { MaintenanceItemFormData, maintenanceItemFormSchema } from '../utils/formSchemas';

interface PendingMaterial {
  localId: string;
  id?: string;
  name: string;
  quantity: number;
  unit: string;
  estimated_cost_per_unit: number;
  notes: string;
}

/** A tool row being edited. Persisted rows carry `id`; new ones only `localId`. */
interface PendingTool {
  localId: string;
  id?: string;
  name: string;
  quantity: number;
  location_hint: string;
  is_required: boolean;
  notes: string;
}

const EMPTY_TOOL: PendingTool = {
  localId: '',
  name: '',
  quantity: 1,
  location_hint: '',
  is_required: true,
  notes: '',
};

const MaintenanceItemFormPage: React.FC = () => {
  const { assetId, id } = useParams<{ assetId: string; id: string }>();
  const navigate = useNavigate();
  const isEditMode = !!id;

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [materials, setMaterials] = useState<PendingMaterial[]>([]);
  const [originalMaterialIds, setOriginalMaterialIds] = useState<string[]>([]);
  const [newMaterial, setNewMaterial] = useState<PendingMaterial>({
    localId: '',
    name: '',
    quantity: 1,
    unit: '',
    estimated_cost_per_unit: 0,
    notes: '',
  });
  const [tools, setTools] = useState<PendingTool[]>([]);
  const [originalToolIds, setOriginalToolIds] = useState<string[]>([]);
  const [newTool, setNewTool] = useState<PendingTool>(EMPTY_TOOL);

  const { control, handleSubmit, reset, setValue } = useForm<MaintenanceItemFormData>({
    // v5 resolvers infer the schema's Zod *input* type (fields with `.default()`
    // are optional pre-parse); this form seeds all fields via defaultValues, so
    // its field values are the *output* shape — assert against MaintenanceItemFormData.
    resolver: zodResolver(maintenanceItemFormSchema) as Resolver<MaintenanceItemFormData>,
    defaultValues: {
      title: '',
      description: '',
      instructions: '',
      estimated_time_minutes: null,
      estimated_cost: 0,
      interval_days: null,
      is_active: true,
    },
  });

  const isActive = useWatch({ control, name: 'is_active' });

  useEffect(() => {
    if (isEditMode && id) {
      loadItem(id);
    }
  }, [id, isEditMode]);

  const loadItem = async (itemId: string) => {
    try {
      setLoading(true);
      const response = await maintenanceAPI.getItem(itemId);
      const item = response.data;
      reset({
        title: item.title,
        description: item.description || '',
        instructions: item.instructions || '',
        estimated_time_minutes: item.estimated_time_minutes ?? null,
        estimated_cost: parseFloat(item.estimated_cost) || 0,
        interval_days: item.interval_days ?? null,
        is_active: item.is_active,
      });
      const loaded = item.materials.map((m: MaintenanceMaterial) => ({
        localId: m.id,
        id: m.id,
        name: m.name,
        quantity: parseFloat(m.quantity),
        unit: m.unit,
        estimated_cost_per_unit: parseFloat(m.estimated_cost_per_unit),
        notes: m.notes,
      }));
      setMaterials(loaded);
      setOriginalMaterialIds(loaded.map((m: PendingMaterial) => m.id!).filter(Boolean));
      const loadedTools = (item.tools ?? []).map((t: MaintenanceTool) => ({
        localId: t.id,
        id: t.id,
        name: t.name,
        quantity: t.quantity,
        location_hint: t.location_hint,
        is_required: t.is_required,
        notes: t.notes,
      }));
      setTools(loadedTools);
      setOriginalToolIds(loadedTools.map((t: PendingTool) => t.id!).filter(Boolean));
    } catch (err) {
      console.error('Error loading maintenance item:', err);
      setError('Failed to load maintenance item.');
    } finally {
      setLoading(false);
    }
  };

  const addMaterial = () => {
    if (!newMaterial.name.trim()) return;
    setMaterials([...materials, { ...newMaterial, localId: crypto.randomUUID() }]);
    setNewMaterial({ localId: '', name: '', quantity: 1, unit: '', estimated_cost_per_unit: 0, notes: '' });
  };

  const removeMaterial = (index: number) => {
    setMaterials(materials.filter((_, i) => i !== index));
  };

  const addTool = () => {
    if (!newTool.name.trim()) return;
    setTools([...tools, { ...newTool, localId: crypto.randomUUID() }]);
    setNewTool(EMPTY_TOOL);
  };

  const removeTool = (index: number) => {
    setTools(tools.filter((_, i) => i !== index));
  };

  const onSubmit = async (data: MaintenanceItemFormData) => {
    if (!assetId) return;
    try {
      setSaving(true);
      setError(null);

      const apiPayload = {
        ...data,
        asset: assetId,
        estimated_cost: String(data.estimated_cost ?? '0'),
      };

      let savedItemId: string;
      if (isEditMode && id) {
        const response = await maintenanceAPI.updateItem(id, apiPayload);
        savedItemId = response.data.id;

        const keepIds = materials.filter((m) => m.id).map((m) => m.id!);
        const toDelete = originalMaterialIds.filter((eid) => !keepIds.includes(eid));
        await Promise.all(toDelete.map((mid) => maintenanceAPI.deleteMaterial(mid)));

        const keepToolIds = tools.filter((t) => t.id).map((t) => t.id!);
        const toolsToDelete = originalToolIds.filter((eid) => !keepToolIds.includes(eid));
        await Promise.all(toolsToDelete.map((tid) => maintenanceToolAPI.delete(tid)));
      } else {
        const response = await maintenanceAPI.createItem(apiPayload);
        savedItemId = response.data.id;
      }

      const newMaterials = materials.filter((m) => !m.id);
      await Promise.all(
        newMaterials.map((m) =>
          maintenanceAPI.createMaterial({
            maintenance_item: savedItemId,
            name: m.name,
            quantity: String(m.quantity),
            unit: m.unit,
            estimated_cost_per_unit: String(m.estimated_cost_per_unit),
            notes: m.notes,
          })
        )
      );

      const newTools = tools.filter((t) => !t.id);
      await Promise.all(
        newTools.map((t) =>
          maintenanceToolAPI.create({
            maintenance_item: savedItemId,
            name: t.name,
            quantity: t.quantity,
            location_hint: t.location_hint,
            is_required: t.is_required,
            notes: t.notes,
          })
        )
      );

      navigate(`/assets/${assetId}`);
    } catch (err: any) {
      console.error('Error saving maintenance item:', err);
      setError(extractErrorMessage(err, 'Failed to save maintenance item.'));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="maintenance-item-form-page"
        hero={{ eyebrow: 'Maintenance · PM task', title: 'PM task', description: 'Loading…' }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading task…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      testId="maintenance-item-form-page"
      hero={{
        eyebrow: 'Maintenance · PM task',
        title: isEditMode ? 'Edit PM task' : 'New PM task',
        description: 'A recurring preventive-maintenance task tied to a specific asset.',
        action: (
          <Button variant="default" onClick={() => navigate(`/assets/${assetId}`)}>
            Cancel
          </Button>
        ),
      }}
    >
      {error && (
        <Alert icon={<IconAlertCircle size={16} />} title="Error" color="red">
          {error}
        </Alert>
      )}

      <form onSubmit={handleSubmit(onSubmit)}>
        <Paper p="md" withBorder>
          <FormLayout
            sections={[
              {
                title: 'Task Details',
                children: (
                  <>
                    <FormInput
                      name="title"
                      control={control}
                      label="Title"
                      required
                      placeholder="e.g. Replace air filter"
                    />
                    <FormTextarea
                      name="description"
                      control={control}
                      label="Description"
                      placeholder="Why this maintenance is needed"
                    />
                    <FormTextarea
                      name="instructions"
                      control={control}
                      label="Instructions"
                      placeholder="Step-by-step instructions"
                      minRows={4}
                    />
                  </>
                ),
              },
              {
                title: 'Schedule & Estimates',
                children: (
                  <>
                    <FormNumberInput
                      name="interval_days"
                      control={control}
                      label="Interval (days)"
                      placeholder="Leave blank for one-time or as-needed"
                      min={1}
                    />
                    <FormNumberInput
                      name="estimated_time_minutes"
                      control={control}
                      label="Estimated Time (minutes)"
                      min={1}
                    />
                    <FormNumberInput
                      name="estimated_cost"
                      control={control}
                      label="Estimated Cost ($)"
                      min={0}
                      step={0.01}
                    />
                    <Switch
                      label="Active"
                      description="Inactive tasks are hidden from the scan page"
                      checked={isActive ?? true}
                      onChange={(e) => setValue('is_active', e.currentTarget.checked)}
                    />
                  </>
                ),
              },
            ]}
          />

          <Divider my="md" label="Materials" labelPosition="left" />
          <Stack gap="sm">
            {materials.length > 0 && (
              <Table striped withTableBorder>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Name</Table.Th>
                    <Table.Th>Qty</Table.Th>
                    <Table.Th>Unit</Table.Th>
                    <Table.Th>Cost/Unit</Table.Th>
                    <Table.Th></Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {materials.map((m, i) => (
                    <Table.Tr key={m.localId}>
                      <Table.Td>{m.name}</Table.Td>
                      <Table.Td>{m.quantity}</Table.Td>
                      <Table.Td>{m.unit}</Table.Td>
                      <Table.Td>${m.estimated_cost_per_unit.toFixed(2)}</Table.Td>
                      <Table.Td>
                        <ActionIcon
                          color="red"
                          variant="subtle"
                          onClick={() => removeMaterial(i)}
                          aria-label="Remove material"
                        >
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}

            <Paper p="sm" withBorder>
              <Text size="sm" fw={500} mb="xs">
                Add Material
              </Text>
              <Group align="flex-end" gap="xs">
                <TextInput
                  label="Name"
                  value={newMaterial.name}
                  onChange={(e) => setNewMaterial({ ...newMaterial, name: e.target.value })}
                  placeholder="e.g. Air filter"
                  style={{ flex: 2 }}
                />
                <NumberInput
                  label="Qty"
                  value={newMaterial.quantity}
                  onChange={(v) => setNewMaterial({ ...newMaterial, quantity: Number(v) || 1 })}
                  min={0.01}
                  step={1}
                  style={{ flex: 1 }}
                />
                <TextInput
                  label="Unit"
                  value={newMaterial.unit}
                  onChange={(e) => setNewMaterial({ ...newMaterial, unit: e.target.value })}
                  placeholder="pcs, oz, ft…"
                  style={{ flex: 1 }}
                />
                <NumberInput
                  label="Cost/Unit ($)"
                  value={newMaterial.estimated_cost_per_unit}
                  onChange={(v) =>
                    setNewMaterial({ ...newMaterial, estimated_cost_per_unit: Number(v) || 0 })
                  }
                  min={0}
                  step={0.01}
                  style={{ flex: 1 }}
                />
                <Button
                  leftSection={<IconPlus size={16} />}
                  onClick={addMaterial}
                  disabled={!newMaterial.name.trim()}
                  variant="light"
                >
                  Add
                </Button>
              </Group>
            </Paper>
          </Stack>

          {/* Tools are gathered and returned (materials get consumed). They
              print at the top of the work order next to the lockout info so
              the tech collects everything before walking to the machine. */}
          <Divider my="md" label="Tools" labelPosition="left" />
          <Stack gap="sm">
            {tools.length > 0 && (
              <Table striped withTableBorder>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Tool</Table.Th>
                    <Table.Th>Qty</Table.Th>
                    <Table.Th>Location</Table.Th>
                    <Table.Th>Required</Table.Th>
                    <Table.Th></Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {tools.map((t, i) => (
                    <Table.Tr key={t.localId}>
                      <Table.Td>{t.name}</Table.Td>
                      <Table.Td>{t.quantity}</Table.Td>
                      <Table.Td>{t.location_hint || '—'}</Table.Td>
                      <Table.Td>{t.is_required ? 'Required' : 'Optional'}</Table.Td>
                      <Table.Td>
                        <ActionIcon
                          color="red"
                          variant="subtle"
                          onClick={() => removeTool(i)}
                          aria-label="Remove tool"
                        >
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}

            <Paper p="sm" withBorder>
              <Text size="sm" fw={500} mb="xs">
                Add Tool
              </Text>
              <Group align="flex-end" gap="xs">
                <TextInput
                  label="Tool"
                  value={newTool.name}
                  onChange={(e) => setNewTool({ ...newTool, name: e.target.value })}
                  placeholder="e.g. Torque wrench"
                  style={{ flex: 2 }}
                />
                <NumberInput
                  label="Qty"
                  value={newTool.quantity}
                  onChange={(v) => setNewTool({ ...newTool, quantity: Number(v) || 1 })}
                  min={1}
                  step={1}
                  style={{ flex: 1 }}
                />
                <TextInput
                  label="Location"
                  value={newTool.location_hint}
                  onChange={(e) => setNewTool({ ...newTool, location_hint: e.target.value })}
                  placeholder="Tool crib, drawer 3"
                  style={{ flex: 2 }}
                />
                <TextInput
                  label="Notes"
                  value={newTool.notes}
                  onChange={(e) => setNewTool({ ...newTool, notes: e.target.value })}
                  placeholder="Optional"
                  style={{ flex: 2 }}
                />
                <Switch
                  label="Required"
                  checked={newTool.is_required}
                  onChange={(e) => setNewTool({ ...newTool, is_required: e.currentTarget.checked })}
                />
                <Button
                  leftSection={<IconPlus size={16} />}
                  onClick={addTool}
                  disabled={!newTool.name.trim()}
                  variant="light"
                >
                  Add Tool
                </Button>
              </Group>
            </Paper>
          </Stack>

          <Group justify="flex-end" mt="xl">
            <Button variant="subtle" onClick={() => navigate(`/assets/${assetId}`)}>
              Cancel
            </Button>
            <Button type="submit" loading={saving}>
              {isEditMode ? 'Save Changes' : 'Create Task'}
            </Button>
          </Group>
        </Paper>
      </form>
    </WorkspacePage>
  );
};

export default MaintenanceItemFormPage;
