/**
 * Modal for adding / editing a preventive-maintenance task that will
 * be attached to the asset on save. Outputs a ``PendingMaintenanceItem``
 * — the parent form sends it through ``maintenanceAPI.createItem`` once
 * the asset.id exists.
 */
import {
  Button,
  Group,
  Modal,
  NumberInput,
  Select,
  Stack,
  Textarea,
  TextInput,
} from '@mantine/core';
import React, { useEffect, useMemo, useState } from 'react';

export interface PendingMaintenanceItem {
  /** Synthetic key for React list reconciliation. */
  key: string;
  title: string;
  description: string;
  instructions: string;
  estimated_time_minutes: number | null;
  interval_days: number | null;
}

interface AssetMaintenanceModalProps {
  opened: boolean;
  initial: PendingMaintenanceItem | null;
  onClose: () => void;
  onSave: (item: PendingMaintenanceItem) => void;
}

const INTERVAL_PRESETS = [
  { value: '7', label: 'Weekly' },
  { value: '14', label: 'Every two weeks' },
  { value: '30', label: 'Monthly' },
  { value: '90', label: 'Quarterly' },
  { value: '180', label: 'Twice a year' },
  { value: '365', label: 'Annually' },
  { value: 'custom', label: 'Custom days…' },
  { value: 'on-demand', label: 'On demand (no schedule)' },
];

const presetFor = (interval_days: number | null): string => {
  if (interval_days == null) return 'on-demand';
  const match = INTERVAL_PRESETS.find((p) => p.value === String(interval_days));
  return match ? match.value : 'custom';
};

const emptyDraft = (): PendingMaintenanceItem => ({
  key: `pending-mi-${Math.random().toString(36).slice(2, 10)}`,
  title: '',
  description: '',
  instructions: '',
  estimated_time_minutes: null,
  interval_days: 30,
});

const AssetMaintenanceModal: React.FC<AssetMaintenanceModalProps> = ({
  opened,
  initial,
  onClose,
  onSave,
}) => {
  const [draft, setDraft] = useState<PendingMaintenanceItem>(() => initial ?? emptyDraft());
  const [intervalPreset, setIntervalPreset] = useState<string>(() =>
    presetFor(initial?.interval_days ?? 30),
  );

  // Reset draft state whenever the modal opens with a new initial value
  // (new vs edit-existing). Mantine's Modal keeps the children mounted
  // between toggles, so we have to sync explicitly.
  useEffect(() => {
    if (opened) {
      const next = initial ?? emptyDraft();
      setDraft(next);
      setIntervalPreset(presetFor(next.interval_days));
    }
  }, [opened, initial]);

  const titleLabel = useMemo(() => (initial ? 'Edit maintenance task' : 'New maintenance task'), [initial]);

  const handleIntervalPresetChange = (value: string | null) => {
    if (!value) return;
    setIntervalPreset(value);
    if (value === 'on-demand') {
      setDraft({ ...draft, interval_days: null });
    } else if (value === 'custom') {
      // Keep whatever the user typed in the custom field
    } else {
      setDraft({ ...draft, interval_days: Number(value) });
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.title.trim()) return;
    onSave({ ...draft, title: draft.title.trim() });
  };

  return (
    <Modal opened={opened} onClose={onClose} title={titleLabel} centered size="lg">
      <form onSubmit={handleSubmit}>
        <Stack gap="md">
          <TextInput
            label="Title"
            required
            value={draft.title}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            placeholder="e.g. Replace HEPA filter"
            autoFocus
          />
          <Textarea
            label="Why this matters"
            description="Optional context — why does this need doing?"
            value={draft.description}
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            autosize
            minRows={2}
          />
          <Textarea
            label="Step-by-step instructions"
            description="Shown on the QR-scan page when this task comes due."
            value={draft.instructions}
            onChange={(e) => setDraft({ ...draft, instructions: e.target.value })}
            autosize
            minRows={3}
          />
          <Group grow>
            <Select
              label="Schedule"
              data={INTERVAL_PRESETS}
              value={intervalPreset}
              onChange={handleIntervalPresetChange}
            />
            {intervalPreset === 'custom' && (
              <NumberInput
                label="Custom interval"
                description="In days"
                min={1}
                value={draft.interval_days ?? ''}
                onChange={(value) =>
                  setDraft({
                    ...draft,
                    interval_days: typeof value === 'number' ? value : null,
                  })
                }
              />
            )}
          </Group>
          <NumberInput
            label="Estimated time (minutes)"
            description="Used for the work-order time estimate"
            min={1}
            value={draft.estimated_time_minutes ?? ''}
            onChange={(value) =>
              setDraft({
                ...draft,
                estimated_time_minutes: typeof value === 'number' ? value : null,
              })
            }
          />
          <Group justify="flex-end">
            <Button variant="default" type="button" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={!draft.title.trim()}>
              {initial ? 'Save task' : 'Add task'}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
};

export default AssetMaintenanceModal;
