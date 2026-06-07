/**
 * Project Storage warden console.
 *
 * Two entry points stacked at the top:
 *   1. A camera scanner that decodes the QR on a stint label.
 *   2. An HID-friendly text input that autofocuses, so a wedge barcode
 *      scanner (which acts like a keyboard ending in Enter) feeds in
 *      directly.
 *
 * Either path resolves to a single stint detail panel below: status
 * badge, member info, timeline of events, and the three warden
 * actions (send violation notice, move to purgatory, mark removed)
 * gated by the stint's current state. We also load and show the
 * member's stint history so a serial offender is obvious at a glance.
 */
import {
  Accordion,
  Badge,
  Button,
  Group,
  List,
  Loader,
  Paper,
  Stack,
  Text,
  TextInput,
  Timeline,
  Title,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconBan,
  IconCamera,
  IconCheck,
  IconKeyboard,
  IconMail,
  IconQrcode,
} from '@tabler/icons-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import QRScanner from '../components/QRScanner';
import WorkspacePage from '../components/landing/WorkspacePage';
import { useNotifications } from '../hooks/useNotifications';
import { projectStorageAPI } from '../services/api';
import {
  ProjectStorageStatus,
  ProjectStorageStint,
} from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

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

const formatTs = (iso: string | null): string => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};

const formatDate = (iso: string | null): string => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
};

const FacilitiesProjectStoragePage: React.FC = () => {
  const notifications = useNotifications();
  const navigate = useNavigate();
  // The :stintId param is optional — the page is mounted at both
  // /facilities/project-storage (lookup form) and
  // /facilities/project-storage/:stintId (permalink detail). When the
  // param is present we auto-load it so an emailed link or a row in
  // the queue list (PR 1) deep-links straight to the warden actions.
  const { stintId: stintIdParam } = useParams<{ stintId?: string }>();

  const [stintId, setStintId] = useState<string>(stintIdParam ?? '');
  const [stint, setStint] = useState<ProjectStorageStint | null>(null);
  const [history, setHistory] = useState<ProjectStorageStint[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showScanner, setShowScanner] = useState(false);
  const [actionInFlight, setActionInFlight] = useState<
    'notice' | 'purgatory' | 'removed' | 'qr' | null
  >(null);

  const inputRef = useRef<HTMLInputElement | null>(null);

  const loadStint = useCallback(
    async (rawId: string) => {
      const id = rawId.trim().toUpperCase();
      if (!id) return;
      setLoading(true);
      setError(null);
      setStint(null);
      setHistory(null);
      try {
        const resp = await projectStorageAPI.get(id);
        setStint(resp.data);
        // History fetch is opportunistic — don't fail the whole load if
        // it 403s (the show endpoint already gated us in).
        try {
          const hist = await projectStorageAPI.byMember(resp.data.username);
          setHistory(hist.data);
        } catch {
          setHistory([]);
        }
      } catch (err) {
        setError(extractErrorMessage(err, `Could not load stint ${id}.`));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  // Auto-load whenever the route param changes (mount, back/forward,
  // navigating between queue rows). When the param is absent, fall back
  // to autofocusing the HID input so a barcode scanner wedge "just
  // works" on the bare lookup form.
  useEffect(() => {
    if (stintIdParam) {
      const upper = stintIdParam.trim().toUpperCase();
      setStintId(upper);
      loadStint(upper);
    } else {
      inputRef.current?.focus();
    }
  }, [stintIdParam, loadStint]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const id = stintId.trim().toUpperCase();
    if (!id) return;
    navigate(`/facilities/project-storage/${encodeURIComponent(id)}`);
  };

  const handleScanSuccess = (decoded: string) => {
    setShowScanner(false);
    const id = decoded.trim().toUpperCase();
    setStintId(id);
    navigate(`/facilities/project-storage/${encodeURIComponent(id)}`);
  };

  const refresh = () => {
    if (stint) loadStint(stint.stint_id);
  };

  // -- warden actions --------------------------------------------------

  const sendNotice = async () => {
    if (!stint) return;
    setActionInFlight('notice');
    try {
      const resp = await projectStorageAPI.sendViolationNotice(stint.stint_id);
      setStint(resp.data);
      notifications.showSuccess(
        `Violation notice sent to ${resp.data.email || resp.data.username}`,
      );
    } catch (err) {
      notifications.showError(extractErrorMessage(err, 'Could not send notice.'));
    } finally {
      setActionInFlight(null);
    }
  };

  const moveToPurgatory = async () => {
    if (!stint) return;
    const location = window.prompt(
      'Purgatory location (leave blank to keep the existing one):',
      stint.purgatory_location_name || '',
    );
    if (location === null) return; // cancelled
    setActionInFlight('purgatory');
    try {
      const resp = await projectStorageAPI.moveToPurgatory(stint.stint_id, location);
      setStint(resp.data);
      notifications.showSuccess(
        `Stint ${resp.data.stint_id} moved to ${location || 'purgatory'}.`,
      );
    } catch (err) {
      notifications.showError(extractErrorMessage(err, 'Could not move to purgatory.'));
    } finally {
      setActionInFlight(null);
    }
  };

  const markRemoved = async () => {
    if (!stint) return;
    const note = window.prompt('Optional note about the removal:', '');
    if (note === null) return; // cancelled
    setActionInFlight('removed');
    try {
      const resp = await projectStorageAPI.markRemoved(stint.stint_id, note);
      setStint(resp.data);
      notifications.showSuccess(`Stint ${resp.data.stint_id} marked removed.`);
    } catch (err) {
      notifications.showError(extractErrorMessage(err, 'Could not mark removed.'));
    } finally {
      setActionInFlight(null);
    }
  };

  const regenerateQr = async () => {
    if (!stint) return;
    setActionInFlight('qr');
    try {
      const resp = await projectStorageAPI.generateQr(stint.stint_id, true);
      setStint(resp.data);
      notifications.showSuccess(`Regenerated QR for ${resp.data.stint_id}.`);
    } catch (err) {
      notifications.showError(extractErrorMessage(err, 'Could not regenerate QR.'));
    } finally {
      setActionInFlight(null);
    }
  };

  // -- which actions are available right now --------------------------

  const status: ProjectStorageStatus | null = stint?.status ?? null;
  const canSendNotice =
    status === 'expired' ||
    status === 'expiring_soon' ||
    status === 'purgatory_warned';
  const canMoveToPurgatory =
    status === 'purgatory_warned' && stint?.notice_sent_at != null;
  const canMarkRemoved =
    status === 'active' ||
    status === 'expiring_soon' ||
    status === 'expired' ||
    status === 'purgatory_warned' ||
    status === 'purgatory';

  return (
    <WorkspacePage
      testId="facilities-project-storage-page"
      hero={{
        eyebrow: 'Facilities',
        title: 'Project storage',
        description:
          'Look up a project-storage stint by QR scan or by typing the stint ID. ' +
          'Send violation notices, move items to purgatory, and mark stints removed.',
        action: stint ? (
          <Button variant="default" onClick={refresh}>
            Refresh
          </Button>
        ) : undefined,
      }}
    >
      {/* Input row: HID/text on the left, camera scan on the right. */}
      <Paper withBorder radius="md" p="md">
        <Stack gap="md">
          <Group justify="space-between" wrap="nowrap">
            <Group gap="xs">
              <IconKeyboard size={20} />
              <Text fw={600}>Enter or scan a stint ID</Text>
            </Group>
            <Button
              variant="default"
              leftSection={<IconCamera size={16} />}
              onClick={() => setShowScanner((v) => !v)}
            >
              {showScanner ? 'Hide camera' : 'Open camera scanner'}
            </Button>
          </Group>

          <form onSubmit={handleSubmit}>
            <Group gap="xs" align="flex-end">
              <TextInput
                ref={inputRef}
                label="Stint ID"
                placeholder="PS-XXXXXXXX"
                value={stintId}
                onChange={(e) => setStintId(e.currentTarget.value)}
                autoComplete="off"
                spellCheck={false}
                style={{ flex: 1 }}
                data-testid="project-storage-stint-input"
              />
              <Button type="submit" disabled={!stintId.trim() || loading}>
                Look up
              </Button>
            </Group>
          </form>

          {showScanner && (
            <Paper withBorder radius="md" p="sm" bg="gray.0">
              <QRScanner
                onScanSuccess={handleScanSuccess}
                onClose={() => setShowScanner(false)}
              />
            </Paper>
          )}
        </Stack>
      </Paper>

      {/* Loading / error states */}
      {loading && (
        <Paper withBorder p="md" radius="md">
          <Group>
            <Loader size="sm" />
            <Text c="dimmed">Loading stint…</Text>
          </Group>
        </Paper>
      )}
      {error && !loading && (
        <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
          <Text>{error}</Text>
        </Paper>
      )}

      {/* Stint detail */}
      {stint && !loading && (
        <Paper withBorder radius="md" p="md" data-testid={`stint-${stint.stint_id}`}>
          <Stack gap="md">
            <Group justify="space-between" wrap="nowrap">
              <Group gap="sm" wrap="nowrap">
                <IconQrcode size={28} />
                <Stack gap={2}>
                  <Title order={3}>{stint.stint_id}</Title>
                  <Text c="dimmed">{stint.display_name} ({stint.username})</Text>
                </Stack>
              </Group>
              <Badge size="xl" color={STATUS_BADGE[stint.status].color}>
                {STATUS_BADGE[stint.status].label}
              </Badge>
            </Group>

            <Group gap="lg" wrap="wrap">
              <Stat label="Started" value={formatDate(stint.started_at)} />
              <Stat label="Expires" value={formatDate(stint.expires_at)} />
              <Stat label="Expiry wk / DoY" value={`Wk ${stint.expiry_week} / Day ${stint.expiry_day_of_year}`} />
              <Stat label="Storage" value={stint.storage_location_name || '—'} />
              {stint.notice_sent_at && (
                <Stat label="Notice sent" value={formatTs(stint.notice_sent_at)} />
              )}
              {stint.purgatory_at && (
                <Stat label="→ Purgatory at" value={formatTs(stint.purgatory_at)} />
              )}
              {stint.moved_to_purgatory_at && (
                <Stat label="Moved to purgatory" value={formatTs(stint.moved_to_purgatory_at)} />
              )}
              {stint.removed_at && (
                <Stat label="Removed" value={formatTs(stint.removed_at)} />
              )}
            </Group>

            {stint.project_title && (
              <Paper withBorder p="sm" radius="sm" bg="gray.0">
                <Text size="sm" c="dimmed">Project</Text>
                <Text>{stint.project_title}</Text>
              </Paper>
            )}

            <Group gap="xs" wrap="wrap">
              <Button
                color="red"
                leftSection={<IconMail size={16} />}
                disabled={!canSendNotice}
                loading={actionInFlight === 'notice'}
                onClick={sendNotice}
                data-testid="send-notice-button"
              >
                Send violation notice
              </Button>
              <Button
                color="dark"
                leftSection={<IconBan size={16} />}
                disabled={!canMoveToPurgatory}
                loading={actionInFlight === 'purgatory'}
                onClick={moveToPurgatory}
                data-testid="move-purgatory-button"
              >
                Move to purgatory
              </Button>
              <Button
                color="teal"
                leftSection={<IconCheck size={16} />}
                disabled={!canMarkRemoved}
                loading={actionInFlight === 'removed'}
                onClick={markRemoved}
                data-testid="mark-removed-button"
              >
                Mark removed
              </Button>
              <Button
                variant="default"
                leftSection={<IconQrcode size={16} />}
                loading={actionInFlight === 'qr'}
                onClick={regenerateQr}
                data-testid="regenerate-qr-button"
              >
                {stint.qr_code_url ? 'Regenerate QR' : 'Generate QR'}
              </Button>
            </Group>

            {stint.qr_code_url && (
              <Paper p="md" withBorder>
                <Stack gap="xs" align="center">
                  <Text size="sm" c="dimmed">
                    Persisted QR PNG (logo-embedded, validated). Encodes the
                    /scan/project-storage/{stint.stint_id} URL.
                  </Text>
                  <img
                    src={stint.qr_code_url}
                    alt={`QR code for stint ${stint.stint_id}`}
                    width={180}
                    height={180}
                    style={{ imageRendering: 'pixelated' }}
                    data-testid="qr-code-preview"
                  />
                </Stack>
              </Paper>
            )}

            {/* Audit timeline */}
            <Accordion variant="separated" radius="md" defaultValue="events">
              <Accordion.Item value="events">
                <Accordion.Control>
                  Event timeline ({stint.events.length})
                </Accordion.Control>
                <Accordion.Panel>
                  {stint.events.length === 0 ? (
                    <Text c="dimmed" size="sm">No events recorded.</Text>
                  ) : (
                    <Timeline active={stint.events.length - 1} bulletSize={20} lineWidth={2}>
                      {stint.events.map((ev) => (
                        <Timeline.Item
                          key={ev.id}
                          bullet={<IconAlertTriangle size={12} />}
                          title={ev.event_type.replace(/_/g, ' ')}
                        >
                          <Text size="xs" c="dimmed">
                            {formatTs(ev.created_at)}
                            {ev.actor_username && ` · by ${ev.actor_username}`}
                            {ev.actor_label && ` · ${ev.actor_label}`}
                          </Text>
                          {ev.note && (
                            <Text size="sm" mt={4}>{ev.note}</Text>
                          )}
                        </Timeline.Item>
                      ))}
                    </Timeline>
                  )}
                </Accordion.Panel>
              </Accordion.Item>

              {/* Member history */}
              {history && history.length > 0 && (
                <Accordion.Item value="history">
                  <Accordion.Control>
                    Member stint history ({history.length})
                  </Accordion.Control>
                  <Accordion.Panel>
                    <List size="sm" spacing={4}>
                      {history.map((h) => (
                        <List.Item key={h.stint_id}>
                          <Group gap="xs" wrap="nowrap">
                            <Text fw={h.stint_id === stint.stint_id ? 700 : 400}>
                              {h.stint_id}
                            </Text>
                            <Badge size="sm" color={STATUS_BADGE[h.status].color}>
                              {STATUS_BADGE[h.status].label}
                            </Badge>
                            <Text size="xs" c="dimmed">
                              {formatDate(h.started_at)} → {formatDate(h.expires_at)}
                              {h.removed_at && ` (removed ${formatDate(h.removed_at)})`}
                            </Text>
                          </Group>
                        </List.Item>
                      ))}
                    </List>
                  </Accordion.Panel>
                </Accordion.Item>
              )}
            </Accordion>
          </Stack>
        </Paper>
      )}
    </WorkspacePage>
  );
};

const Stat: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <Stack gap={2}>
    <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{label}</Text>
    <Text size="sm" fw={500}>{value}</Text>
  </Stack>
);

export default FacilitiesProjectStoragePage;
