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
import { useForm, useWatch } from 'react-hook-form';
import { useNavigate, useParams } from 'react-router-dom';
import { FormInput } from '../components/forms/FormInput';
import { FormLayout } from '../components/forms/FormLayout';
import { FormNumberInput } from '../components/forms/FormNumberInput';
import { FormTextarea } from '../components/forms/FormTextarea';
import WorkspacePage from '../components/landing/WorkspacePage';
import { maintenanceAPI } from '../services/api';
import { MaintenanceMaterial } from '../types';
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

  const { control, handleSubmit, reset, setValue } = useForm<MaintenanceItemFormData>({
    resolver: zodResolver(maintenanceItemFormSchema),
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
