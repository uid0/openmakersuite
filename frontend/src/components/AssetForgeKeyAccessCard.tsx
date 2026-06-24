/**
 * AssetForgeKeyAccessCard
 *
 * Surfaces the ForgeKey access controls for a single inventory Asset on the
 * asset detail page (#7b):
 *  - Operational mode + classroom-mode toggle (enable / disable)
 *  - Authorized users, each revocable, with a staff grant flow (user picker)
 *    (access-control interlock, op-tup)
 *  - Active device lockouts, each unlockable (server enforces the unlock
 *    permission hierarchy; a 403 surfaces as an error toast)
 *
 * The card renders nothing for assets that have no ForgeKey relationship (no
 * operational mode, no active authorizations, no active lockouts), so it's a
 * no-op on ordinary inventory assets.
 */
import { Badge, Button, Card, Group, Select, Stack, Text, Textarea } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  DirectoryUser,
  ForgeKeyAssetAuthorization,
  ForgeKeyDeviceLockout,
  ForgeKeyOperationalMode,
  forgekeyAPI,
  userAPI,
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

// Staff/superuser can grant/revoke; mirrors the gate used by the ForgeKey pages.
function isStaffUser(): boolean {
  if (typeof window === 'undefined') return false;
  return (
    localStorage.getItem('is_staff') === 'true' ||
    localStorage.getItem('is_superuser') === 'true'
  );
}

function formatDate(value: string | null): string {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString();
}

const MODE_COLORS: Record<string, string> = {
  available: 'green',
  classroom: 'blue',
  maintenance: 'yellow',
  locked_out: 'red',
};

export default function AssetForgeKeyAccessCard({ assetId }: Props) {
  const isStaff = isStaffUser();
  const [mode, setMode] = useState<ForgeKeyOperationalMode | null>(null);
  const [auths, setAuths] = useState<ForgeKeyAssetAuthorization[]>([]);
  const [lockouts, setLockouts] = useState<ForgeKeyDeviceLockout[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  // Grant flow (staff only).
  const [grantOpen, setGrantOpen] = useState(false);
  const [userOptions, setUserOptions] = useState<DirectoryUser[]>([]);
  const [userSearch, setUserSearch] = useState('');
  const [debouncedSearch] = useDebouncedValue(userSearch, 250);
  const [selectedUser, setSelectedUser] = useState<string | null>(null);
  const [grantNotes, setGrantNotes] = useState('');

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

  // Lazily load members for the picker only once a staffer opens the grant
  // panel; re-fetch (server-side search) as they type.
  useEffect(() => {
    if (!grantOpen) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const res = await userAPI.listUsers({ search: debouncedSearch || undefined });
        if (!cancelled) setUserOptions(unwrap<DirectoryUser>(res.data));
      } catch (err) {
        if (!cancelled) showError(extractErrorMessage(err, 'Failed to load members.'));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [grantOpen, debouncedSearch]);

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

  const userSelectData = useMemo(
    () =>
      userOptions.map((u) => ({
        value: String(u.id),
        label:
          u.full_name && u.full_name !== u.username
            ? `${u.full_name} (${u.username})`
            : u.username,
      })),
    [userOptions],
  );

  const resetGrant = () => {
    setGrantOpen(false);
    setSelectedUser(null);
    setGrantNotes('');
    setUserSearch('');
  };

  const handleGrant = async () => {
    if (!selectedUser) return;
    setBusy('grant');
    try {
      await forgekeyAPI.grantAuthorization({
        asset: assetId,
        user: Number(selectedUser),
        notes: grantNotes.trim() || undefined,
      });
      showSuccess('Access granted.');
      resetGrant();
      await load();
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to grant access.'));
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
                    <Text size="xs" c="dimmed">
                      {a.authorized_by_username ? `by ${a.authorized_by_username}` : 'granted'}
                      {formatDate(a.authorized_at) && ` · ${formatDate(a.authorized_at)}`}
                    </Text>
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

          {isStaff && (
            <div style={{ marginTop: 'var(--mantine-spacing-sm)' }}>
              {!grantOpen ? (
                <Button
                  size="xs"
                  variant="light"
                  onClick={() => setGrantOpen(true)}
                  data-testid="grant-open"
                >
                  Grant access
                </Button>
              ) : (
                <Stack gap="xs" data-testid="grant-panel">
                  <Select
                    placeholder="Search members…"
                    searchable
                    data={userSelectData}
                    value={selectedUser}
                    onChange={setSelectedUser}
                    searchValue={userSearch}
                    onSearchChange={setUserSearch}
                    filter={({ options }) => options}
                    nothingFoundMessage="No members found"
                    aria-label="Member"
                    data-testid="grant-user-select"
                  />
                  <Textarea
                    placeholder="Notes (optional)"
                    autosize
                    minRows={1}
                    value={grantNotes}
                    onChange={(e) => setGrantNotes(e.currentTarget.value)}
                    data-testid="grant-notes"
                  />
                  <Group gap="xs">
                    <Button
                      size="xs"
                      loading={busy === 'grant'}
                      disabled={!selectedUser}
                      onClick={handleGrant}
                      data-testid="grant-submit"
                    >
                      Grant
                    </Button>
                    <Button size="xs" variant="subtle" onClick={resetGrant}>
                      Cancel
                    </Button>
                  </Group>
                </Stack>
              )}
            </div>
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
