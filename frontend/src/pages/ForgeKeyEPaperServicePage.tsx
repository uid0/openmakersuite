/**
 * ForgeKey ePaper "Work Order" Page
 *
 * Mobile-first page a maintainer lands on after scanning the QR on a
 * mounted ePaper panel (`?did=<uuid>`). It presents the asset's recurring
 * maintenance as a work order — lockout/tagout to perform first, tools &
 * materials to gather, the ordered steps to follow — and lets a logged-in
 * member check the work off. Logging a completion records an attributable
 * MaintenanceLog; the panel repaints the reset countdown on its next wake.
 *
 * Mirrors ForgeKeyEPaperBindPage's shape (route param, api service,
 * Mantine UI). Viewing is public; the "Mark complete" action requires a
 * login (`token` in localStorage) and the backend re-checks auth.
 */
import {
  Alert,
  Badge,
  Button,
  Divider,
  Group,
  List,
  Loader,
  Paper,
  Stack,
  Text,
  ThemeIcon,
  Title,
} from '@mantine/core';
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { forgekeyAPI } from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface Step {
  order: number;
  title: string;
  description: string;
  is_required: boolean;
}

interface Material {
  name: string;
  quantity: string | null;
  unit: string;
  location: string;
  on_hand: number | null;
  sku: string;
  inventory_item_id: string | null;
}

interface Tool {
  name: string;
  quantity: number;
  is_required: boolean;
  notes: string;
  location: string;
  on_hand: number | null;
  sku: string;
  inventory_item_id: string | null;
}

interface EnergySource {
  source_type: string;
  source_type_display: string;
  magnitude: string;
  isolation_point: string;
  notes: string;
  devices: Array<{ device_type: string; label: string }>;
}

interface Loto {
  instructions: string;
  energy_sources: EnergySource[];
}

interface Power {
  wiring_type: string;
  breaker: {
    label: string;
    position: string;
    amperage: number | null;
    panel: string;
    panel_location: string;
  } | null;
  disconnect: { label: string; type: string; location: string } | null;
  breaker_location: string;
  electrical_box: string;
  suite: string;
}

interface ItemRow {
  id: string;
  title: string;
  interval_days: number | null;
  status: string;
  days_until_due: number | null;
  status_line: string;
  last_completed: string | null;
  instructions: string;
  estimated_time_minutes: number | null;
  steps: Step[];
  tools: Tool[];
  materials: Material[];
}

interface ServiceInfo {
  display_id: string;
  bound: boolean;
  asset: { id: string; name: string; asset_tag: string; location: string | null };
  loto: Loto;
  power: Power;
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

const formatMaterial = (material: Material): string => {
  const qty = [material.quantity, material.unit].filter(Boolean).join(' ').trim();
  return qty ? `${material.name} — ${qty}` : material.name;
};

const LotoSection: React.FC<{ loto: Loto }> = ({ loto }) => {
  const hasContent = loto.instructions || loto.energy_sources.length > 0;
  if (!hasContent) {
    return null;
  }
  return (
    <Alert color="red" variant="light" title="Lockout / Tagout — isolate before servicing">
      <Stack gap="xs">
        {loto.instructions && (
          <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
            {loto.instructions}
          </Text>
        )}
        {loto.energy_sources.map((source, idx) => (
          <Stack key={idx} gap={2}>
            <Text size="sm" fw={600}>
              {source.source_type_display}
              {source.magnitude ? ` · ${source.magnitude}` : ''}
            </Text>
            {source.isolation_point && (
              <Text size="xs" c="dimmed">
                Isolate at: {source.isolation_point}
              </Text>
            )}
            {source.devices.length > 0 && (
              <Text size="xs" c="dimmed">
                Locks: {source.devices.map((d) => `${d.label} (${d.device_type})`).join(', ')}
              </Text>
            )}
            {source.notes && (
              <Text size="xs" c="dimmed">
                {source.notes}
              </Text>
            )}
          </Stack>
        ))}
      </Stack>
    </Alert>
  );
};

const hasPower = (power: Power): boolean =>
  Boolean(
    power.breaker ||
      power.disconnect ||
      power.breaker_location ||
      power.electrical_box ||
      power.suite,
  );

const PowerSection: React.FC<{ power: Power }> = ({ power }) => {
  if (!hasPower(power)) {
    return null;
  }
  const breakerLine = power.breaker
    ? [
        power.breaker.label,
        power.breaker.position ? `pos ${power.breaker.position}` : '',
        power.breaker.amperage ? `${power.breaker.amperage}A` : '',
      ]
        .filter(Boolean)
        .join(' · ')
    : '';
  return (
    <Alert color="orange" variant="light" title="Power — find this before servicing">
      <Stack gap={4}>
        {power.breaker && (
          <Text size="sm">
            <Text span fw={600}>
              Breaker:
            </Text>{' '}
            {breakerLine || 'see panel'}
            {power.breaker.panel ? ` — ${power.breaker.panel}` : ''}
            {power.breaker.panel_location ? ` (${power.breaker.panel_location})` : ''}
          </Text>
        )}
        {!power.breaker && power.breaker_location && (
          <Text size="sm">
            <Text span fw={600}>
              Breaker:
            </Text>{' '}
            {power.breaker_location}
          </Text>
        )}
        {power.disconnect && (
          <Text size="sm">
            <Text span fw={600}>
              Disconnect:
            </Text>{' '}
            {[power.disconnect.label, power.disconnect.type].filter(Boolean).join(' · ')}
            {power.disconnect.location ? ` — ${power.disconnect.location}` : ''}
          </Text>
        )}
        {power.electrical_box && (
          <Text size="sm">
            <Text span fw={600}>
              Panel / box:
            </Text>{' '}
            {power.electrical_box}
          </Text>
        )}
        {power.suite && (
          <Text size="sm">
            <Text span fw={600}>
              Area:
            </Text>{' '}
            {power.suite}
          </Text>
        )}
        {power.wiring_type && (
          <Text size="xs" c="dimmed">
            Wiring: {power.wiring_type}
          </Text>
        )}
      </Stack>
    </Alert>
  );
};

/** Where a tool/consumable lives + how many are on hand (when known). */
const SupplyWhere: React.FC<{ location: string; onHand: number | null }> = ({
  location,
  onHand,
}) => {
  if (!location && onHand == null) {
    return null;
  }
  return (
    <Text size="xs" c="dimmed">
      {location ? `📍 ${location}` : 'Location not set'}
      {onHand != null ? ` · ${onHand} on hand` : ''}
    </Text>
  );
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
          Loading work order…
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
            Maintenance work order
          </Text>
          <Title order={2}>{info.asset.name}</Title>
          <Text size="sm" c="dimmed">
            {info.asset.location || 'No location'}
            {info.asset.asset_tag ? ` · ${info.asset.asset_tag}` : ''}
          </Text>
        </Stack>

        <PowerSection power={info.power} />
        <LotoSection loto={info.loto} />

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
          <Stack gap="md">
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
                  <Stack gap="sm">
                    <Group justify="space-between" wrap="nowrap" align="flex-start">
                      <Text fw={600}>{item.title}</Text>
                      <Badge color={statusColor(done ? 'ok' : item.status)} variant="light">
                        {done || item.status_line}
                      </Badge>
                    </Group>

                    <Text size="xs" c="dimmed">
                      {item.last_completed
                        ? `Last serviced: ${item.last_completed}`
                        : 'Last serviced: never'}
                      {item.interval_days ? ` · every ${item.interval_days} days` : ''}
                      {item.estimated_time_minutes ? ` · ~${item.estimated_time_minutes} min` : ''}
                    </Text>

                    {item.instructions && (
                      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                        {item.instructions}
                      </Text>
                    )}

                    {item.tools.length > 0 && (
                      <Stack gap={4}>
                        <Text size="sm" fw={600}>
                          Tools to gather
                        </Text>
                        <List size="sm" spacing={4}>
                          {item.tools.map((tool, idx) => (
                            <List.Item
                              key={idx}
                              icon={
                                tool.is_required ? (
                                  <ThemeIcon color="red" size={16} radius="xl">
                                    <Text size="9px" fw={700}>
                                      !
                                    </Text>
                                  </ThemeIcon>
                                ) : undefined
                              }
                            >
                              <Text size="sm" span>
                                {tool.quantity > 1 ? `${tool.quantity}× ` : ''}
                                {tool.name}
                              </Text>
                              <SupplyWhere location={tool.location} onHand={tool.on_hand} />
                              {tool.notes && (
                                <Text size="xs" c="dimmed">
                                  {tool.notes}
                                </Text>
                              )}
                            </List.Item>
                          ))}
                        </List>
                      </Stack>
                    )}

                    {item.materials.length > 0 && (
                      <Stack gap={4}>
                        <Text size="sm" fw={600}>
                          Consumables
                        </Text>
                        <List size="sm" spacing={4}>
                          {item.materials.map((material, idx) => (
                            <List.Item key={idx}>
                              <Text size="sm" span>
                                {formatMaterial(material)}
                              </Text>
                              <SupplyWhere
                                location={material.location}
                                onHand={material.on_hand}
                              />
                            </List.Item>
                          ))}
                        </List>
                      </Stack>
                    )}

                    {item.steps.length > 0 && (
                      <Stack gap={4}>
                        <Text size="sm" fw={600}>
                          Steps
                        </Text>
                        <List type="ordered" size="sm" spacing={4}>
                          {item.steps.map((step) => (
                            <List.Item
                              key={step.order}
                              icon={
                                step.is_required ? (
                                  <ThemeIcon color="red" size={16} radius="xl">
                                    <Text size="9px" fw={700}>
                                      !
                                    </Text>
                                  </ThemeIcon>
                                ) : undefined
                              }
                            >
                              <Text size="sm" fw={500} span>
                                {step.title}
                              </Text>
                              {step.description && (
                                <Text size="xs" c="dimmed" style={{ whiteSpace: 'pre-wrap' }}>
                                  {step.description}
                                </Text>
                              )}
                            </List.Item>
                          ))}
                        </List>
                      </Stack>
                    )}

                    <Divider />
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
