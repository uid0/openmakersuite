/**
 * Visual indicator + drawer for the electrical offline-edit queue.
 *
 * Renders nothing when there are zero pending entries AND no recent
 * sync to report — keeps the panel page chrome quiet during normal
 * operation. Lights up when something is queued or fails to sync.
 *
 * Click the badge → drawer with one row per queued entry, "Retry"
 * button to force a sync, and "Discard" to drop an entry.
 */
import {
  ActionIcon,
  Badge,
  Button,
  Drawer,
  Group,
  Stack,
  Text,
  Tooltip,
} from '@mantine/core';
import {
  IconCheck,
  IconCloudOff,
  IconRefresh,
  IconTrash,
  IconWifi,
  IconWifiOff,
} from '@tabler/icons-react';
import React, { useMemo, useState } from 'react';

import { useOfflineQueue } from '../hooks/useOfflineQueue';
import { buildOfflineSender } from '../services/electricalOfflineEdits';

function formatAge(ts: number | null): string {
  if (!ts) return 'never';
  const seconds = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

const OfflineSyncBadge: React.FC = () => {
  const sender = useMemo(() => buildOfflineSender(), []);
  const queue = useOfflineQueue({ sender });
  const [drawerOpen, setDrawerOpen] = useState(false);

  if (queue.pendingCount === 0 && !queue.lastSyncSummary) {
    // Quiet state: nothing to report.
    return null;
  }

  const badgeColor =
    queue.pendingCount === 0 ? 'green' : !queue.isOnline ? 'red' : 'yellow';
  const Icon =
    queue.pendingCount === 0
      ? IconCheck
      : !queue.isOnline
        ? IconCloudOff
        : IconRefresh;

  return (
    <>
      <Tooltip
        label={
          queue.pendingCount === 0
            ? `All edits synced (last: ${formatAge(queue.lastSyncAt)})`
            : `${queue.pendingCount} edit${queue.pendingCount === 1 ? '' : 's'} queued${queue.isOnline ? ' — click to sync' : ' offline — will retry on reconnect'}`
        }
      >
        <Badge
          leftSection={<Icon size={12} />}
          color={badgeColor}
          variant="filled"
          radius="sm"
          size="sm"
          onClick={() => setDrawerOpen(true)}
          style={{ cursor: 'pointer' }}
          data-testid="offline-sync-badge"
          data-pending-count={queue.pendingCount}
        >
          {queue.pendingCount === 0 ? 'Synced' : `${queue.pendingCount} pending sync`}
        </Badge>
      </Tooltip>

      <Drawer
        opened={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        position="right"
        size="md"
        title={
          <Group gap="xs">
            {queue.isOnline ? <IconWifi size={16} /> : <IconWifiOff size={16} />}
            <Text fw={600}>Offline edit queue</Text>
          </Group>
        }
      >
        <Stack gap="md">
          <Group justify="space-between" wrap="wrap">
            <Text size="sm" c="dimmed">
              {queue.pendingCount === 0
                ? 'Nothing queued.'
                : `${queue.pendingCount} edit${queue.pendingCount === 1 ? '' : 's'} waiting.`}
            </Text>
            <Button
              size="xs"
              variant="default"
              leftSection={<IconRefresh size={14} />}
              loading={queue.isSyncing}
              disabled={queue.pendingCount === 0 || !queue.isOnline}
              onClick={() => queue.syncNow()}
            >
              Sync now
            </Button>
          </Group>

          {queue.entries.map((entry) => (
            <Stack
              key={entry.id}
              gap={4}
              p="sm"
              style={{ border: '1px solid var(--mantine-color-gray-3)', borderRadius: 6 }}
              data-testid={`queue-entry-${entry.id}`}
            >
              <Group justify="space-between" wrap="nowrap" align="flex-start">
                <Stack gap={0}>
                  <Text fw={500} size="sm">
                    {entry.label}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {entry.method} {entry.resource}/{entry.resourceId} · queued{' '}
                    {formatAge(entry.queuedAt)}
                  </Text>
                </Stack>
                <ActionIcon
                  variant="subtle"
                  color="red"
                  size="sm"
                  aria-label="Discard queued edit"
                  onClick={() => queue.removeEntry(entry.id)}
                >
                  <IconTrash size={14} />
                </ActionIcon>
              </Group>
              {entry.lastError && (
                <Text size="xs" c="red">
                  last error: {entry.lastError} (tried {entry.attemptCount}×)
                </Text>
              )}
            </Stack>
          ))}

          {queue.lastSyncSummary && (
            <Text size="xs" c="dimmed">
              Last sync {formatAge(queue.lastSyncAt)}: {queue.lastSyncSummary.succeeded} succeeded
              {queue.lastSyncSummary.failed > 0 ? `, ${queue.lastSyncSummary.failed} failed` : ''}.
            </Text>
          )}
        </Stack>
      </Drawer>
    </>
  );
};

export default OfflineSyncBadge;
