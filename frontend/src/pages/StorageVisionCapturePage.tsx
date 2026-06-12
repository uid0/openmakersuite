/**
 * Storage Vision capture upload (AC-9 phone path; second half of AC-28).
 *
 * Staff / Logistics page at /facilities/storage-vision/capture. Lets
 * the operator pick a monitored area, pick or take a photo, and post
 * it to the slice-3 capture endpoint. After the 202 the page polls
 * the capture's status every 2 seconds until it transitions to
 * processed or failed; the result panel surfaces the markers
 * detected, the failure_code (e.g. ``no_markers_detected``), and a
 * deep link into the slice-9 review queue for any observation rows
 * the processor created.
 *
 * AC-10 (fixed-camera upload) lives on the device side; AC-29 gates
 * non-staff / non-Logistics at both the route and the sidebar.
 */
import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Code,
  FileInput,
  Group,
  LoadingOverlay,
  Paper,
  Progress,
  Select,
  Stack,
  Table,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import {
  storageVisionAPI,
  VisionArea,
  VisionCapture,
  VisionCaptureStatus,
} from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

type Tracked = {
  capture: VisionCapture;
  // The poll handle; cleared once the capture reaches a terminal
  // state so the page doesn't keep hammering the API after the
  // worker finishes.
  pollHandle: number | null;
};

const TERMINAL_STATUSES: VisionCaptureStatus[] = ['processed', 'failed'];

const POLL_INTERVAL_MS = 2_000;

const unwrap = <T,>(data: { results: T[] } | T[]): T[] =>
  Array.isArray(data) ? data : data.results;

const statusColor = (s: VisionCaptureStatus): string => {
  switch (s) {
    case 'queued':
      return 'gray';
    case 'processing':
      return 'blue';
    case 'processed':
      return 'green';
    case 'failed':
      return 'red';
  }
};

const formatRelative = (iso: string | null): string => {
  if (!iso) return '—';
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

const StorageVisionCapturePage: React.FC = () => {
  const isStaff =
    typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' &&
    localStorage.getItem('is_superuser') === 'true';
  const isLogistics =
    typeof window !== 'undefined' &&
    (localStorage.getItem('is_logistics') === 'true' ||
      (localStorage.getItem('groups') || '').includes('Logistics'));
  const isAllowed = isStaff || isSuperuser || isLogistics;

  const [areas, setAreas] = useState<VisionArea[]>([]);
  const [areaId, setAreaId] = useState<number | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [capturedAt, setCapturedAt] = useState<string>('');
  const [notes, setNotes] = useState<string>('');

  const [recent, setRecent] = useState<VisionCapture[]>([]);
  const [tracked, setTracked] = useState<Tracked | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Hold the poll handle in a ref too so the unmount cleanup can
  // cancel it without depending on state freshness.
  const pollRef = useRef<number | null>(null);

  const loadAreas = useCallback(async () => {
    try {
      const res = await storageVisionAPI.listAreas();
      setAreas(unwrap(res.data).filter((a) => a.is_active));
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load areas.'));
    }
  }, []);

  const loadRecent = useCallback(async (filterAreaId: number | null) => {
    try {
      const res = await storageVisionAPI.listCaptures(
        filterAreaId != null ? { area: filterAreaId } : undefined,
      );
      const list = unwrap(res.data);
      // Page list shows ten most recent.
      setRecent(list.slice(0, 10));
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load recent captures.'));
    }
  }, []);

  useEffect(() => {
    if (!isAllowed) return;
    setLoading(true);
    Promise.all([loadAreas(), loadRecent(null)]).finally(() => setLoading(false));
  }, [isAllowed, loadAreas, loadRecent]);

  useEffect(() => {
    return () => {
      if (pollRef.current != null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, []);

  if (!isAllowed) {
    return <Navigate to="/" replace />;
  }

  const startPolling = (capture: VisionCapture) => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setTracked({ capture, pollHandle: null });

    const handle = window.setInterval(async () => {
      try {
        const res = await storageVisionAPI.getCapture(capture.id);
        const next = res.data;
        setTracked({ capture: next, pollHandle: handle });
        if (TERMINAL_STATUSES.includes(next.status)) {
          window.clearInterval(handle);
          pollRef.current = null;
          // Refresh the recent list so the new capture shows
          // its terminal state in the history.
          void loadRecent(areaId);
        }
      } catch (err) {
        // Don't kill the page on a transient poll error — just
        // surface it so the operator knows polling is paused.
        setError(extractErrorMessage(err, 'Polling failed; will keep trying.'));
      }
    }, POLL_INTERVAL_MS);
    pollRef.current = handle;
  };

  const submit = async () => {
    if (areaId == null || file == null) return;
    setUploading(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('area', String(areaId));
      fd.append('original_image', file);
      if (capturedAt) fd.append('captured_at', new Date(capturedAt).toISOString());
      const res = await storageVisionAPI.uploadCapture(fd);
      // Reset form for the next shot.
      setFile(null);
      setCapturedAt('');
      setNotes('');
      // Track the new capture and start polling.
      startPolling(res.data);
      void loadRecent(areaId);
    } catch (err) {
      setError(extractErrorMessage(err, 'Upload failed.'));
    } finally {
      setUploading(false);
    }
  };

  const areaOptions = areas.map((a) => ({
    value: String(a.id),
    label: `${a.name} — ${a.location_name}`,
  }));

  return (
    <WorkspacePage
      hero={{
        eyebrow: 'Facilities',
        title: 'Upload supply capture',
        description:
          'Pick a monitored area, snap or upload a photo, and the marker detector will queue any empty / low slot reviews for staff approval.',
      }}
      testId="storage-vision-capture-page"
    >
      <Box pos="relative">
        <LoadingOverlay visible={loading} />

        {error && (
          <Alert
            color="red"
            mb="md"
            onClose={() => setError(null)}
            withCloseButton
          >
            {error}
          </Alert>
        )}

        <Card withBorder mb="lg" padding="lg">
          <Stack>
            <Title order={4}>New capture</Title>
            <Select
              label="Area"
              data={areaOptions}
              value={areaId != null ? String(areaId) : null}
              onChange={(v) => setAreaId(v ? Number(v) : null)}
              placeholder="Pick the area you photographed"
              searchable
              required
              data-testid="capture-area-select"
              nothingFoundMessage="No active areas. Set one up in Storage Vision setup first."
            />
            <FileInput
              label="Photo (JPEG or PNG)"
              accept="image/jpeg,image/png"
              value={file}
              onChange={setFile}
              placeholder="Choose or take a photo"
              required
              data-testid="capture-file-input"
              description="Maximum 10 MB. Phones can capture directly from the file picker."
            />
            <TextInput
              label="Captured at (optional)"
              type="datetime-local"
              value={capturedAt}
              onChange={(e) => setCapturedAt(e.currentTarget.value)}
              description="Defaults to upload time if blank."
            />
            <Textarea
              label="Reviewer notes (optional, local only)"
              value={notes}
              onChange={(e) => setNotes(e.currentTarget.value)}
              description="Kept on this screen for your own reference; not sent with the upload."
            />
            <Group justify="flex-end">
              <Button
                onClick={submit}
                disabled={uploading || areaId == null || file == null}
                loading={uploading}
                data-testid="capture-submit"
              >
                Upload
              </Button>
            </Group>
          </Stack>
        </Card>

        {tracked && <TrackedPanel tracked={tracked} />}

        <Title order={4} mt="xl" mb="sm">
          Recent captures
        </Title>
        {recent.length === 0 ? (
          <Text c="dimmed">No captures yet.</Text>
        ) : (
          <Table data-testid="recent-captures-table">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Area</Table.Th>
                <Table.Th>Source</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Markers</Table.Th>
                <Table.Th>Received</Table.Th>
                <Table.Th>Review</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {recent.map((c) => (
                <Table.Tr key={c.id}>
                  <Table.Td>{c.area_name}</Table.Td>
                  <Table.Td>
                    <Badge variant="light">{c.source}</Badge>
                  </Table.Td>
                  <Table.Td>
                    <Badge color={statusColor(c.status)}>{c.status}</Badge>
                  </Table.Td>
                  <Table.Td>
                    {c.markers_detected.length || 0}
                    {c.failure_code === 'no_markers_detected' && (
                      <Text component="span" size="xs" c="dimmed" ml="xs">
                        none readable
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>{formatRelative(c.received_at)}</Table.Td>
                  <Table.Td>
                    <Button
                      size="xs"
                      variant="default"
                      component={Link}
                      to={`/facilities/storage-vision/review?capture=${c.id}`}
                    >
                      Open
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Box>
    </WorkspacePage>
  );
};

interface TrackedPanelProps {
  tracked: Tracked;
}

const TrackedPanel: React.FC<TrackedPanelProps> = ({ tracked }) => {
  const { capture } = tracked;
  const matched = capture.markers_detected.filter(
    (m) => m.matched_slot_id != null,
  );
  const unmatched = capture.markers_detected.filter(
    (m) => m.matched_slot_id == null,
  );

  return (
    <Paper withBorder p="lg" mb="lg" data-testid="tracked-capture-panel">
      <Stack gap="sm">
        <Group justify="space-between">
          <Group gap="sm">
            <Title order={5}>Capture #{capture.id}</Title>
            <Badge color={statusColor(capture.status)}>{capture.status}</Badge>
            <Text size="sm" c="dimmed">
              {capture.area_name}
            </Text>
          </Group>
          <Button
            size="xs"
            variant="default"
            component={Link}
            to={`/facilities/storage-vision/review?capture=${capture.id}`}
          >
            Open in review
          </Button>
        </Group>

        {capture.status === 'queued' && (
          <Progress value={20} animated striped />
        )}
        {capture.status === 'processing' && (
          <Progress value={70} animated striped />
        )}
        {capture.status === 'processed' && (
          <Stack gap="xs">
            {capture.failure_code === 'no_markers_detected' ? (
              <Alert color="yellow">
                No storage-vision markers were readable in this image. Try
                another angle or check the lighting.
              </Alert>
            ) : (
              <>
                <Text size="sm">
                  Matched {matched.length} known slot(s); {unmatched.length}{' '}
                  marker(s) didn&apos;t resolve to a slot.
                </Text>
                {matched.length > 0 && (
                  <Table data-testid="matched-markers-table">
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th>Marker</Table.Th>
                        <Table.Th>Confidence</Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {matched.map((m) => (
                        <Table.Tr key={m.marker_code}>
                          <Table.Td>
                            <Code>{m.marker_code}</Code>
                          </Table.Td>
                          <Table.Td>{m.confidence.toFixed(2)}</Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                )}
                {unmatched.length > 0 && (
                  <Text size="xs" c="dimmed">
                    Unknown payloads:{' '}
                    {unmatched.map((m) => m.marker_code).join(', ')}
                  </Text>
                )}
              </>
            )}
          </Stack>
        )}
        {capture.status === 'failed' && (
          <Alert color="red">
            Processing failed: <Code>{capture.failure_code || 'error'}</Code>.{' '}
            {capture.failure_reason}
          </Alert>
        )}
      </Stack>
    </Paper>
  );
};

export default StorageVisionCapturePage;
