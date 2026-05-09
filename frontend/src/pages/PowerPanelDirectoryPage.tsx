/**
 * Power panel directory at `/facilities/electrical/panels`.
 *
 * Lists every PowerPanel with a breaker count, voltage, and a flag for
 * panels that the data migration created as placeholders and that an
 * admin still needs to triage.
 */
import {
  Badge,
  Box,
  Container,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Text,
} from '@mantine/core';
import { IconAlertTriangle, IconBolt, IconSitemap } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import PageHero from '../components/landing/PageHero';
import { electricalSafetyAPI, PowerPanelSummary } from '../services/api';
import '../styles/landing.css';

const phaseLabel = (phase: PowerPanelSummary['phase_configuration']) => {
  switch (phase) {
    case 'single':
      return 'Single phase';
    case 'split':
      return 'Split phase 120/240V';
    case 'three':
      return 'Three phase';
    default:
      return phase;
  }
};

const PowerPanelDirectoryPage: React.FC = () => {
  const [panels, setPanels] = useState<PowerPanelSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    electricalSafetyAPI
      .listPanels()
      .then((response) => {
        if (cancelled) return;
        setPanels(response.data.results);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.response?.data?.detail || 'Failed to load panels.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const reviewCount = panels.filter((p) => p.needs_review).length;

  return (
    <Box className="landing-surface" data-testid="power-panel-directory">
      <Container size="xl" py="xl">
        <Stack gap="xl">
          <PageHero
            eyebrow="Electrical · Topology"
            title="Power panel directory"
            description="Every load center in the building. Open one to see its breakers, circuits, and the assets they feed."
          />

          {error && (
            <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
              <Text>{error}</Text>
            </Paper>
          )}

          {!error && reviewCount > 0 && (
            <Paper withBorder p="md" radius="md" bg="yellow.0" c="yellow.9">
              <Group gap="sm" align="center">
                <IconAlertTriangle size={18} />
                <Text fw={500}>
                  {reviewCount} panel{reviewCount === 1 ? '' : 's'} flagged for review (migration
                  placeholder).
                </Text>
              </Group>
            </Paper>
          )}

          {loading ? (
            <Paper withBorder p="xl" radius="md">
              <Text c="dimmed" ta="center">
                Loading panels…
              </Text>
            </Paper>
          ) : panels.length === 0 ? (
            <Paper withBorder p="xl" radius="md">
              <Stack gap="xs" align="center">
                <IconSitemap size={28} stroke={1.6} />
                <Text fw={500}>No power panels yet</Text>
                <Text size="sm" c="dimmed" ta="center" maw={400}>
                  Add a PowerPanel from Django admin to see it here. The legacy breaker/outlet
                  inventory lives under the "Fixture inventory" card on the Electrical overview.
                </Text>
              </Stack>
            </Paper>
          ) : (
            <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md">
              {panels.map((panel) => (
                <Link
                  key={panel.id}
                  to={`/facilities/electrical/panels/${panel.id}`}
                  className="landing-capability-card"
                  data-testid={`panel-card-${panel.id}`}
                  aria-label={panel.name}
                  style={{ padding: '1.4rem' }}
                >
                  <Stack gap="md" h="100%">
                    <Group justify="space-between" align="flex-start" wrap="nowrap">
                      <Box className="landing-icon-chip" aria-hidden>
                        <IconBolt size={22} stroke={1.8} />
                      </Box>
                      {panel.needs_review && (
                        <Badge color="yellow" variant="light" size="sm" radius="sm">
                          Review
                        </Badge>
                      )}
                    </Group>
                    <Stack gap={4} style={{ flex: 1 }}>
                      <Text component="span" className="landing-eyebrow">
                        {panel.location_name}
                      </Text>
                      <Text component="h3" fw={600} size="lg" style={{ margin: 0, lineHeight: 1.25 }}>
                        {panel.name}
                      </Text>
                      <Text size="sm" c="dimmed" style={{ lineHeight: 1.5 }}>
                        {phaseLabel(panel.phase_configuration)} · {panel.voltage}V
                        {panel.main_breaker_amperage
                          ? ` · ${panel.main_breaker_amperage}A main`
                          : ''}
                      </Text>
                    </Stack>
                    <Group justify="space-between" align="center">
                      <Text size="sm" c="dimmed">
                        {panel.breaker_count} breaker
                        {panel.breaker_count === 1 ? '' : 's'}
                      </Text>
                      <span className="landing-arrow">Open →</span>
                    </Group>
                  </Stack>
                </Link>
              ))}
            </SimpleGrid>
          )}
        </Stack>
      </Container>
    </Box>
  );
};

export default PowerPanelDirectoryPage;
