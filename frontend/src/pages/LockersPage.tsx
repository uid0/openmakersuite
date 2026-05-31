/**
 * ForgeKey Lockers — staff monitoring console.
 *
 * Live status of the locker fleet from the firmware's cabinet_lock/status
 * heartbeat: secure / online / state per locker, with possible intrusions
 * (ALARM or a sustained not-secure reading) flagged. Refreshes every 30s.
 * Staff can also unlock a locker (signs + publishes the ES256 command) and
 * issue / revoke one-time access codes.
 */
import {
  Alert,
  Badge,
  Box,
  Button,
  Card,
  CopyButton,
  Group,
  Loader,
  Modal,
  Paper,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Navigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import LockerSetupDrawer from '../components/LockerSetupDrawer';
import { ForgeKeyLocker, ForgeKeyLockerOtp, lockersAPI } from '../services/api';
import { showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const POLL_INTERVAL_MS = 30_000;

const STATE_COLORS: Record<string, string> = {
  SECURE: 'green',
  UNLOCKED: 'blue',
  ACCESSING: 'blue',
  ALARM: 'red',
  INITIALIZING: 'gray',
};

const asList = <T,>(data: { results?: T[] } | T[]): T[] =>
  Array.isArray(data) ? data : data.results ?? [];

const formatRelative = (iso: string | null): string => {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
};

const needsAttention = (lk: ForgeKeyLocker): boolean =>
  Boolean(lk.status && (lk.status.is_alarm || lk.status.is_insecure));

const StatCard: React.FC<{ label: string; value: React.ReactNode; color?: string; testId?: string }> = ({
  label,
  value,
  color,
  testId,
}) => (
  <Card withBorder p="md" radius="md" data-testid={testId}>
    <Text size="sm" c="dimmed" fw={500}>
      {label}
    </Text>
    <Text size="xl" fw={700} c={color}>
      {value}
    </Text>
  </Card>
);

const LockersPage: React.FC = () => {
  const isStaff = typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' && localStorage.getItem('is_superuser') === 'true';

  const [lockers, setLockers] = useState<ForgeKeyLocker[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unlockingId, setUnlockingId] = useState<string | null>(null);
  const [otpLocker, setOtpLocker] = useState<ForgeKeyLocker | null>(null);
  const [otps, setOtps] = useState<ForgeKeyLockerOtp[]>([]);
  const [otpsLoading, setOtpsLoading] = useState(false);
  const [issuing, setIssuing] = useState(false);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [setupLocker, setSetupLocker] = useState<ForgeKeyLocker | null>(null);

  const reload = useCallback(async () => {
    try {
      const res = await lockersAPI.listLockers();
      setLockers(asList(res.data));
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load lockers.'));
    }
  }, []);

  useEffect(() => {
    if (!isStaff && !isSuperuser) return undefined;
    let cancelled = false;
    const load = async () => {
      try {
        const res = await lockersAPI.listLockers();
        if (!cancelled) {
          setLockers(asList(res.data));
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(extractErrorMessage(err, 'Failed to load lockers.'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const handle = window.setInterval(load, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [isStaff, isSuperuser]);

  const summary = useMemo(() => {
    const total = lockers.length;
    const secure = lockers.filter((l) => l.status?.secure === true).length;
    const attention = lockers.filter(needsAttention).length;
    return { total, secure, attention };
  }, [lockers]);

  const handleUnlock = async (lk: ForgeKeyLocker) => {
    setUnlockingId(lk.id);
    try {
      await lockersAPI.unlock(lk.id);
      showSuccess(`Unlock command sent to ${lk.name}.`);
    } catch (err) {
      showError(extractErrorMessage(err, 'Unlock failed.'));
    } finally {
      setUnlockingId(null);
    }
  };

  const openOtps = async (lk: ForgeKeyLocker) => {
    setOtpLocker(lk);
    setOtps([]);
    setOtpsLoading(true);
    try {
      const res = await lockersAPI.listOtps(lk.id);
      setOtps(res.data);
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to load access codes.'));
    } finally {
      setOtpsLoading(false);
    }
  };

  const handleIssueOtp = async () => {
    if (!otpLocker) return;
    setIssuing(true);
    try {
      const res = await lockersAPI.issueOtp(otpLocker.id);
      setOtps((prev) => [res.data, ...prev]);
      showSuccess(`New access code: ${res.data.code}`);
    } catch (err) {
      showError(extractErrorMessage(err, 'Could not issue an access code.'));
    } finally {
      setIssuing(false);
    }
  };

  const handleRevokeOtp = async (otpId: string) => {
    if (!otpLocker) return;
    setRevokingId(otpId);
    try {
      const res = await lockersAPI.revokeOtp(otpLocker.id, otpId);
      setOtps((prev) => prev.map((o) => (o.id === otpId ? res.data : o)));
    } catch (err) {
      showError(extractErrorMessage(err, 'Revoke failed.'));
    } finally {
      setRevokingId(null);
    }
  };

  if (!isStaff && !isSuperuser) {
    return <Navigate to="/" replace />;
  }

  return (
    <WorkspacePage
      testId="lockers-page"
      hero={{
        eyebrow: 'Facilities · ForgeKey',
        title: 'Lockers',
        description:
          'Live status of every ForgeKey-gated locker — secure / online state, with possible intrusions flagged.',
      }}
    >
      {error && (
        <Alert color="red" variant="light" data-testid="lockers-error">
          {error}
        </Alert>
      )}

      {loading && lockers.length === 0 ? (
        <Group justify="center" p="xl">
          <Loader />
        </Group>
      ) : (
        <Stack gap="lg">
          <SimpleGrid cols={{ base: 3 }} data-testid="locker-stats">
            <StatCard label="Lockers" value={summary.total} testId="stat-total" />
            <StatCard label="Secure" value={summary.secure} color="green" testId="stat-secure" />
            <StatCard
              label="Needs attention"
              value={summary.attention}
              color={summary.attention > 0 ? 'red' : undefined}
              testId="stat-attention"
            />
          </SimpleGrid>

          <Group justify="space-between">
            <Title order={4}>Locker fleet</Title>
            <Button
              onClick={() => {
                setSetupLocker(null);
                setSetupOpen(true);
              }}
              data-testid="new-locker"
            >
              New locker
            </Button>
          </Group>

          {lockers.length === 0 ? (
            <Paper p="xl" withBorder>
              <Text c="dimmed" data-testid="lockers-empty">
                No lockers have been configured yet.
              </Text>
            </Paper>
          ) : (
            <Box>
              <Paper withBorder>
                <Table.ScrollContainer minWidth={820}>
                  <Table highlightOnHover>
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th>Locker</Table.Th>
                        <Table.Th>Location</Table.Th>
                        <Table.Th>SIG</Table.Th>
                        <Table.Th>Stored asset</Table.Th>
                        <Table.Th>Lock state</Table.Th>
                        <Table.Th>Online</Table.Th>
                        <Table.Th>Updated</Table.Th>
                        <Table.Th>Actions</Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {lockers.map((lk) => {
                        const attention = needsAttention(lk);
                        const state = lk.status?.state || '';
                        return (
                          <Table.Tr
                            key={lk.id}
                            data-testid={`locker-row-${lk.id}`}
                            style={{ backgroundColor: attention ? '#fff0f0' : undefined }}
                          >
                            <Table.Td>
                              <Text fw={500}>{lk.name}</Text>
                              {attention && (
                                <Badge color="red" size="sm" variant="light">
                                  {lk.status?.is_alarm ? 'Alarm' : 'Not secure'}
                                </Badge>
                              )}
                            </Table.Td>
                            <Table.Td>{lk.location_name || '—'}</Table.Td>
                            <Table.Td>{lk.owning_sig_name || '—'}</Table.Td>
                            <Table.Td>{lk.current_asset_name || '—'}</Table.Td>
                            <Table.Td>
                              {state ? (
                                <Badge color={STATE_COLORS[state] || 'gray'}>{state}</Badge>
                              ) : (
                                <Text size="sm" c="dimmed">
                                  no status
                                </Text>
                              )}
                            </Table.Td>
                            <Table.Td>
                              {lk.status?.device_is_online == null ? (
                                <Text size="sm" c="dimmed">
                                  —
                                </Text>
                              ) : (
                                <Badge color={lk.status.device_is_online ? 'green' : 'gray'}>
                                  {lk.status.device_is_online ? 'Online' : 'Offline'}
                                </Badge>
                              )}
                            </Table.Td>
                            <Table.Td>
                              <Text size="xs" c="dimmed">
                                {formatRelative(lk.status?.last_status_at ?? null)}
                              </Text>
                            </Table.Td>
                            <Table.Td onClick={(e) => e.stopPropagation()}>
                              <Group gap="xs">
                                <Button
                                  size="xs"
                                  loading={unlockingId === lk.id}
                                  onClick={() => handleUnlock(lk)}
                                  data-testid={`unlock-${lk.id}`}
                                >
                                  Unlock
                                </Button>
                                <Button
                                  size="xs"
                                  variant="light"
                                  onClick={() => openOtps(lk)}
                                  data-testid={`otps-${lk.id}`}
                                >
                                  Codes
                                </Button>
                                <Button
                                  size="xs"
                                  variant="default"
                                  onClick={() => {
                                    setSetupLocker(lk);
                                    setSetupOpen(true);
                                  }}
                                  data-testid={`setup-${lk.id}`}
                                >
                                  Setup
                                </Button>
                              </Group>
                            </Table.Td>
                          </Table.Tr>
                        );
                      })}
                    </Table.Tbody>
                  </Table>
                </Table.ScrollContainer>
              </Paper>
            </Box>
          )}
        </Stack>
      )}

      <Modal
        opened={otpLocker !== null}
        onClose={() => setOtpLocker(null)}
        title={otpLocker ? `Access codes — ${otpLocker.name}` : 'Access codes'}
      >
        <Stack gap="sm" data-testid="otp-modal">
          <Button onClick={handleIssueOtp} loading={issuing} data-testid="issue-otp">
            Issue new code
          </Button>
          {otpsLoading ? (
            <Group justify="center" p="md">
              <Loader size="sm" />
            </Group>
          ) : otps.length === 0 ? (
            <Text size="sm" c="dimmed">
              No access codes yet.
            </Text>
          ) : (
            <Table>
              <Table.Tbody>
                {otps.map((otp) => (
                  <Table.Tr key={otp.id}>
                    <Table.Td>
                      <Group gap="xs">
                        <Text fw={600} ff="monospace">
                          {otp.code}
                        </Text>
                        <CopyButton value={otp.code}>
                          {({ copied, copy }) => (
                            <Button size="compact-xs" variant="subtle" onClick={copy}>
                              {copied ? 'Copied' : 'Copy'}
                            </Button>
                          )}
                        </CopyButton>
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      <Badge
                        size="sm"
                        color={
                          otp.state === 'active' ? 'green' : otp.state === 'used' ? 'blue' : 'gray'
                        }
                      >
                        {otp.state}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      {otp.state === 'active' && (
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          color="red"
                          loading={revokingId === otp.id}
                          onClick={() => handleRevokeOtp(otp.id)}
                        >
                          Revoke
                        </Button>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </Stack>
      </Modal>

      <LockerSetupDrawer
        opened={setupOpen}
        onClose={() => setSetupOpen(false)}
        locker={setupLocker}
        onSaved={reload}
      />
    </WorkspacePage>
  );
};

export default LockersPage;
