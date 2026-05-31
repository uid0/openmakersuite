/**
 * Landing page for the Facilities workspace, mounted at `/facilities/`.
 * Refactored in nav-cohesion PR2 to use the shared <WorkspaceLanding>
 * + <CapabilityCard> primitives.
 */
import {
  IconBolt,
  IconBox,
  IconChecklist,
  IconDeviceTv,
  IconGauge,
  IconKey,
  IconLock,
  IconPackage,
  IconRocket,
  IconScreenshot,
  IconTruck,
} from '@tabler/icons-react';
import React from 'react';

import CapabilityCard from '../components/landing/CapabilityCard';
import WorkspaceLanding from '../components/landing/WorkspaceLanding';

const FacilitiesOverviewPage: React.FC = () => (
  <WorkspaceLanding
    testId="facilities-overview"
    hero={{
      eyebrow: 'Facilities',
      title: 'Facilities',
      description:
        'Operations dashboards, kiosks, and the physical-plant systems that keep the shop running.',
    }}
  >
    <CapabilityCard
      to="/facilities/tv-dashboard"
      title="TV dashboard"
      description="Full-screen status board for shop displays."
      icon={<IconDeviceTv size={22} stroke={1.8} />}
      eyebrow="Display"
      testId="facilities-overview-card-/facilities/tv-dashboard"
    />
    <CapabilityCard
      to="/facilities/logistics"
      title="Logistics dashboard"
      description="Logistics view of open work, reorders, and location problems."
      icon={<IconTruck size={22} stroke={1.8} />}
      eyebrow="Display"
      testId="facilities-overview-card-/facilities/logistics"
    />
    <CapabilityCard
      to="/facilities/screens"
      title="Screens"
      description="Configure kiosk and display screens."
      icon={<IconScreenshot size={22} stroke={1.8} />}
      eyebrow="Setup"
      testId="facilities-overview-card-/facilities/screens"
    />
    <CapabilityCard
      to="/facilities/maker-boxes"
      title="Maker boxes"
      description="Inventory and check-out activity for member maker boxes."
      icon={<IconBox size={22} stroke={1.8} />}
      eyebrow="Members"
      testId="facilities-overview-card-/facilities/maker-boxes"
    />
    <CapabilityCard
      to="/facilities/electrical"
      title="Electrical"
      description="Breakers, outlets, light switches, and network drops."
      icon={<IconBolt size={22} stroke={1.8} />}
      eyebrow="Plant"
      testId="facilities-overview-card-/facilities/electrical"
    />
    <CapabilityCard
      to="/facilities/forgekey-dashboard"
      title="ForgeKey dashboard"
      description="Fleet health at a glance — online/offline, firmware, e-paper panels, and alerts."
      icon={<IconGauge size={22} stroke={1.8} />}
      eyebrow="Access"
      testId="facilities-overview-card-/facilities/forgekey-dashboard"
    />
    <CapabilityCard
      to="/facilities/forgekey-devices"
      title="ForgeKey devices"
      description="Door, locker, and equipment-control devices on the ForgeKey bus."
      icon={<IconKey size={22} stroke={1.8} />}
      eyebrow="Access"
      testId="facilities-overview-card-/facilities/forgekey-devices"
    />
    <CapabilityCard
      to="/facilities/forgekey-epaper"
      title="e-Paper panels"
      description="PM status panels glued to machines — binding, battery, and refresh state."
      icon={<IconDeviceTv size={22} stroke={1.8} />}
      eyebrow="Access"
      testId="facilities-overview-card-/facilities/forgekey-epaper"
    />
    <CapabilityCard
      to="/facilities/forgekey-rollouts"
      title="Firmware rollouts"
      description="Stage a firmware version across the fleet — set the pace and watch each wave land."
      icon={<IconRocket size={22} stroke={1.8} />}
      eyebrow="Access"
      testId="facilities-overview-card-/facilities/forgekey-rollouts"
    />
    <CapabilityCard
      to="/facilities/lockers"
      title="Lockers"
      description="ForgeKey-gated lockers — live secure / online status with intrusions flagged."
      icon={<IconLock size={22} stroke={1.8} />}
      eyebrow="Access"
      testId="facilities-overview-card-/facilities/lockers"
    />
    <CapabilityCard
      to="/facilities/checklist"
      title="Checklists"
      description="Recurring opening, closing, and safety walk-through checklists."
      icon={<IconChecklist size={22} stroke={1.8} />}
      eyebrow="Routine"
      testId="facilities-overview-card-/facilities/checklist"
    />
    <CapabilityCard
      to="/facilities/project-storage"
      title="Project storage"
      description="Look up project-storage stints, send violation notices, and move items to purgatory."
      icon={<IconPackage size={22} stroke={1.8} />}
      eyebrow="Members"
      testId="facilities-overview-card-/facilities/project-storage"
    />
  </WorkspaceLanding>
);

export default FacilitiesOverviewPage;
