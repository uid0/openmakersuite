/**
 * Tests for LocationSafetySignPage (oms-gzycmj).
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import LocationSafetySignPage from '../../pages/LocationSafetySignPage';
import { SafetySheet, safetySheetAPI } from '../../services/api';

jest.mock('../../services/api');

const mockSafetySheetAPI = safetySheetAPI as jest.Mocked<typeof safetySheetAPI>;

const renderAt = (path: string) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/facilities/locations/:id/safety-sign"
            element={<LocationSafetySignPage />}
          />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

const buildSheet = (overrides: Partial<SafetySheet> = {}): SafetySheet => ({
  location: { id: 12, name: 'Wood Shop' },
  lights: [
    {
      switch_id: 1,
      switch_label: 'door-east',
      panel: 'A',
      position: '14',
      amperage: 15,
      breaker_id: 91,
    },
  ],
  outlets: [
    {
      panel: 'A',
      position: '18',
      amperage: 20,
      breaker_id: 92,
      source: 'legacy',
      outlet_count: 5,
      outlets: [],
    },
    {
      panel: 'B',
      position: '6',
      amperage: 30,
      breaker_id: 93,
      source: 'legacy',
      outlet_count: 1,
      outlets: [],
    },
  ],
  thermostats: [
    {
      id: 7,
      label: 'Wood Shop North',
      manufacturer: 'Honeywell',
      model: 'T6 Pro',
      controlled_asset: { id: 'asset-88', name: 'RTU-3' },
      kill_breakers: [
        {
          breaker_id: 94,
          panel: 'Panel C',
          position: '22',
          amperage: 30,
          source: 'direct',
        },
      ],
      needs_review: false,
    },
  ],
  all_kill_breakers: [
    { panel: 'A', position: '14', amperage: 15 },
    { panel: 'A', position: '18', amperage: 20 },
    { panel: 'B', position: '6', amperage: 30 },
    { panel: 'Panel C', position: '22', amperage: 30 },
  ],
  ...overrides,
});

describe('LocationSafetySignPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders all four sections with deduped emergency kill list', async () => {
    mockSafetySheetAPI.getLocationSafetySheet.mockResolvedValue({
      data: buildSheet(),
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });

    renderAt('/facilities/locations/12/safety-sign');

    expect(await screen.findByTestId('safety-sign-body')).toBeInTheDocument();
    expect(screen.getByText(/WOOD SHOP — Safety Sign/i)).toBeInTheDocument();

    // Lights section
    expect(screen.getByTestId('safety-sign-lights')).toHaveTextContent('door-east');
    expect(screen.getByTestId('safety-sign-lights')).toHaveTextContent('A · 14');

    // Outlets section — two breaker rows, with outlet counts
    const outlets = screen.getByTestId('safety-sign-outlets');
    expect(outlets).toHaveTextContent('A · 18');
    expect(outlets).toHaveTextContent('5 outlets');
    expect(outlets).toHaveTextContent('B · 6');
    expect(outlets).toHaveTextContent('1 outlet');

    // Climate section
    const climate = screen.getByTestId('safety-sign-climate');
    expect(climate).toHaveTextContent('Wood Shop North → RTU-3');
    expect(climate).toHaveTextContent('Kill: Panel C · 22');

    // Emergency kill list — deduped
    const emergency = screen.getByTestId('safety-sign-emergency');
    expect(emergency).toHaveTextContent('A · 14');
    expect(emergency).toHaveTextContent('A · 18');
    expect(emergency).toHaveTextContent('B · 6');
    expect(emergency).toHaveTextContent('Panel C · 22');

    // Print button leads to the print route
    const printLink = screen.getByTestId('print-safety-sign-button').closest('a');
    expect(printLink).toHaveAttribute('href', '/facilities/locations/12/safety-sign/print');
  });

  it('shows a needs-review badge when a thermostat has no kill breakers', async () => {
    mockSafetySheetAPI.getLocationSafetySheet.mockResolvedValue({
      data: buildSheet({
        thermostats: [
          {
            id: 7,
            label: 'Storage stat',
            manufacturer: '',
            model: '',
            controlled_asset: null,
            kill_breakers: [],
            needs_review: true,
          },
        ],
        all_kill_breakers: [],
      }),
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });

    renderAt('/facilities/locations/12/safety-sign');
    expect(await screen.findByTestId('needs-review-badge')).toBeInTheDocument();
    expect(screen.getByText(/No kill breakers resolved/i)).toBeInTheDocument();
  });

  it('renders an error state when the API call fails', async () => {
    mockSafetySheetAPI.getLocationSafetySheet.mockRejectedValue({
      response: { data: { detail: 'Permission denied' } },
    });
    renderAt('/facilities/locations/12/safety-sign');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Permission denied');
  });
});
