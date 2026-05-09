/**
 * Landing page for the Settings workspace, mounted at `/settings/`.
 * Refactored in nav-cohesion PR2 to use the shared <WorkspaceLanding>
 * + <CapabilityCard> primitives.
 */
import { IconAdjustments, IconReceipt, IconUser, IconWebhook } from '@tabler/icons-react';
import React from 'react';

import CapabilityCard from '../components/landing/CapabilityCard';
import WorkspaceLanding from '../components/landing/WorkspaceLanding';

const SettingsOverviewPage: React.FC = () => (
  <WorkspaceLanding
    testId="settings-overview"
    hero={{
      eyebrow: 'Settings',
      title: 'Settings',
      description: 'Account, organization, and integration configuration.',
    }}
  >
    <CapabilityCard
      to="/settings/profile"
      title="Profile"
      description="Manage your account, contact info, and notification preferences."
      icon={<IconUser size={22} stroke={1.8} />}
      eyebrow="Account"
      testId="settings-overview-card-/settings/profile"
    />
    <CapabilityCard
      to="/settings/site"
      title="Site settings"
      description="Organization-wide configuration for OpenMakerSuite."
      icon={<IconAdjustments size={22} stroke={1.8} />}
      eyebrow="Org"
      testId="settings-overview-card-/settings/site"
    />
    <CapabilityCard
      to="/settings/webhooks"
      title="Webhooks"
      description="Outbound webhook endpoints and delivery history."
      icon={<IconWebhook size={22} stroke={1.8} />}
      eyebrow="Integrations"
      testId="settings-overview-card-/settings/webhooks"
    />
    <CapabilityCard
      to="/settings/tax-receipt/lookup"
      title="Tax receipt lookup"
      description="Look up an issued donor tax receipt by donation reference."
      icon={<IconReceipt size={22} stroke={1.8} />}
      eyebrow="Tools"
      testId="settings-overview-card-/settings/tax-receipt/lookup"
    />
  </WorkspaceLanding>
);

export default SettingsOverviewPage;
