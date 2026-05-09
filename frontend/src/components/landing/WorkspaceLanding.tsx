/**
 * WorkspaceLanding — page shell composed of <PageHero> + a card grid +
 * an optional footer slot. Used by every workspace overview page so
 * spacing, typography, and card rhythm stay identical (AC-2).
 *
 * Pass capability cards as `children`; this component handles the
 * SimpleGrid layout and the warm-paper landing surface. If a workspace
 * needs more bespoke content beneath the grid (e.g. a list of recent
 * activity), put it in `footer`.
 *
 * Usage:
 *   <WorkspaceLanding
 *     hero={{ eyebrow: "Reports", title: "...", description: "..." }}
 *   >
 *     <CapabilityCard ... />
 *     <CapabilityCard ... />
 *   </WorkspaceLanding>
 */
import { Box, Container, SimpleGrid, Stack } from '@mantine/core';
import React from 'react';

import '../../styles/landing.css';
import PageHero, { PageHeroProps } from './PageHero';

export interface WorkspaceLandingProps {
  /** Hero props passed straight to <PageHero>. */
  hero: PageHeroProps;
  /**
   * Capability cards to lay out in a responsive grid. The grid is 1/2/3
   * columns at base/sm/lg breakpoints — matches the existing dashboard
   * stat-card pattern so workspaces feel rhythmically consistent.
   */
  children?: React.ReactNode;
  /**
   * Optional content rendered below the capability grid (e.g. recent
   * activity, "what changed last month" callouts).
   */
  footer?: React.ReactNode;
  /** Override the column count if the workspace has fewer cards. */
  cols?: { base?: number; sm?: number; lg?: number };
  /** Stable test selector. */
  testId?: string;
}

const WorkspaceLanding: React.FC<WorkspaceLandingProps> = ({
  hero,
  children,
  footer,
  cols = { base: 1, sm: 2, lg: 3 },
  testId = 'workspace-landing',
}) => (
  <Box className="landing-surface" data-testid={testId}>
    <Container size="xl" py="xl">
      <Stack gap="xl">
        <PageHero {...hero} />
        {children && (
          <SimpleGrid cols={cols} spacing="md" verticalSpacing="md">
            {children}
          </SimpleGrid>
        )}
        {footer && <Box>{footer}</Box>}
      </Stack>
    </Container>
  </Box>
);

export default WorkspaceLanding;
