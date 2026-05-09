/**
 * Landing page for the Purchasing workspace. Listed under
 * `/purchasing/` so breadcrumb clicks on the "Purchasing" segment have
 * a real destination (was previously a 404).
 *
 * Layout uses Mantine primitives directly; PR2 will refactor onto the
 * shared <CapabilityCard> / <PageHero> / <WorkspaceLanding> components.
 */
import { Card, Container, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { IconClipboardList, IconPlus, IconReceipt } from '@tabler/icons-react';
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
    to: '/purchasing/orders',
    title: 'Purchase orders',
    description: 'Open the queue of pending and recent purchase orders.',
    icon: <IconClipboardList size={28} />,
  },
  {
    to: '/purchasing/orders/new',
    title: 'New purchase order',
    description: 'Start a new purchase order from suggested reorders or a blank template.',
    icon: <IconPlus size={28} />,
  },
  {
    to: '/inventory/transparency',
    title: 'Transparency ledger',
    description: 'Public-facing view of recent reorder activity for membership transparency.',
    icon: <IconReceipt size={28} />,
  },
];

const PurchasingOverviewPage: React.FC = () => (
  <Container size="xl" py="lg">
    <Stack gap="xl">
      <Stack gap={4}>
        <Title order={1}>Purchasing</Title>
        <Text c="dimmed">Manage purchase orders, reorder requests, and vendor transparency.</Text>
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
            data-testid={`purchasing-overview-card-${item.to}`}
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

export default PurchasingOverviewPage;
