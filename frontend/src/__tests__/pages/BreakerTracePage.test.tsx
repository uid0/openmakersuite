/**
 * Tests for BreakerTracePage (oms-a5f).
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import BreakerTracePage from '../../pages/BreakerTracePage';
import * as api from '../../services/api';

vi.mock('../../services/api');

const renderAt = (path: string) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/facilities/electrical/breakers/:id/trace"
            element={<BreakerTracePage />}
          />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('BreakerTracePage', () => {
  const breaker = {
    id: 5,
    location: 1,
    location_name: 'Electrical Room',
    panel: 'A',
    breaker_number: '12',
    amperage: 30,
    voltage: 240,
    poles: 2,
    description: 'Wood shop saw',
    notes: '',
    is_active: true,
    created_at: '',
    updated_at: '',
  };
  const outlet = {
    id: 11,
    location: 2,
    location_name: 'Wood shop',
    identifier: 'bench-1',
    breaker: 5,
    breaker_label: 'A / 12',
    outlet_type: 'nema_5_20',
    description: '',
    plugged_in_notes: 'Table saw',
    photo: null,
    is_active: true,
    created_at: '',
    updated_at: '',
  };
  const matchingSwitch = {
    id: 21,
    location: 2,
    location_name: 'Wood shop',
    identifier: 'door-east',
    controls_location: 2,
    controls_location_name: 'Wood shop',
    breaker: 5,
    breaker_label: 'A / 12',
    description: '',
    notes: '',
    is_active: true,
    created_at: '',
    updated_at: '',
  };
  const otherSwitch = { ...matchingSwitch, id: 22, breaker: 99, identifier: 'unrelated' };

  beforeEach(() => {
    jest.clearAllMocks();
    (api.electricalCircuitsAPI.getBreaker as jest.Mock).mockResolvedValue({
      data: breaker,
    });
    (api.electricalCircuitsAPI.listOutlets as jest.Mock).mockResolvedValue({
      data: { results: [outlet] },
    });
    (api.electricalCircuitsAPI.listLightSwitches as jest.Mock).mockResolvedValue({
      data: { results: [matchingSwitch, otherSwitch] },
    });
  });

  it('shows outlets and only switches that match the breaker', async () => {
    renderAt('/facilities/electrical/breakers/5/trace');

    expect(await screen.findByText(/Trace: A \/ 12/)).toBeInTheDocument();
    // Outlet rendered
    expect(screen.getByText('bench-1')).toBeInTheDocument();
    expect(screen.getByText('Table saw')).toBeInTheDocument();
    // Matching switch rendered, unrelated one filtered out
    expect(screen.getByText('door-east')).toBeInTheDocument();
    expect(screen.queryByText('unrelated')).not.toBeInTheDocument();

    // Outlets request was filtered server-side by breaker id
    expect(api.electricalCircuitsAPI.listOutlets).toHaveBeenCalledWith({
      breaker: '5',
    });
  });

  it('renders an error state when the breaker fails to load', async () => {
    (api.electricalCircuitsAPI.getBreaker as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Not found' } },
    });
    renderAt('/facilities/electrical/breakers/999/trace');
    expect(await screen.findByText('Not found')).toBeInTheDocument();
  });
});
