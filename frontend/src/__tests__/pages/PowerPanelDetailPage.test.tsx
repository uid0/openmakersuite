/**
 * Tests for PowerPanelDetailPage.
 *
 * Covers the per-outlet asset surfacing (connected_assets sub-line) and
 * the "Loads" rename of the per-breaker action that used to read "Trip
 * impact" — see oms-twnhlv.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import PowerPanelDetailPage from '../../pages/PowerPanelDetailPage';
import * as api from '../../services/api';

jest.mock('../../services/api');

const baseBreaker = {
  id: 5,
  panel_id: 1,
  panel_name: 'Sewing A',
  position: '12',
  amperage: 20,
  phase: 'A',
  pole_count: 1,
  status: 'on',
  review_status: 'ok',
  review_note: '',
  label: 'Bench row 1',
};

const baseTopology = {
  id: 1,
  name: 'Sewing A',
  location_id: 10,
  location_name: 'Sewing Room',
  phase_configuration: 'split',
  voltage: 240,
  main_breaker_amperage: 100,
  breaker_type: '',
  numbering_direction: 'top_down',
  fed_by_summary: null,
  downstream_panels: [],
  breakers: [
    {
      ...baseBreaker,
      circuits: [
        {
          id: 50,
          label: 'Bench row 1',
          breaker_id: 5,
          max_load_amps: 16,
          conductor_size: '12 AWG',
          outlets: [
            {
              id: 101,
              label: 'NW-bench-1',
              outlet_type: '5-15R',
              status: 'active',
              location_id: 10,
              location_name: 'Wood Shop',
              connected_assets: [
                { id: 'a1', name: 'Bridgeport Mill', asset_tag: 'TAG-1', is_critical: false },
                { id: 'a2', name: 'Drill press', asset_tag: 'TAG-2', is_critical: false },
              ],
            },
            {
              id: 102,
              label: 'NW-bench-2',
              outlet_type: '5-15R',
              status: 'active',
              location_id: 10,
              location_name: 'Wood Shop',
              connected_assets: [],
            },
          ],
        },
      ],
    },
  ],
};

const renderAt = (path: string) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/facilities/electrical/panels/:id"
            element={<PowerPanelDetailPage />}
          />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('PowerPanelDetailPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders connected assets inline under outlets and uses the "Loads" label', async () => {
    (api.electricalSafetyAPI.getPanelTopology as jest.Mock).mockResolvedValue({
      data: baseTopology,
    });

    renderAt('/facilities/electrical/panels/1?view=list');

    // Wait for the breaker row to land.
    expect(await screen.findByTestId('breaker-row-5')).toBeInTheDocument();

    // The per-breaker action row now says "Loads" instead of "Trip impact".
    const loadsLink = screen.getByTestId('breaker-loads-5');
    expect(loadsLink).toHaveTextContent('Loads');
    expect(loadsLink).toHaveAttribute(
      'href',
      '/facilities/electrical/breakers/5/trip-impact',
    );
    expect(screen.queryByText('Trip impact')).not.toBeInTheDocument();

    // Outlet with connected_assets surfaces both names, linked to the
    // asset detail page.
    const outletAssets = screen.getByTestId('outlet-101-assets');
    expect(within(outletAssets).getByText('Bridgeport Mill')).toBeInTheDocument();
    expect(within(outletAssets).getByText('Drill press')).toBeInTheDocument();

    const millLink = screen.getByTestId('outlet-asset-a1');
    expect(millLink).toHaveAttribute('href', '/assets/a1');

    // The empty-list outlet renders no asset sub-line and no "no assets" noise.
    expect(screen.queryByTestId('outlet-102-assets')).not.toBeInTheDocument();
  });
});
