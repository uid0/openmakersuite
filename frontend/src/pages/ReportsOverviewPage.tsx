/**
 * Landing page for the Reports workspace. Same shape as the other
 * overview pages added in this PR. Includes the new Analytics Pulse
 * dashboard so it is reachable through breadcrumb + sidebar navigation.
 */
import { Card, Container, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import {
  IconChartBar,
  IconReportAnalytics,
  IconShoppingCart,
  IconStack,
} from '@tabler/icons-react';
import React from 'react';
import { Link } from 'react-router-dom';

interface OverviewItem {
  to: string;
  title: string;
  description: string;
  icon: React.ReactNode;
}

const ITEMS: OverviewItem[] = [
  {
    to: '/analytics',
    title: 'Analytics pulse',
    description:
      'Executive dashboard: ROI, utilization, category spend, and the maintenance forecast. Source for the monthly board email.',
    icon: <IconChartBar size={28} />,
  },
  {
    to: '/reports/inventory',
    title: 'Inventory report',
    description: 'Stock levels, low-stock alerts, and item-level activity.',
    icon: <IconStack size={28} />,
  },
  {
    to: '/reports/purchasing',
    title: 'Purchasing report',
    description: 'Spend by supplier, vendor performance, and reorder cycle metrics.',
    icon: <IconShoppingCart size={28} />,
  },
  {
    to: '/reports/assets',
    title: 'Asset report',
    description: 'Asset roster, lifecycle status, and maintenance history.',
    icon: <IconReportAnalytics size={28} />,
  },
];

const ReportsOverviewPage: React.FC = () => (
  <Container size="xl" py="lg">
    <Stack gap="xl">
      <Stack gap={4}>
        <Title order={1}>Reports</Title>
        <Text c="dimmed">Operational and executive reports across inventory, purchasing, assets, and the analytics pulse.</Text>
      </Stack>

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md">
        {ITEMS.map((item) => (
          <Card
            key={item.to}
            withBorder
            radius="md"
            p="lg"
            component={Link}
            to={item.to}
            data-testid={`reports-overview-card-${item.to}`}
            style={{ textDecoration: 'none', color: 'inherit' }}
          >
            <Stack gap="sm">
              <Text c="blue.7">{item.icon}</Text>
              <Title order={4}>{item.title}</Title>
              <Text size="sm" c="dimmed">{item.description}</Text>
            </Stack>
          </Card>
        ))}
      </SimpleGrid>
    </Stack>
  </Container>
);

export default ReportsOverviewPage;
