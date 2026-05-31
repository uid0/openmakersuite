/**
 * ForgeKey Firmware Rollouts — staff campaign management.
 *
 * Create a staged rollout of a firmware version: pick the version and how
 * aggressively to roll it out (% of the fleet per interval). The rollout then
 * advances in waves automatically; staff can start, pause/resume, cancel, or
 * advance a wave by hand and watch progress.
 */
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  NumberInput,
  Paper,
  Progress,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import React, { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import {
  ForgeKeyFirmwareRollout,
  ForgeKeyFirmwareVersion,
  forgekeyAPI,
} from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const POLL_INTERVAL_MS = 30_000;

const STATUS_COLORS: Record<string, string> = {
  draft: 'gray',
  active: 'blue',
  paused: 'yellow',
  completed: 'green',
  cancelled: 'red',
};

const asList = <T,>(data: { results?: T[] } | T[]): T[] =>
  Array.isArray(data) ? data : data.results ?? [];

const ForgeKeyFirmwareRolloutsPage: React.FC = () => {
  const isStaff = typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' && localStorage.getItem('is_superuser') === 'true';

  const [rollouts, setRollouts] = useState<ForgeKeyFirmwareRollout[]>([]);
  const [versions, setVersions] = useState<ForgeKeyFirmwareVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Create form.
  const [formVersion, setFormVersion] = useState<string | null>(null);
  const [formBatch, setFormBatch] = useState<number>(20);
  const [formInterval, setFormInterval] = useState<number>(60);
  const [formName, setFormName] = useState('');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!isStaff && !isSuperuser) return undefined;
    let cancelled = false;
    const loadRollouts = async () => {
      try {
        const res = await forgekeyAPI.listFirmwareRollouts();
        if (!cancelled) {
          setRollouts(asList(res.data));
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(extractErrorMessage(err, 'Failed to load rollouts.'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    forgekeyAPI
      .listFirmwareVersions()
      .then((res) => {
        if (!cancelled) setVersions(asList(res.data).filter((v) => v.is_active !== false));
      })
      .catch(() => undefined);
    loadRollouts();
    const handle = window.setInterval(loadRollouts, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [isStaff, isSuperuser]);

  if (!isStaff && !isSuperuser) {
    return <Navigate to="/" replace />;
  }

  const runAction = async (
    id: string,
    fn: (id: string) => Promise<{ data: ForgeKeyFirmwareRollout }>,
  ) => {
    setBusyId(id);
    try {
      const res = await fn(id);
      setRollouts((prev) => prev.map((r) => (r.id === id ? res.data : r)));
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Action failed.'));
    } finally {
      setBusyId(null);
    }
  };

  const handleCreate = async () => {
    if (!formVersion) {
      setError('Pick a firmware version first.');
      return;
    }
    setCreating(true);
    try {
      const res = await forgekeyAPI.createFirmwareRollout({
        firmware_version: formVersion,
        batch_size_percent: formBatch,
        interval_minutes: formInterval,
        name: formName || undefined,
      });
      setRollouts((prev) => [res.data, ...prev]);
      setFormVersion(null);
      setFormName('');
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to create the rollout.'));
    } finally {
      setCreating(false);
    }
  };

  const pct = (n: number, total: number) => (total > 0 ? (n / total) * 100 : 0);

  return (
    <WorkspacePage
      testId="forgekey-rollouts-page"
      hero={{
        eyebrow: 'Facilities · ForgeKey',
        title: 'Firmware rollouts',
        description:
          'Stage a firmware version across the fleet — set how aggressively to roll it out and watch each wave land.',
      }}
    >
      {error && (
        <Alert color="red" variant="light" data-testid="rollouts-error">
          {error}
        </Alert>
      )}

      {/* Create campaign */}
      <Paper p="md" withBorder data-testid="rollout-create">
        <Title order={5} mb="sm">
          New rollout
        </Title>
        <Group align="flex-end" gap="md" wrap="wrap">
          <Select
            label="Firmware version"
            placeholder={versions.length ? 'Pick a version' : 'No active versions'}
            data={versions.map((v) => ({
              value: v.id,
              label: `${v.version}${v.device_type_name ? ` · ${v.device_type_name}` : ''}`,
            }))}
            value={formVersion}
            onChange={setFormVersion}
            searchable
            style={{ minWidth: 240 }}
          />
          <NumberInput
            label="Batch size"
            description="% of fleet per wave"
            min={1}
            max={100}
            value={formBatch}
            onChange={(v) => setFormBatch(typeof v === 'number' ? v : 20)}
            style={{ width: 140 }}
          />
          <NumberInput
            label="Interval"
            description="minutes between waves"
            min={1}
            value={formInterval}
            onChange={(v) => setFormInterval(typeof v === 'number' ? v : 60)}
            style={{ width: 160 }}
          />
          <TextInput
            label="Name (optional)"
            value={formName}
            onChange={(e) => setFormName(e.currentTarget.value)}
            style={{ minWidth: 180 }}
          />
          <Button onClick={handleCreate} loading={creating} disabled={!formVersion}>
            Create draft
          </Button>
        </Group>
      </Paper>

      {loading ? (
        <Group justify="center" p="xl">
          <Loader />
        </Group>
      ) : rollouts.length === 0 ? (
        <Paper p="xl" withBorder>
          <Text c="dimmed" data-testid="rollouts-empty">
            No rollouts yet. Create one above to stage a firmware version.
          </Text>
        </Paper>
      ) : (
        <SimpleGrid cols={{ base: 1, lg: 2 }}>
          {rollouts.map((r) => {
            const p = r.progress;
            const inFlight = p.pending + p.in_progress;
            return (
              <Card withBorder p="md" radius="md" key={r.id} data-testid={`rollout-${r.id}`}>
                <Group justify="space-between" mb="xs">
                  <div>
                    <Text fw={600}>{r.firmware_version_string}</Text>
                    <Text size="xs" c="dimmed">
                      {r.device_type_name}
                      {r.name ? ` · ${r.name}` : ''}
                    </Text>
                  </div>
                  <Badge color={STATUS_COLORS[r.status] || 'gray'} data-testid={`rollout-status-${r.id}`}>
                    {r.status}
                  </Badge>
                </Group>

                <Text size="sm" c="dimmed" mb={4}>
                  {r.batch_size_percent}% of the fleet every {r.interval_minutes} min
                </Text>

                <Progress.Root size="lg" mb={4}>
                  <Progress.Section value={pct(p.on_target, p.total)} color="green" />
                  <Progress.Section value={pct(inFlight, p.total)} color="blue" />
                </Progress.Root>
                <Text size="xs" c="dimmed" mb="sm">
                  {p.on_target} on target · {inFlight} in flight · {p.remaining} remaining
                  {p.total ? ` of ${p.total}` : ''}
                  {p.failed > 0 ? ` · ${p.failed} failed` : ''}
                </Text>

                <Group gap="xs">
                  {r.status === 'draft' && (
                    <Button
                      size="xs"
                      loading={busyId === r.id}
                      onClick={() => runAction(r.id, forgekeyAPI.startRollout)}
                    >
                      Start
                    </Button>
                  )}
                  {r.status === 'paused' && (
                    <Button
                      size="xs"
                      loading={busyId === r.id}
                      onClick={() => runAction(r.id, forgekeyAPI.startRollout)}
                    >
                      Resume
                    </Button>
                  )}
                  {r.status === 'active' && (
                    <>
                      <Button
                        size="xs"
                        variant="light"
                        loading={busyId === r.id}
                        onClick={() => runAction(r.id, forgekeyAPI.advanceRollout)}
                      >
                        Advance now
                      </Button>
                      <Button
                        size="xs"
                        variant="light"
                        color="yellow"
                        loading={busyId === r.id}
                        onClick={() => runAction(r.id, forgekeyAPI.pauseRollout)}
                      >
                        Pause
                      </Button>
                    </>
                  )}
                  {(r.status === 'active' || r.status === 'paused') && (
                    <Button
                      size="xs"
                      variant="subtle"
                      color="red"
                      loading={busyId === r.id}
                      onClick={() => runAction(r.id, forgekeyAPI.cancelRollout)}
                    >
                      Cancel
                    </Button>
                  )}
                </Group>
              </Card>
            );
          })}
        </SimpleGrid>
      )}
    </WorkspacePage>
  );
};

export default ForgeKeyFirmwareRolloutsPage;
