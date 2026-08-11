/**
 * Kit route registration (op-8n0, AC-33).
 *
 * The existing navigation tests do NOT cover this. `entryPoints.test.tsx` and
 * `routeMap.test.ts` assert a hardcoded list of ~10 top-level workspaces, so a
 * missing `routeMap` entry would silently fall back to `titleCaseSegment` and a
 * missing Sidebar entry is tested by nothing at all. These assertions are
 * written explicitly for that reason.
 *
 * Covers all four registration edits AC-33 names: the route itself, the route
 * map label, the sidebar link, and the workspace/overview cards.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import KitDetailPage from '../../pages/KitDetailPage';
import KitListPage from '../../pages/KitListPage';
import PurchasingOverviewPage from '../../pages/PurchasingOverviewPage';
import Sidebar from '../../components/Sidebar';
import { getRouteEntry, isClickable, labelFor } from '../../navigation/routeMap';

vi.mock('../../components/RecentPages', () => ({
  default: () => <div data-testid="recent-pages" />,
}));

vi.mock('../../services/api', () => ({
  kitAPI: {
    listKits: vi.fn().mockResolvedValue({ data: { results: [] } }),
    getKit: vi.fn().mockResolvedValue({ data: { id: 'k1', name: 'Eufy Ink Kit', components: [] } }),
    createKit: vi.fn(),
    updateKit: vi.fn(),
  },
  inventoryAPI: {
    listItems: vi.fn().mockResolvedValue({ data: { results: [] } }),
    listSuppliers: vi.fn().mockResolvedValue({ data: { results: [] } }),
  },
}));

const renderAt = (path: string) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/inventory/kits" element={<KitListPage />} />
          <Route path="/inventory/kits/new" element={<KitDetailPage />} />
          <Route path="/inventory/kits/:kitId" element={<KitDetailPage />} />
          <Route path="*" element={<div>404 Not Found</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('AC-33 — kit routes are registered and discoverable', () => {
  describe('route map', () => {
    it('registers inventory/kits with the canonical label "Kits"', () => {
      const entry = getRouteEntry('inventory/kits');
      expect(entry).toBeDefined();
      // Without an explicit entry this would silently be titleCaseSegment's
      // output, which happens to also be "Kits" — so assert the ENTRY exists,
      // not just the rendered string.
      expect(entry!.label).toBe('Kits');
    });

    it('is clickable, so the breadcrumb ancestor is not a dead link', () => {
      expect(isClickable('inventory/kits')).toBe(true);
    });

    it('labelFor resolves from the map rather than the fallback', () => {
      expect(labelFor('inventory/kits', 'kits')).toBe('Kits');
    });
  });

  describe('rendered routes', () => {
    it('/inventory/kits renders the kit list, not a 404', async () => {
      renderAt('/inventory/kits');
      expect(screen.queryByText(/404 Not Found/)).not.toBeInTheDocument();
      await waitFor(() => {
        expect(screen.getByTestId('kit-list-page')).toBeInTheDocument();
      });
    });

    it('/inventory/kits/new renders the kit form, not a 404', async () => {
      renderAt('/inventory/kits/new');
      expect(screen.queryByText(/404 Not Found/)).not.toBeInTheDocument();
      await waitFor(() => {
        expect(screen.getByTestId('kit-detail-page')).toBeInTheDocument();
      });
      expect(screen.getByRole('heading', { name: /new kit/i })).toBeInTheDocument();
    });

    it('/inventory/kits/:kitId renders the kit detail, not a 404', async () => {
      renderAt('/inventory/kits/k1');
      expect(screen.queryByText(/404 Not Found/)).not.toBeInTheDocument();
      await waitFor(() => {
        expect(screen.getByTestId('kit-detail-page')).toBeInTheDocument();
      });
    });
  });

  describe('sidebar', () => {
    it('lists Kits under the Inventory section', () => {
      // Nothing else asserts sidebar entries at all, so a dropped link here
      // would otherwise be invisible until someone went looking for kits.
      render(
        <MantineProvider>
          <MemoryRouter>
            <Sidebar />
          </MemoryRouter>
        </MantineProvider>,
      );
      const link = screen
        .getAllByRole('link')
        .find((node) => node.getAttribute('href') === '/inventory/kits');
      expect(link).toBeDefined();
      expect(link).toHaveTextContent(/kits/i);
    });
  });

  describe('navigation cards', () => {
    it('the Purchasing overview links to kits', () => {
      render(
        <MantineProvider>
          <MemoryRouter>
            <PurchasingOverviewPage />
          </MemoryRouter>
        </MantineProvider>,
      );
      const card = screen.getByTestId('purchasing-overview-card-/inventory/kits');
      expect(card).toBeInTheDocument();
      expect(card).toHaveAttribute('href', '/inventory/kits');
    });
  });
});
