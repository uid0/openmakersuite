/**
 * Member self-service kiosk for starting a project-storage stint.
 *
 * Sits on a public route (no auth) at /project-storage/kiosk. Member
 * enters their username + a short project title; we POST to the
 * AllowAny `/api/project-storage/stints/start/` endpoint, then show:
 *   - the stint ID and the printable label (so the kiosk operator
 *     can confirm what's about to come out of the printer)
 *   - a friendly success message naming the expiry date
 * The Raspberry Pi print daemon (separate repo) is what actually
 * drives the physical printer; this page just creates the stint and
 * shows the operator what to expect.
 *
 * The 409 paths from the backend become inline messages, with the
 * unblock date surfaced when it's a cool-down rejection.
 */
import {
  Alert,
  Anchor,
  Button,
  Group,
  Image,
  Loader,
  Paper,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import {
  IconAlertCircle,
  IconCheck,
  IconPackage,
  IconRefresh,
} from '@tabler/icons-react';
import axios from 'axios';
import React, { useEffect, useRef, useState } from 'react';
import { projectStorageAPI } from '../services/api';
import { ProjectStorageStint } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

type RejectionKind = 'active_stint_exists' | 'cooldown_active' | null;

interface RejectionDetails {
  kind: RejectionKind;
  message: string;
  cooldownUntil?: string;
}

const formatDate = (iso: string): string => {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      weekday: 'long',
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  } catch {
    return iso;
  }
};

const ProjectStorageKioskPage: React.FC = () => {
  const [username, setUsername] = useState('');
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [projectTitle, setProjectTitle] = useState('');
  const [storageLocation, setStorageLocation] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejection, setRejection] = useState<RejectionDetails | null>(null);
  const [stint, setStint] = useState<ProjectStorageStint | null>(null);

  const usernameRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    usernameRef.current?.focus();
  }, []);

  const reset = () => {
    setUsername('');
    setFirstName('');
    setLastName('');
    setEmail('');
    setProjectTitle('');
    setStorageLocation('');
    setError(null);
    setRejection(null);
    setStint(null);
    setTimeout(() => usernameRef.current?.focus(), 0);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim()) return;
    setSubmitting(true);
    setError(null);
    setRejection(null);
    setStint(null);
    try {
      const resp = await projectStorageAPI.start({
        username: username.trim(),
        first_name: firstName.trim() || undefined,
        last_name: lastName.trim() || undefined,
        email: email.trim() || undefined,
        project_title: projectTitle.trim() || undefined,
        storage_location_name: storageLocation.trim() || undefined,
      });
      setStint(resp.data);
    } catch (err) {
      // Backend uses 409 + a `code` field for the structured rejections.
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        const data = err.response.data as {
          code?: RejectionKind;
          detail?: string;
          cooldown_until?: string;
        };
        setRejection({
          kind: data?.code ?? null,
          message: data?.detail ?? 'Unable to start a new stint right now.',
          cooldownUntil: data?.cooldown_until,
        });
      } else {
        setError(extractErrorMessage(err, 'Could not start the stint.'));
      }
    } finally {
      setSubmitting(false);
    }
  };

  // Once a stint is created, show the result + the rendered label image
  // so the kiosk operator can verify what the printer will produce.
  if (stint) {
    return (
      <div style={{ maxWidth: 720, margin: '40px auto', padding: 16 }}>
        <Paper withBorder radius="md" p="lg" shadow="sm">
          <Stack gap="md">
            <Group gap="sm" align="center">
              <IconCheck size={28} color="green" />
              <Title order={2}>Stint started</Title>
            </Group>

            <Alert color="green" variant="light">
              Stint <strong>{stint.stint_id}</strong> is registered for{' '}
              <strong>{stint.display_name || stint.username}</strong>. The label
              is being sent to the print station now. Stick the label on your
              project and place it in{' '}
              <strong>{stint.storage_location_name || 'project storage'}</strong>.
            </Alert>

            <Paper withBorder radius="md" p="md" bg="gray.0">
              <Stack gap="xs" align="center">
                <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
                  Preview of your label
                </Text>
                <Image
                  src={projectStorageAPI.labelUrl(stint.stint_id, 'brother_ql')}
                  alt={`Label for ${stint.stint_id}`}
                  fit="contain"
                  h={200}
                  fallbackSrc=""
                />
              </Stack>
            </Paper>

            <Stack gap={4}>
              <Text>
                <strong>Expires:</strong> {formatDate(stint.expires_at)}
              </Text>
              <Text size="sm" c="dimmed">
                Week {stint.expiry_week} · Day {stint.expiry_day_of_year}
              </Text>
              <Text size="sm" c="dimmed">
                Project-storage stints last 30 days. Please remove your items
                by the expiry date — there is a 3-day cool-down before you can
                start another stint.
              </Text>
            </Stack>

            <Group justify="flex-end">
              <Button
                leftSection={<IconRefresh size={16} />}
                onClick={reset}
                size="lg"
              >
                Start another stint
              </Button>
            </Group>
          </Stack>
        </Paper>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 540, margin: '40px auto', padding: 16 }}>
      <Paper withBorder radius="md" p="lg" shadow="sm">
        <Stack gap="md">
          <Group gap="sm" align="center">
            <IconPackage size={28} />
            <Title order={2}>Start a project-storage stint</Title>
          </Group>

          <Text size="sm" c="dimmed">
            Enter your member username and (optionally) the name of your
            project. The kiosk prints a label with a QR code and the expiry
            date. Stints last <strong>30 days</strong>; after the items are
            removed there's a <strong>3-day cool-down</strong> before you can
            start another.
          </Text>

          {rejection && (
            <Alert
              icon={<IconAlertCircle size={18} />}
              color="orange"
              variant="light"
            >
              <Text size="sm" fw={600}>
                {rejection.kind === 'cooldown_active'
                  ? 'Cool-down in effect'
                  : 'You already have an active stint'}
              </Text>
              <Text size="sm">{rejection.message}</Text>
              {rejection.cooldownUntil && (
                <Text size="xs" c="dimmed" mt={4}>
                  Next eligible: {new Date(rejection.cooldownUntil).toLocaleString()}
                </Text>
              )}
              <Text size="xs" c="dimmed" mt={4}>
                If you think this is wrong, find the storage warden.
              </Text>
            </Alert>
          )}

          {error && (
            <Alert color="red" variant="light" icon={<IconAlertCircle size={18} />}>
              {error}
            </Alert>
          )}

          <form onSubmit={submit}>
            <Stack gap="sm">
              <TextInput
                ref={usernameRef}
                label="Username"
                placeholder="Your member username"
                value={username}
                onChange={(e) => setUsername(e.currentTarget.value)}
                required
                autoComplete="off"
                spellCheck={false}
                data-testid="kiosk-username-input"
              />
              <Group grow>
                <TextInput
                  label="First name"
                  placeholder="optional"
                  value={firstName}
                  onChange={(e) => setFirstName(e.currentTarget.value)}
                  autoComplete="off"
                />
                <TextInput
                  label="Last name"
                  placeholder="optional"
                  value={lastName}
                  onChange={(e) => setLastName(e.currentTarget.value)}
                  autoComplete="off"
                />
              </Group>
              <TextInput
                label="Email"
                placeholder="optional — used for the violation notice"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.currentTarget.value)}
                autoComplete="off"
              />
              <TextInput
                label="Project name"
                placeholder={'optional — e.g. "Restoring my 1972 Schwinn"'}
                value={projectTitle}
                onChange={(e) => setProjectTitle(e.currentTarget.value)}
                autoComplete="off"
              />
              <TextInput
                label="Storage location"
                placeholder={'optional — e.g. "Shelf A"'}
                value={storageLocation}
                onChange={(e) => setStorageLocation(e.currentTarget.value)}
                autoComplete="off"
              />

              <Button
                type="submit"
                size="lg"
                disabled={!username.trim() || submitting}
                leftSection={submitting ? <Loader size="xs" color="white" /> : null}
                data-testid="kiosk-start-button"
              >
                Start stint &amp; print label
              </Button>
            </Stack>
          </form>

          <Text size="xs" c="dimmed" ta="center">
            Already have items in project storage? See the{' '}
            <Anchor href="/facilities/project-storage" size="xs">storage warden</Anchor>.
          </Text>
        </Stack>
      </Paper>
    </div>
  );
};

export default ProjectStorageKioskPage;
