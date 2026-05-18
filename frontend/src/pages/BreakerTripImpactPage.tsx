/**
 * Breaker trip-impact view at
 * `/facilities/electrical/breakers/:id/trip-impact`.
 *
 * Answers "what loses power if I trip this breaker?" by surfacing the
 * `/api/electrical/breakers/<id>/trip-impact/` endpoint. Critical loads
 * are listed first so a maintainer never accidentally drops fire alarms,
 * APs, or freezers.
 */
import { Badge, Box, Container, List, Paper, Stack, Text, ThemeIcon } from '@mantine/core';
import { IconAlertCircle, IconBolt, IconShieldHalfFilled } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

import PageHero from '../components/landing/PageHero';
import { BreakerTripImpact, electricalSafetyAPI } from '../services/api';
import '../styles/landing.css';

const BreakerTripImpactPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [impact, setImpact] = useState<BreakerTripImpact | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);
    electricalSafetyAPI
      .getBreakerTripImpact(id)
      .then((response) => {
        if (cancelled) return;
        setImpact(response.data);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.response?.data?.detail || 'Failed to load trip impact.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <Box className="landing-surface" data-testid="breaker-trip-impact">
      <Container size="lg" py="xl">
        <Stack gap="xl">
          <PageHero
            eyebrow={impact ? `Electrical · ${impact.breaker.panel_name}` : 'Electrical'}
            title={impact ? `Loads on breaker ${impact.breaker.position}` : 'Loads'}
            description="What loses power if this breaker is opened. Verify before maintenance."
          />

          {error && (
            <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
              <Text>{error}</Text>
            </Paper>
          )}

          {loading && !impact && (
            <Paper withBorder p="xl" radius="md">
              <Text c="dimmed" ta="center">
                Loading…
              </Text>
            </Paper>
          )}

          {impact && (
            <>
              <Paper withBorder p="md" radius="md">
                <Stack gap="xs">
                  <Text component="span" className="landing-eyebrow">
                    Breaker
                  </Text>
                  <Text fw={600} size="lg">
                    {impact.breaker.label || `Breaker ${impact.breaker.position}`}
                  </Text>
                  <Text size="sm" c="dimmed">
                    {impact.breaker.amperage}A · {impact.breaker.pole_count}-pole · phase{' '}
                    {impact.breaker.phase}
                  </Text>
                </Stack>
              </Paper>

              <Paper
                withBorder
                p="md"
                radius="md"
                data-testid="trip-impact-critical-loads"
                bg={impact.critical_loads.length > 0 ? 'red.0' : undefined}
              >
                <Stack gap="sm">
                  <Stack gap={2}>
                    <Text component="span" className="landing-eyebrow">
                      Critical loads
                    </Text>
                    <Text fw={600} size="md">
                      {impact.critical_loads.length} critical asset
                      {impact.critical_loads.length === 1 ? '' : 's'} on this breaker
                    </Text>
                  </Stack>
                  {impact.critical_loads.length === 0 ? (
                    <Text size="sm" c="dimmed">
                      No assets on this breaker are flagged as critical.
                    </Text>
                  ) : (
                    <List
                      spacing="xs"
                      icon={
                        <ThemeIcon color="red" radius="xl" size={20}>
                          <IconAlertCircle size={12} />
                        </ThemeIcon>
                      }
                    >
                      {impact.critical_loads.map((asset) => (
                        <List.Item key={asset.id} data-testid={`critical-asset-${asset.id}`}>
                          <Text fw={500} component="span">
                            {asset.name}
                          </Text>{' '}
                          <Text component="span" size="sm" c="dimmed">
                            ({asset.asset_tag})
                          </Text>
                        </List.Item>
                      ))}
                    </List>
                  )}
                </Stack>
              </Paper>

              <Paper withBorder p="md" radius="md" data-testid="trip-impact-all-assets">
                <Stack gap="sm">
                  <Stack gap={2}>
                    <Text component="span" className="landing-eyebrow">
                      All assets
                    </Text>
                    <Text fw={600} size="md">
                      {impact.assets.length} total asset
                      {impact.assets.length === 1 ? '' : 's'} fed by this breaker
                    </Text>
                  </Stack>
                  {impact.assets.length === 0 ? (
                    <Text size="sm" c="dimmed">
                      No assets are wired into this breaker yet.
                    </Text>
                  ) : (
                    <List
                      spacing={4}
                      icon={
                        <ThemeIcon color="gray" variant="light" radius="xl" size={20}>
                          <IconBolt size={12} />
                        </ThemeIcon>
                      }
                    >
                      {impact.assets.map((asset) => (
                        <List.Item key={asset.id} data-testid={`asset-${asset.id}`}>
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
                <Stack gap="xs">
                  <Text component="span" className="landing-eyebrow">
                    Reminder
                  </Text>
                  <Text size="sm" c="dimmed">
                    <ThemeIcon color="gray" variant="light" size={16} mr={6}>
                      <IconShieldHalfFilled size={10} />
                    </ThemeIcon>
                    LOTO requirements for these assets are surfaced under
                    Maintenance · Lockout/Tagout. Cross-check before opening the panel.
                  </Text>
                </Stack>
              </Paper>
            </>
          )}
        </Stack>
      </Container>
    </Box>
  );
};

export default BreakerTripImpactPage;
