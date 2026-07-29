/**
 * Packaging-chain editor for the inventory item form (op-lkxl, phase 3).
 *
 * Edits an item's `packaging_levels` as an ordered list, largest rung first —
 * "case, ream, sheet". The row's position IS its `sort_order` (0 = outermost),
 * so moving a row up/down is the whole of "reorder"; `base_units` is always how
 * many BASE units one of that rung holds, which is what makes the derived
 * "1 case = 10 reams" line computable without a second column to keep in sync.
 *
 * Purely controlled: the parent owns the rows and does the saving. Validation
 * messages come from the shared `validatePackagingChain` so the form and the
 * backend agree on what a legal chain is.
 */
import { ActionIcon, Button, Group, NumberInput, Stack, Table, Text, TextInput } from '@mantine/core';
import { IconArrowDown, IconArrowUp, IconPlus, IconTrash } from '@tabler/icons-react';
import React from 'react';
import { PackagingRow, blankPackagingRow, perParent, pluralizeUnit } from '../../utils/packaging';

interface PackagingChainEditorProps {
  rows: PackagingRow[];
  onChange: (rows: PackagingRow[]) => void;
  /** The item's base unit, used to label the innermost rung's size. */
  baseUnit: string;
  /** Chain-level validation messages, rendered under the table. */
  errors?: string[];
}

const PackagingChainEditor: React.FC<PackagingChainEditorProps> = ({
  rows,
  onChange,
  baseUnit,
  errors = [],
}) => {
  const unit = baseUnit.trim() || 'unit';

  const updateRow = (index: number, patch: Partial<PackagingRow>) => {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };

  const removeRow = (index: number) => {
    onChange(rows.filter((_, i) => i !== index));
  };

  const moveRow = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= rows.length) return;
    const next = [...rows];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };

  const addRow = () => {
    onChange([...rows, blankPackagingRow()]);
  };

  return (
    <Stack gap="xs" data-testid="packaging-chain-editor">
      <Text size="sm" fw={500}>
        Packaging chain
      </Text>
      <Text size="xs" c="dimmed">
        Largest package first, ending with the base unit. Each level says how many{' '}
        {pluralizeUnit(unit, 2)} it holds — a case of 10 reams of 100 sheets is 1000, 100, 1.
      </Text>

      {rows.length === 0 ? (
        <Text size="sm" c="dimmed" data-testid="packaging-chain-empty">
          No packaging levels — this item is counted in {pluralizeUnit(unit, 2)}.
        </Text>
      ) : (
        <Table data-testid="packaging-chain-table">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Level name</Table.Th>
              <Table.Th>{pluralizeUnit(unit, 2)} held</Table.Th>
              <Table.Th>Contains</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((row, index) => {
              const ratio = perParent(rows, index);
              const below = rows[index + 1];
              return (
                <Table.Tr key={row.key} data-testid={`packaging-row-${index}`}>
                  <Table.Td>
                    <TextInput
                      aria-label={`Level ${index + 1} name`}
                      placeholder="case"
                      value={row.name}
                      onChange={(e) => updateRow(index, { name: e.currentTarget.value })}
                      data-testid={`packaging-row-name-${index}`}
                    />
                  </Table.Td>
                  <Table.Td>
                    <NumberInput
                      aria-label={`Level ${index + 1} base units`}
                      placeholder="1"
                      min={1}
                      allowDecimal={false}
                      allowNegative={false}
                      value={row.base_units}
                      onChange={(v) =>
                        updateRow(index, { base_units: v === '' ? '' : Number(v) })
                      }
                      data-testid={`packaging-row-base-units-${index}`}
                    />
                  </Table.Td>
                  <Table.Td>
                    {ratio !== null && below ? (
                      <Text size="sm" c="dimmed" data-testid={`packaging-row-per-parent-${index}`}>
                        1 {row.name.trim() || 'level'} = {ratio}{' '}
                        {pluralizeUnit(below.name.trim() || 'level', ratio)}
                      </Text>
                    ) : (
                      <Text size="sm" c="dimmed">
                        —
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} justify="flex-end" wrap="nowrap">
                      <ActionIcon
                        variant="subtle"
                        aria-label={`Move level ${index + 1} up`}
                        disabled={index === 0}
                        onClick={() => moveRow(index, -1)}
                        data-testid={`packaging-row-up-${index}`}
                      >
                        <IconArrowUp size={16} />
                      </ActionIcon>
                      <ActionIcon
                        variant="subtle"
                        aria-label={`Move level ${index + 1} down`}
                        disabled={index === rows.length - 1}
                        onClick={() => moveRow(index, 1)}
                        data-testid={`packaging-row-down-${index}`}
                      >
                        <IconArrowDown size={16} />
                      </ActionIcon>
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        aria-label={`Remove level ${index + 1}`}
                        onClick={() => removeRow(index)}
                        data-testid={`packaging-row-remove-${index}`}
                      >
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}

      {errors.length > 0 && (
        <Stack gap={2} data-testid="packaging-chain-errors">
          {errors.map((message) => (
            <Text key={message} size="xs" c="red">
              {message}
            </Text>
          ))}
        </Stack>
      )}

      <Group>
        <Button
          variant="light"
          size="xs"
          leftSection={<IconPlus size={14} />}
          onClick={addRow}
          data-testid="packaging-add-level"
        >
          Add packaging level
        </Button>
      </Group>
    </Stack>
  );
};

export default PackagingChainEditor;
