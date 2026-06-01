/**
 * DeviceLifecycleCard
 *
 * Staff lifecycle controls for a ForgeKey device, on the device detail page:
 *  - Lock / Unlock  — cut / restore power (disable / enable commands)
 *  - Reset          — restart the device
 *  - Retire / Reactivate — take out of service / return to service (is_active)
 *  - Delete         — remove permanently (with confirmation)
 *
 * Lock/unlock/reset are fire-and-forget MQTT commands; retire/reactivate and
 * delete are gated to staff server-side.
 */
import { Badge, Button, Card, Group, Modal, Stack, Text } from '@mantine/core';
import { useState } from 'react';
import { ForgeKeyDevice, forgekeyAPI } from '../services/api';
import { showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface Props {
  device: ForgeKeyDevice;
  /** Reload the device after retire / reactivate. */
  onChanged: () => void;
  /** Navigate away after a successful delete. */
  onDeleted: () => void;
}

export default function DeviceLifecycleCard({ device, onChanged, onDeleted }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const label = device.name || device.mac_address;

  const run = async (
    key: string,
    fn: () => Promise<unknown>,
    success: string,
    after?: () => void,
  ) => {
    setBusy(key);
    try {
      await fn();
      showSuccess(success);
      after?.();
    } catch (err) {
      showError(extractErrorMessage(err, 'Action failed.'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card withBorder radius="md" p="md" data-testid="device-lifecycle">
      <Group justify="space-between" mb="sm">
        <Text fw={600}>Device lifecycle</Text>
        {!device.is_active && (
          <Badge color="gray" variant="light" data-testid="device-retired-badge">
            Retired
          </Badge>
        )}
      </Group>

      <Stack gap="sm">
        <Group gap="xs" wrap="wrap">
          <Button
            variant="light"
            color="orange"
            loading={busy === 'lock'}
            onClick={() =>
              run('lock', () => forgekeyAPI.disableDevice(device.id), `Power-off (lock) sent to ${label}.`)
            }
            data-testid="device-lock"
          >
            Lock (cut power)
          </Button>
          <Button
            variant="light"
            color="green"
            loading={busy === 'unlock'}
            onClick={() =>
              run('unlock', () => forgekeyAPI.enableDevice(device.id), `Power-on (unlock) sent to ${label}.`)
            }
            data-testid="device-unlock"
          >
            Unlock (restore power)
          </Button>
          <Button
            variant="light"
            loading={busy === 'reset'}
            onClick={() => run('reset', () => forgekeyAPI.restart(device.id), 'Restart command sent.')}
            data-testid="device-reset"
          >
            Reset (restart)
          </Button>
        </Group>

        <Group gap="xs" wrap="wrap">
          {device.is_active ? (
            <Button
              variant="default"
              loading={busy === 'retire'}
              onClick={() =>
                run(
                  'retire',
                  () => forgekeyAPI.retireDevice(device.id),
                  'Device retired (taken out of service).',
                  onChanged,
                )
              }
              data-testid="device-retire"
            >
              Retire
            </Button>
          ) : (
            <Button
              variant="default"
              loading={busy === 'reactivate'}
              onClick={() =>
                run(
                  'reactivate',
                  () => forgekeyAPI.reactivateDevice(device.id),
                  'Device reactivated.',
                  onChanged,
                )
              }
              data-testid="device-reactivate"
            >
              Reactivate
            </Button>
          )}
          <Button
            color="red"
            variant="light"
            onClick={() => setConfirmDelete(true)}
            data-testid="device-delete"
          >
            Delete…
          </Button>
        </Group>

        <Text size="xs" c="dimmed">
          Lock / Unlock and Reset send live MQTT commands. Retire takes the
          device out of service but keeps its history; Delete removes it
          permanently.
        </Text>
      </Stack>

      <Modal
        opened={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Delete device?"
      >
        <Stack gap="sm">
          <Text size="sm">
            Permanently delete <strong>{label}</strong> and its command history?
            This can&rsquo;t be undone. To temporarily take it out of service,
            use <strong>Retire</strong> instead.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button
              color="red"
              loading={busy === 'delete'}
              onClick={() =>
                run('delete', () => forgekeyAPI.deleteDevice(device.id), 'Device deleted.', () => {
                  setConfirmDelete(false);
                  onDeleted();
                })
              }
              data-testid="device-delete-confirm"
            >
              Delete permanently
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Card>
  );
}
