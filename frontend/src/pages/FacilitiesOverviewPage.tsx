/**
 * Landing page for the Facilities workspace. Same shape as the other
 * overview pages added in this PR: a card grid with one entry per major
 * child route. Lives at `/facilities/` so breadcrumb clicks have a real
 * destination (was previously a 404).
 */
import { Card, Container, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import {
  IconBolt,
  IconBox,
  IconChecklist,
  IconDeviceTv,
  IconKey,
  IconScreenshot,
  IconTruck,
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
    to: '/facilities/tv-dashboard',
    title: 'TV dashboard',
    description: 'Full-screen status board for shop displays.',
    icon: <IconDeviceTv size={28} />,
  },
  {
    to: '/facilities/logistics',
    title: 'Logistics dashboard',
    description: 'Logistics view of open work, reorders, and location problems.',
    icon: <IconTruck size={28} />,
  },
  {
    to: '/facilities/screens',
    title: 'Screens',
    description: 'Configure kiosk and display screens.',
    icon: <IconScreenshot size={28} />,
  },
  {
    to: '/facilities/maker-boxes',
    title: 'Maker boxes',
    description: 'Inventory and check-out activity for member maker boxes.',
    icon: <IconBox size={28} />,
  },
  {
    to: '/facilities/electrical',
    title: 'Electrical',
    description: 'Breakers, outlets, light switches, and network drops.',
    icon: <IconBolt size={28} />,
  },
  {
    to: '/facilities/forgekey-devices',
    title: 'ForgeKey devices',
    description: 'Door, locker, and equipment-control devices on the ForgeKey bus.',
    icon: <IconKey size={28} />,
  },
  {
    to: '/facilities/checklist',
    title: 'Checklists',
    description: 'Recurring opening, closing, and safety walk-through checklists.',
    icon: <IconChecklist size={28} />,
  },
];

const FacilitiesOverviewPage: React.FC = () => (
  <Container size="xl" py="lg">
    <Stack gap="xl">
      <Stack gap={4}>
        <Title order={1}>Facilities</Title>
        <Text c="dimmed">Operations dashboards, kiosks, and the physical-plant systems that keep the shop running.</Text>
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
            data-testid={`facilities-overview-card-${item.to}`}
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

export default FacilitiesOverviewPage;
