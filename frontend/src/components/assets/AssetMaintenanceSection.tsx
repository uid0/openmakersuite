/**
 * Asset preventive-maintenance section for the asset form.
 *
 * Replaces the old freeform ``maintenance_plan`` Textarea with a list
 * of structured ``MaintenanceItem`` rows. A "Schedule preventive
 * maintenance" toggle controls visibility; users add each task via a
 * modal so the section stays compact even for assets with several PM
 * intervals.
 *
 * Like the supplies section, the rows live in form state until the
 * parent form saves the asset (the MaintenanceItem create endpoint
 * needs the asset.id).
 */
import { ActionIcon, Badge, Button, Group, Paper, Stack, Switch, Text } from '@mantine/core';
import { IconCalendarEvent, IconEdit, IconPlus, IconTrash } from '@tabler/icons-react';
import React, { useState } from 'react';

import AssetMaintenanceModal, { PendingMaintenanceItem } from './AssetMaintenanceModal';

interface AssetMaintenanceSectionProps {
  enabled: boolean;
  onEnabledChange: (next: boolean) => void;
  items: PendingMaintenanceItem[];
  onChange: (next: PendingMaintenanceItem[]) => void;
}

const formatInterval = (days: number | null): string => {
  if (days == null) return 'On demand';
  if (days % 365 === 0 && days >= 365) return `Every ${days / 365} year${days === 365 ? '' : 's'}`;
  if (days % 30 === 0 && days >= 30) return `Every ${days / 30} month${days === 30 ? '' : 's'}`;
  if (days % 7 === 0 && days >= 7) return `Every ${days / 7} week${days === 7 ? '' : 's'}`;
  return `Every ${days} day${days === 1 ? '' : 's'}`;
};

const AssetMaintenanceSection: React.FC<AssetMaintenanceSectionProps> = ({
  enabled,
  onEnabledChange,
  items,
  onChange,
}) => {
  const [modalOpen, setModalOpen] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);

  const handleToggle = (next: boolean) => {
    onEnabledChange(next);
    if (!next) {
      onChange([]);
      setModalOpen(false);
      setEditingKey(null);
    }
  };

  const handleAdd = () => {
    setEditingKey(null);
    setModalOpen(true);
  };

  const handleEdit = (key: string) => {
    setEditingKey(key);
    setModalOpen(true);
  };

  const handleRemove = (key: string) => {
    onChange(items.filter((item) => item.key !== key));
  };

  const handleSave = (item: PendingMaintenanceItem) => {
    if (editingKey) {
      onChange(items.map((existing) => (existing.key === editingKey ? item : existing)));
    } else {
      onChange([...items, item]);
    }
    setModalOpen(false);
    setEditingKey(null);
  };

  const editingItem = editingKey != null ? items.find((item) => item.key === editingKey) ?? null : null;

  return (
    <Stack gap="md" data-testid="asset-form-maintenance-section">
      <Switch
        label="Schedule preventive maintenance"
        description="Recurring tasks like oil changes, belt swaps, calibration runs — shown on the asset QR scan page when they come due."
        checked={enabled}
        onChange={(e) => handleToggle(e.currentTarget.checked)}
      />

      {enabled && (
        <Stack gap="sm">
          {items.length === 0 ? (
            <Paper withBorder p="md" radius="md">
              <Stack gap="xs" align="center">
                <IconCalendarEvent size={22} stroke={1.6} />
                <Text size="sm" c="dimmed" ta="center">
                  No PM tasks scheduled yet. Add one to define what should be done and how often.
                </Text>
                <Button leftSection={<IconPlus size={14} />} onClick={handleAdd}>
                  Add maintenance task
                </Button>
              </Stack>
            </Paper>
          ) : (
            <>
              {items.map((item) => (
                <Paper key={item.key} withBorder p="md" radius="md">
                  <Group justify="space-between" align="flex-start" wrap="nowrap">
                    <Stack gap={4} style={{ flex: 1 }}>
                      <Group gap="xs">
                        <Text fw={500}>{item.title}</Text>
                        <Badge variant="light" radius="sm">
                          {formatInterval(item.interval_days)}
                        </Badge>
                        {item.estimated_time_minutes != null && (
                          <Badge variant="default" radius="sm">
                            ~{item.estimated_time_minutes} min
                          </Badge>
                        )}
                      </Group>
                      {item.description && (
                        <Text size="sm" c="dimmed">
                          {item.description}
                        </Text>
                      )}
                    </Stack>
                    <Group gap="xs">
                      <ActionIcon
                        variant="subtle"
                        onClick={() => handleEdit(item.key)}
                        aria-label="Edit task"
                      >
                        <IconEdit size={16} />
                      </ActionIcon>
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        onClick={() => handleRemove(item.key)}
                        aria-label="Remove task"
                      >
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Group>
                  </Group>
                </Paper>
              ))}
              <Group>
                <Button
                  variant="default"
                  leftSection={<IconPlus size={16} />}
                  onClick={handleAdd}
                >
                  Add another task
                </Button>
              </Group>
            </>
          )}
        </Stack>
      )}

      <AssetMaintenanceModal
        opened={modalOpen}
        initial={editingItem}
        onClose={() => {
          setModalOpen(false);
          setEditingKey(null);
        }}
        onSave={handleSave}
      />
    </Stack>
  );
};

export default AssetMaintenanceSection;
