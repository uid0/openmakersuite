/**
 * Tests for the ForgeKey access log (op-tup): non-staff redirect, rendering
 * grant/deny rows with their reason, and filtering by action.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ForgeKeyAccessLogPage from '../../pages/ForgeKeyAccessLogPage';
import { forgekeyAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    forgekeyAPI: {
      ...(actual as any).forgekeyAPI,
      listAccessLog: jest.fn(),
    },
  };
});

const mockForgekey = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;

const grantedEvent = {
  id: 'evt-1',
  created_at: '2026-06-01T10:00:00Z',
  action: 'access_granted',
  action_display: 'Access granted',
  actor: 5,
  actor_username: 'alice',
  asset: 'a1',
  asset_name: 'Table Saw',
  device: 'd1',
  device_mac: 'AA:BB',
  notes: '',
  metadata: {},
};

const deniedEvent = {
  id: 'evt-2',
  created_at: '2026-06-01T11:00:00Z',
  action: 'access_denied',
  action_display: 'Access denied',
  actor: null,
  actor_username: null,
  asset: 'a1',
  asset_name: 'Table Saw',
  device: 'd1',
  device_mac: 'AA:BB',
  notes: 'access denied: not authorized',
  metadata: { reason: 'not_authorized' },
};

const renderPage = (initialEntry = '/facilities/forgekey-access-log') =>
  render(
    <MantineProvider env="test">
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/facilities/forgekey-access-log" element={<ForgeKeyAccessLogPage />} />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ForgeKeyAccessLogPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
  });

  test('non-staff users are redirected away', async () => {
    localStorage.setItem('is_staff', 'false');
    renderPage();
    expect(await screen.findByText('HOME')).toBeInTheDocument();
    expect(mockForgekey.listAccessLog).not.toHaveBeenCalled();
  });

  test('renders grant and deny rows with the deny reason', async () => {
    localStorage.setItem('is_staff', 'true');
    mockForgekey.listAccessLog.mockResolvedValue({ data: [grantedEvent, deniedEvent] } as any);

    renderPage();
    expect(await screen.findByTestId('access-log-row-evt-1')).toBeInTheDocument();
    // Scope action labels to the table — the filter <Select> mounts the same
    // labels as options in the DOM.
    const table = screen.getByTestId('access-log-table');
    expect(within(table).getByText('Access granted')).toBeInTheDocument();
    expect(within(table).getByText('Access denied')).toBeInTheDocument();
    expect(within(table).getByText('alice')).toBeInTheDocument();
    // The deny reason is humanized from metadata.reason.
    expect(within(table).getByText('Not authorized')).toBeInTheDocument();
    expect(within(table).getByText('unknown card')).toBeInTheDocument();
    // First load defaults to access-control events only.
    expect(mockForgekey.listAccessLog).toHaveBeenCalledWith({ accessOnly: true, action: undefined });
  });

  test('filters by action', async () => {
    localStorage.setItem('is_staff', 'true');
    mockForgekey.listAccessLog.mockResolvedValue({ data: [grantedEvent, deniedEvent] } as any);

    renderPage();
    await screen.findByTestId('access-log-table');

    fireEvent.click(screen.getByTestId('access-log-action-filter'));
    fireEvent.click(await screen.findByRole('option', { name: 'Denied' }));

    await waitFor(() =>
      expect(mockForgekey.listAccessLog).toHaveBeenCalledWith({
        accessOnly: true,
        action: 'access_denied',
      }),
    );
  });
});
