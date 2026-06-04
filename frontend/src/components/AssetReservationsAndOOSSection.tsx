/**
 * Reservations + out-of-service surfaces for AssetDetailPage.
 *
 * Self-contained so the asset detail page only adds one import + one
 * JSX line. Renders:
 *
 *   1. OUT OF SERVICE banner at the top when the asset has an open OOS,
 *      with a "Restore" action.
 *   2. "Mark out of service" button + modal when none is open.
 *   3. Reservations list (active future + currently running) with a
 *      "Reserve asset" button + modal.
 *   4. Past / cancelled history collapsible.
 *
 * Mirrors the AssetReservation + AssetOutOfService API shape from
 * services/api.ts. Staff + SIG admins write; everyone reads (the
 * endpoints surface 403 on cross-SIG attempts and the UI hides the
 * write buttons when the action button is rendered against a 403,
 * but doesn't pre-check — we let the backend be the source of truth).
 */
import {
  Alert,
  Badge,
  Button,
  Collapse,
  Group,
  Modal,
  Paper,
  Stack,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import { DateTimePicker } from '@mantine/dates';
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import {
  AssetOutOfService,
  AssetReservation,
  assetOutOfServiceAPI,
  assetReservationsAPI,
} from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface Props {
  assetId: string;
}

const fmtDateTime = (iso: string): string =>
  new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

const fmtDate = (iso: string): string =>
  new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });

const AssetReservationsAndOOSSection: React.FC<Props> = ({ assetId }) => {
  const [reservations, setReservations] = useState<AssetReservation[]>([]);
  const [oosEvents, setOOSEvents] = useState<AssetOutOfService[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Reserve modal state
  const [reserveOpen, setReserveOpen] = useState(false);
  const [reserveTitle, setReserveTitle] = useState('');
  const [reserveStart, setReserveStart] = useState<Date | null>(null);
  const [reserveEnd, setReserveEnd] = useState<Date | null>(null);
  const [reserveNotes, setReserveNotes] = useState('');
  const [reserveSubmitting, setReserveSubmitting] = useState(false);
  const [reserveError, setReserveError] = useState<string | null>(null);

  // OOS modal state
  const [oosOpen, setOOSOpen] = useState(false);
  const [oosReason, setOOSReason] = useState('');
  const [oosExpected, setOOSExpected] = useState<Date | null>(null);
  const [oosSubmitting, setOOSSubmitting] = useState(false);
  const [oosError, setOOSError] = useState<string | null>(null);

  // History collapse
  const [historyOpen, setHistoryOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [resResp, oosResp] = await Promise.all([
        assetReservationsAPI.list({ asset: assetId }),
        assetOutOfServiceAPI.list({ asset: assetId }),
      ]);
      setReservations(resResp.data.results);
      setOOSEvents(oosResp.data.results);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load reservations and OOS history.'));
    } finally {
      setLoading(false);
    }
  }, [assetId]);

  useEffect(() => {
    load();
  }, [load]);

  const openOOS = useMemo(() => oosEvents.find((o) => o.is_open) ?? null, [oosEvents]);
  const pastOOS = useMemo(() => oosEvents.filter((o) => !o.is_open), [oosEvents]);
  const now = Date.now();
  const activeReservations = useMemo(
    () =>
      reservations
        .filter((r) => r.cancelled_at === null && new Date(r.ends_at).getTime() > now)
        .sort((a, b) => new Date(a.starts_at).getTime() - new Date(b.starts_at).getTime()),
    [reservations, now],
  );
  const historicReservations = useMemo(
    () => reservations.filter((r) => !activeReservations.includes(r)),
    [reservations, activeReservations],
  );

  const handleRestore = async (id: string) => {
    setBusyId(id);
    try {
      await assetOutOfServiceAPI.restore(id);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to restore the asset.'));
    } finally {
      setBusyId(null);
    }
  };

  const handleCancelReservation = async (id: string) => {
    setBusyId(id);
    try {
      await assetReservationsAPI.cancel(id);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to cancel the reservation.'));
    } finally {
      setBusyId(null);
    }
  };

  const submitReserve = async () => {
    if (!reserveStart || !reserveEnd || !reserveTitle.trim()) {
      setReserveError('Title, start, and end are required.');
      return;
    }
    setReserveSubmitting(true);
    setReserveError(null);
    try {
      await assetReservationsAPI.create({
        asset: assetId,
        title: reserveTitle.trim(),
        starts_at: reserveStart.toISOString(),
        ends_at: reserveEnd.toISOString(),
        notes: reserveNotes.trim() || undefined,
      });
      setReserveOpen(false);
      setReserveTitle('');
      setReserveStart(null);
      setReserveEnd(null);
      setReserveNotes('');
      await load();
    } catch (err) {
      setReserveError(extractErrorMessage(err, 'Failed to create the reservation.'));
    } finally {
      setReserveSubmitting(false);
    }
  };

  const submitOOS = async () => {
    if (!oosReason.trim()) {
      setOOSError('Reason is required.');
      return;
    }
    setOOSSubmitting(true);
    setOOSError(null);
    try {
      await assetOutOfServiceAPI.open({
        asset: assetId,
        reason: oosReason.trim(),
        expected_return_at: oosExpected ? oosExpected.toISOString() : null,
      });
      setOOSOpen(false);
      setOOSReason('');
      setOOSExpected(null);
      await load();
    } catch (err) {
      setOOSError(extractErrorMessage(err, 'Failed to mark the asset out of service.'));
    } finally {
      setOOSSubmitting(false);
    }
  };

  return (
    <Stack gap="md" mt="md" data-testid="reservations-and-oos-section">
      {error && (
        <Alert color="red" variant="light">
          {error}
        </Alert>
      )}

      {openOOS && (
        <Alert
          color="red"
          variant="filled"
          title="OUT OF SERVICE"
          data-testid="oos-banner"
        >
          <Stack gap="xs">
            <Text size="sm">
              Placed out {fmtDateTime(openOOS.placed_out_at)} by{' '}
              <strong>{openOOS.placed_by_username || 'unknown'}</strong>.
              {openOOS.expected_return_at && (
                <>
                  {' '}
                  Expected back {fmtDate(openOOS.expected_return_at)}.
                </>
              )}
            </Text>
            <Text size="sm">{openOOS.reason}</Text>
            <Group gap="xs">
              <Button
                size="xs"
                color="green"
                loading={busyId === openOOS.id}
                onClick={() => handleRestore(openOOS.id)}
                data-testid="restore-button"
              >
                Restore (back in service)
              </Button>
            </Group>
          </Stack>
        </Alert>
      )}

      <Paper withBorder p="md" radius="md">
        <Group justify="space-between" align="center" mb="sm">
          <Title order={4}>Reservations</Title>
          <Group gap="xs">
            {!openOOS && (
              <Button
                color="red"
                variant="light"
                size="xs"
                onClick={() => setOOSOpen(true)}
                data-testid="mark-oos-button"
              >
                Mark out of service
              </Button>
            )}
            <Button size="xs" onClick={() => setReserveOpen(true)} data-testid="reserve-button">
              Reserve asset
            </Button>
          </Group>
        </Group>

        {loading ? (
          <Text c="dimmed" size="sm">
            Loading…
          </Text>
        ) : activeReservations.length === 0 ? (
          <Text c="dimmed" size="sm">
            No upcoming or active reservations.
          </Text>
        ) : (
          <Stack gap="xs">
            {activeReservations.map((r) => (
              <Paper key={r.id} withBorder p="sm" radius="sm" data-testid={`reservation-${r.id}`}>
                <Group justify="space-between" align="flex-start" wrap="nowrap">
                  <Stack gap={2} style={{ flex: 1 }}>
                    <Group gap="xs">
                      <Text fw={600}>{r.title}</Text>
                      {r.is_current && (
                        <Badge color="green" variant="light">
                          NOW
                        </Badge>
                      )}
                    </Group>
                    <Text size="xs" c="dimmed">
                      {fmtDateTime(r.starts_at)} → {fmtDateTime(r.ends_at)} · reserved by{' '}
                      {r.reserved_by_username || 'unknown'}
                    </Text>
                    {r.notes && (
                      <Text size="xs" c="dimmed">
                        {r.notes}
                      </Text>
                    )}
                  </Stack>
                  <Button
                    size="xs"
                    color="red"
                    variant="subtle"
                    loading={busyId === r.id}
                    onClick={() => handleCancelReservation(r.id)}
                    data-testid={`cancel-reservation-${r.id}`}
                  >
                    Cancel
                  </Button>
                </Group>
              </Paper>
            ))}
          </Stack>
        )}

        {(historicReservations.length > 0 || pastOOS.length > 0) && (
          <>
            <Group mt="md">
              <Button
                size="xs"
                variant="subtle"
                onClick={() => setHistoryOpen((v) => !v)}
                data-testid="toggle-history"
              >
                {historyOpen ? 'Hide history' : 'Show history'} (
                {historicReservations.length + pastOOS.length})
              </Button>
            </Group>
            <Collapse in={historyOpen}>
              <Stack gap="xs" mt="sm">
                {pastOOS.map((o) => (
                  <Paper key={o.id} withBorder p="sm" radius="sm" bg="gray.0">
                    <Text size="sm">
                      <strong>OOS</strong> {fmtDateTime(o.placed_out_at)} →{' '}
                      {o.restored_at ? fmtDateTime(o.restored_at) : 'open'} · by{' '}
                      {o.placed_by_username || 'unknown'}
                      {o.restored_by_username && ` · restored by ${o.restored_by_username}`}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {o.reason}
                    </Text>
                  </Paper>
                ))}
                {historicReservations.map((r) => (
                  <Paper key={r.id} withBorder p="sm" radius="sm" bg="gray.0">
                    <Text size="sm">
                      <strong>{r.title}</strong> {fmtDateTime(r.starts_at)} → {fmtDateTime(r.ends_at)}
                      {r.cancelled_at && ' · cancelled'}
                    </Text>
                    <Text size="xs" c="dimmed">
                      reserved by {r.reserved_by_username || 'unknown'}
                    </Text>
                  </Paper>
                ))}
              </Stack>
            </Collapse>
          </>
        )}
      </Paper>

      {/* Reserve modal */}
      <Modal
        opened={reserveOpen}
        onClose={() => setReserveOpen(false)}
        title="Reserve asset for class / training / event"
        data-testid="reserve-modal"
      >
        <Stack gap="sm">
          <TextInput
            label="Title"
            placeholder="Welding Class — Jane Doe"
            value={reserveTitle}
            onChange={(e) => setReserveTitle(e.currentTarget.value)}
            required
          />
          <DateTimePicker
            label="Starts at"
            value={reserveStart}
            onChange={(v) => setReserveStart(v ? new Date(v) : null)}
            required
          />
          <DateTimePicker
            label="Ends at"
            value={reserveEnd}
            onChange={(v) => setReserveEnd(v ? new Date(v) : null)}
            required
          />
          <Textarea
            label="Notes"
            placeholder="Bay 3, beginner curriculum"
            value={reserveNotes}
            onChange={(e) => setReserveNotes(e.currentTarget.value)}
            autosize
            minRows={2}
          />
          {reserveError && (
            <Alert color="red" variant="light">
              {reserveError}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setReserveOpen(false)}>
              Cancel
            </Button>
            <Button
              loading={reserveSubmitting}
              onClick={submitReserve}
              data-testid="reserve-submit"
            >
              Reserve
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* OOS modal */}
      <Modal
        opened={oosOpen}
        onClose={() => setOOSOpen(false)}
        title="Mark asset out of service"
        data-testid="oos-modal"
      >
        <Stack gap="sm">
          <Textarea
            label="Reason"
            placeholder="Spindle bearing seized; needs replacement"
            value={oosReason}
            onChange={(e) => setOOSReason(e.currentTarget.value)}
            autosize
            minRows={3}
            required
          />
          <DateTimePicker
            label="Expected back (optional)"
            value={oosExpected}
            onChange={(v) => setOOSExpected(v ? new Date(v) : null)}
            clearable
          />
          {oosError && (
            <Alert color="red" variant="light">
              {oosError}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setOOSOpen(false)}>
              Cancel
            </Button>
            <Button
              color="red"
              loading={oosSubmitting}
              onClick={submitOOS}
              data-testid="oos-submit"
            >
              Mark out of service
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
};

export default AssetReservationsAndOOSSection;
