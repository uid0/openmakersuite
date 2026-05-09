/**
 * Circuit load report at `/facilities/electrical/circuits/:id/load`.
 *
 * Surfaces `/api/electrical/circuits/<id>/load/` so an operator can see
 * the connected devices on a circuit, the estimated nameplate draw, and
 * how that compares to the breaker's NEC-derated capacity (default 80%
 * of the breaker amperage).
 */
import {
  Badge,
  Box,
  Container,
  Group,
  List,
  Paper,
  Progress,
  Stack,
  Text,
  ThemeIcon,
} from '@mantine/core';
import { IconBolt, IconPlugConnected } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

import PageHero from '../components/landing/PageHero';
import { CircuitLoad, electricalSafetyAPI } from '../services/api';
import '../styles/landing.css';

const utilizationColor = (percent: number | null): string => {
  if (percent == null) return 'gray';
  if (percent >= 100) return 'red';
  if (percent >= 80) return 'orange';
  if (percent >= 60) return 'yellow';
  return 'green';
};

const CircuitLoadPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [load, setLoad] = useState<CircuitLoad | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);
    electricalSafetyAPI
      .getCircuitLoad(id)
      .then((response) => {
        if (cancelled) return;
        setLoad(response.data);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.response?.data?.detail || 'Failed to load circuit data.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <Box className="landing-surface" data-testid="circuit-load">
      <Container size="lg" py="xl">
        <Stack gap="xl">
          <PageHero
            eyebrow="Electrical · Circuit"
            title={load ? load.circuit.label || `Circuit ${load.circuit.id}` : 'Circuit load'}
            description="Connected devices, estimated nameplate draw, and capacity utilization vs the NEC 80% derate."
          />

          {error && (
            <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
              <Text>{error}</Text>
            </Paper>
          )}

          {loading && !load && (
            <Paper withBorder p="xl" radius="md">
              <Text c="dimmed" ta="center">
                Loading…
              </Text>
            </Paper>
          )}

          {load && (
            <>
              <Paper withBorder p="md" radius="md">
                <Stack gap="xs">
                  <Group justify="space-between" align="flex-end" wrap="wrap">
                    <Stack gap={2}>
                      <Text component="span" className="landing-eyebrow">
                        Capacity utilization
                      </Text>
                      <Text fw={600} size="xl">
                        {load.capacity_utilization_percent != null
                          ? `${load.capacity_utilization_percent.toFixed(1)}%`
                          : 'No capacity configured'}
                      </Text>
                      <Text size="sm" c="dimmed">
                        {load.estimated_max_draw_amps.toFixed(1)}A estimated draw
                        {load.capacity_amps != null
                          ? ` of ${load.capacity_amps}A circuit capacity`
                          : ''}
                      </Text>
                    </Stack>
                    <Badge
                      color={utilizationColor(load.capacity_utilization_percent)}
                      variant="light"
                      size="lg"
                      radius="sm"
                    >
                      {load.connected_device_count} connected
                    </Badge>
                  </Group>
                  {load.capacity_utilization_percent != null && (
                    <Progress
                      value={Math.min(load.capacity_utilization_percent, 100)}
                      color={utilizationColor(load.capacity_utilization_percent)}
                      size="lg"
                      radius="md"
                      striped={load.capacity_utilization_percent >= 80}
                      animated={load.capacity_utilization_percent >= 100}
                    />
                  )}
                </Stack>
              </Paper>

              <Paper withBorder p="md" radius="md">
                <Stack gap="sm">
                  <Stack gap={2}>
                    <Text component="span" className="landing-eyebrow">
                      Connected devices
                    </Text>
                    <Text fw={500}>
                      {load.connected_device_count} asset
                      {load.connected_device_count === 1 ? '' : 's'} on this circuit
                    </Text>
                  </Stack>
                  {load.connected_devices.length === 0 ? (
                    <Text size="sm" c="dimmed">
                      No assets are wired into this circuit yet.
                    </Text>
                  ) : (
                    <List
                      spacing={4}
                      icon={
                        <ThemeIcon color="gray" variant="light" radius="xl" size={20}>
                          <IconPlugConnected size={12} />
                        </ThemeIcon>
                      }
                    >
                      {load.connected_devices.map((asset) => (
                        <List.Item key={asset.id} data-testid={`circuit-asset-${asset.id}`}>
                          <Text component="span">{asset.name}</Text>{' '}
                          <Text component="span" size="sm" c="dimmed">
                            ({asset.asset_tag})
                          </Text>
                          {asset.is_critical && (
                            <Badge ml="xs" color="red" size="xs" variant="filled">
                              critical
                            </Badge>
                          )}
                        </List.Item>
                      ))}
                    </List>
                  )}
                </Stack>
              </Paper>

              <Paper withBorder p="md" radius="md" bg="gray.0">
                <Group gap="xs" align="center">
                  <ThemeIcon color="gray" variant="light" size={20}>
                    <IconBolt size={12} />
                  </ThemeIcon>
                  <Text size="sm" c="dimmed">
                    Capacity defaults to 80% of breaker amperage per NEC continuous-load rule.
                    Override via Django admin if a different derate applies.
                  </Text>
                </Group>
              </Paper>
            </>
          )}
        </Stack>
      </Container>
    </Box>
  );
};

export default CircuitLoadPage;
