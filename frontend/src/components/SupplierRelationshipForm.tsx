/**
 * SupplierRelationshipForm - Component for managing supplier relationships
 * Can be used in a modal or inline in a form
 *
 * Every control here is written back through the `item-suppliers` API when the
 * containing form is saved. `Supplier` and `Supplier SKU` carry an asterisk
 * because `ItemSupplier` cannot be stored without them.
 *
 * `withAsterisk` rather than `required`: the native attribute would have the
 * browser block the whole item form on an unfinished row with only a transient
 * bubble as explanation. The containing form checks both fields before it sends
 * anything and names what is missing in its own error banner, which stays on
 * screen — and which the operator can act on.
 */
import { ActionIcon, Button, Group, Select, Stack, Text, TextInput } from '@mantine/core';
import { IconPlus, IconTrash } from '@tabler/icons-react';
import React from 'react';
import { Supplier } from '../types';

export interface SupplierRelationship {
  id?: number; // For existing relationships
  supplier: number | null;
  supplier_sku: string;
  supplier_url: string;
  unit_cost: string | null;
  package_cost: string | null;
  quantity_per_package: number;
  average_lead_time: number;
  is_primary: boolean;
}

export interface SupplierRelationshipFormProps {
  suppliers: Supplier[];
  relationships: SupplierRelationship[];
  onChange: (relationships: SupplierRelationship[]) => void;
  disabled?: boolean;
}

export const SupplierRelationshipForm: React.FC<SupplierRelationshipFormProps> = ({
  suppliers,
  relationships,
  onChange,
  disabled = false,
}) => {
  const supplierOptions = suppliers.map((s) => ({
    value: String(s.id),
    label: s.name,
  }));

  const addRelationship = () => {
    onChange([
      ...relationships,
      {
        supplier: null,
        supplier_sku: '',
        supplier_url: '',
        unit_cost: null,
        package_cost: null,
        quantity_per_package: 1,
        average_lead_time: 0,
        is_primary: relationships.length === 0, // First one is primary by default
      },
    ]);
  };

  const removeRelationship = (index: number) => {
    const newRelationships = relationships.filter((_, i) => i !== index);
    // If we removed the primary, make the first one primary
    if (relationships[index].is_primary && newRelationships.length > 0) {
      newRelationships[0].is_primary = true;
    }
    onChange(newRelationships);
  };

  const updateRelationship = (index: number, updates: Partial<SupplierRelationship>) => {
    const newRelationships = [...relationships];
    newRelationships[index] = { ...newRelationships[index], ...updates };
    
    // If setting a relationship as primary, unset others
    if (updates.is_primary) {
      newRelationships.forEach((rel, i) => {
        if (i !== index) {
          rel.is_primary = false;
        }
      });
    }
    
    onChange(newRelationships);
  };

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text size="sm" fw={500}>
          Supplier Relationships
        </Text>
        <Button
          size="xs"
          leftSection={<IconPlus size={16} />}
          onClick={addRelationship}
          disabled={disabled}
        >
          Add Supplier
        </Button>
      </Group>

      {relationships.length === 0 ? (
        <Text size="sm" c="dimmed" ta="center" py="md">
          No suppliers added. Click "Add Supplier" to add one.
        </Text>
      ) : (
        <Stack gap="md">
          {relationships.map((rel, index) => (
            <Stack key={index} gap="xs" p="md" style={{ border: '1px solid #dee2e6', borderRadius: '4px' }}>
              <Group justify="space-between" align="flex-start">
                <Text size="sm" fw={500}>
                  Supplier #{index + 1}
                  {rel.is_primary && (
                    <Text component="span" size="xs" c="blue" ml="xs">
                      (Primary)
                    </Text>
                  )}
                </Text>
                <ActionIcon
                  color="red"
                  variant="subtle"
                  aria-label={`Remove supplier #${index + 1}`}
                  onClick={() => removeRelationship(index)}
                  disabled={disabled}
                >
                  <IconTrash size={16} />
                </ActionIcon>
              </Group>

              <Group grow>
                <Select
                  label="Supplier"
                  withAsterisk
                  data={supplierOptions}
                  searchable
                  value={rel.supplier ? String(rel.supplier) : ''}
                  onChange={(value) =>
                    updateRelationship(index, { supplier: value ? Number(value) : null })
                  }
                  disabled={disabled}
                />
              </Group>

              <Group grow>
                <TextInput
                  label="Supplier SKU"
                  withAsterisk
                  value={rel.supplier_sku}
                  onChange={(e) =>
                    updateRelationship(index, { supplier_sku: e.target.value })
                  }
                  disabled={disabled}
                />
                <TextInput
                  label="Supplier URL"
                  value={rel.supplier_url}
                  onChange={(e) =>
                    updateRelationship(index, { supplier_url: e.target.value })
                  }
                  disabled={disabled}
                />
              </Group>

              <Group grow>
                <TextInput
                  label="Unit Cost"
                  description="Package cost ÷ quantity per package. Change this box and it governs: the package cost is re-priced from it."
                  type="number"
                  value={rel.unit_cost || ''}
                  onChange={(e) =>
                    updateRelationship(index, {
                      unit_cost: e.target.value || null,
                    })
                  }
                  disabled={disabled}
                />
                <TextInput
                  label="Package Cost"
                  description="What you pay per package. Change this box and it governs: the unit cost is re-derived from it."
                  type="number"
                  value={rel.package_cost || ''}
                  onChange={(e) =>
                    updateRelationship(index, {
                      package_cost: e.target.value || null,
                    })
                  }
                  disabled={disabled}
                />
              </Group>

              <Group grow>
                <TextInput
                  label="Quantity per Package"
                  type="number"
                  value={rel.quantity_per_package}
                  onChange={(e) =>
                    updateRelationship(index, {
                      quantity_per_package: Number(e.target.value) || 1,
                    })
                  }
                  disabled={disabled}
                />
                <TextInput
                  label="Average Lead Time (days)"
                  type="number"
                  value={rel.average_lead_time}
                  onChange={(e) =>
                    updateRelationship(index, {
                      average_lead_time: Number(e.target.value) || 0,
                    })
                  }
                  disabled={disabled}
                />
              </Group>

              <Group>
                <Button
                  size="xs"
                  variant={rel.is_primary ? 'filled' : 'outline'}
                  onClick={() => updateRelationship(index, { is_primary: !rel.is_primary })}
                  disabled={disabled}
                >
                  {rel.is_primary ? 'Primary Supplier' : 'Set as Primary'}
                </Button>
              </Group>
            </Stack>
          ))}
        </Stack>
      )}
    </Stack>
  );
};

export default SupplierRelationshipForm;
