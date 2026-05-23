/**
 * Facilities → Checklists landing.
 *
 * Lists every checklist the current user can see (public + their SIGs),
 * with an inline expand for the step list and a one-click "Start" that
 * creates a completion and navigates into the existing
 * ChecklistCompletionPage at /facilities/checklist/<id>/complete/<cid>.
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
} from '@mantine/core';
import { IconCamera, IconChecklist, IconPlayerPlay } from '@tabler/icons-react';
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { useNotifications } from '../hooks/useNotifications';
import { checklistsAPI } from '../services/api';
import { Checklist } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const FacilitiesChecklistsPage: React.FC = () => {
  const navigate = useNavigate();
  const notifications = useNotifications();

  const [checklists, setChecklists] = useState<Checklist[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [startingId, setStartingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const resp = await checklistsAPI.getAvailableChecklists();
      setChecklists(resp.data);
    } catch (err) {
      console.error('Failed to load checklists', err);
      setError(extractErrorMessage(err, 'Unable to load checklists.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleStart = async (checklist: Checklist) => {
    try {
      setStartingId(checklist.id);
      const resp = await checklistsAPI.startChecklist(checklist.id);
      navigate(`/facilities/checklist/${checklist.id}/complete/${resp.data.id}`);
    } catch (err) {
      console.error('Failed to start checklist', err);
      notifications.showError(extractErrorMessage(err, 'Could not start checklist.'));
      setStartingId(null);
    }
  };

  return (
    <WorkspacePage
      testId="facilities-checklists-page"
      hero={{
        eyebrow: 'Facilities',
        title: 'Checklists',
        description:
          'Recurring opening, closing, and safety walk-through checklists. Start one to scan through its steps.',
        action: (
          <Button variant="default" onClick={load}>
            Refresh
          </Button>
        ),
      }}
    >
      {loading && (
        <Paper withBorder p="md" radius="md">
          <Group>
            <Loader size="sm" />
            <Text c="dimmed">Loading checklists…</Text>
          </Group>
        </Paper>
      )}

      {error && (
        <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
          <Text>{error}</Text>
        </Paper>
      )}

      {!loading && !error && checklists.length === 0 && (
        <Paper withBorder p="xl" radius="md">
          <Text c="dimmed" ta="center">
            No checklists are available to you right now. Public checklists
            and any owned by your SIGs will appear here.
          </Text>
        </Paper>
      )}

      {!loading && !error && checklists.length > 0 && (
        <Paper withBorder radius="md" p={0}>
          <Accordion variant="separated" radius="md" multiple chevronPosition="left">
            {checklists.map((c) => {
              const stepCount = c.step_count ?? c.steps?.length ?? 0;
              return (
                <Accordion.Item key={c.id} value={c.id} data-testid={`checklist-row-${c.id}`}>
                  <Accordion.Control>
                    <Group justify="space-between" wrap="nowrap">
                      <Group gap="sm" wrap="nowrap">
                        <IconChecklist size={20} stroke={1.8} />
                        <Stack gap={2}>
                          <Text fw={600}>{c.name}</Text>
                          {c.description && (
                            <Text size="sm" c="dimmed" lineClamp={2}>
                              {c.description}
                            </Text>
                          )}
                        </Stack>
                      </Group>
                      <Group gap="xs" wrap="nowrap">
                        {c.is_public && <Badge color="blue" variant="light">Public</Badge>}
                        {c.sig_name && (
                          <Badge color="grape" variant="light">{c.sig_name}</Badge>
                        )}
                        <Badge color="gray" variant="light">
                          {stepCount} step{stepCount === 1 ? '' : 's'}
                        </Badge>
                      </Group>
                    </Group>
                  </Accordion.Control>
                  <Accordion.Panel>
                    <Stack gap="sm">
                      <ChecklistStepsPreview checklistId={c.id} fallback={c.steps} />
                      <Group justify="flex-end">
                        <Button
                          leftSection={<IconPlayerPlay size={16} />}
                          onClick={() => handleStart(c)}
                          loading={startingId === c.id}
                          data-testid={`start-checklist-${c.id}`}
                        >
                          Start checklist
                        </Button>
                      </Group>
                    </Stack>
                  </Accordion.Panel>
                </Accordion.Item>
              );
            })}
          </Accordion>
        </Paper>
      )}
    </WorkspacePage>
  );
};

/**
 * Inline preview of a checklist's steps. The `available` endpoint returns
 * a lightweight serializer that may omit step bodies — fetch the full
 * detail on first expand so the preview shows real step names.
 */
const ChecklistStepsPreview: React.FC<{
  checklistId: string;
  fallback?: Checklist['steps'];
}> = ({ checklistId, fallback }) => {
  const [steps, setSteps] = useState<Checklist['steps']>(fallback ?? []);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (steps && steps.length > 0) return;
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const resp = await checklistsAPI.getChecklist(checklistId);
        if (!cancelled) setSteps(resp.data.steps || []);
      } catch (err) {
        if (!cancelled) {
          setError(extractErrorMessage(err, 'Could not load steps.'));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [checklistId, steps]);

  if (loading) {
    return (
      <Group gap="xs">
        <Loader size="xs" />
        <Text size="sm" c="dimmed">Loading steps…</Text>
      </Group>
    );
  }
  if (error) {
    return <Text size="sm" c="red">{error}</Text>;
  }
  if (!steps || steps.length === 0) {
    return <Text size="sm" c="dimmed">This checklist has no steps yet.</Text>;
  }
  return (
    <List size="sm" spacing={4}>
      {steps.map((step) => (
        <List.Item key={step.id}>
          <Group gap="xs" wrap="nowrap">
            <Text size="sm" fw={500}>
              {step.step_number}. {step.name}
            </Text>
            {!step.required && (
              <Badge size="xs" color="gray" variant="light">Optional</Badge>
            )}
            {step.requires_photo && (
              <Badge size="xs" color="orange" variant="light" leftSection={<IconCamera size={10} />}>
                Photo
              </Badge>
            )}
          </Group>
          {step.notes && (
            <Text size="xs" c="dimmed" mt={2}>{step.notes}</Text>
          )}
        </List.Item>
      ))}
    </List>
  );
};

export default FacilitiesChecklistsPage;
