/**
 * Tests for AssetMaintenanceSection (toggle + modal-driven PM task list).
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import React, { useState } from 'react';

import { PendingMaintenanceItem } from '../../components/assets/AssetMaintenanceModal';
import AssetMaintenanceSection from '../../components/assets/AssetMaintenanceSection';

const Harness: React.FC<{ initialItems?: PendingMaintenanceItem[] }> = ({
  initialItems = [],
}) => {
  const [enabled, setEnabled] = useState(initialItems.length > 0);
  const [items, setItems] = useState<PendingMaintenanceItem[]>(initialItems);
  return (
    <MantineProvider>
      <AssetMaintenanceSection
        enabled={enabled}
        onEnabledChange={setEnabled}
        items={items}
        onChange={setItems}
      />
    </MantineProvider>
  );
};

describe('AssetMaintenanceSection', () => {
  it('renders just the prompt when PM is off', () => {
    render(<Harness />);
    expect(screen.getByText(/Schedule preventive maintenance/)).toBeInTheDocument();
    // Add-task CTA only appears when the toggle is on.
    expect(screen.queryByText(/Add maintenance task/)).not.toBeInTheDocument();
  });

  it('toggling on shows the empty-state with an add CTA', () => {
    render(<Harness />);
    const toggle = screen.getByLabelText(/Schedule preventive maintenance/);
    fireEvent.click(toggle);
    expect(screen.getByText(/Add maintenance task/)).toBeInTheDocument();
  });

  it('lists existing tasks with interval badges', () => {
    render(
      <Harness
        initialItems={[
          {
            key: 'item-1',
            title: 'Replace HEPA filter',
            description: '',
            instructions: '',
            estimated_time_minutes: 15,
            interval_days: 30,
          },
        ]}
      />,
    );
    expect(screen.getByText('Replace HEPA filter')).toBeInTheDocument();
    // ``Every 1 month`` per the section's formatInterval helper.
    expect(screen.getByText(/Every 1 month/)).toBeInTheDocument();
  });

  it('exposes an Add CTA when toggled on with no existing items', () => {
    render(<Harness />);
    fireEvent.click(screen.getByLabelText(/Schedule preventive maintenance/));
    // The Add CTA is the affordance that drives the modal. The modal
    // itself uses Mantine's Portal which jsdom + the suite's render
    // host don't always reconcile in time; the e2e suite verifies the
    // modal opens. Here we just confirm the affordance exists.
    expect(screen.getByText(/Add maintenance task/)).toBeInTheDocument();
  });
});
