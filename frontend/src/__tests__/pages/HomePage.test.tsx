/**
 * HomePage tests (AC-1).
 *
 * Asserts every spec-listed capability area is enumerated on the
 * homepage with an actionable link, and that the Common workflows row
 * is present. Catches regressions where a workspace silently disappears
 * from the homepage capability grid.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, within } from '@testing-library/react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';

import HomePage from '../../pages/HomePage';

// AuthSection talks to the API and renders auth UI. The homepage tests
// don't need to exercise that surface; render a stub instead.
vi.mock('../../components/AuthSection', () => ({ default: () => <div data-testid="auth-section-stub" /> }));
// Footer pulls in Mantine + assets we don't care about for these tests.
vi.mock('../../components/Footer', () => ({ default: () => <div data-testid="footer-stub" /> }));

const renderHome = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    </MantineProvider>
  );

describe('HomePage (AC-1)', () => {
  it('renders the hero and Common workflows row', () => {
    renderHome();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/run the shop/i);
    expect(screen.getByTestId('home-quick-workflows')).toBeInTheDocument();
  });

  it('Common workflows row links to scan, dashboard, and maintenance', () => {
    renderHome();
    const row = screen.getByTestId('home-quick-workflows');
    expect(within(row).getByRole('link', { name: /scan a qr code/i }))
      .toHaveAttribute('href', '/inventory/scan');
    expect(within(row).getByRole('link', { name: /operations dashboard/i }))
      .toHaveAttribute('href', '/dashboard');
    expect(within(row).getByRole('link', { name: /maintenance/i }))
      .toHaveAttribute('href', '/maintenance');
  });

  describe('every spec-listed workspace has a CapabilityCard', () => {
    // Path → expected card title (used to disambiguate when multiple
    // sections mention the same word).
    const expected: Array<[string, RegExp]> = [
      ['/inventory', /^inventory$/i],
      ['/purchasing', /^purchasing$/i],
      ['/assets', /^assets$/i],
      ['/facilities', /^facilities$/i],
      ['/maintenance', /^maintenance$/i],
      ['/sigs/dashboard', /^sigs$/i],
      ['/reports', /^reports$/i],
      ['/analytics', /analytics pulse/i],
      ['/settings', /settings/i],
    ];
    it.each(expected)('links to %s with title matching %s', (href, _titleRe) => {
      renderHome();
      const grid = screen.getByTestId('home-workspaces');
      const link = within(grid).getByRole('link', { name: _titleRe });
      expect(link).toHaveAttribute('href', href);
    });
  });

  it('badges Analytics Pulse as Staff', () => {
    renderHome();
    const grid = screen.getByTestId('home-workspaces');
    const analyticsCard = within(grid).getByTestId('home-workspace-/analytics');
    expect(within(analyticsCard).getByText(/staff/i)).toBeInTheDocument();
  });
});
