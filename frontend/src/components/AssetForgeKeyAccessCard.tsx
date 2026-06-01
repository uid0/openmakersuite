/**
 * AssetForgeKeyAccessCard
 *
 * Surfaces the ForgeKey access controls for a single inventory Asset on the
 * asset detail page (#7b):
 *  - Operational mode + classroom-mode toggle (enable / disable)
 *  - Authorized users, each revocable
 *  - Active device lockouts, each unlockable (server enforces the unlock
 *    permission hierarchy; a 403 surfaces as an error toast)
 *
 * The card renders nothing for assets that have no ForgeKey relationship (no
 * operational mode, no active authorizations, no active lockouts), so it's a
 * no-op on ordinary inventory assets.
 */
import { Badge, Button, Card, Group, Stack, Text } from '@mantine/core';
import { useCallback, useEffect, useState } from 'react';
import {
  ForgeKeyAssetAuthorization,
  ForgeKeyDeviceLockout,
  ForgeKeyOperationalMode,
  forgekeyAPI,
} from '../services/api';
import { confirmAction, showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface Props {
  assetId: string;
}

function unwrap<T>(data: { results?: T[] } | T[]): T[] {
  if (Array.isArray(data)) return data;
  return data.results ?? [];
}

const MODE_COLORS: Record<string, string> = {
  available: 'green',
  classroom: 'blue',
  maintenance: 'yellow',
  locked_out: 'red',
};

export default function AssetForgeKeyAccessCard({ assetId }: Props) {
  const [mode, setMode] = useState<ForgeKeyOperationalMode | null>(null);
  const [auths, setAuths] = useState<ForgeKeyAssetAuthorization[]>([]);
  const [lockouts, setLockouts] = useState<ForgeKeyDeviceLockout[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [modeRes, authRes, lockRes] = await Promise.all([
        forgekeyAPI.listOperationalModes(assetId),
        forgekeyAPI.listAuthorizations(assetId, { activeOnly: true }),
        forgekeyAPI.listLockouts(assetId, { activeOnly: true }),
      ]);
      const modes = unwrap<ForgeKeyOperationalMode>(modeRes.data);
      setMode(modes.length > 0 ? modes[0] : null);
      setAuths(unwrap<ForgeKeyAssetAuthorization>(authRes.data));
      setLockouts(unwrap<ForgeKeyDeviceLockout>(lockRes.data));
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to load device access controls.'));
    } finally {
      setLoading(false);
    }
  }, [assetId]);

  useEffect(() => {
    load();
  }, [load]);

  const run = async (key: string, fn: () => Promise<unknown>, success: string) => {
    setBusy(key);
    try {
      await fn();
      showSuccess(success);
      await load();
    } catch (err) {
      showError(extractErrorMessage(err, 'Action failed.'));
    } finally {
      setBusy(null);
    }
  };

  if (loading) return null;
  if (!mode && auths.length === 0 && lockouts.length === 0) return null;

  return (
    <Card withBorder radius="md" p="md" data-testid="asset-forgekey-access">
      <Text fw={600}>Device access and modes</Text>
      <Text size="xs" c="dimmed" mb="md">
        ForgeKey access controls for this asset.
      </Text>

      <Stack gap="lg">
        {mode && (
          <div>
            <Group gap="xs" mb="xs">
              <Text size="sm" fw={500}>
                Operational mode
              </Text>
              <Badge
                color={MODE_COLORS[mode.mode] ?? 'gray'}
                data-testid="operational-mode-badge"
              >
                {mode.mode.replace('_', ' ')}
              </Badge>
            </Group>
            {mode.classroom_mode_enabled ? (
              <>
                <Button
                  size="xs"
                  variant="light"
                  loading={busy === 'classroom'}
                  onClick={() =>
                    run(
                      'classroom',
                      () => forgekeyAPI.disableClassroomMode(mode.id),
                      'Classroom mode disabled.',
                    )
                  }
                >
                  Disable classroom mode
                </Button>
                {mode.classroom_mode_enabled_by_username && (
                  <Text size="xs" c="dimmed" mt={4}>
                    Enabled by {mode.classroom_mode_enabled_by_username}
                  </Text>
                )}
              </>
            ) : (
              <Button
                size="xs"
                variant="light"
                loading={busy === 'classroom'}
                onClick={() =>
                  run(
                    'classroom',
                    () => forgekeyAPI.enableClassroomMode(mode.id),
                    'Classroom mode enabled.',
                  )
                }
              >
                Enable classroom mode
              </Button>
            )}
          </div>
        )}

        <div>
          <Text size="sm" fw={500} mb="xs">
            Authorized users ({auths.length})
          </Text>
          {auths.length === 0 ? (
            <Text size="xs" c="dimmed">
              No active authorizations.
            </Text>
          ) : (
            <Stack gap="xs">
              {auths.map((a) => (
                <Group key={a.id} justify="space-between" wrap="nowrap">
                  <div>
                    <Text size="sm">{a.username}</Text>
                    {a.authorized_by_username && (
                      <Text size="xs" c="dimmed">
                        by {a.authorized_by_username}
                      </Text>
                    )}
                  </div>
                  <Button
                    size="xs"
                    variant="light"
                    color="red"
                    loading={busy === `revoke-${a.id}`}
                    onClick={() =>
                      confirmAction(
                        'Revoke authorization',
                        `Revoke ${a.username}'s access to this asset?`,
                        () =>
                          run(
                            `revoke-${a.id}`,
                            () => forgekeyAPI.revokeAuthorization(a.id),
                            'Authorization revoked.',
                          ),
                        { color: 'red' },
                      )
                    }
                  >
                    Revoke
                  </Button>
                </Group>
              ))}
            </Stack>
          )}
        </div>

        <div>
          <Text size="sm" fw={500} mb="xs">
            Active lockouts ({lockouts.length})
          </Text>
          {lockouts.length === 0 ? (
            <Text size="xs" c="dimmed">
              No active lockouts.
            </Text>
          ) : (
            <Stack gap="xs">
              {lockouts.map((l) => (
                <Group key={l.id} justify="space-between" wrap="nowrap">
                  <div>
                    <Text size="sm">
                      {l.locked_by_username ?? 'unknown'} · {l.lockout_level}
                    </Text>
                    {l.reason && (
                      <Text size="xs" c="dimmed">
                        {l.reason}
                      </Text>
                    )}
                  </div>
                  <Button
                    size="xs"
                    variant="light"
                    loading={busy === `unlock-${l.id}`}
                    onClick={() =>
                      confirmAction(
                        'Unlock device',
                        'Clear this lockout? You must have sufficient permissions.',
                        () =>
                          run(
                            `unlock-${l.id}`,
                            () => forgekeyAPI.unlockLockout(l.id),
                            'Lockout cleared.',
                          ),
                      )
                    }
                  >
                    Unlock
                  </Button>
                </Group>
              ))}
            </Stack>
          )}
        </div>
      </Stack>
    </Card>
  );
}
