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
  Code,
  Group,
  Loader,
  Modal,
  NumberInput,
  Paper,
  Progress,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import React, { useEffect, useRef, useState } from 'react';
import { Navigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import {
  ForgeKeyDeviceType,
  ForgeKeyFirmwareBuild,
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

const BUILD_STATUS_COLORS: Record<string, string> = {
  queued: 'gray',
  building: 'blue',
  succeeded: 'green',
  failed: 'red',
  cancelled: 'gray',
};

// Default PlatformIO env per device-type code (matches the firmware's
// platformio.ini). Picking a device type pre-fills the env; the operator can
// still override it.
const PIO_ENV_BY_CODE: Record<string, string> = {
  epaper_screen: 'seeed_xiao_epaper',
  temperature_sensor: 'seeed_xiao_esp32s3_temperature',
  env_sensor: 'seeed_xiao_esp32s3_temperature',
};
const DEFAULT_PIO_ENV = 'seeed_xiao_esp32s3';

/** Default PlatformIO env for a device-type code (overridable in the form). */
export const defaultPioEnv = (code: string): string => PIO_ENV_BY_CODE[code] ?? DEFAULT_PIO_ENV;

/**
 * Pick the firmware-version id to auto-select in the New Rollout form when a
 * build *newly* succeeds. Returns the `firmware_version` of the most recently
 * completed not-yet-seen succeeded build (mutating `seen` to mark the fresh
 * ones handled), or `null` when nothing new succeeded. Seeding `seen` with the
 * builds present on first load keeps page reloads / pre-existing successes from
 * hijacking the form — only a success observed live updates the version.
 */
export const pickFreshlySucceededVersion = (
  builds: ForgeKeyFirmwareBuild[],
  seen: Set<string>,
): string | null => {
  const fresh = builds.filter(
    (b) => b.status === 'succeeded' && b.firmware_version && !seen.has(b.id),
  );
  fresh.forEach((b) => seen.add(b.id));
  if (fresh.length === 0) return null;
  const newest = fresh.reduce((a, b) => ((a.completed_at ?? '') >= (b.completed_at ?? '') ? a : b));
  return newest.firmware_version ?? null;
};

const fmtBuildElapsed = (build: ForgeKeyFirmwareBuild): string | null => {
  if (!build.started_at) return null;
  const start = new Date(build.started_at).getTime();
  const end = build.completed_at
    ? new Date(build.completed_at).getTime()
    : build.status === 'building'
      ? Date.now()
      : null;
  if (end == null) return null;
  const secs = Math.max(0, Math.round((end - start) / 1000));
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
};

const asList = <T,>(data: { results?: T[] } | T[]): T[] =>
  Array.isArray(data) ? data : data.results ?? [];

const ForgeKeyFirmwareRolloutsPage: React.FC = () => {
  const isStaff = typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' && localStorage.getItem('is_superuser') === 'true';

  const [rollouts, setRollouts] = useState<ForgeKeyFirmwareRollout[]>([]);
  const [epaperRollouts, setEpaperRollouts] = useState<ForgeKeyFirmwareRollout[]>([]);
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

  // Firmware build form + list.
  const [builds, setBuilds] = useState<ForgeKeyFirmwareBuild[]>([]);
  const [deviceTypes, setDeviceTypes] = useState<ForgeKeyDeviceType[]>([]);
  const [buildDeviceType, setBuildDeviceType] = useState<string | null>(null);
  const [buildEnv, setBuildEnv] = useState('');
  const [buildVersion, setBuildVersion] = useState('');
  const [buildRef, setBuildRef] = useState('main');
  const [building, setBuilding] = useState(false);
  const [logBuildId, setLogBuildId] = useState<string | null>(null);
  const [redispatchingBuildId, setRedispatchingBuildId] = useState<string | null>(null);

  // Succeeded builds we've already reacted to — seeded on first load so only a
  // build that succeeds while the page is open auto-updates the rollout version.
  const seenSucceededBuilds = useRef<Set<string> | null>(null);

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
    const loadEpaperRollouts = async () => {
      try {
        const res = await forgekeyAPI.listEpaperFirmwareRollouts();
        if (!cancelled) setEpaperRollouts(asList(res.data));
      } catch {
        /* ePaper rollouts are secondary on this page; ignore transient errors */
      }
    };
    const loadVersions = async () => {
      try {
        const res = await forgekeyAPI.listFirmwareVersions();
        if (!cancelled) setVersions(asList(res.data).filter((v) => v.is_active !== false));
      } catch {
        /* versions refresh is best-effort; keep the last good list */
      }
    };
    const loadBuilds = async () => {
      try {
        const res = await forgekeyAPI.listFirmwareBuilds();
        if (cancelled) return;
        const list = asList(res.data);
        setBuilds(list);
        // Auto-update the New Rollout version when a build *newly* succeeds, so a
        // fresh build is one click from a rollout instead of a manual re-pick.
        if (seenSucceededBuilds.current === null) {
          // First load: remember existing successes so reloads don't hijack the form.
          seenSucceededBuilds.current = new Set(
            list.filter((b) => b.status === 'succeeded' && b.firmware_version).map((b) => b.id),
          );
        } else {
          const picked = pickFreshlySucceededVersion(list, seenSucceededBuilds.current);
          if (picked) {
            // Refresh the dropdown first so the just-built version is selectable.
            await loadVersions();
            if (!cancelled) setFormVersion(picked);
          }
        }
      } catch {
        /* builds are secondary; ignore transient errors */
      }
    };
    loadVersions();
    forgekeyAPI
      .listDeviceTypes()
      .then((res) => {
        if (!cancelled) setDeviceTypes(asList(res.data));
      })
      .catch(() => undefined);
    loadRollouts();
    loadEpaperRollouts();
    loadBuilds();
    const handle = window.setInterval(() => {
      loadRollouts();
      loadEpaperRollouts();
      loadVersions();
      loadBuilds();
    }, POLL_INTERVAL_MS);
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

  // ePaper rollouts hit the parallel /epaper-firmware-rollouts/ endpoints
  // — same response shape so the rendering is uniform, but they update a
  // separate state slot so the MQTT rollouts list isn't disturbed.
  const runEpaperAction = async (
    id: string,
    fn: (id: string) => Promise<{ data: ForgeKeyFirmwareRollout }>,
  ) => {
    setBusyId(id);
    try {
      const res = await fn(id);
      setEpaperRollouts((prev) => prev.map((r) => (r.id === id ? res.data : r)));
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
      // ePaper FirmwareVersions need the parallel HTTPS-pull rollout
      // pipeline (panels skip MQTT); everything else takes the standard
      // MQTT push pipeline. The picked version's device_type_code is the
      // routing key — the form is otherwise identical.
      const pickedVersion = versions.find((v) => v.id === formVersion);
      const isEpaper = pickedVersion?.device_type_code === 'epaper_screen';
      const body = {
        firmware_version: formVersion,
        batch_size_percent: formBatch,
        interval_minutes: formInterval,
        name: formName || undefined,
      };
      if (isEpaper) {
        const res = await forgekeyAPI.createEpaperFirmwareRollout(body);
        setEpaperRollouts((prev) => [res.data, ...prev]);
      } else {
        const res = await forgekeyAPI.createFirmwareRollout(body);
        setRollouts((prev) => [res.data, ...prev]);
      }
      setFormVersion(null);
      setFormName('');
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to create the rollout.'));
    } finally {
      setCreating(false);
    }
  };

  const handleBuild = async () => {
    if (!buildDeviceType || !buildEnv.trim() || !buildVersion.trim()) {
      setError('Device type, PlatformIO env, and version are required to build.');
      return;
    }
    setBuilding(true);
    try {
      const res = await forgekeyAPI.createFirmwareBuild({
        device_type: Number(buildDeviceType),
        pio_env: buildEnv.trim(),
        version: buildVersion.trim(),
        source_ref: buildRef.trim() || 'main',
      });
      setBuilds((prev) => [res.data, ...prev]);
      setBuildVersion('');
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to queue the build.'));
    } finally {
      setBuilding(false);
    }
  };

  // Derived from the polled list so the modal stays live while a build runs.
  const logBuild = builds.find((b) => b.id === logBuildId) ?? null;

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

      {/* Build firmware */}
      <Paper p="md" withBorder data-testid="build-create">
        <Title order={5} mb="sm">
          Build firmware
        </Title>
        <Group align="flex-end" gap="md" wrap="wrap">
          <Select
            label="Device type"
            placeholder={deviceTypes.length ? 'Pick a type' : 'No device types'}
            data={deviceTypes.map((d) => ({ value: String(d.id), label: d.name }))}
            value={buildDeviceType}
            onChange={(v) => {
              setBuildDeviceType(v);
              const dt = deviceTypes.find((d) => String(d.id) === v);
              setBuildEnv(dt ? defaultPioEnv(dt.code) : '');
            }}
            searchable
            style={{ minWidth: 180 }}
          />
          <TextInput
            label="PlatformIO env"
            placeholder="seeed_xiao_esp32s3"
            value={buildEnv}
            onChange={(e) => setBuildEnv(e.currentTarget.value)}
            style={{ minWidth: 200 }}
          />
          <TextInput
            label="Version"
            placeholder="2.3.0"
            value={buildVersion}
            onChange={(e) => setBuildVersion(e.currentTarget.value)}
            style={{ width: 140 }}
          />
          <TextInput
            label="Git ref"
            value={buildRef}
            onChange={(e) => setBuildRef(e.currentTarget.value)}
            style={{ width: 140 }}
          />
          <Button
            onClick={handleBuild}
            loading={building}
            disabled={!buildDeviceType || !buildEnv.trim() || !buildVersion.trim()}
            data-testid="build-submit"
          >
            Queue build
          </Button>
        </Group>
        {builds.length > 0 && (
          <Table mt="md" highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Version</Table.Th>
                <Table.Th>Env</Table.Th>
                <Table.Th>Ref</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Requested</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {builds.map((b) => (
                <Table.Tr key={b.id} data-testid={`build-${b.id}`}>
                  <Table.Td>{b.version}</Table.Td>
                  <Table.Td>{b.pio_env}</Table.Td>
                  <Table.Td>{b.source_ref}</Table.Td>
                  <Table.Td>
                    <Badge color={BUILD_STATUS_COLORS[b.status] || 'gray'}>{b.status}</Badge>
                    {b.status === 'failed' && b.error_message && (
                      <Text size="xs" c="red" lineClamp={2}>
                        {b.error_message}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" c="dimmed">
                      {new Date(b.requested_at).toLocaleString()}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      onClick={() => setLogBuildId(b.id)}
                      data-testid={`build-log-${b.id}`}
                    >
                      Log
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        <Text size="xs" c="dimmed" mt="xs">
          Builds run on the self-hosted firmware-builder worker; a succeeded
          build becomes a firmware version you can roll out below. This list
          refreshes every 30s.
        </Text>
      </Paper>

      <Modal
        opened={logBuildId !== null}
        onClose={() => setLogBuildId(null)}
        size="lg"
        title={logBuild ? `Build ${logBuild.version} · ${logBuild.pio_env}` : 'Build'}
      >
        {logBuild && (
          <Stack gap="xs">
            <Group gap="xs">
              <Badge color={BUILD_STATUS_COLORS[logBuild.status] || 'gray'}>{logBuild.status}</Badge>
              {logBuild.status === 'queued' && (
                <Text size="sm" c="dimmed">
                  {
                    builds.filter(
                      (x) =>
                        x.status === 'queued' &&
                        new Date(x.requested_at) < new Date(logBuild.requested_at),
                    ).length
                  }{' '}
                  build(s) ahead in the queue
                </Text>
              )}
              {fmtBuildElapsed(logBuild) && (
                <Text size="sm" c="dimmed">
                  {logBuild.status === 'building' ? 'running ' : ''}
                  {fmtBuildElapsed(logBuild)}
                </Text>
              )}
            </Group>
            <Text size="xs" c="dimmed">
              Requested {new Date(logBuild.requested_at).toLocaleString()}
              {logBuild.started_at &&
                ` · started ${new Date(logBuild.started_at).toLocaleTimeString()}`}
              {logBuild.completed_at &&
                ` · finished ${new Date(logBuild.completed_at).toLocaleTimeString()}`}
            </Text>
            {logBuild.status === 'queued' && (
              <Alert color="blue" variant="light">
                Queued builds only start once the self-hosted firmware-builder worker is running and
                consuming the builds queue. If this stays queued, that worker isn&rsquo;t up — or its
                task message was lost when Redis last restarted. Re-dispatch republishes the build
                task so the worker can pick it up:
                <Group mt="xs">
                  <Button
                    size="xs"
                    variant="light"
                    color="blue"
                    loading={redispatchingBuildId === logBuild.id}
                    onClick={async () => {
                      setRedispatchingBuildId(logBuild.id);
                      try {
                        const res = await forgekeyAPI.redispatchFirmwareBuild(logBuild.id);
                        setBuilds((prev) =>
                          prev.map((b) => (b.id === res.data.id ? res.data : b)),
                        );
                      } catch (err) {
                        setError(extractErrorMessage(err, 'Re-dispatch failed.'));
                      } finally {
                        setRedispatchingBuildId(null);
                      }
                    }}
                    data-testid="redispatch-build"
                  >
                    Re-dispatch to worker
                  </Button>
                </Group>
              </Alert>
            )}
            {logBuild.error_message && (
              <Alert color="red" variant="light">
                {logBuild.error_message}
              </Alert>
            )}
            <Text size="xs" fw={600}>
              Build log
            </Text>
            <Code
              block
              style={{ maxHeight: 360, overflow: 'auto', whiteSpace: 'pre-wrap' }}
              data-testid="build-log"
            >
              {logBuild.log ||
                (logBuild.status === 'queued' ? 'Not started yet.' : 'No log captured.')}
            </Code>
          </Stack>
        )}
      </Modal>

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

      {/* ePaper rollouts — separate pane because they target a different
          fleet (EPaperDisplay rows, HTTPS-pull) than the MQTT-push rollouts
          above (ESP32Device rows). Same status machine + action shape, so
          the buttons mirror the MQTT pane. */}
      {epaperRollouts.length > 0 && (
        <>
          <Title order={4} mt="xl" mb="sm">
            ePaper rollouts
          </Title>
          <Text size="sm" c="dimmed" mb="md">
            ePaper panels pull firmware over HTTPS on each wake; a wave here
            promotes the next batch of panels to the target version rather
            than publishing an MQTT trigger.
          </Text>
          <SimpleGrid cols={{ base: 1, lg: 2 }} data-testid="epaper-rollouts">
            {epaperRollouts.map((r) => {
              // ePaper progress shape: { target_total, promoted, remaining }
              // — no in-flight concept since the install happens lazily on the
              // panel's next wake. Render with just two bands.
              const p = r.progress as unknown as {
                target_total: number;
                promoted: number;
                remaining: number;
              };
              return (
                <Card
                  withBorder
                  p="md"
                  radius="md"
                  key={r.id}
                  data-testid={`epaper-rollout-${r.id}`}
                >
                  <Group justify="space-between" mb="xs">
                    <div>
                      <Text fw={600}>{r.firmware_version_string}</Text>
                      <Text size="xs" c="dimmed">
                        {r.device_type_name}
                        {r.name ? ` · ${r.name}` : ''}
                      </Text>
                    </div>
                    <Badge color={STATUS_COLORS[r.status] || 'gray'}>{r.status}</Badge>
                  </Group>

                  <Text size="sm" c="dimmed" mb={4}>
                    {r.batch_size_percent}% of the fleet every {r.interval_minutes} min
                  </Text>

                  <Progress.Root size="lg" mb={4}>
                    <Progress.Section
                      value={pct(p.promoted, p.target_total)}
                      color="green"
                    />
                  </Progress.Root>
                  <Text size="xs" c="dimmed" mb="sm">
                    {p.promoted} promoted · {p.remaining} remaining
                    {p.target_total ? ` of ${p.target_total}` : ''}
                  </Text>

                  <Group gap="xs">
                    {r.status === 'draft' && (
                      <Button
                        size="xs"
                        loading={busyId === r.id}
                        onClick={() => runEpaperAction(r.id, forgekeyAPI.startEpaperRollout)}
                      >
                        Start
                      </Button>
                    )}
                    {r.status === 'paused' && (
                      <Button
                        size="xs"
                        loading={busyId === r.id}
                        onClick={() => runEpaperAction(r.id, forgekeyAPI.startEpaperRollout)}
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
                          onClick={() =>
                            runEpaperAction(r.id, forgekeyAPI.advanceEpaperRollout)
                          }
                        >
                          Advance now
                        </Button>
                        <Button
                          size="xs"
                          variant="light"
                          color="yellow"
                          loading={busyId === r.id}
                          onClick={() => runEpaperAction(r.id, forgekeyAPI.pauseEpaperRollout)}
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
                        onClick={() => runEpaperAction(r.id, forgekeyAPI.cancelEpaperRollout)}
                      >
                        Cancel
                      </Button>
                    )}
                  </Group>
                </Card>
              );
            })}
          </SimpleGrid>
        </>
      )}
    </WorkspacePage>
  );
};

export default ForgeKeyFirmwareRolloutsPage;
