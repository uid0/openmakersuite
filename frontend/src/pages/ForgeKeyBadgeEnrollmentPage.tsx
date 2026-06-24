/**
 * ForgeKeyBadgeEnrollmentPage — staff member/badge admin (op-tup).
 *
 * Lists members (staff-only directory) with their current access badge and
 * lets staff:
 *  - **Enroll a badge** by arming "enroll next scan" for a member and prompting
 *    them to tap the reader; the access-control interlock binds the captured
 *    UID and this page polls until it sees the capture.
 *  - **Set a badge manually** (fallback when no reader is handy).
 *  - **Clear a badge.**
 *
 * Staff-gated client-side (the backend enforces it too); non-staff are bounced
 * to the home page like the other ForgeKey admin pages.
 */
import { Alert, Badge, Button, Card, Group, Loader, Stack, Text, TextInput } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import React, { useCallback, useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { DirectoryUser, forgekeyAPI, userAPI } from '../services/api';
import { confirmAction, showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const POLL_MS = 2000;

type EnrollStatus = 'waiting' | 'captured' | 'expired';

function unwrap<T>(data: { results?: T[] } | T[]): T[] {
  if (Array.isArray(data)) return data;
  return data.results ?? [];
}

function isStaffUser(): boolean {
  if (typeof window === 'undefined') return false;
  return (
    localStorage.getItem('is_staff') === 'true' ||
    localStorage.getItem('is_superuser') === 'true'
  );
}

const ForgeKeyBadgeEnrollmentPage: React.FC = () => {
  const isStaff = isStaffUser();

  const [users, setUsers] = useState<DirectoryUser[]>([]);
  const [search, setSearch] = useState('');
  const [debouncedSearch] = useDebouncedValue(search, 250);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [editingUserId, setEditingUserId] = useState<number | null>(null);
  const [badgeDraft, setBadgeDraft] = useState('');

  const [enrollUserId, setEnrollUserId] = useState<number | null>(null);
  const [enrollStatus, setEnrollStatus] = useState<EnrollStatus | null>(null);
  const [capturedBadge, setCapturedBadge] = useState<string | null>(null);

  const loadUsers = useCallback(async () => {
    setLoading(true);
    try {
      const res = await userAPI.listUsers({ search: debouncedSearch || undefined });
      setUsers(unwrap<DirectoryUser>(res.data));
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load members.'));
    } finally {
      setLoading(false);
    }
  }, [debouncedSearch]);

  useEffect(() => {
    if (isStaff) loadUsers();
  }, [isStaff, loadUsers]);

  const setBadgeOnRow = (userId: number, badge: string | null) =>
    setUsers((prev) =>
      prev.map((u) => (u.id === userId ? { ...u, badge_number: badge } : u)),
    );

  // Poll for a captured scan while an enrollment is armed. Recursive setTimeout
  // (not setInterval) so we never schedule another poll after we stop.
  useEffect(() => {
    if (enrollUserId == null) return undefined;
    let stopped = false;
    let handle: number | undefined;
    const tick = async () => {
      if (stopped) return;
      try {
        const res = await forgekeyAPI.getBadgeEnrollmentState({ userId: enrollUserId });
        if (stopped) return;
        const state = res.data;
        if (state.captured) {
          stopped = true;
          setCapturedBadge(state.captured.badge_number);
          setEnrollStatus('captured');
          setBadgeOnRow(enrollUserId, state.captured.badge_number);
          showSuccess(`Badge ${state.captured.badge_number} enrolled.`);
          return;
        }
        if (!state.armed) {
          stopped = true;
          setEnrollStatus('expired');
          return;
        }
      } catch (err) {
        stopped = true;
        showError(extractErrorMessage(err, 'Enrollment check failed.'));
        return;
      }
      handle = window.setTimeout(tick, POLL_MS);
    };
    tick();
    return () => {
      stopped = true;
      if (handle) window.clearTimeout(handle);
    };
  }, [enrollUserId]);

  if (!isStaff) {
    return <Navigate to="/" replace />;
  }

  const startEnroll = async (userId: number) => {
    setEditingUserId(null);
    setCapturedBadge(null);
    setEnrollStatus(null);
    try {
      await forgekeyAPI.armBadgeEnrollment({ userId });
      setEnrollStatus('waiting');
      setEnrollUserId(userId);
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to arm enrollment.'));
    }
  };

  const cancelEnroll = async () => {
    setEnrollUserId(null);
    setEnrollStatus(null);
    setCapturedBadge(null);
    try {
      await forgekeyAPI.cancelBadgeEnrollment();
    } catch {
      /* best-effort disarm; the armed record TTL-expires on its own anyway */
    }
  };

  const openManual = (user: DirectoryUser) => {
    setEnrollUserId(null);
    setEnrollStatus(null);
    setEditingUserId(user.id);
    setBadgeDraft(user.badge_number ?? '');
  };

  const saveManual = async (userId: number) => {
    const value = badgeDraft.trim();
    try {
      const res = await forgekeyAPI.setBadge({ userId, badgeNumber: value || null });
      setBadgeOnRow(userId, res.data.badge_number);
      showSuccess(value ? `Badge set to ${value}.` : 'Badge cleared.');
      setEditingUserId(null);
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to set badge.'));
    }
  };

  const clearBadge = (userId: number) =>
    confirmAction(
      'Clear badge',
      'Remove this member’s badge? They will no longer be able to scan in.',
      async () => {
        try {
          await forgekeyAPI.setBadge({ userId, badgeNumber: null });
          setBadgeOnRow(userId, null);
          showSuccess('Badge cleared.');
        } catch (err) {
          showError(extractErrorMessage(err, 'Failed to clear badge.'));
        }
      },
      { color: 'red' },
    );

  return (
    <WorkspacePage
      testId="forgekey-badge-enrollment-page"
      hero={{
        eyebrow: 'Facilities · ForgeKey',
        title: 'Badge enrollment',
        description:
          'Assign access badges to members — arm a reader for the next tap, or enter a UID by hand.',
      }}
    >
      <TextInput
        placeholder="Search members by name, username, or email…"
        value={search}
        onChange={(e) => setSearch(e.currentTarget.value)}
        data-testid="badge-user-search"
        mb="md"
      />

      {error && (
        <Alert color="red" variant="light" data-testid="badge-error" mb="md">
          {error}
        </Alert>
      )}

      {loading && users.length === 0 ? (
        <Group justify="center" p="xl">
          <Loader />
        </Group>
      ) : users.length === 0 ? (
        <Text c="dimmed" data-testid="badge-empty">
          No members found.
        </Text>
      ) : (
        <Stack gap="sm">
          {users.map((u) => (
            <Card key={u.id} withBorder p="sm" radius="md" data-testid={`user-row-${u.id}`}>
              <Group justify="space-between" wrap="nowrap" align="flex-start">
                <div>
                  <Text fw={500}>
                    {u.full_name}{' '}
                    <Text span c="dimmed" size="sm">
                      ({u.username})
                    </Text>
                  </Text>
                  <Group gap="xs" mt={4}>
                    <Text size="sm">Badge:</Text>
                    {u.badge_number ? (
                      <Badge variant="light" data-testid={`badge-value-${u.id}`}>
                        {u.badge_number}
                      </Badge>
                    ) : (
                      <Text size="sm" c="dimmed" data-testid={`badge-value-${u.id}`}>
                        none
                      </Text>
                    )}
                  </Group>
                </div>
                <Group gap="xs" wrap="nowrap">
                  {enrollUserId === u.id ? null : (
                    <>
                      <Button
                        size="xs"
                        onClick={() => startEnroll(u.id)}
                        data-testid={`enroll-${u.id}`}
                      >
                        Enroll badge
                      </Button>
                      <Button
                        size="xs"
                        variant="light"
                        onClick={() => openManual(u)}
                        data-testid={`set-${u.id}`}
                      >
                        Set manually
                      </Button>
                      {u.badge_number && (
                        <Button
                          size="xs"
                          variant="light"
                          color="red"
                          onClick={() => clearBadge(u.id)}
                          data-testid={`clear-${u.id}`}
                        >
                          Clear
                        </Button>
                      )}
                    </>
                  )}
                </Group>
              </Group>

              {editingUserId === u.id && (
                <Group mt="sm" gap="xs" data-testid={`manual-${u.id}`}>
                  <TextInput
                    placeholder="Badge UID"
                    value={badgeDraft}
                    onChange={(e) => setBadgeDraft(e.currentTarget.value)}
                    data-testid="manual-input"
                    style={{ flex: 1 }}
                  />
                  <Button size="xs" onClick={() => saveManual(u.id)} data-testid="manual-save">
                    Save
                  </Button>
                  <Button size="xs" variant="subtle" onClick={() => setEditingUserId(null)}>
                    Cancel
                  </Button>
                </Group>
              )}

              {enrollUserId === u.id && (
                <Group mt="sm" gap="xs" data-testid={`enroll-status-${u.id}`}>
                  {enrollStatus === 'waiting' && (
                    <Text size="sm" c="blue">
                      Ask {u.full_name || u.username} to tap their badge on the reader…
                    </Text>
                  )}
                  {enrollStatus === 'captured' && (
                    <Text size="sm" c="green">
                      Captured badge {capturedBadge}.
                    </Text>
                  )}
                  {enrollStatus === 'expired' && (
                    <Text size="sm" c="red">
                      No tap detected — enrollment expired. Try again.
                    </Text>
                  )}
                  <Button size="xs" variant="subtle" onClick={cancelEnroll}>
                    {enrollStatus === 'captured' || enrollStatus === 'expired' ? 'Done' : 'Cancel'}
                  </Button>
                </Group>
              )}
            </Card>
          ))}
        </Stack>
      )}
    </WorkspacePage>
  );
};

export default ForgeKeyBadgeEnrollmentPage;
