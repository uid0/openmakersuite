/**
 * Tests for the ForgeKey badge enrollment admin (op-tup): non-staff redirect,
 * member listing with current badge, manual set, and the armed → captured →
 * saved enroll handshake.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ForgeKeyBadgeEnrollmentPage from '../../pages/ForgeKeyBadgeEnrollmentPage';
import { forgekeyAPI, userAPI } from '../../services/api';

vi.mock('../../utils/dialogs', async () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
  confirmAction: (_t: string, _m: string, onConfirm: () => void) => onConfirm(),
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    userAPI: {
      ...(actual as any).userAPI,
      listUsers: jest.fn(),
    },
    forgekeyAPI: {
      ...(actual as any).forgekeyAPI,
      armBadgeEnrollment: jest.fn(),
      cancelBadgeEnrollment: jest.fn(),
      getBadgeEnrollmentState: jest.fn(),
      setBadge: jest.fn(),
    },
  };
});

const mockUserApi = userAPI as jest.Mocked<typeof userAPI>;
const mockForgekey = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;

const buildUser = (overrides: Partial<any> = {}) => ({
  id: 1,
  username: 'alice',
  first_name: 'Alice',
  last_name: 'Adams',
  full_name: 'Alice Adams',
  email: 'alice@example.com',
  badge_number: null,
  is_active: true,
  ...overrides,
});

const renderPage = (initialEntry = '/facilities/forgekey-badges') =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/facilities/forgekey-badges" element={<ForgeKeyBadgeEnrollmentPage />} />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ForgeKeyBadgeEnrollmentPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
  });

  test('non-staff users are redirected away', async () => {
    localStorage.setItem('is_staff', 'false');
    renderPage();
    expect(await screen.findByText('HOME')).toBeInTheDocument();
    expect(mockUserApi.listUsers).not.toHaveBeenCalled();
  });

  test('lists members with their current badge', async () => {
    localStorage.setItem('is_staff', 'true');
    mockUserApi.listUsers.mockResolvedValue({
      data: [buildUser({ badge_number: 'A1' }), buildUser({ id: 2, username: 'bob', full_name: 'Bob Boyd' })],
    } as any);

    renderPage();
    expect(await screen.findByTestId('user-row-1')).toBeInTheDocument();
    expect(screen.getByTestId('badge-value-1')).toHaveTextContent('A1');
    expect(screen.getByTestId('badge-value-2')).toHaveTextContent('none');
  });

  test('sets a badge manually', async () => {
    localStorage.setItem('is_staff', 'true');
    mockUserApi.listUsers.mockResolvedValue({ data: [buildUser()] } as any);
    mockForgekey.setBadge.mockResolvedValue({ data: { user_id: 1, badge_number: 'MANUAL9' } } as any);

    renderPage();
    fireEvent.click(await screen.findByTestId('set-1'));
    fireEvent.change(screen.getByTestId('manual-input'), { target: { value: 'MANUAL9' } });
    fireEvent.click(screen.getByTestId('manual-save'));

    await waitFor(() =>
      expect(mockForgekey.setBadge).toHaveBeenCalledWith({ userId: 1, badgeNumber: 'MANUAL9' }),
    );
    await waitFor(() => expect(screen.getByTestId('badge-value-1')).toHaveTextContent('MANUAL9'));
  });

  test('enroll flow: armed then captured binds the scanned UID', async () => {
    localStorage.setItem('is_staff', 'true');
    mockUserApi.listUsers.mockResolvedValue({ data: [buildUser()] } as any);
    mockForgekey.armBadgeEnrollment.mockResolvedValue({
      data: { armed: true, user_id: 1, reader_id: null, ttl_seconds: 120 },
    } as any);
    mockForgekey.getBadgeEnrollmentState.mockResolvedValue({
      data: {
        armed: false,
        armed_user_id: null,
        reader_id: null,
        captured: { user_id: 1, badge_number: 'SCAN7' },
      },
    } as any);

    renderPage();
    fireEvent.click(await screen.findByTestId('enroll-1'));

    await waitFor(() =>
      expect(mockForgekey.armBadgeEnrollment).toHaveBeenCalledWith({ userId: 1 }),
    );
    expect(await screen.findByText(/Captured badge SCAN7/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('badge-value-1')).toHaveTextContent('SCAN7'));
  });
});
