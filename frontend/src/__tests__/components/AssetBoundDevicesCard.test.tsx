/**
 * Tests for the "Bound devices" section on the asset detail page (op-rmic):
 * listing AssetDevice + IndicatorBinding rows, detaching either, and the
 * attach flow (picker filtering + which endpoint each choice writes to).
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AssetBoundDevicesCard from '../../components/AssetBoundDevicesCard';
import { forgekeyAPI } from '../../services/api';

vi.mock('../../utils/dialogs', async () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
  // Auto-confirm so the detach paths run in tests.
  confirmAction: (_t: string, _m: string, onConfirm: () => void) => onConfirm(),
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    forgekeyAPI: {
      ...(actual as any).forgekeyAPI,
      listAssetDevices: jest.fn(),
      listIndicatorBindings: jest.fn(),
      listDevices: jest.fn(),
      listDeviceTypes: jest.fn(),
      createAssetDevice: jest.fn(),
      createIndicatorBinding: jest.fn(),
      deleteAssetDevice: jest.fn(),
      deleteIndicatorBinding: jest.fn(),
    },
  };
});

const mockApi = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;

const buildAssetDevice = (overrides: Partial<any> = {}) => ({
  id: 11,
  asset: 'a1',
  asset_name: 'Table Saw',
  device: 'dev-relay',
  device_name: 'Relay 1',
  device_mac_address: 'DE:AD:00:00:00:01',
  role: 'power_control',
  is_primary: true,
  power_off_delay_seconds: 0,
  created_at: '2026-06-01T00:00:00Z',
  ...overrides,
});

const buildIndicatorBinding = (overrides: Partial<any> = {}) => ({
  id: 'b1',
  device: 'dev-light',
  device_name: 'Light 1',
  device_mac_address: 'DE:AD:00:00:00:02',
  asset: 'a1',
  asset_name: 'Table Saw',
  location: null,
  location_name: null,
  last_status: 'available',
  last_presentation: null,
  last_synced_at: null,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  ...overrides,
});

const buildDevice = (overrides: Partial<any> = {}) => ({
  id: 'dev-free',
  mac_address: 'DE:AD:00:00:00:09',
  device_type: 1,
  device_type_name: 'AC Relay',
  name: 'Spare Relay',
  description: '',
  firmware_version: '1.0.0',
  last_seen: null,
  is_online: true,
  is_active: true,
  location: null,
  enrollment_photo: null,
  last_photo: null,
  boot_count: null,
  free_heap: null,
  ip: null,
  capabilities: [],
  capabilities_announced_at: null,
  relay_channels: [],
  indicator_state: {},
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  ...overrides,
});

const DEVICE_TYPES = [
  { id: 1, name: 'AC Relay', code: 'power_relay' },
  { id: 2, name: 'Indicator/Status Light', code: 'indicator' },
];

const renderCard = (assetId = 'a1') =>
  render(
    <MantineProvider env="test">
      <AssetBoundDevicesCard assetId={assetId} />
    </MantineProvider>,
  );

/** Serve an empty asset with the given fleet available to the picker. */
const serveEmpty = (fleet: any[] = []) => {
  mockApi.listAssetDevices.mockResolvedValue({ data: [] } as any);
  mockApi.listIndicatorBindings.mockResolvedValue({ data: [] } as any);
  mockApi.listDevices.mockResolvedValue({ data: fleet } as any);
  mockApi.listDeviceTypes.mockResolvedValue({ data: DEVICE_TYPES } as any);
};

describe('AssetBoundDevicesCard', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('lists asset-device and indicator bindings for the asset', async () => {
    mockApi.listAssetDevices.mockResolvedValue({ data: [buildAssetDevice()] } as any);
    mockApi.listIndicatorBindings.mockResolvedValue({
      data: [buildIndicatorBinding()],
    } as any);

    renderCard();

    await screen.findByTestId('asset-bound-devices');
    expect(mockApi.listAssetDevices).toHaveBeenCalledWith({ asset: 'a1' });
    expect(mockApi.listIndicatorBindings).toHaveBeenCalledWith({ asset: 'a1' });
    expect(screen.getByText('Bound devices (2)')).toBeInTheDocument();
    expect(screen.getByText('Relay 1')).toBeInTheDocument();
    expect(screen.getByTestId('role-badge-11')).toHaveTextContent('Power control');
    expect(screen.getByTestId('primary-badge-11')).toBeInTheDocument();
    expect(screen.getByText('Light 1')).toBeInTheDocument();
    expect(screen.getByText('Indicator')).toBeInTheDocument();
  });

  it('shows an empty state with the attach control', async () => {
    serveEmpty();

    renderCard();

    await screen.findByTestId('bound-devices-empty');
    expect(screen.getByTestId('attach-device-open')).toBeInTheDocument();
  });

  it('detaches an asset device (after confirm)', async () => {
    mockApi.listAssetDevices.mockResolvedValue({ data: [buildAssetDevice()] } as any);
    mockApi.listIndicatorBindings.mockResolvedValue({ data: [] } as any);
    mockApi.deleteAssetDevice.mockResolvedValue({} as any);

    renderCard();

    fireEvent.click(await screen.findByTestId('detach-11'));
    await waitFor(() => expect(mockApi.deleteAssetDevice).toHaveBeenCalledWith(11));
  });

  it('detaches an indicator binding (after confirm)', async () => {
    mockApi.listAssetDevices.mockResolvedValue({ data: [] } as any);
    mockApi.listIndicatorBindings.mockResolvedValue({
      data: [buildIndicatorBinding()],
    } as any);
    mockApi.deleteIndicatorBinding.mockResolvedValue({} as any);

    renderCard();

    fireEvent.click(await screen.findByTestId('detach-indicator-b1'));
    await waitFor(() => expect(mockApi.deleteIndicatorBinding).toHaveBeenCalledWith('b1'));
  });

  it('attaches a relay as the primary control device', async () => {
    serveEmpty([buildDevice()]);
    mockApi.createAssetDevice.mockResolvedValue({ data: {} } as any);

    renderCard();

    fireEvent.click(await screen.findByTestId('attach-device-open'));
    // The picker loads lazily once the panel opens.
    await waitFor(() => expect(mockApi.listDevices).toHaveBeenCalled());
    // No devices bound yet, so "primary" is pre-checked.
    expect(screen.getByTestId('attach-primary')).toBeChecked();

    fireEvent.click(screen.getByPlaceholderText('Pick a device…'));
    fireEvent.click(await screen.findByRole('option', { name: 'Spare Relay (DE:AD:00:00:00:09)' }));
    fireEvent.click(screen.getByTestId('attach-submit'));

    await waitFor(() =>
      expect(mockApi.createAssetDevice).toHaveBeenCalledWith({
        asset: 'a1',
        device: 'dev-free',
        role: 'power_control',
        is_primary: true,
      }),
    );
  });

  it('attaching an indicator writes a binding and only offers indicator devices', async () => {
    serveEmpty([
      buildDevice(),
      buildDevice({
        id: 'dev-light-free',
        name: 'Spare Light',
        mac_address: 'DE:AD:00:00:00:0A',
        device_type: 2,
        device_type_name: 'Indicator/Status Light',
      }),
    ]);
    mockApi.createIndicatorBinding.mockResolvedValue({ data: {} } as any);

    renderCard();

    fireEvent.click(await screen.findByTestId('attach-device-open'));
    await waitFor(() => expect(mockApi.listDeviceTypes).toHaveBeenCalled());

    // Mantine renders a hidden value input alongside the visible one, both tied
    // to the label — go through the test id to hit the real control.
    fireEvent.click(screen.getByTestId('attach-role-select'));
    fireEvent.click(await screen.findByRole('option', { name: 'Indicator light' }));

    // The relay drops out of the picker; the indicator device stays.
    fireEvent.click(screen.getByPlaceholderText('Pick an indicator device…'));
    expect(
      await screen.findByRole('option', { name: 'Spare Light (DE:AD:00:00:00:0A)' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: 'Spare Relay (DE:AD:00:00:00:09)' }),
    ).not.toBeInTheDocument();
    // is_primary is an AssetDevice column — not offered for indicators.
    expect(screen.queryByTestId('attach-primary')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('option', { name: 'Spare Light (DE:AD:00:00:00:0A)' }));
    fireEvent.click(screen.getByTestId('attach-submit'));

    await waitFor(() =>
      expect(mockApi.createIndicatorBinding).toHaveBeenCalledWith({
        device: 'dev-light-free',
        asset: 'a1',
      }),
    );
    expect(mockApi.createAssetDevice).not.toHaveBeenCalled();
  });

  it('walks every page of the fleet for the picker', async () => {
    mockApi.listAssetDevices.mockResolvedValue({ data: [] } as any);
    mockApi.listIndicatorBindings.mockResolvedValue({ data: [] } as any);
    mockApi.listDeviceTypes.mockResolvedValue({ data: DEVICE_TYPES } as any);
    mockApi.listDevices
      .mockResolvedValueOnce({
        data: { results: [buildDevice()], next: 'http://api/forgekey/devices/?page=2' },
      } as any)
      .mockResolvedValueOnce({
        data: {
          results: [
            buildDevice({
              id: 'dev-page-2',
              name: 'Page Two Relay',
              mac_address: 'DE:AD:00:00:00:0B',
            }),
          ],
          next: null,
        },
      } as any);

    renderCard();

    fireEvent.click(await screen.findByTestId('attach-device-open'));
    await waitFor(() => expect(mockApi.listDevices).toHaveBeenCalledTimes(2));
    expect(mockApi.listDevices).toHaveBeenNthCalledWith(1, { page: 1 });
    expect(mockApi.listDevices).toHaveBeenNthCalledWith(2, { page: 2 });

    fireEvent.click(screen.getByPlaceholderText('Pick a device…'));
    expect(
      await screen.findByRole('option', { name: 'Page Two Relay (DE:AD:00:00:00:0B)' }),
    ).toBeInTheDocument();
  });

  it('keeps already-bound devices out of the picker', async () => {
    mockApi.listAssetDevices.mockResolvedValue({ data: [buildAssetDevice()] } as any);
    mockApi.listIndicatorBindings.mockResolvedValue({ data: [] } as any);
    mockApi.listDevices.mockResolvedValue({
      data: [buildDevice({ id: 'dev-relay', name: 'Relay 1' }), buildDevice()],
    } as any);
    mockApi.listDeviceTypes.mockResolvedValue({ data: DEVICE_TYPES } as any);

    renderCard();

    fireEvent.click(await screen.findByTestId('attach-device-open'));
    await waitFor(() => expect(mockApi.listDevices).toHaveBeenCalled());
    // An asset already carrying a device does not pre-check "primary".
    expect(screen.getByTestId('attach-primary')).not.toBeChecked();

    fireEvent.click(screen.getByPlaceholderText('Pick a device…'));
    expect(
      await screen.findByRole('option', { name: 'Spare Relay (DE:AD:00:00:00:09)' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: 'Relay 1 (DE:AD:00:00:00:09)' }),
    ).not.toBeInTheDocument();
  });
});
