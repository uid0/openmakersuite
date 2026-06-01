/**
 * Tests for the ForgeKey device lifecycle controls (lock/unlock, reset,
 * retire/reactivate, delete).
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import DeviceLifecycleCard from '../../components/DeviceLifecycleCard';
import { forgekeyAPI } from '../../services/api';

vi.mock('../../utils/dialogs', async () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
  showInfo: jest.fn(),
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    forgekeyAPI: {
      ...(actual as any).forgekeyAPI,
      disableDevice: jest.fn(),
      enableDevice: jest.fn(),
      restart: jest.fn(),
      retireDevice: jest.fn(),
      reactivateDevice: jest.fn(),
      deleteDevice: jest.fn(),
    },
  };
});

const mockApi = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;

const buildDevice = (overrides: Partial<any> = {}) => ({
  id: 'd1',
  mac_address: 'AA:BB:CC:DD:EE:01',
  device_type: 1,
  device_type_name: 'indicator',
  name: 'Shop Indicator',
  description: '',
  firmware_version: '1.0',
  last_seen: null,
  is_online: true,
  is_active: true,
  location: null,
  ...overrides,
});

const renderCard = (device: any, onChanged = jest.fn(), onDeleted = jest.fn()) =>
  render(
    <MantineProvider>
      <DeviceLifecycleCard device={device} onChanged={onChanged} onDeleted={onDeleted} />
    </MantineProvider>,
  );

describe('DeviceLifecycleCard', () => {
  beforeEach(() => jest.clearAllMocks());

  it('locks (cuts power) via disableDevice', async () => {
    mockApi.disableDevice.mockResolvedValue({} as any);
    renderCard(buildDevice());
    fireEvent.click(screen.getByTestId('device-lock'));
    await waitFor(() => expect(mockApi.disableDevice).toHaveBeenCalledWith('d1'));
  });

  it('retires an active device and reloads', async () => {
    const onChanged = jest.fn();
    mockApi.retireDevice.mockResolvedValue({} as any);
    renderCard(buildDevice(), onChanged);
    fireEvent.click(screen.getByTestId('device-retire'));
    await waitFor(() => expect(mockApi.retireDevice).toHaveBeenCalledWith('d1'));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('shows reactivate + retired badge for an inactive device', () => {
    renderCard(buildDevice({ is_active: false }));
    expect(screen.getByTestId('device-reactivate')).toBeInTheDocument();
    expect(screen.getByTestId('device-retired-badge')).toBeInTheDocument();
    expect(screen.queryByTestId('device-retire')).not.toBeInTheDocument();
  });

  it('deletes after confirmation and navigates away', async () => {
    const onDeleted = jest.fn();
    mockApi.deleteDevice.mockResolvedValue({} as any);
    renderCard(buildDevice(), jest.fn(), onDeleted);
    fireEvent.click(screen.getByTestId('device-delete'));
    fireEvent.click(await screen.findByTestId('device-delete-confirm'));
    await waitFor(() => expect(mockApi.deleteDevice).toHaveBeenCalledWith('d1'));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
  });
});
