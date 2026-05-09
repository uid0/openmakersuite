/**
 * Navigation regression tests (AC-3, AC-7).
 *
 * The four parent paths /purchasing, /facilities, /reports, /settings
 * previously had no route handler — clicking the matching breadcrumb
 * ancestor on any deep route 404'd. PR1 ships overview pages for each.
 * These tests assert each overview page renders meaningful content with
 * actionable links, so regressions are caught before they ship.
 *
 * AC-7 dead-link prevention is split between this file (overview entry
 * points) and entryPoints.test.tsx (homepage-card + sidebar coverage).
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';

import FacilitiesOverviewPage from '../../pages/FacilitiesOverviewPage';
import PurchasingOverviewPage from '../../pages/PurchasingOverviewPage';
import ReportsOverviewPage from '../../pages/ReportsOverviewPage';
import SettingsOverviewPage from '../../pages/SettingsOverviewPage';

const renderWithProviders = (ui: React.ReactElement) =>
  render(
    <MantineProvider>
      <MemoryRouter>{ui}</MemoryRouter>
    </MantineProvider>
  );

describe('Workspace overview pages (AC-3, AC-7)', () => {
  describe('Purchasing', () => {
    it('renders title + at least 2 actionable links', () => {
      renderWithProviders(<PurchasingOverviewPage />);
      expect(screen.getByRole('heading', { level: 1, name: /purchasing/i })).toBeInTheDocument();
      const links = screen.getAllByRole('link');
      expect(links.length).toBeGreaterThanOrEqual(2);
      expect(screen.getByRole('heading', { level: 4, name: /purchase orders/i })).toBeInTheDocument();
    });
  });

  describe('Facilities', () => {
    it('renders title + the major child entry points', () => {
      renderWithProviders(<FacilitiesOverviewPage />);
      expect(screen.getByRole('heading', { level: 1, name: /facilities/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 4, name: /tv dashboard/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 4, name: /logistics/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 4, name: /^electrical$/i })).toBeInTheDocument();
      const links = screen.getAllByRole('link');
      expect(links.length).toBeGreaterThanOrEqual(5);
    });
  });

  describe('Reports', () => {
    it('renders all four report links including the analytics pulse', () => {
      renderWithProviders(<ReportsOverviewPage />);
      expect(screen.getByRole('heading', { level: 1, name: /reports/i })).toBeInTheDocument();
      // Use heading role (the card titles) so we don't collide with the
      // accessible-name text inside Link-wrapped cards.
      expect(screen.getByRole('heading', { level: 4, name: /analytics pulse/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 4, name: /inventory report/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 4, name: /purchasing report/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 4, name: /asset report/i })).toBeInTheDocument();
    });
  });

  describe('Settings', () => {
    it('renders profile + site + webhooks + tax-receipt links', () => {
      renderWithProviders(<SettingsOverviewPage />);
      expect(screen.getByRole('heading', { level: 1, name: /settings/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 4, name: /^profile$/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 4, name: /site settings/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 4, name: /^webhooks$/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 4, name: /tax receipt lookup/i })).toBeInTheDocument();
    });
  });
});

describe('Overview pages have actionable href targets (AC-7)', () => {
  // Each overview page link should point to a path inside its workspace.
  const cases: Array<[string, React.ComponentType, RegExp]> = [
    ['Purchasing', PurchasingOverviewPage, /^\/purchasing\//],
    ['Facilities', FacilitiesOverviewPage, /^\/facilities\//],
    ['Settings', SettingsOverviewPage, /^\/settings\//],
  ];

  it.each(cases)('%s links stay inside the workspace', (_name, Component, prefix) => {
    renderWithProviders(<Component />);
    const links = screen.getAllByRole('link');
    expect(links.length).toBeGreaterThan(0);
    // At least one link must match the workspace prefix; cross-workspace
    // links (e.g. Reports → /analytics) are intentional and skipped here.
    const inside = links.filter((link) => prefix.test(link.getAttribute('href') || ''));
    expect(inside.length).toBeGreaterThan(0);
  });
});
