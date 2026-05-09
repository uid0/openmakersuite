/**
 * Tests for PowerPanelDirectoryPage (oms-b25 [6/7]).
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import PowerPanelDirectoryPage from '../../pages/PowerPanelDirectoryPage';
import * as api from '../../services/api';

jest.mock('../../services/api');

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <PowerPanelDirectoryPage />
      </MemoryRouter>
    </MantineProvider>,
  );

describe('PowerPanelDirectoryPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('lists every panel with its breaker count', async () => {
    (api.electricalSafetyAPI.listPanels as jest.Mock).mockResolvedValue({
      data: {
        results: [
          {
            id: 1,
            name: 'Sewing A',
            location_id: 10,
            location_name: 'Sewing Room',
            phase_configuration: 'split',
            voltage: 240,
            main_breaker_amperage: 100,
            breaker_count: 12,
            needs_review: false,
          },
        ],
      },
    });

    renderPage();

    expect(await screen.findByText('Sewing A')).toBeInTheDocument();
    expect(screen.getByText(/12 breakers/)).toBeInTheDocument();
    expect(screen.getByText('Sewing Room')).toBeInTheDocument();
    const card = screen.getByTestId('panel-card-1');
    expect(card).toHaveAttribute('href', '/facilities/electrical/panels/1');
  });

  it('flags placeholders that need admin review', async () => {
    (api.electricalSafetyAPI.listPanels as jest.Mock).mockResolvedValue({
      data: {
        results: [
          {
            id: 9,
            name: 'Placeholder',
            location_id: 5,
            location_name: 'Workshop',
            phase_configuration: 'single',
            voltage: 120,
            main_breaker_amperage: null,
            breaker_count: 0,
            needs_review: true,
          },
        ],
      },
    });

    renderPage();

    await waitFor(() => expect(screen.getByText('Placeholder')).toBeInTheDocument());
    expect(screen.getByText(/1 panel flagged for review/i)).toBeInTheDocument();
  });

  it('renders an empty state when there are no panels', async () => {
    (api.electricalSafetyAPI.listPanels as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    renderPage();
    expect(await screen.findByText(/no power panels yet/i)).toBeInTheDocument();
  });

  it('shows an error message if the API rejects', async () => {
    (api.electricalSafetyAPI.listPanels as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Boom' } },
    });
    renderPage();
    expect(await screen.findByText('Boom')).toBeInTheDocument();
  });
});
