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
  Button,
  Container,
  Group,
  Paper,
  SegmentedControl,
  Stack,
  Table,
  Text,
} from '@mantine/core';
import { IconBolt, IconEdit, IconPlus, IconPrinter, IconRouteAltLeft } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import OfflineSyncBadge from '../components/OfflineSyncBadge';
import PageHero from '../components/landing/PageHero';
import PanelLayoutGrid from '../components/PanelLayoutGrid';
import { electricalSafetyAPI, PowerPanelTopology } from '../services/api';
import '../styles/landing.css';

type PanelView = 'panel' | 'list';

const isPanelView = (v: string | null): v is PanelView => v === 'panel' || v === 'list';

const PowerPanelDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const viewParam = searchParams.get('view');
  const view: PanelView = isPanelView(viewParam) ? viewParam : 'panel';
  const setView = (next: PanelView) => {
    const params = new URLSearchParams(searchParams);
    if (next === 'panel') {
      params.delete('view');
    } else {
      params.set('view', next);
    }
    setSearchParams(params, { replace: true });
  };
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
            eyebrow={
              topology
                ? topology.fed_by_summary
                  ? `Electrical · ${topology.location_name} · Sub-panel of ${topology.fed_by_summary.panel_name}`
                  : `Electrical · ${topology.location_name}`
                : 'Electrical'
            }
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
            action={
              topology ? (
                <Group gap="sm">
                  <OfflineSyncBadge />
                  <Button
                    component="a"
                    href={`/facilities/electrical/panels/${topology.id}/print`}
                    target="_blank"
                    rel="noopener noreferrer"
                    variant="default"
                    leftSection={<IconPrinter size={16} />}
                  >
                    Print directory
                  </Button>
                  <Button
                    component={Link}
                    to={`/facilities/electrical/panels/${topology.id}/edit`}
                    variant="default"
                    leftSection={<IconEdit size={16} />}
                  >
                    Edit panel
                  </Button>
                  <Button
                    component={Link}
                    to={`/facilities/electrical/panels/${topology.id}/breakers/new`}
                    leftSection={<IconPlus size={16} />}
                  >
                    Add breaker
                  </Button>
                </Group>
              ) : undefined
            }
          />

          {topology && topology.breakers.length > 0 && (
            <Group justify="space-between" align="center" wrap="wrap">
              <SegmentedControl
                data={[
                  { label: 'Panel layout', value: 'panel' },
                  { label: 'List', value: 'list' },
                ]}
                value={view}
                onChange={(v) => setView(v as PanelView)}
                data-testid="panel-view-toggle"
              />
              <Text size="sm" c="dimmed">
                {view === 'panel'
                  ? 'Odds left, evens right — mirrors the physical panel'
                  : 'One card per breaker with full circuit detail'}
              </Text>
            </Group>
          )}

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

          {topology && (topology.fed_by_summary || topology.downstream_panels.length > 0) && (
            <Paper withBorder p="md" radius="md" data-testid="panel-lineage">
              <Stack gap="xs">
                {topology.fed_by_summary && (
                  <Group gap="xs" wrap="wrap">
                    <Text size="sm" c="dimmed">
                      Sub-panel of
                    </Text>
                    <Link
                      to={`/facilities/electrical/panels/${topology.fed_by_summary.panel_id}`}
                      style={{ fontWeight: 600 }}
                    >
                      {topology.fed_by_summary.panel_name}
                    </Link>
                    <Text size="sm" c="dimmed">
                      · fed by breaker {topology.fed_by_summary.breaker_position} (
                      {topology.fed_by_summary.breaker_amperage}A{' '}
                      {topology.fed_by_summary.breaker_pole_count}P)
                      {topology.fed_by_summary.circuit_label
                        ? ` · circuit "${topology.fed_by_summary.circuit_label}"`
                        : ''}
                    </Text>
                  </Group>
                )}
                {topology.downstream_panels.length > 0 && (
                  <Group gap="xs" wrap="wrap">
                    <Text size="sm" c="dimmed">
                      Sub-panels:
                    </Text>
                    {topology.downstream_panels.map((sp) => (
                      <Link
                        key={sp.id}
                        to={`/facilities/electrical/panels/${sp.id}`}
                        data-testid={`downstream-panel-${sp.id}`}
                      >
                        {sp.name}
                      </Link>
                    ))}
                  </Group>
                )}
              </Stack>
            </Paper>
          )}

          {topology && topology.breakers.length === 0 && (
            <Paper withBorder p="xl" radius="md">
              <Stack gap="sm" align="center">
                <IconBolt size={28} stroke={1.6} />
                <Text fw={500}>No breakers configured for this panel</Text>
                <Text size="sm" c="dimmed" ta="center" maw={380}>
                  Add the first breaker to start populating the panel. Circuits hang off
                  breakers, outlets hang off circuits.
                </Text>
                <Button
                  component={Link}
                  to={`/facilities/electrical/panels/${topology.id}/breakers/new`}
                  leftSection={<IconPlus size={16} />}
                >
                  Add first breaker
                </Button>
              </Stack>
            </Paper>
          )}

          {topology && topology.breakers.length > 0 && view === 'panel' && (
            <PanelLayoutGrid topology={topology} density="interactive" />
          )}

          {topology &&
            view === 'list' &&
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
                    <Group gap="xs" wrap="wrap">
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
                      <Button
                        component={Link}
                        to={`/facilities/electrical/breakers/${breaker.id}/edit`}
                        size="xs"
                        variant="default"
                        leftSection={<IconEdit size={14} />}
                      >
                        Edit
                      </Button>
                      <Button
                        component={Link}
                        to={`/facilities/electrical/breakers/${breaker.id}/circuits/new`}
                        size="xs"
                        leftSection={<IconPlus size={14} />}
                      >
                        Add circuit
                      </Button>
                    </Group>
                  </Group>

                  {breaker.circuits.length === 0 ? (
                    <Group gap="sm" align="center">
                      <Text size="sm" c="dimmed">
                        No circuits wired to this breaker yet.
                      </Text>
                      <Button
                        component={Link}
                        to={`/facilities/electrical/breakers/${breaker.id}/circuits/new`}
                        size="xs"
                        variant="light"
                        leftSection={<IconPlus size={14} />}
                      >
                        Add circuit
                      </Button>
                    </Group>
                  ) : (
                    <Table withTableBorder withColumnBorders striped>
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th>Circuit</Table.Th>
                          <Table.Th>Conductor</Table.Th>
                          <Table.Th>Capacity</Table.Th>
                          <Table.Th>Outlets</Table.Th>
                          <Table.Th>Actions</Table.Th>
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
                                    <Group key={outlet.id} gap="xs" wrap="nowrap">
                                      <Text size="sm" style={{ flex: 1 }}>
                                        {outlet.label || `Outlet ${outlet.id}`}
                                        {outlet.location_name ? ` — ${outlet.location_name}` : ''}
                                      </Text>
                                      <Link
                                        to={`/facilities/electrical/outlets/${outlet.id}/edit`}
                                        aria-label={`Edit outlet ${outlet.label || outlet.id}`}
                                      >
                                        <IconEdit size={14} />
                                      </Link>
                                    </Group>
                                  ))}
                                </Stack>
                              )}
                            </Table.Td>
                            <Table.Td>
                              <Group gap="xs" wrap="wrap">
                                <Link
                                  to={`/facilities/electrical/circuits/${circuit.id}/load`}
                                  data-testid={`circuit-load-${circuit.id}`}
                                >
                                  Load
                                </Link>
                                <Link
                                  to={`/facilities/electrical/circuits/${circuit.id}/edit`}
                                  aria-label={`Edit circuit ${circuit.label || circuit.id}`}
                                >
                                  Edit
                                </Link>
                                <Link
                                  to={`/facilities/electrical/circuits/${circuit.id}/outlets/new`}
                                  aria-label={`Add outlet to circuit ${circuit.label || circuit.id}`}
                                >
                                  + Outlet
                                </Link>
                              </Group>
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
