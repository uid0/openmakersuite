/**
 * Home Page (`/`).
 *
 * Capability-showcase landing for OpenMakerSuite (AC-1). Lists every
 * top-level workspace as a CapabilityCard with a 1-line value prop and
 * a deep link, plus a "Common workflows" quick-action row at the top
 * for the highest-traffic flows (scan, dashboard, work orders).
 *
 * Uses the same PageHero + CapabilityCard primitives the workspace
 * overview pages use, so visuals stay continuous across the app (AC-2).
 */
import { Box, Container, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import {
  IconAdjustments,
  IconBoxSeam,
  IconBuildingFactory2,
  IconChartBar,
  IconChartLine,
  IconClipboardList,
  IconLayoutDashboard,
  IconQrcode,
  IconReceipt,
  IconShoppingCart,
  IconStack2,
  IconUsersGroup,
  IconTool,
} from '@tabler/icons-react';
import React, { useState } from 'react';

import AuthSection from '../components/AuthSection';
import Footer from '../components/Footer';
import CapabilityCard from '../components/landing/CapabilityCard';
import PageHero from '../components/landing/PageHero';
import '../styles/landing.css';

interface Capability {
  to: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  eyebrow: string;
  badge?: string;
}

/**
 * Most-used workflows surfaced above the workspace grid. Kept short so
 * a member arriving at /, with a phone in hand, can act in one tap.
 */
const QUICK_WORKFLOWS: Capability[] = [
  {
    to: '/inventory/scan',
    title: 'Scan a QR code',
    description: 'Look up a shelf, asset, or fixture from a printed label.',
    icon: <IconQrcode size={22} stroke={1.8} />,
    eyebrow: '01 / Quick',
  },
  {
    to: '/dashboard',
    title: 'Operations dashboard',
    description: 'Live status across reorders, deliveries, and asset problems.',
    icon: <IconLayoutDashboard size={22} stroke={1.8} />,
    eyebrow: '02 / Quick',
  },
  {
    to: '/maintenance',
    title: 'Maintenance + work orders',
    description: 'Open the PM dashboard, queue up scheduled work, log completions.',
    icon: <IconClipboardList size={22} stroke={1.8} />,
    eyebrow: '03 / Quick',
  },
];

/**
 * Every top-level workspace. Order matches the sidebar so navigation
 * patterns are reinforced rather than competing.
 */
const WORKSPACES: Capability[] = [
  {
    to: '/inventory',
    title: 'Inventory',
    description: 'Items, suppliers, locations, and the QR-coded scan workflows the shop runs on.',
    icon: <IconBoxSeam size={22} stroke={1.8} />,
    eyebrow: 'Inventory',
  },
  {
    to: '/purchasing',
    title: 'Purchasing',
    description: 'Purchase orders, suggested reorders, and the public transparency ledger.',
    icon: <IconShoppingCart size={22} stroke={1.8} />,
    eyebrow: 'Purchasing',
  },
  {
    to: '/assets',
    title: 'Assets',
    description: 'Equipment register: lifecycle, ownership, maintenance plans, and parts.',
    icon: <IconStack2 size={22} stroke={1.8} />,
    eyebrow: 'Assets',
  },
  {
    to: '/facilities',
    title: 'Facilities',
    description: 'Operations dashboards, kiosks, electrical, ForgeKey devices, and checklists.',
    icon: <IconBuildingFactory2 size={22} stroke={1.8} />,
    eyebrow: 'Facilities',
  },
  {
    to: '/maintenance',
    title: 'Maintenance',
    description: 'Preventive maintenance, work orders, and third-party vendor coordination.',
    icon: <IconTool size={22} stroke={1.8} />,
    eyebrow: 'Maintenance',
  },
  {
    to: '/sigs/dashboard',
    title: 'SIGs',
    description: 'Special Interest Group operations: members, assets, and SIG-owned inventory.',
    icon: <IconUsersGroup size={22} stroke={1.8} />,
    eyebrow: 'Community',
  },
  {
    to: '/reports',
    title: 'Reports',
    description: 'Operational reports across inventory, purchasing, and assets.',
    icon: <IconChartBar size={22} stroke={1.8} />,
    eyebrow: 'Reports',
  },
  {
    to: '/analytics',
    title: 'Analytics pulse',
    description: 'Executive dashboard: ROI, utilization, category spend, and the maintenance forecast.',
    icon: <IconChartLine size={22} stroke={1.8} />,
    eyebrow: 'Reports',
    badge: 'Staff',
  },
  {
    to: '/settings',
    title: 'Settings + integrations',
    description: 'Profile, organization configuration, webhooks, and tax-receipt tooling.',
    icon: <IconAdjustments size={22} stroke={1.8} />,
    eyebrow: 'Admin',
  },
];

const HomePage: React.FC = () => {
  const [, setIsLoggedIn] = useState(false);
  const [, setUsername] = useState('');

  const handleAuthChange = (loggedIn: boolean, user?: string) => {
    setIsLoggedIn(loggedIn);
    setUsername(user || '');
  };

  return (
    <Box className="landing-surface" data-testid="home-page">
      <Container size="xl" py="xl">
        <Stack gap="xl">
          <PageHero
            eyebrow="OpenMakerSuite"
            title="Run the shop."
            description="One operations layer for the makerspace — inventory, equipment, maintenance, purchasing, and the analytics that show your members what they get back from the room."
          />

          <Box>
            <AuthSection onAuthChange={handleAuthChange} />
          </Box>

          <Stack gap="md" data-testid="home-quick-workflows">
            <Stack gap={2}>
              <Text component="span" className="landing-eyebrow">Common workflows</Text>
              <Title order={2} className="landing-display" style={{ fontSize: '1.4rem', margin: 0 }}>
                Start here
              </Title>
            </Stack>
            <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md">
              {QUICK_WORKFLOWS.map((c) => (
                <CapabilityCard
                  key={c.to}
                  to={c.to}
                  title={c.title}
                  description={c.description}
                  icon={c.icon}
                  eyebrow={c.eyebrow}
                  testId={`home-quick-${c.to}`}
                  ctaLabel="Go"
                />
              ))}
            </SimpleGrid>
          </Stack>

          <hr className="landing-rule" />

          <Stack gap="md" data-testid="home-workspaces">
            <Stack gap={2}>
              <Text component="span" className="landing-eyebrow">Workspaces</Text>
              <Title order={2} className="landing-display" style={{ fontSize: '1.4rem', margin: 0 }}>
                Everything OpenMakerSuite covers
              </Title>
            </Stack>
            <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md">
              {WORKSPACES.map((w) => (
                <CapabilityCard
                  key={w.to}
                  to={w.to}
                  title={w.title}
                  description={w.description}
                  icon={w.icon}
                  eyebrow={w.eyebrow}
                  badge={w.badge}
                  testId={`home-workspace-${w.to}`}
                />
              ))}
            </SimpleGrid>
          </Stack>
        </Stack>
      </Container>
      <Footer />
    </Box>
  );
};

export default HomePage;
