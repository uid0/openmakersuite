/**
 * Asset supplies / parts section for the asset form.
 *
 * Renders a "Does this asset use consumable supplies?" toggle. When
 * the answer is YES, an inline editor lets the user attach
 * ``InventoryItem`` rows (quantity, required flag, replace interval).
 * The data is held in form state and persisted by the parent form
 * after the asset is saved (an AssetPart needs the asset.id, which
 * doesn't exist until then).
 *
 * Cleaner than the old freeform Maintenance Textarea: parts are now
 * structured rows that surface on the asset detail page and feed the
 * "needs replacement" detector.
 */
import { ActionIcon, Button, Group, NumberInput, Paper, Select, Stack, Switch, Text, Textarea } from '@mantine/core';
import { IconPlus, IconTrash } from '@tabler/icons-react';
import React from 'react';

import { InventoryItem } from '../../types';

export interface PendingAssetPart {
  /** Synthetic key for React. Server-generated id replaces this after save. */
  key: string;
  part: number | string;
  quantity_needed: number;
  is_required: boolean;
  maintenance_interval_days: number | null;
  notes: string;
}

interface AssetSuppliesSectionProps {
  enabled: boolean;
  onEnabledChange: (next: boolean) => void;
  supplies: PendingAssetPart[];
  onChange: (next: PendingAssetPart[]) => void;
  inventoryItems: InventoryItem[];
}

const newRow = (): PendingAssetPart => ({
  key: `pending-${Math.random().toString(36).slice(2, 10)}`,
  part: '',
  quantity_needed: 1,
  is_required: true,
  maintenance_interval_days: null,
  notes: '',
});

const AssetSuppliesSection: React.FC<AssetSuppliesSectionProps> = ({
  enabled,
  onEnabledChange,
  supplies,
  onChange,
  inventoryItems,
}) => {
  const updateRow = (key: string, patch: Partial<PendingAssetPart>) => {
    onChange(supplies.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  };

  const removeRow = (key: string) => {
    onChange(supplies.filter((row) => row.key !== key));
  };

  const handleToggle = (next: boolean) => {
    onEnabledChange(next);
    if (next && supplies.length === 0) {
      // Seed with one empty row so the user has something to fill in.
      onChange([newRow()]);
    }
    if (!next) {
      onChange([]);
    }
  };

  const itemOptions = inventoryItems.map((item) => ({
    value: String(item.id),
    label: item.sku ? `${item.name} (${item.sku})` : item.name,
  }));

  return (
    <Stack gap="md" data-testid="asset-form-supplies-section">
      <Switch
        label="This asset uses consumable supplies"
        description="Saw blades, filters, nozzles, 3D printer hot ends — anything you'd reorder over time."
        checked={enabled}
        onChange={(e) => handleToggle(e.currentTarget.checked)}
      />

      {enabled && (
        <>
          {supplies.map((row) => (
            <Paper key={row.key} withBorder p="md" radius="md">
              <Stack gap="sm">
                <Group justify="space-between" align="flex-start">
                  <Text fw={500} size="sm">
                    Supply
                  </Text>
                  <ActionIcon
                    variant="subtle"
                    color="red"
                    onClick={() => removeRow(row.key)}
                    aria-label="Remove supply"
                  >
                    <IconTrash size={16} />
                  </ActionIcon>
                </Group>
                <Select
                  label="Inventory item"
                  required
                  searchable
                  data={itemOptions}
                  value={row.part ? String(row.part) : null}
                  onChange={(value) => updateRow(row.key, { part: value ?? '' })}
                  nothingFoundMessage="No matching items"
                  placeholder="Pick the inventory item used by this asset"
                />
                <Group grow>
                  <NumberInput
                    label="Quantity needed"
                    min={1}
                    value={row.quantity_needed}
                    onChange={(value) =>
                      updateRow(row.key, {
                        quantity_needed: typeof value === 'number' ? value : 1,
                      })
                    }
                  />
                  <NumberInput
                    label="Replace every (days)"
                    description="Blank = on demand"
                    min={1}
                    value={row.maintenance_interval_days ?? ''}
                    onChange={(value) =>
                      updateRow(row.key, {
                        maintenance_interval_days: typeof value === 'number' ? value : null,
                      })
                    }
                  />
                </Group>
                <Switch
                  label="Required for the asset to operate"
                  checked={row.is_required}
                  onChange={(e) => updateRow(row.key, { is_required: e.currentTarget.checked })}
                />
                <Textarea
                  label="Notes"
                  value={row.notes}
                  onChange={(e) => updateRow(row.key, { notes: e.target.value })}
                  autosize
                  minRows={1}
                />
              </Stack>
            </Paper>
          ))}

          <Group>
            <Button
              variant="default"
              leftSection={<IconPlus size={16} />}
              onClick={() => onChange([...supplies, newRow()])}
            >
              Add another supply
            </Button>
          </Group>
        </>
      )}
    </Stack>
  );
};

export default AssetSuppliesSection;
