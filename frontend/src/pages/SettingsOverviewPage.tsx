/**
 * Landing page for the Settings workspace. Same shape as the other
 * overview pages added in this PR.
 */
import { Card, Container, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { IconAdjustments, IconReceipt, IconUser, IconWebhook } from '@tabler/icons-react';
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
    to: '/settings/profile',
    title: 'Profile',
    description: 'Manage your account, contact info, and notification preferences.',
    icon: <IconUser size={28} />,
  },
  {
    to: '/settings/site',
    title: 'Site settings',
    description: 'Organization-wide configuration for OpenMakerSuite.',
    icon: <IconAdjustments size={28} />,
  },
  {
    to: '/settings/webhooks',
    title: 'Webhooks',
    description: 'Outbound webhook endpoints and delivery history.',
    icon: <IconWebhook size={28} />,
  },
  {
    to: '/settings/tax-receipt/lookup',
    title: 'Tax receipt lookup',
    description: 'Look up an issued donor tax receipt by donation reference.',
    icon: <IconReceipt size={28} />,
  },
];

const SettingsOverviewPage: React.FC = () => (
  <Container size="xl" py="lg">
    <Stack gap="xl">
      <Stack gap={4}>
        <Title order={1}>Settings</Title>
        <Text c="dimmed">Account, organization, and integration configuration.</Text>
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
            data-testid={`settings-overview-card-${item.to}`}
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

export default SettingsOverviewPage;
