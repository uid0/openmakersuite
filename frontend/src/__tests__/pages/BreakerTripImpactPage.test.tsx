/**
 * Tests for BreakerTripImpactPage (oms-b25 [6/7]).
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import BreakerTripImpactPage from '../../pages/BreakerTripImpactPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

const renderAt = (path: string) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/facilities/electrical/breakers/:id/trip-impact"
            element={<BreakerTripImpactPage />}
          />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('BreakerTripImpactPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('separates critical loads from the full asset list', async () => {
    (api.electricalSafetyAPI.getBreakerTripImpact as jest.Mock).mockResolvedValue({
      data: {
        breaker: {
          id: 5,
          panel_id: 1,
          panel_name: 'Sewing A',
          position: '12',
          amperage: 20,
          phase: 'A',
          pole_count: 1,
          status: 'on',
          label: 'Bench row 1',
        },
        assets: [
          { id: 'a1', name: 'Server', asset_tag: 'TAG-1', is_critical: true },
          { id: 'a2', name: 'Lamp', asset_tag: 'TAG-2', is_critical: false },
        ],
        critical_loads: [
          { id: 'a1', name: 'Server', asset_tag: 'TAG-1', is_critical: true },
        ],
      },
    });

    renderAt('/facilities/electrical/breakers/5/trip-impact');

    expect(await screen.findByText(/Bench row 1/)).toBeInTheDocument();
    expect(screen.getByText(/1 critical asset on this breaker/i)).toBeInTheDocument();
    expect(screen.getByText(/2 total assets fed by this breaker/i)).toBeInTheDocument();
    expect(screen.getByTestId('critical-asset-a1')).toHaveTextContent('Server');
    expect(screen.getByTestId('asset-a2')).toHaveTextContent('Lamp');
  });

  it('renders an error state when the API rejects', async () => {
    (api.electricalSafetyAPI.getBreakerTripImpact as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Not found' } },
    });
    renderAt('/facilities/electrical/breakers/999/trip-impact');
    expect(await screen.findByText('Not found')).toBeInTheDocument();
  });
});
