/**
 * Landing page for the Reports workspace, mounted at `/reports/`.
 * Refactored in nav-cohesion PR2 to use the shared <WorkspaceLanding>
 * + <CapabilityCard> primitives.
 */
import {
  IconChartBar,
  IconReportAnalytics,
  IconReportMoney,
  IconShoppingCart,
  IconStack,
} from '@tabler/icons-react';
import React from 'react';

import CapabilityCard from '../components/landing/CapabilityCard';
import WorkspaceLanding from '../components/landing/WorkspaceLanding';

const ReportsOverviewPage: React.FC = () => (
  <WorkspaceLanding
    testId="reports-overview"
    hero={{
      eyebrow: 'Reports',
      title: 'Reports',
      description:
        'Operational and executive reports across inventory, purchasing, assets, and the analytics pulse.',
    }}
  >
    <CapabilityCard
      to="/analytics"
      title="Analytics pulse"
      description="Executive dashboard: ROI, utilization, category spend, and the maintenance forecast. Source for the monthly board email."
      icon={<IconChartBar size={22} stroke={1.8} />}
      eyebrow="Executive"
      badge="Staff"
      testId="reports-overview-card-/analytics"
    />
    <CapabilityCard
      to="/reports/inventory"
      title="Inventory report"
      description="Stock levels, low-stock alerts, and item-level activity."
      icon={<IconStack size={22} stroke={1.8} />}
      eyebrow="Operational"
      testId="reports-overview-card-/reports/inventory"
    />
    <CapabilityCard
      to="/reports/purchasing"
      title="Purchasing report"
      description="Spend by supplier, vendor performance, and reorder cycle metrics."
      icon={<IconShoppingCart size={22} stroke={1.8} />}
      eyebrow="Operational"
      testId="reports-overview-card-/reports/purchasing"
    />
    <CapabilityCard
      to="/reports/assets"
      title="Asset report"
      description="Asset roster, lifecycle status, and maintenance history."
      icon={<IconReportAnalytics size={22} stroke={1.8} />}
      eyebrow="Operational"
      testId="reports-overview-card-/reports/assets"
    />
    <CapabilityCard
      to="/reports/cost-recovery"
      title="Cost recovery report"
      description="Per-asset service statement with the vendor-invoice total recoverable from the landlord. Exports a CSV and a landlord-ready PDF."
      icon={<IconReportMoney size={22} stroke={1.8} />}
      eyebrow="Operational"
      testId="reports-overview-card-/reports/cost-recovery"
    />
  </WorkspaceLanding>
);

export default ReportsOverviewPage;
