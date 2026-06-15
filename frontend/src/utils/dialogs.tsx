/**
 * Imperative dialog helpers backed by @mantine/modals + @mantine/notifications.
 *
 * Phase 1 infrastructure: these wrap Mantine's imperative APIs with a small,
 * stable surface so call sites can replace native window.alert/confirm/prompt
 * without wiring contexts or local state for every usage.
 */
import { Button, Group, Stack, Text, TextInput } from '@mantine/core';
import { modals } from '@mantine/modals';
import { notifications } from '@mantine/notifications';
import React from 'react';

export interface ConfirmActionOptions {
  labels?: { confirm: string; cancel: string };
  color?: string;
}

const DEFAULT_CONFIRM_LABELS = { confirm: 'Confirm', cancel: 'Cancel' };
const DEFAULT_DELETE_LABELS = { confirm: 'Delete', cancel: 'Cancel' };

export function showSuccess(message: string, title = 'Success'): void {
  notifications.show({ color: 'green', title, message });
}

export function showError(message: string, title = 'Error'): void {
  notifications.show({ color: 'red', title, message });
}

export function showInfo(message: string, title?: string): void {
  notifications.show({ title, message });
}

/**
 * Opens a destructive red confirm modal. onConfirm fires only when the user
 * confirms; cancel/dismiss is a no-op.
 */
export function confirmDelete(message: string, onConfirm: () => void): void {
  modals.openConfirmModal({
    title: 'Confirm deletion',
    children: <Text size="sm">{message}</Text>,
    labels: DEFAULT_DELETE_LABELS,
    confirmProps: { color: 'red' },
    onConfirm,
  });
}

/**
 * General-purpose confirmation modal. onConfirm fires on confirm; cancel is a no-op.
 */
export function confirmAction(
  title: string,
  message: string,
  onConfirm: () => void,
  options: ConfirmActionOptions = {},
): void {
  modals.openConfirmModal({
    title,
    children: <Text size="sm">{message}</Text>,
    labels: options.labels ?? DEFAULT_CONFIRM_LABELS,
    confirmProps: options.color ? { color: options.color } : undefined,
    onConfirm,
  });
}

interface PromptFormProps {
  modalId: string;
  label: string;
  initialValue: string;
  placeholder?: string;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}

const PromptForm: React.FC<PromptFormProps> = ({
  modalId,
  label,
  initialValue,
  placeholder,
  onSubmit,
  onCancel,
}) => {
  // Use plain React state instead of @mantine/form: v9 defaults useForm
  // to uncontrolled mode where TextInput holds its value in a ref and
  // synthetic-event changes (fireEvent.change in tests, programmatic
  // .value sets in some browser shells) don't always land in the
  // submitted payload. A single-field prompt doesn't need form
  // machinery anyway.
  const [value, setValue] = React.useState(initialValue);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // Call onSubmit BEFORE closing the modal: Mantine v9 dispatches
    // the close synchronously and the re-render unmounts this form,
    // and if `onSubmit` triggers any state changes or microtasks
    // they race with the close in v9's reducer (the AssetScanPage
    // anonymous-checklist flow used to drop the resolved value
    // silently). Resolving first guarantees the caller sees it.
    onSubmit(value);
    modals.close(modalId);
  };

  const handleCancel = () => {
    modals.close(modalId);
    onCancel();
  };

  return (
    <form onSubmit={handleSubmit}>
      <Stack>
        <TextInput
          label={label}
          placeholder={placeholder}
          autoFocus
          value={value}
          onChange={(e) => setValue(e.currentTarget.value)}
        />
        <Group justify="flex-end">
          <Button variant="default" onClick={handleCancel} type="button">
            Cancel
          </Button>
          <Button type="submit">Submit</Button>
        </Group>
      </Stack>
    </form>
  );
};

export interface PromptInputOptions {
  initialValue?: string;
  placeholder?: string;
}

/**
 * Opens a modal with a single text input. The returned promise resolves with
 * the submitted value on submit, or null on cancel/dismiss. If onSubmit is
 * provided it is invoked with the submitted value (not called on cancel).
 */
export function promptInput(
  title: string,
  label: string,
  onSubmit?: (value: string) => void,
  options: PromptInputOptions = {},
): Promise<string | null> {
  const modalId = `prompt-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  return new Promise((resolve) => {
    let settled = false;
    const resolveOnce = (value: string | null) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };

    modals.open({
      modalId,
      title,
      onClose: () => resolveOnce(null),
      children: (
        <PromptForm
          modalId={modalId}
          label={label}
          initialValue={options.initialValue ?? ''}
          placeholder={options.placeholder}
          onSubmit={(value) => {
            if (onSubmit) onSubmit(value);
            resolveOnce(value);
          }}
          onCancel={() => resolveOnce(null)}
        />
      ),
    });
  });
}
