/**
 * Tests for the ForgeKey fleet dashboard page.
 *
 * Covers: non-staff redirect, the headline stat cards, the needs-attention
 * feed (offline devices + low-battery panels), breakdowns, and the all-clear
 * empty state.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ForgeKeyDashboardPage from '../../pages/ForgeKeyDashboardPage';
import { forgekeyAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    forgekeyAPI: {
      getFleetSummary: jest.fn(),
    },
  };
});

const mockForgekey = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;

const buildSummary = (overrides: Partial<any> = {}) => ({
  generated_at: '2026-05-31T00:00:00Z',
  devices: {
    total: 5,
    active: 4,
    online: 3,
    offline: 1,
    never_seen: 0,
    by_type: [{ code: 'ac_relay', name: 'AC Relay', count: 3, online: 2 }],
    by_capability: [{ capability: 'status_led', count: 4 }],
    by_firmware: [{ version: '1.0.0', count: 5 }],
  },
  epaper: { total: 2, bound: 1, unbound: 1, low_battery: 1 },
  firmware: { updates_in_flight: 1, recent_failures: 0 },
  attention: {
    offline: [
      { kind: 'offline', device_id: 'd9', name: 'Back door', mac_address: 'AA:BB', last_seen: null },
    ],
    low_battery: [
      {
        kind: 'low_battery',
        display_id: 'e1',
        asset_name: 'Lathe panel',
        battery_percent: 7,
        last_battery_at: null,
      },
    ],
    ota_failed: [],
  },
  recent_commands: [
    {
      id: 'c1',
      device_id: 'd1',
      device_name: 'Front door',
      command: 'restart',
      ack_status: 'acked',
      sent_at: '2026-05-31T00:00:00Z',
      sent_by: 'admin',
    },
  ],
  recent_updates: [],
  ...overrides,
});

const renderPage = (initialEntry = '/facilities/forgekey-dashboard') =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/facilities/forgekey-dashboard" element={<ForgeKeyDashboardPage />} />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ForgeKeyDashboardPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
  });

  test('non-staff users are redirected away', async () => {
    localStorage.setItem('is_staff', 'false');
    localStorage.setItem('is_superuser', 'false');

    renderPage();

    expect(await screen.findByText('HOME')).toBeInTheDocument();
    expect(mockForgekey.getFleetSummary).not.toHaveBeenCalled();
  });

  test('staff sees fleet stats and the needs-attention feed', async () => {
    localStorage.setItem('is_staff', 'true');
    mockForgekey.getFleetSummary.mockResolvedValue({ data: buildSummary() } as any);

    renderPage();

    expect(await screen.findByTestId('stat-total')).toHaveTextContent('5');
    expect(screen.getByTestId('stat-online')).toHaveTextContent('3');
    expect(screen.getByTestId('stat-offline')).toHaveTextContent('1');

    await waitFor(() => {
      expect(screen.getByTestId('attention-offline')).toBeInTheDocument();
    });
    expect(screen.getByText('Back door')).toBeInTheDocument();
    expect(screen.getByTestId('attention-lowbatt')).toBeInTheDocument();
    expect(screen.getByText('Lathe panel')).toBeInTheDocument();
    expect(screen.getByText('7%')).toBeInTheDocument();

    expect(screen.getByTestId('breakdown-firmware')).toHaveTextContent('1.0.0');
    expect(screen.getByText('Front door')).toBeInTheDocument();
  });

  test('shows an all-clear state when nothing needs attention', async () => {
    localStorage.setItem('is_staff', 'true');
    mockForgekey.getFleetSummary.mockResolvedValue({
      data: buildSummary({
        devices: {
          total: 1,
          active: 1,
          online: 1,
          offline: 0,
          never_seen: 0,
          by_type: [],
          by_capability: [],
          by_firmware: [],
        },
        epaper: { total: 0, bound: 0, unbound: 0, low_battery: 0 },
        firmware: { updates_in_flight: 0, recent_failures: 0 },
        attention: { offline: [], low_battery: [], ota_failed: [] },
        recent_commands: [],
        recent_updates: [],
      }),
    } as any);

    renderPage();

    expect(await screen.findByTestId('all-clear')).toBeInTheDocument();
  });
});
