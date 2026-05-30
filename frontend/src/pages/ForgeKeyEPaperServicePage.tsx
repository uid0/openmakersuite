/**
 * ForgeKey ePaper "Log Service" Page
 *
 * Mobile-first page a maintainer lands on after scanning the QR on a
 * mounted ePaper panel (`?did=<uuid>`). It shows the asset's recurring
 * maintenance task(s) — readable by anyone, like the panel face itself —
 * and lets a logged-in member check the work off. Logging a completion
 * records an attributable MaintenanceLog; the panel repaints the reset
 * countdown on its next wake.
 *
 * Mirrors ForgeKeyEPaperBindPage's shape (route param, api service,
 * Mantine UI). Viewing is public; the "Mark complete" action requires a
 * login (`token` in localStorage) and the backend re-checks auth.
 */
import { Alert, Badge, Button, Loader, Paper, Stack, Text, Title } from '@mantine/core';
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { forgekeyAPI } from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface ItemRow {
  id: string;
  title: string;
  interval_days: number | null;
  status: string;
  days_until_due: number | null;
  status_line: string;
  last_completed: string | null;
  instructions: string;
}

interface ServiceInfo {
  display_id: string;
  bound: boolean;
  asset: { id: string; name: string; asset_tag: string; location: string | null };
  items: ItemRow[];
  primary_item_id: string | null;
}

const statusColor = (status: string): string => {
  switch (status) {
    case 'overdue':
      return 'red';
    case 'warning':
      return 'yellow';
    case 'never':
      return 'gray';
    default:
      return 'green';
  }
};

const ForgeKeyEPaperServicePage: React.FC = () => {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const did = params.get('did') ?? '';
  const loggedIn =
    typeof window !== 'undefined' && Boolean(localStorage.getItem('token'));

  const [info, setInfo] = useState<ServiceInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submittingId, setSubmittingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [justCompleted, setJustCompleted] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    if (!did) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setLoadError(null);
    try {
      const response = await forgekeyAPI.getEPaperServiceInfo(did);
      setInfo(response.data);
    } catch (err) {
      const code = (err as { response?: { status?: number } })?.response?.status;
      if (code === 409) {
        setLoadError("This panel isn't bound to an asset yet. Scan the bind QR first.");
      } else if (code === 404) {
        setLoadError('This panel has been retired in OMS.');
      } else {
        setLoadError(extractErrorMessage(err, 'Failed to load panel info.'));
      }
      setInfo(null);
    } finally {
      setLoading(false);
    }
  }, [did]);

  useEffect(() => {
    load();
  }, [load]);

  const handleComplete = useCallback(
    async (itemId: string) => {
      if (!loggedIn) {
        // Punt to the login surface; SessionExpiredBanner restores the URL.
        navigate('/');
        return;
      }
      setSubmittingId(itemId);
      setActionError(null);
      try {
        const response = await forgekeyAPI.completeEPaperService(did, {
          item_id: itemId,
        });
        setJustCompleted((prev) => ({ ...prev, [itemId]: response.data.status_line }));
        await load();
      } catch (err) {
        setActionError(extractErrorMessage(err, 'Failed to record completion.'));
      } finally {
        setSubmittingId(null);
      }
    },
    [did, loggedIn, navigate, load],
  );

  if (!did) {
    return (
      <Paper p="lg" m="md" withBorder>
        <Title order={3}>No display_id</Title>
        <Text mt="sm">
          This page expects a <code>?did=</code> query parameter from the
          panel's QR. Scan the QR on the panel face instead.
        </Text>
      </Paper>
    );
  }

  if (loading) {
    return (
      <Stack align="center" gap="xs" py="xl">
        <Loader />
        <Text size="sm" c="dimmed">
          Loading panel…
        </Text>
      </Stack>
    );
  }

  if (loadError) {
    return (
      <Paper p="lg" m="md" withBorder>
        <Stack gap="sm">
          <Title order={3}>Preventive maintenance</Title>
          <Alert color="red" variant="light">
            {loadError}
          </Alert>
        </Stack>
      </Paper>
    );
  }

  if (!info) {
    return null;
  }

  return (
    <Paper p="lg" m="md" withBorder>
      <Stack gap="md">
        <Stack gap={2}>
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            Preventive maintenance
          </Text>
          <Title order={2}>{info.asset.name}</Title>
          <Text size="sm" c="dimmed">
            {info.asset.location || 'No location'}
            {info.asset.asset_tag ? ` · ${info.asset.asset_tag}` : ''}
          </Text>
        </Stack>

        {!loggedIn && (
          <Alert color="blue" variant="light">
            You can mark a task complete once you’re logged in to OMS.
          </Alert>
        )}
        {actionError && (
          <Alert color="red" variant="light">
            {actionError}
          </Alert>
        )}

        {info.items.length === 0 ? (
          <Text size="sm" c="dimmed">
            No recurring maintenance tasks are scheduled for this asset.
          </Text>
        ) : (
          <Stack gap="sm">
            {info.items.map((item) => {
              const done = justCompleted[item.id];
              return (
                <Paper
                  key={item.id}
                  p="md"
                  withBorder
                  radius="md"
                  data-testid={`item-row-${item.id}`}
                >
                  <Stack gap="xs">
                    <Stack gap={2}>
                      <Text fw={600}>{item.title}</Text>
                      <Badge color={statusColor(done ? 'ok' : item.status)} variant="light">
                        {done || item.status_line}
                      </Badge>
                      <Text size="xs" c="dimmed">
                        {item.last_completed
                          ? `Last serviced: ${item.last_completed}`
                          : 'Last serviced: never'}
                        {item.interval_days ? ` · every ${item.interval_days} days` : ''}
                      </Text>
                      {item.instructions && (
                        <Text size="sm" mt={4} style={{ whiteSpace: 'pre-wrap' }}>
                          {item.instructions}
                        </Text>
                      )}
                    </Stack>
                    <Button
                      onClick={() => handleComplete(item.id)}
                      loading={submittingId === item.id}
                      disabled={Boolean(done)}
                      variant={done ? 'light' : 'filled'}
                      fullWidth
                    >
                      {done
                        ? 'Completed — thank you'
                        : loggedIn
                          ? 'Mark complete'
                          : 'Log in to mark complete'}
                    </Button>
                  </Stack>
                </Paper>
              );
            })}
          </Stack>
        )}

        <Text size="xs" c="dimmed">
          The panel updates on its next wake (within the configured wake
          interval). display_id: <code>{did}</code>
        </Text>
      </Stack>
    </Paper>
  );
};

export default ForgeKeyEPaperServicePage;
