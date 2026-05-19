/**
 * The visual content of the per-Location Safety Sign (oms-gzycmj).
 *
 * Extracted from the page component so the on-screen and print routes
 * can share rendering without duplicating layout decisions. The print
 * route wraps this in the minimal chrome described by
 * ``styles/safety-sign-print.css``.
 */
import { Badge, Box, Group, Stack, Text } from '@mantine/core';
import React from 'react';

import { SafetySheet } from '../services/api';

export interface LocationSafetySignBodyProps {
  sheet: SafetySheet;
}

const formatBreaker = (panel: string | null, position: string | null) => {
  if (!panel) return 'No breaker recorded';
  if (!position) return panel;
  return `${panel} · ${position}`;
};

const LocationSafetySignBody: React.FC<LocationSafetySignBodyProps> = ({ sheet }) => {
  const lights = sheet.lights;
  const outlets = sheet.outlets;
  const thermostats = sheet.thermostats;
  const killBreakers = sheet.all_kill_breakers;

  return (
    <Stack gap="md" data-testid="safety-sign-body">
      <Box>
        <Text size="xl" fw={800}>
          {sheet.location.name.toUpperCase()} — Safety Sign
        </Text>
        <Text size="xs" c="dimmed">
          Generated from OMS — trip the breakers below to de-energize the room.
        </Text>
      </Box>

      <Box className="safety-sign-section" data-testid="safety-sign-lights">
        <Text className="safety-sign-section-title">Lights</Text>
        {lights.length === 0 ? (
          <Text size="sm" c="dimmed">
            No light switches recorded for this room.
          </Text>
        ) : (
          <Stack gap={2}>
            {lights.map((l) => (
              <Group key={l.switch_id} className="safety-sign-row" justify="space-between">
                <span className="safety-sign-row-label">{l.switch_label}</span>
                <span className="safety-sign-row-meta">
                  {formatBreaker(l.panel, l.position)}
                  {l.amperage ? ` (${l.amperage}A)` : ''}
                </span>
              </Group>
            ))}
          </Stack>
        )}
      </Box>

      <Box className="safety-sign-section" data-testid="safety-sign-outlets">
        <Text className="safety-sign-section-title">
          Outlets
          {outlets.length > 0 && (
            <Text component="span" size="xs" c="dimmed" ml="sm">
              {outlets.reduce((sum, g) => sum + g.outlet_count, 0)} outlets across{' '}
              {outlets.length} breaker{outlets.length === 1 ? '' : 's'}
            </Text>
          )}
        </Text>
        {outlets.length === 0 ? (
          <Text size="sm" c="dimmed">
            No outlets recorded for this room.
          </Text>
        ) : (
          <Stack gap={2}>
            {outlets.map((g) => (
              <Group
                key={`${g.source}-${g.panel ?? '-'}-${g.position ?? '-'}`}
                className="safety-sign-row"
                justify="space-between"
              >
                <span className="safety-sign-row-label">
                  {formatBreaker(g.panel, g.position)}
                  {g.amperage ? ` (${g.amperage}A)` : ''}
                </span>
                <span className="safety-sign-row-meta">
                  {g.outlet_count} outlet{g.outlet_count === 1 ? '' : 's'}
                </span>
              </Group>
            ))}
          </Stack>
        )}
      </Box>

      <Box className="safety-sign-section" data-testid="safety-sign-climate">
        <Text className="safety-sign-section-title">Climate</Text>
        {thermostats.length === 0 ? (
          <Text size="sm" c="dimmed">
            No thermostats registered for this room.
          </Text>
        ) : (
          <Stack gap="xs">
            {thermostats.map((t) => (
              <Box key={t.id} data-testid={`safety-sign-thermostat-${t.id}`}>
                <Group justify="space-between" gap="xs" wrap="wrap">
                  <span className="safety-sign-row-label">
                    {t.label}
                    {t.controlled_asset ? ` → ${t.controlled_asset.name}` : ''}
                  </span>
                  {t.needs_review && (
                    <Badge color="orange" variant="filled" data-testid="needs-review-badge">
                      Needs review
                    </Badge>
                  )}
                </Group>
                {t.kill_breakers.length === 0 ? (
                  <Text size="xs" c="orange" mt={2}>
                    No kill breakers resolved. Confirm the controlled asset.
                  </Text>
                ) : (
                  <Group gap={4} mt={2}>
                    {t.kill_breakers.map((b) => (
                      <Badge key={b.breaker_id} variant="light" color="red">
                        Kill: {b.panel} · {b.position}
                        {b.amperage ? ` (${b.amperage}A)` : ''}
                      </Badge>
                    ))}
                  </Group>
                )}
              </Box>
            ))}
          </Stack>
        )}
      </Box>

      <Box className="safety-sign-emergency" data-testid="safety-sign-emergency">
        <Text className="safety-sign-emergency-title">EMERGENCY</Text>
        <Text size="xs">Trip these to de-energize the room:</Text>
        {killBreakers.length === 0 ? (
          <Text size="sm" c="dimmed" mt="xs">
            No breakers have been linked to this room yet.
          </Text>
        ) : (
          <Box className="safety-sign-kill-grid">
            {killBreakers.map((b) => (
              <span key={`${b.panel}-${b.position}`} className="safety-sign-kill-pill">
                {b.panel} · {b.position}
              </span>
            ))}
          </Box>
        )}
      </Box>
    </Stack>
  );
};

export default LocationSafetySignBody;
