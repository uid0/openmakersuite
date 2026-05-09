/**
 * Smoke tests for the shared CapabilityCard primitive (AC-2).
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';

import CapabilityCard from '../../../components/landing/CapabilityCard';

const renderCard = (props: Partial<React.ComponentProps<typeof CapabilityCard>> = {}) =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <CapabilityCard
          to="/inventory"
          title="Inventory"
          description="Items, suppliers, locations."
          icon={<svg data-testid="card-icon" />}
          {...props}
        />
      </MemoryRouter>
    </MantineProvider>
  );

describe('CapabilityCard', () => {
  it('renders title, description, and icon', () => {
    renderCard();
    expect(screen.getByRole('heading', { level: 3, name: /inventory/i })).toBeInTheDocument();
    expect(screen.getByText(/items, suppliers/i)).toBeInTheDocument();
    expect(screen.getByTestId('card-icon')).toBeInTheDocument();
  });

  it('renders the eyebrow when provided', () => {
    renderCard({ eyebrow: 'Workspace' });
    expect(screen.getByText(/workspace/i)).toBeInTheDocument();
  });

  it('renders the staff badge when provided', () => {
    renderCard({ badge: 'Staff' });
    expect(screen.getByText('Staff')).toBeInTheDocument();
  });

  it('uses the custom CTA label when provided', () => {
    renderCard({ ctaLabel: 'Launch' });
    expect(screen.getByText(/launch/i)).toBeInTheDocument();
  });

  it('whole card is a single link to the destination', () => {
    renderCard({ to: '/inventory/scan' });
    const link = screen.getByRole('link', { name: /inventory/i });
    expect(link).toHaveAttribute('href', '/inventory/scan');
  });

  it('uses the override testId when provided', () => {
    renderCard({ testId: 'custom-id' });
    expect(screen.getByTestId('custom-id')).toBeInTheDocument();
  });
});
