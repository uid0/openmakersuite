/**
 * Tests for the lockers monitoring page.
 *
 * Covers: non-staff redirect, the fleet table + stat cards, and the
 * intrusion (ALARM / not-secure) highlight.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import LockersPage from '../../pages/LockersPage';
import { lockersAPI } from '../../services/api';
import { showSuccess } from '../../utils/dialogs';

vi.mock('../../utils/dialogs', async () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
  showInfo: jest.fn(),
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    lockersAPI: {
      listLockers: jest.fn(),
      unlock: jest.fn(),
      issueOtp: jest.fn(),
      listOtps: jest.fn(),
      revokeOtp: jest.fn(),
    },
  };
});

const mockApi = lockersAPI as jest.Mocked<typeof lockersAPI>;

const SECURE_STATUS = {
  secure: true,
  state: 'SECURE',
  reed_closed: true,
  latch_locked: true,
  ir_broken: false,
  mortise_active: false,
  item_present: true,
  last_trigger: 'signed_command',
  firmware_version: '0.1.0',
  last_status_at: '2026-05-01T00:00:00Z',
  is_alarm: false,
  is_insecure: false,
  device_mac: 'AA:BB',
  device_is_online: true,
};

const buildLocker = (overrides: Partial<any> = {}) => ({
  id: 'lk1',
  name: 'Wood Shop locker 4',
  slug: 'ws-4',
  location: 1,
  location_name: 'Wood Shop',
  owning_sig: 2,
  owning_sig_name: 'Wood SIG',
  description: '',
  power_source: 'poe',
  current_asset: null,
  current_asset_name: null,
  is_high_trust: false,
  led_count: 8,
  is_active: true,
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-01T00:00:00Z',
  devices: [
    {
      id: 1,
      locker: 'lk1',
      device: 'd1',
      device_mac: 'AA:BB',
      device_is_online: true,
      role: 'latch',
      role_display: 'Latch controller',
      is_primary: true,
      notes: '',
    },
  ],
  status: SECURE_STATUS,
  ...overrides,
});

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/facilities/lockers']}>
        <Routes>
          <Route path="/facilities/lockers" element={<LockersPage />} />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('LockersPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
  });

  test('non-staff users are redirected', async () => {
    localStorage.setItem('is_staff', 'false');
    localStorage.setItem('is_superuser', 'false');

    renderPage();

    expect(await screen.findByText('HOME')).toBeInTheDocument();
    expect(mockApi.listLockers).not.toHaveBeenCalled();
  });

  test('staff sees the locker fleet with status + stats', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.listLockers.mockResolvedValue({ data: [buildLocker()] } as any);

    renderPage();

    expect(await screen.findByText('Wood Shop locker 4')).toBeInTheDocument();
    expect(screen.getByText('Wood Shop')).toBeInTheDocument();
    expect(screen.getByText('SECURE')).toBeInTheDocument();
    expect(screen.getByTestId('stat-secure')).toHaveTextContent('1');
    expect(screen.getByTestId('stat-attention')).toHaveTextContent('0');
  });

  test('flags a locker in ALARM as needing attention', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.listLockers.mockResolvedValue({
      data: [
        buildLocker({
          status: { ...SECURE_STATUS, secure: false, state: 'ALARM', is_alarm: true, is_insecure: true },
        }),
      ],
    } as any);

    renderPage();

    expect(await screen.findByText('ALARM')).toBeInTheDocument();
    expect(screen.getByText('Alarm')).toBeInTheDocument(); // the attention badge
    expect(screen.getByTestId('stat-attention')).toHaveTextContent('1');
  });

  test('staff can send an unlock command', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.listLockers.mockResolvedValue({ data: [buildLocker()] } as any);
    mockApi.unlock.mockResolvedValue({
      data: { status: 'unlock_sent', topic: 'forgekey/x/command', reason: 'staff_bypass' },
    } as any);

    renderPage();

    const unlockBtn = await screen.findByTestId('unlock-lk1');
    fireEvent.click(unlockBtn);

    await waitFor(() => expect(mockApi.unlock).toHaveBeenCalledWith('lk1'));
    await waitFor(() => expect(showSuccess).toHaveBeenCalled());
  });
});
