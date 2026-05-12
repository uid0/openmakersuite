/**
 * Tests for AssetSuppliesSection (asset form supplies toggle + editor).
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import React, { useState } from 'react';

import AssetSuppliesSection, {
  PendingAssetPart,
} from '../../components/assets/AssetSuppliesSection';
import { InventoryItem } from '../../types';

const mockItems = [
  { id: 'item-1', name: 'Blade A', sku: 'BLD-A' },
  { id: 'item-2', name: 'Filter B', sku: 'FLT-B' },
] as unknown as InventoryItem[];

const Harness: React.FC = () => {
  const [enabled, setEnabled] = useState(false);
  const [supplies, setSupplies] = useState<PendingAssetPart[]>([]);
  return (
    <MantineProvider>
      <AssetSuppliesSection
        enabled={enabled}
        onEnabledChange={setEnabled}
        supplies={supplies}
        onChange={setSupplies}
        inventoryItems={mockItems}
      />
    </MantineProvider>
  );
};

describe('AssetSuppliesSection', () => {
  it('renders only the prompt when supplies are not enabled', () => {
    render(<Harness />);
    expect(screen.getByText(/uses consumable supplies/i)).toBeInTheDocument();
    expect(screen.queryByText(/Add another supply/i)).not.toBeInTheDocument();
  });

  it('toggling the switch on seeds an empty supply row', () => {
    render(<Harness />);
    const toggle = screen.getByLabelText(/uses consumable supplies/i);
    fireEvent.click(toggle);
    // The "Supply" panel header appears for the seeded row.
    expect(screen.getByText('Supply')).toBeInTheDocument();
    expect(screen.getByText(/Add another supply/i)).toBeInTheDocument();
  });

  it('toggling off clears any rows', () => {
    render(<Harness />);
    const toggle = screen.getByLabelText(/uses consumable supplies/i);
    fireEvent.click(toggle); // on
    expect(screen.getByText('Supply')).toBeInTheDocument();
    fireEvent.click(toggle); // off
    expect(screen.queryByText('Supply')).not.toBeInTheDocument();
  });
});
