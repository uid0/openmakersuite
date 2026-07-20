/**
 * Landing page for the Purchasing workspace, mounted at `/purchasing/`.
 * Refactored in nav-cohesion PR2 to use the shared <WorkspaceLanding>
 * + <CapabilityCard> primitives so it matches the homepage rhythm.
 */
import { Stack } from '@mantine/core';
import { IconClipboardList, IconPlus, IconReceipt } from '@tabler/icons-react';
import React from 'react';
import { useNavigate } from 'react-router-dom';

import DemandForecastPanel from '../components/inventory/DemandForecastPanel';
import SerializedForecastPanel from '../components/inventory/SerializedForecastPanel';
import CapabilityCard from '../components/landing/CapabilityCard';
import WorkspaceLanding from '../components/landing/WorkspaceLanding';

const PurchasingOverviewPage: React.FC = () => {
  const navigate = useNavigate();
  return (
  <WorkspaceLanding
    testId="purchasing-overview"
    hero={{
      eyebrow: 'Purchasing',
      title: 'Purchasing',
      description:
        'Manage purchase orders, reorder requests, and the public transparency ledger.',
    }}
    footer={
      <Stack gap="md">
        <DemandForecastPanel
          title="Supplies to reorder"
          defaultLowStockOnly
          onSelectItem={(itemId) => navigate(`/inventory/items/${itemId}`)}
        />
        <SerializedForecastPanel
          title="Serialized components to reorder"
          defaultLowStockOnly
          onSelectItem={(itemId) => navigate(`/inventory/items/${itemId}`)}
        />
      </Stack>
    }
  >
    <CapabilityCard
      to="/purchasing/orders"
      title="Purchase orders"
      description="Open the queue of pending and recent purchase orders."
      icon={<IconClipboardList size={22} stroke={1.8} />}
      eyebrow="Workflow"
      testId="purchasing-overview-card-/purchasing/orders"
    />
    <CapabilityCard
      to="/purchasing/orders/new"
      title="New purchase order"
      description="Start a new purchase order from suggested reorders or a blank template."
      icon={<IconPlus size={22} stroke={1.8} />}
      eyebrow="Action"
      testId="purchasing-overview-card-/purchasing/orders/new"
    />
    <CapabilityCard
      to="/inventory/transparency"
      title="Transparency ledger"
      description="Public-facing view of recent reorder activity for membership transparency."
      icon={<IconReceipt size={22} stroke={1.8} />}
      eyebrow="Public"
      testId="purchasing-overview-card-/inventory/transparency"
    />
  </WorkspaceLanding>
  );
};

export default PurchasingOverviewPage;
