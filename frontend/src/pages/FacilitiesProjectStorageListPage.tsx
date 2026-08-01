/**
 * Staff queue for the project-storage warden workflow.
 *
 * The original FacilitiesProjectStoragePage is a lookup-and-act panel —
 * useful when you already know a stint ID, useless for sweep day. This
 * page lists every stint with status filters so a warden can pick a
 * bucket ("expiring soon" / "in purgatory") and walk the list.
 *
 * Status filtering happens server-side via /api/project-storage/stints/
 * ?status=… so the frontend doesn't have to recompute the same priority
 * rules backend/project_storage/models.py::compute_status already owns.
 *
 * Permissions: backend gates this with IsStorageAdminOrStaff, so a
 * member of the "Storage Admin" Django group can browse without being
 * platform-wide is_staff.
 */
import {
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Modal,
  Paper,
  SegmentedControl,
  Stack,
  Table,
  Text,
} from '@mantine/core';
import { IconEye } from '@tabler/icons-react';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import ProjectStorageLabelPreview from '../components/ProjectStorageLabelPreview';
import WorkspacePage from '../components/landing/WorkspacePage';
import { projectStorageAPI } from '../services/api';
import { ProjectStorageStatus, ProjectStorageStint } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

type StatusFilter = 'all' | ProjectStorageStatus;

const STATUS_BADGE: Record<
  ProjectStorageStatus,
  { color: string; label: string }
> = {
  active: { color: 'teal', label: 'Active' },
  expiring_soon: { color: 'yellow', label: 'Expiring soon' },
  expired: { color: 'orange', label: 'Expired' },
  purgatory_warned: { color: 'red', label: 'Warned — 7d grace' },
  purgatory: { color: 'dark', label: 'In purgatory' },
  removed: { color: 'gray', label: 'Removed' },
};

// Order matters — these are also the SegmentedControl tabs left-to-right.
// "All" is first so a warden landing fresh sees the global queue; the
// urgency-ordered tabs follow.
const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'expired', label: 'Expired' },
  { value: 'expiring_soon', label: 'Expiring soon' },
  { value: 'purgatory_warned', label: 'Warned' },
  { value: 'purgatory', label: 'Purgatory' },
  { value: 'active', label: 'Active' },
  { value: 'removed', label: 'Removed' },
];

const fmtDate = (iso: string | null): string => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
};

const FacilitiesProjectStorageListPage: React.FC = () => {
  const [stints, setStints] = useState<ProjectStorageStint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  // Row-level label preview: the stint whose label modal is open, or null.
  const [previewStint, setPreviewStint] = useState<ProjectStorageStint | null>(
    null,
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await projectStorageAPI.list({
        status: statusFilter === 'all' ? undefined : statusFilter,
        // Soonest-to-expire first — the urgency order the warden cares about.
        ordering: 'expires_at',
        page_size: 100,
      });
      setStints(res.data.results ?? []);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load project-storage stints.'));
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const counts = useMemo(() => {
    const out: Record<ProjectStorageStatus, number> = {
      active: 0,
      expiring_soon: 0,
      expired: 0,
      purgatory_warned: 0,
      purgatory: 0,
      removed: 0,
    };
    stints.forEach((s) => {
      out[s.status] = (out[s.status] ?? 0) + 1;
    });
    return out;
  }, [stints]);

  return (
    <WorkspacePage
      testId="facilities-project-storage-list-page"
      hero={{
        eyebrow: 'Facilities',
        title: 'Project storage queue',
        description:
          'Walk the storage queue by status — expiring soon, expired, in purgatory. ' +
          'Pick a row to open the warden actions (notice, purgatory, remove).',
        action: (
          <Group gap="sm">
            <Button component={Link} to="/facilities/project-storage/overview" variant="light">
              Rack overview
            </Button>
            <Button component={Link} to="/facilities/project-storage/slots" variant="light">
              Storage slots
            </Button>
            <Button component={Link} to="/facilities/project-storage" variant="light">
              Lookup by stint ID
            </Button>
          </Group>
        ),
      }}
    >
      <Stack gap="md">
        <Paper p="md" withBorder>
          <Stack gap="sm">
            <Text size="sm" c="dimmed">
              Filter by status
            </Text>
            <SegmentedControl
              value={statusFilter}
              onChange={(v) => setStatusFilter(v as StatusFilter)}
              data={STATUS_FILTERS.map((f) => ({
                label: f.label,
                value: f.value,
              }))}
              data-testid="status-filter"
            />
            {statusFilter === 'all' && stints.length > 0 && (
              <Group gap="sm" wrap="wrap">
                {(
                  Object.entries(counts) as [ProjectStorageStatus, number][]
                ).map(([s, n]) => (
                  <Badge
                    key={s}
                    color={STATUS_BADGE[s].color}
                    variant={n === 0 ? 'light' : 'filled'}
                  >
                    {STATUS_BADGE[s].label}: {n}
                  </Badge>
                ))}
              </Group>
            )}
          </Stack>
        </Paper>

        {error && (
          <Alert color="red" variant="light" data-testid="list-error">
            {error}
          </Alert>
        )}

        {loading ? (
          <Group justify="center" p="xl">
            <Loader />
          </Group>
        ) : stints.length === 0 ? (
          <Paper p="xl" withBorder>
            <Text c="dimmed" ta="center" data-testid="list-empty">
              No stints in this bucket.
            </Text>
          </Paper>
        ) : (
          <Paper withBorder>
            <Table.ScrollContainer minWidth={760}>
              <Table highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Status</Table.Th>
                    <Table.Th>Member</Table.Th>
                    <Table.Th>Project</Table.Th>
                    <Table.Th>Location</Table.Th>
                    <Table.Th>Expires</Table.Th>
                    <Table.Th>Stint</Table.Th>
                    <Table.Th>Label</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {stints.map((s) => (
                    <Table.Tr
                      key={s.stint_id}
                      data-testid={`stint-row-${s.stint_id}`}
                    >
                      <Table.Td>
                        <Badge color={STATUS_BADGE[s.status].color}>
                          {STATUS_BADGE[s.status].label}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Text fw={500}>{s.display_name}</Text>
                        {s.email && (
                          <Text size="xs" c="dimmed">
                            {s.email}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>{s.project_title || '—'}</Table.Td>
                      <Table.Td>
                        {/* location_display is the slot code when the stint
                            claimed one and the free-text location otherwise —
                            a racked stint leaves storage_location_name blank. */}
                        {s.status === 'purgatory'
                          ? s.purgatory_location_name || '—'
                          : s.location_display || s.storage_location_name || '—'}
                      </Table.Td>
                      <Table.Td>{fmtDate(s.expires_at)}</Table.Td>
                      <Table.Td>
                        <Text
                          ff="monospace"
                          size="sm"
                          component={Link}
                          to={`/facilities/project-storage/${encodeURIComponent(s.stint_id)}`}
                          c="blue"
                        >
                          {s.stint_id}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Button
                          variant="subtle"
                          size="compact-xs"
                          leftSection={<IconEye size={14} />}
                          onClick={() => setPreviewStint(s)}
                          data-testid={`preview-label-${s.stint_id}`}
                        >
                          Preview
                        </Button>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Paper>
        )}
      </Stack>

      <Modal
        opened={previewStint !== null}
        onClose={() => setPreviewStint(null)}
        title={
          previewStint
            ? `Label — ${previewStint.stint_id}`
            : 'Label preview'
        }
        centered
      >
        {previewStint && (
          <ProjectStorageLabelPreview
            stintId={previewStint.stint_id}
            testId="row-label-preview"
          />
        )}
      </Modal>
    </WorkspacePage>
  );
};

export default FacilitiesProjectStorageListPage;
