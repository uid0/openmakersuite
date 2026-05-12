/**
 * Tests for PowerPanelFormPage (oms-b25 [7/7]).
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import PowerPanelFormPage from '../../pages/PowerPanelFormPage';
import * as api from '../../services/api';

jest.mock('../../services/api');

const mockLocations = [
  { id: 1, name: 'Sewing Room', description: '', is_active: true },
  { id: 2, name: 'Workshop', description: '', is_active: true },
];

const renderAt = (path: string, route: string) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={route} element={<PowerPanelFormPage />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('PowerPanelFormPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (api.inventoryAPI.listLocations as jest.Mock).mockResolvedValue({
      data: { results: mockLocations },
    });
  });

  it('renders the create form with the canonical hero', async () => {
    renderAt('/facilities/electrical/panels/new', '/facilities/electrical/panels/new');
    expect(screen.getByTestId('page-hero-title')).toHaveTextContent(/new power panel/i);
    expect(await screen.findByLabelText(/Name/)).toBeInTheDocument();
  });

  it('exposes a submit button and a cancel button', async () => {
    renderAt('/facilities/electrical/panels/new', '/facilities/electrical/panels/new');
    // Both buttons should be present; the submit one is what fires the
    // create flow. End-to-end Mantine Select interaction is intentionally
    // covered in the e2e suite, not unit tests.
    expect(await screen.findByText(/Create panel/)).toBeInTheDocument();
    // Two cancel buttons: hero action + form-footer button.
    expect(screen.getAllByText('Cancel').length).toBeGreaterThan(0);
  });

  it('loads the existing panel in edit mode', async () => {
    (api.electricalTopologyAPI.getPanel as jest.Mock).mockResolvedValue({
      data: {
        id: 5,
        location: 2,
        location_name: 'Workshop',
        name: 'Bench A',
        phase_configuration: 'split',
        voltage: 240,
        main_breaker_amperage: 100,
        manufacturer: 'Acme',
        model: 'X100',
        install_date: null,
        notes: '',
        needs_review: false,
        breaker_count: 0,
        created_at: '',
        updated_at: '',
      },
    });

    renderAt('/facilities/electrical/panels/5/edit', '/facilities/electrical/panels/:id/edit');
    expect(screen.getByTestId('page-hero-title')).toHaveTextContent(/edit power panel/i);
    expect(await screen.findByDisplayValue('Bench A')).toBeInTheDocument();
    expect(api.electricalTopologyAPI.getPanel).toHaveBeenCalledWith('5');
  });
});
