/**
 * WorkspacePage — page shell for non-grid workspace views (lists,
 * dashboards, forms, detail pages). Pairs a ``<PageHero>`` with the
 * warm-paper landing surface so non-overview pages stay visually
 * continuous with the homepage and the workspace overviews.
 *
 * Use ``<WorkspaceLanding>`` instead when the page is a capability-card
 * grid; this component is for everything else (tables, dashboards,
 * forms, detail views).
 *
 * Usage:
 *
 *   <WorkspacePage
 *     hero={{
 *       eyebrow: 'Inventory',
 *       title: 'Items',
 *       description: 'Browse, search, and adjust stock for every SKU.',
 *       action: <Button onClick={create}>Add new item</Button>,
 *     }}
 *   >
 *     <FilterBar />
 *     <ItemsTable />
 *   </WorkspacePage>
 */
import { Box, Container, Stack } from '@mantine/core';
import React from 'react';

import '../../styles/landing.css';
import PageHero, { PageHeroProps } from './PageHero';

export interface WorkspacePageProps {
  hero: PageHeroProps;
  children: React.ReactNode;
  /** Container size; defaults to "xl" to match the homepage. */
  containerSize?: 'sm' | 'md' | 'lg' | 'xl' | 'xxl';
  /** Stable test selector. */
  testId?: string;
}

const WorkspacePage: React.FC<WorkspacePageProps> = ({
  hero,
  children,
  containerSize = 'xl',
  testId = 'workspace-page',
}) => (
  <Box className="landing-surface" data-testid={testId}>
    <Container size={containerSize} py="xl">
      <Stack gap="xl">
        <PageHero {...hero} />
        {children}
      </Stack>
    </Container>
  </Box>
);

export default WorkspacePage;
