/**
 * PageHero — the top-of-page banner for the homepage and workspace
 * overview pages. Renders an optional eyebrow (mono caps), a display
 * title, an optional description, and an optional action slot
 * (right-aligned on wide viewports, stacked under the description on
 * narrow ones).
 *
 * Usage:
 *   <PageHero
 *     eyebrow="Reports"
 *     title="Operational + executive reporting"
 *     description="Pulse, inventory, purchasing, and asset reports."
 *     action={<ShareLinkButton ... />}
 *   />
 *
 * AC-2: this is the canonical hero used by the homepage and every
 * workspace landing page so typography + spacing stay consistent.
 */
import { Box, Group, Stack, Text } from '@mantine/core';
import React from 'react';

import '../../styles/landing.css';

export interface PageHeroProps {
  eyebrow?: string;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
}

const PageHero: React.FC<PageHeroProps> = ({ eyebrow, title, description, action }) => (
  <Box component="header" data-testid="page-hero" mb="xl">
    <Group justify="space-between" align="flex-end" wrap="wrap" gap="md">
      <Stack gap="xs" style={{ flex: '1 1 360px' }}>
        {eyebrow && (
          <Text component="span" className="landing-eyebrow" data-testid="page-hero-eyebrow">
            {eyebrow}
          </Text>
        )}
        <Text
          component="h1"
          className="landing-display"
          data-testid="page-hero-title"
          style={{ fontSize: 'clamp(1.9rem, 3.4vw, 2.7rem)', margin: 0 }}
        >
          {title}
        </Text>
        {description && (
          <Text
            size="md"
            c="dimmed"
            data-testid="page-hero-description"
            style={{ maxWidth: 64 * 8 }}
          >
            {description}
          </Text>
        )}
      </Stack>
      {action && (
        <Box data-testid="page-hero-action" style={{ flexShrink: 0 }}>
          {action}
        </Box>
      )}
    </Group>
  </Box>
);

export default PageHero;
