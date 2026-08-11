/**
 * KitComponentEditor — build a kit's bill of materials fast (op-8n0).
 *
 * Adding five ink cartridges is the whole job, so the loop is optimised for
 * repetition rather than for a single careful edit:
 *
 *   type → ↓ → Enter (pick) → Tab (qty) → Enter (commit) → picker refocused
 *
 * Four decisions make that work:
 *   - Search is DEBOUNCED and SERVER-SIDE, so the picker stays usable against a
 *     catalog far larger than one page.
 *   - Items already in the kit are FILTERED OUT of the options. Duplicates
 *     become impossible rather than an error message to read and recover from.
 *   - Quantity defaults to 1, which is what a kit row almost always is.
 *   - Enter in the quantity field commits, then the picker clears and refocuses,
 *     so the operator never reaches for the mouse between rows.
 *
 * `FormAutocomplete` cannot back this: it hardcodes 'suppliers' | 'categories' |
 * 'locations', has no item mode, and is react-hook-form Controller-bound.
 *
 * This component is CONTROLLED — it owns no persistence. The parent holds the
 * draft rows and decides when to save, so the same editor serves both the
 * create form (no kit yet) and the detail page (patching an existing kit).
 */
import { ActionIcon, Autocomplete, Button, Group, NumberInput, Stack, Table, Text } from '@mantine/core';
import { IconTrash } from '@tabler/icons-react';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { inventoryAPI } from '../../services/api';

/** A draft bill-of-materials row. `id` is present only for saved rows. */
export interface KitComponentDraft {
  id?: number;
  component: string;
  component_name: string;
  component_sku?: string;
  quantity: number;
}

export interface KitComponentEditorProps {
  value: KitComponentDraft[];
  onChange: (rows: KitComponentDraft[]) => void;
  /** Disable every control while a save is in flight (scoped pending UI). */
  disabled?: boolean;
  /**
   * The kit being edited, excluded from its own picker so a kit can never be
   * offered as its own component. Absent on the create form.
   */
  excludeItemId?: string;
  testId?: string;
}

const KitComponentEditor: React.FC<KitComponentEditorProps> = ({
  value,
  onChange,
  disabled = false,
  excludeItemId,
  testId = 'kit-component-editor',
}) => {
  const [search, setSearch] = useState('');
  const [options, setOptions] = useState<Array<{ value: string; label: string }>>([]);
  const [byId, setById] = useState<Record<string, { name: string; sku: string }>>({});
  const [pendingComponent, setPendingComponent] = useState<string | null>(null);
  const [pendingQuantity, setPendingQuantity] = useState<number | string>(1);
  const [searchError, setSearchError] = useState<string | null>(null);

  const pickerRef = useRef<HTMLInputElement>(null);
  const quantityRef = useRef<HTMLInputElement>(null);

  const chosenIds = useMemo(() => new Set(value.map((row) => row.component)), [value]);

  // Debounced server-side search. Kits are excluded by the endpoint's default
  // (is_kit rows are hidden unless include_kits=true), so nested kits are
  // unreachable from here without a second guard.
  useEffect(() => {
    let cancelled = false;
    const handle = setTimeout(async () => {
      try {
        const res = await inventoryAPI.listItems({
          search: search || undefined,
          is_active: true,
          page_size: 25,
        });
        if (cancelled) return;
        const items = res?.data?.results ?? [];
        setById((prev) => {
          const next = { ...prev };
          items.forEach((item) => {
            next[item.id] = { name: item.name, sku: item.sku };
          });
          return next;
        });
        setOptions(
          items
            // Already-added items and the kit itself never appear as options.
            .filter((item) => !chosenIds.has(item.id) && item.id !== excludeItemId)
            .map((item) => ({ value: item.id, label: item.name })),
        );
        setSearchError(null);
      } catch {
        if (!cancelled) setSearchError('Could not load items. Try again.');
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [search, chosenIds, excludeItemId]);

  const commitRow = useCallback(() => {
    if (!pendingComponent || disabled) return;
    const quantity = Number(pendingQuantity) || 1;
    if (quantity < 1) return;

    const meta = byId[pendingComponent];
    onChange([
      ...value,
      {
        component: pendingComponent,
        component_name: meta?.name ?? 'Item',
        component_sku: meta?.sku,
        quantity,
      },
    ]);

    // Reset for the next row and put the cursor back where typing continues.
    setPendingComponent(null);
    setPendingQuantity(1);
    setSearch('');
    pickerRef.current?.focus();
  }, [pendingComponent, pendingQuantity, byId, value, onChange, disabled]);

  const handlePick = (label: string) => {
    const match = options.find((option) => option.label === label);
    if (!match) {
      setSearch(label);
      return;
    }
    setPendingComponent(match.value);
    setSearch(label);
    // Selecting an item means the next keystroke is the quantity.
    window.setTimeout(() => quantityRef.current?.focus(), 0);
  };

  const updateQuantity = (component: string, quantity: number) => {
    onChange(
      value.map((row) => (row.component === component ? { ...row, quantity } : row)),
    );
  };

  const removeRow = (component: string) => {
    onChange(value.filter((row) => row.component !== component));
  };

  return (
    <Stack gap="sm" data-testid={testId}>
      <Group align="flex-end" gap="sm">
        <Autocomplete
          ref={pickerRef}
          label="Add component"
          placeholder="Search items…"
          data={options.map((option) => option.label)}
          value={search}
          onChange={handlePick}
          onOptionSubmit={handlePick}
          disabled={disabled}
          error={searchError}
          style={{ flex: 1 }}
          data-testid="kit-component-picker"
        />
        <NumberInput
          ref={quantityRef}
          label="Qty"
          min={1}
          value={pendingQuantity}
          onChange={setPendingQuantity}
          disabled={disabled}
          w={110}
          data-testid="kit-component-quantity"
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              // Enter commits the row rather than submitting the whole form.
              event.preventDefault();
              commitRow();
            }
          }}
        />
        <Button
          onClick={commitRow}
          disabled={disabled || !pendingComponent}
          data-testid="kit-component-add"
        >
          Add
        </Button>
      </Group>

      {value.length === 0 ? (
        <Text size="sm" c="dimmed" data-testid="kit-component-editor-empty">
          A kit needs at least one component.
        </Text>
      ) : (
        <Table withTableBorder data-testid="kit-component-editor-rows" aria-label="Kit components">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Component</Table.Th>
              <Table.Th scope="col">Per kit</Table.Th>
              <Table.Th scope="col" aria-label="Actions" />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {value.map((row) => (
              <Table.Tr key={row.component} data-testid={`kit-editor-row-${row.component}`}>
                <Table.Td>
                  <Text size="sm">{row.component_name}</Text>
                  {row.component_sku && (
                    <Text size="xs" c="dimmed">
                      {row.component_sku}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  <NumberInput
                    min={1}
                    value={row.quantity}
                    onChange={(next) => updateQuantity(row.component, Number(next) || 1)}
                    disabled={disabled}
                    w={100}
                    aria-label={`Quantity of ${row.component_name} per kit`}
                  />
                </Table.Td>
                <Table.Td>
                  <ActionIcon
                    variant="subtle"
                    color="red"
                    onClick={() => removeRow(row.component)}
                    disabled={disabled}
                    aria-label={`Remove ${row.component_name}`}
                    data-testid={`kit-editor-remove-${row.component}`}
                  >
                    <IconTrash size={16} />
                  </ActionIcon>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
};

export default KitComponentEditor;
