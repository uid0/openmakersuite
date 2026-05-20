/**
 * Electrical workspace overview, mounted at `/facilities/electrical`.
 *
 * Replaces the legacy tabbed `ElectricalCircuitsPage` as the landing
 * surface so the entry point matches the homepage design language. The
 * old fixture inventory is still reachable via the "Fixture inventory"
 * card so existing bookmarks keep working.
 */
import {
  IconBolt,
  IconClipboardList,
  IconLayoutGrid,
  IconRouteAltLeft,
  IconSitemap,
} from '@tabler/icons-react';
import React from 'react';

import CapabilityCard from '../components/landing/CapabilityCard';
import WorkspaceLanding from '../components/landing/WorkspaceLanding';

const ElectricalOverviewPage: React.FC = () => (
  <WorkspaceLanding
    testId="electrical-overview"
    hero={{
      eyebrow: 'Facilities',
      title: 'Electrical',
      description:
        'Power-distribution topology, trip-impact lookups, and the fixture inventory the shop runs on.',
    }}
  >
    <CapabilityCard
      to="/facilities/electrical/panels"
      title="Power panel directory"
      description="Every panel in the building, with breaker counts and migration-review flags."
      icon={<IconSitemap size={22} stroke={1.8} />}
      eyebrow="Topology"
      testId="electrical-overview-card-panels"
    />
    <CapabilityCard
      to="/facilities/electrical/power-chain"
      title="Asset power chain"
      description='"What feeds this?" — trace any asset back to its panel through its assigned breaker.'
      icon={<IconRouteAltLeft size={22} stroke={1.8} />}
      eyebrow="Lookup"
      testId="electrical-overview-card-power-chain"
    />
    <CapabilityCard
      to="/facilities/electrical/fixtures"
      title="Fixture inventory"
      description="Legacy breakers, outlets, light switches, and network drops with create/edit forms and PDF reports."
      icon={<IconLayoutGrid size={22} stroke={1.8} />}
      eyebrow="Inventory"
      testId="electrical-overview-card-fixtures"
    />
    <CapabilityCard
      to="/api/electrical-circuits/reports/panel-directory.pdf"
      title="Panel directory PDF"
      description="Printable directory of every breaker and the outlets / switches it feeds. Tape it to the panel."
      icon={<IconClipboardList size={22} stroke={1.8} />}
      eyebrow="Report"
      testId="electrical-overview-card-panel-directory-pdf"
    />
    <CapabilityCard
      to="/api/electrical-circuits/reports/network-drop-list.pdf"
      title="Network-drop report PDF"
      description="Patch-panel + port assignments grouped by location. Useful for switch audits."
      icon={<IconBolt size={22} stroke={1.8} />}
      eyebrow="Report"
      testId="electrical-overview-card-network-drop-pdf"
    />
  </WorkspaceLanding>
);

export default ElectricalOverviewPage;
