/**
 * Single power panel topology view at
 * `/facilities/electrical/panels/:id`.
 *
 * Renders the breaker → circuit → outlet tree returned by
 * `/api/electrical/panels/<id>/topology/` so a maintainer can scan a
 * panel without picking through Django admin. Each breaker links to its
 * trip-impact view ("what loses power if I trip this?") and each
 * circuit links to its load report.
 */
import {
  Badge,
  Box,
  Container,
  Group,
  Paper,
  Stack,
  Table,
  Text,
} from '@mantine/core';
import { IconBolt, IconRouteAltLeft } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import PageHero from '../components/landing/PageHero';
import { electricalSafetyAPI, PowerPanelTopology } from '../services/api';
import '../styles/landing.css';

const PowerPanelDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [topology, setTopology] = useState<PowerPanelTopology | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);
    electricalSafetyAPI
      .getPanelTopology(id)
      .then((response) => {
        if (cancelled) return;
        setTopology(response.data);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.response?.data?.detail || 'Failed to load panel topology.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <Box className="landing-surface" data-testid="power-panel-detail">
      <Container size="xl" py="xl">
        <Stack gap="xl">
          <PageHero
            eyebrow={topology ? `Electrical · ${topology.location_name}` : 'Electrical'}
            title={topology ? topology.name : 'Power panel'}
            description={
              topology
                ? `${topology.voltage}V · ${topology.phase_configuration} phase${
                    topology.main_breaker_amperage
                      ? ` · ${topology.main_breaker_amperage}A main`
                      : ''
                  }`
                : 'Loading panel topology…'
            }
          />

          {error && (
            <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
              <Text>{error}</Text>
            </Paper>
          )}

          {loading && !topology && (
            <Paper withBorder p="xl" radius="md">
              <Text c="dimmed" ta="center">
                Loading…
              </Text>
            </Paper>
          )}

          {topology && topology.breakers.length === 0 && (
            <Paper withBorder p="xl" radius="md">
              <Stack gap="xs" align="center">
                <IconBolt size={28} stroke={1.6} />
                <Text fw={500}>No breakers configured for this panel</Text>
                <Text size="sm" c="dimmed">
                  Add breakers via Django admin to populate this view.
                </Text>
              </Stack>
            </Paper>
          )}

          {topology &&
            topology.breakers.map((breaker) => (
              <Paper
                key={breaker.id}
                withBorder
                p="md"
                radius="md"
                data-testid={`breaker-row-${breaker.id}`}
              >
                <Stack gap="sm">
                  <Group justify="space-between" align="flex-start" wrap="wrap">
                    <Stack gap={2}>
                      <Text component="span" className="landing-eyebrow">
                        Breaker · position {breaker.position}
                      </Text>
                      <Text fw={600} size="lg">
                        {breaker.label || `Breaker ${breaker.position}`}
                      </Text>
                      <Text size="sm" c="dimmed">
                        {breaker.amperage}A · {breaker.pole_count}-pole · phase {breaker.phase}
                      </Text>
                    </Stack>
                    <Group gap="xs">
                      <Badge
                        color={breaker.status === 'on' ? 'green' : 'gray'}
                        variant="light"
                        size="sm"
                        radius="sm"
                      >
                        {breaker.status}
                      </Badge>
                      <Link
                        to={`/facilities/electrical/breakers/${breaker.id}/trip-impact`}
                        className="landing-arrow"
                        data-testid={`breaker-trip-impact-${breaker.id}`}
                        style={{ textDecoration: 'none' }}
                      >
                        <IconRouteAltLeft size={16} stroke={2.4} />
                        Trip impact
                      </Link>
                    </Group>
                  </Group>

                  {breaker.circuits.length === 0 ? (
                    <Text size="sm" c="dimmed">
                      No circuits wired to this breaker yet.
                    </Text>
                  ) : (
                    <Table withTableBorder withColumnBorders striped>
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th>Circuit</Table.Th>
                          <Table.Th>Conductor</Table.Th>
                          <Table.Th>Capacity</Table.Th>
                          <Table.Th>Outlets</Table.Th>
                          <Table.Th>Load report</Table.Th>
                        </Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {breaker.circuits.map((circuit) => (
                          <Table.Tr key={circuit.id}>
                            <Table.Td>
                              <Text fw={500}>{circuit.label || `Circuit ${circuit.id}`}</Text>
                            </Table.Td>
                            <Table.Td>{circuit.conductor_size || '—'}</Table.Td>
                            <Table.Td>
                              {circuit.max_load_amps != null ? `${circuit.max_load_amps}A` : '—'}
                            </Table.Td>
                            <Table.Td>
                              {circuit.outlets.length === 0 ? (
                                <Text size="sm" c="dimmed">
                                  no outlets
                                </Text>
                              ) : (
                                <Stack gap={2}>
                                  {circuit.outlets.map((outlet) => (
                                    <Text key={outlet.id} size="sm">
                                      {outlet.label || `Outlet ${outlet.id}`}
                                      {outlet.location_name ? ` — ${outlet.location_name}` : ''}
                                    </Text>
                                  ))}
                                </Stack>
                              )}
                            </Table.Td>
                            <Table.Td>
                              <Link
                                to={`/facilities/electrical/circuits/${circuit.id}/load`}
                                data-testid={`circuit-load-${circuit.id}`}
                              >
                                Load
                              </Link>
                            </Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                  )}
                </Stack>
              </Paper>
            ))}
        </Stack>
      </Container>
    </Box>
  );
};

export default PowerPanelDetailPage;
