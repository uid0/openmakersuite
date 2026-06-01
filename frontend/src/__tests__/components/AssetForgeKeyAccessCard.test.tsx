/**
 * Tests for the ForgeKey access controls surfaced on the asset detail page
 * (#7b): operational mode + classroom toggle, authorizations (revoke), and
 * lockouts (unlock).
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AssetForgeKeyAccessCard from '../../components/AssetForgeKeyAccessCard';
import { forgekeyAPI } from '../../services/api';

vi.mock('../../utils/dialogs', async () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
  // Auto-confirm so the revoke/unlock action paths run in tests.
  confirmAction: (_t: string, _m: string, onConfirm: () => void) => onConfirm(),
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    forgekeyAPI: {
      ...(actual as any).forgekeyAPI,
      listOperationalModes: jest.fn(),
      listAuthorizations: jest.fn(),
      listLockouts: jest.fn(),
      enableClassroomMode: jest.fn(),
      disableClassroomMode: jest.fn(),
      revokeAuthorization: jest.fn(),
      unlockLockout: jest.fn(),
    },
  };
});

const mockApi = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;

const buildMode = (overrides: Partial<any> = {}) => ({
  id: 1,
  asset: 'a1',
  asset_name: 'Table Saw',
  mode: 'classroom',
  classroom_mode_enabled: false,
  classroom_mode_enabled_by: null,
  classroom_mode_enabled_by_username: null,
  classroom_mode_enabled_at: null,
  updated_at: '2026-06-01T00:00:00Z',
  ...overrides,
});

const buildAuth = (overrides: Partial<any> = {}) => ({
  id: 10,
  asset: 'a1',
  asset_name: 'Table Saw',
  user: 5,
  username: 'alice',
  authorized_by: 2,
  authorized_by_username: 'bob',
  authorized_at: '2026-06-01T00:00:00Z',
  is_active: true,
  notes: '',
  ...overrides,
});

const buildLockout = (overrides: Partial<any> = {}) => ({
  id: 'l1',
  asset: 'a1',
  asset_name: 'Table Saw',
  locked_by: 3,
  locked_by_username: 'carol',
  lockout_level: 'user',
  reason: 'Blade guard missing',
  locked_at: '2026-06-01T00:00:00Z',
  unlocked_at: null,
  unlocked_by: null,
  unlocked_by_username: null,
  is_active: true,
  ...overrides,
});

const renderCard = (assetId = 'a1') =>
  render(
    <MantineProvider>
      <AssetForgeKeyAccessCard assetId={assetId} />
    </MantineProvider>,
  );

describe('AssetForgeKeyAccessCard', () => {
  beforeEach(() => jest.clearAllMocks());

  it('renders nothing for an asset with no ForgeKey data', async () => {
    mockApi.listOperationalModes.mockResolvedValue({ data: [] } as any);
    mockApi.listAuthorizations.mockResolvedValue({ data: [] } as any);
    mockApi.listLockouts.mockResolvedValue({ data: [] } as any);

    renderCard();
    await waitFor(() => expect(mockApi.listOperationalModes).toHaveBeenCalledWith('a1'));
    expect(screen.queryByTestId('asset-forgekey-access')).not.toBeInTheDocument();
  });

  it('renders mode, authorizations, and lockouts', async () => {
    mockApi.listOperationalModes.mockResolvedValue({ data: [buildMode()] } as any);
    mockApi.listAuthorizations.mockResolvedValue({ data: [buildAuth()] } as any);
    mockApi.listLockouts.mockResolvedValue({ data: [buildLockout()] } as any);

    renderCard();
    await screen.findByTestId('asset-forgekey-access');
    expect(screen.getByTestId('operational-mode-badge')).toHaveTextContent('classroom');
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.getByText('carol · user')).toBeInTheDocument();
    expect(screen.getByText('Enable classroom mode')).toBeInTheDocument();
    expect(screen.getByText('Revoke')).toBeInTheDocument();
    expect(screen.getByText('Unlock')).toBeInTheDocument();
  });

  it('enables classroom mode', async () => {
    mockApi.listOperationalModes.mockResolvedValue({ data: [buildMode()] } as any);
    mockApi.listAuthorizations.mockResolvedValue({ data: [] } as any);
    mockApi.listLockouts.mockResolvedValue({ data: [] } as any);
    mockApi.enableClassroomMode.mockResolvedValue({} as any);

    renderCard();
    fireEvent.click(await screen.findByText('Enable classroom mode'));
    await waitFor(() => expect(mockApi.enableClassroomMode).toHaveBeenCalledWith(1));
  });

  it('revokes an authorization (after confirm)', async () => {
    mockApi.listOperationalModes.mockResolvedValue({ data: [] } as any);
    mockApi.listAuthorizations.mockResolvedValue({ data: [buildAuth()] } as any);
    mockApi.listLockouts.mockResolvedValue({ data: [] } as any);
    mockApi.revokeAuthorization.mockResolvedValue({} as any);

    renderCard();
    fireEvent.click(await screen.findByText('Revoke'));
    await waitFor(() => expect(mockApi.revokeAuthorization).toHaveBeenCalledWith(10));
  });
});
