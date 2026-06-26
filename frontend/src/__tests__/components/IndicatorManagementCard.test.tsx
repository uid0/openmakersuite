/**
 * Tests for IndicatorManagementCard (epic ga-72l): gating to indicator devices,
 * rendering a binding, the bind / unbind / sync / test actions firing the right
 * API calls, and the live-preview legend.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import IndicatorManagementCard from '../../components/IndicatorManagementCard';
import { assetsAPI, forgekeyAPI, inventoryAPI } from '../../services/api';

vi.mock('../../utils/dialogs', async () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
  // Auto-confirm so the unbind path runs in tests.
  confirmAction: (_t: string, _m: string, onConfirm: () => void) => onConfirm(),
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    forgekeyAPI: {
      ...(actual as any).forgekeyAPI,
      listDeviceTypes: jest.fn(),
      listIndicatorBindings: jest.fn(),
      createIndicatorBinding: jest.fn(),
      deleteIndicatorBinding: jest.fn(),
      indicatorSync: jest.fn(),
      indicatorTest: jest.fn(),
    },
    assetsAPI: {
      ...(actual as any).assetsAPI,
      listAssets: jest.fn(),
    },
    inventoryAPI: {
      ...(actual as any).inventoryAPI,
      listLocations: jest.fn(),
    },
  };
});

const mockApi = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;
const mockAssets = assetsAPI as jest.Mocked<typeof assetsAPI>;
const mockInventory = inventoryAPI as jest.Mocked<typeof inventoryAPI>;

const INDICATOR_TYPE = { id: 9, name: 'Indicator/Status Light', code: 'indicator' };

const buildDevice = (overrides: Partial<any> = {}) => ({
  id: 'ind-1',
  mac_address: 'AA:BB:CC:DD:EE:01',
  device_type: 9,
  device_type_name: 'Indicator/Status Light',
  name: 'Bay light',
  description: '',
  firmware_version: '1.0.0',
  last_seen: '2026-06-01T00:00:00Z',
  is_online: true,
  is_active: true,
  location: null,
  enrollment_photo: null,
  last_photo: null,
  boot_count: 1,
  free_heap: null,
  ip: null,
  capabilities: [],
  capabilities_announced_at: null,
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  ...overrides,
});

const buildBinding = (overrides: Partial<any> = {}) => ({
  id: 'bind-1',
  device: 'ind-1',
  device_mac_address: 'AA:BB:CC:DD:EE:01',
  device_name: 'Bay light',
  asset: 'asset-1',
  asset_name: 'Table Saw',
  location: null,
  location_name: null,
  last_status: 'in_use',
  last_presentation: { cmd: 'set_indicator', color: 'green', brightness: 'high', pattern: 'solid' },
  last_synced_at: '2026-06-02T00:00:00Z',
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-06-02T00:00:00Z',
  ...overrides,
});

const renderCard = (device = buildDevice()) =>
  render(
    <MantineProvider>
      <IndicatorManagementCard device={device as any} />
    </MantineProvider>,
  );

describe('IndicatorManagementCard', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockApi.listDeviceTypes.mockResolvedValue({ data: [INDICATOR_TYPE] } as any);
    mockApi.listIndicatorBindings.mockResolvedValue({ data: [] } as any);
    mockAssets.listAssets.mockResolvedValue({
      data: { count: 1, next: null, previous: null, results: [
        { id: 'asset-1', name: 'Table Saw', asset_tag: 'TS-1' },
      ] },
    } as any);
    mockInventory.listLocations.mockResolvedValue({
      data: [{ id: 5, name: 'Wood Shop' }],
    } as any);
  });

  it('renders a greyed not-applicable stub for a non-indicator device', async () => {
    const device = buildDevice({ device_type: 1, device_type_name: 'people_counter' });
    renderCard(device);
    await waitFor(() => expect(mockApi.listDeviceTypes).toHaveBeenCalled());
    // The full management card is absent...
    expect(screen.queryByTestId('indicator-management')).not.toBeInTheDocument();
    // ...replaced by a dimmed "not applicable" stub (op-3u4).
    const stub = await screen.findByTestId('indicator-not-applicable');
    expect(stub).toHaveAttribute('aria-disabled', 'true');
    expect(within(stub).getByText(/not applicable for this device type/i)).toBeInTheDocument();
  });

  it('renders the current binding with its derived light state', async () => {
    mockApi.listIndicatorBindings.mockResolvedValue({ data: [buildBinding()] } as any);
    renderCard();

    await screen.findByTestId('indicator-binding');
    expect(screen.getByTestId('indicator-target-name')).toHaveTextContent('Table Saw');
    expect(screen.getByTestId('indicator-current-status')).toHaveTextContent('In use');
    expect(screen.getByTestId('indicator-current-swatch')).toBeInTheDocument();
    // Legend is always shown.
    expect(screen.getByTestId('indicator-legend')).toBeInTheDocument();
  });

  it('syncs the bound indicator', async () => {
    mockApi.listIndicatorBindings.mockResolvedValue({ data: [buildBinding()] } as any);
    mockApi.indicatorSync.mockResolvedValue({
      data: { status: 'available', presentation: {}, command_id: 'cmd-9' },
    } as any);
    renderCard();

    fireEvent.click(await screen.findByTestId('indicator-sync-btn'));
    await waitFor(() => expect(mockApi.indicatorSync).toHaveBeenCalledWith('bind-1'));
  });

  it('unbinds the indicator after confirm', async () => {
    mockApi.listIndicatorBindings.mockResolvedValue({ data: [buildBinding()] } as any);
    mockApi.deleteIndicatorBinding.mockResolvedValue({} as any);
    renderCard();

    fireEvent.click(await screen.findByTestId('indicator-unbind-btn'));
    await waitFor(() => expect(mockApi.deleteIndicatorBinding).toHaveBeenCalledWith('bind-1'));
  });

  it('binds the indicator to an asset', async () => {
    mockApi.createIndicatorBinding.mockResolvedValue({} as any);
    renderCard();

    await screen.findByTestId('indicator-bind-form');
    fireEvent.click(screen.getByPlaceholderText('Pick an asset'));
    fireEvent.click(await screen.findByRole('option', { name: 'Table Saw (TS-1)' }));
    fireEvent.click(screen.getByTestId('indicator-bind-btn'));

    await waitFor(() =>
      expect(mockApi.createIndicatorBinding).toHaveBeenCalledWith({
        device: 'ind-1',
        asset: 'asset-1',
      }),
    );
  });

  it('binds the indicator to a room', async () => {
    mockApi.createIndicatorBinding.mockResolvedValue({} as any);
    renderCard();

    await screen.findByTestId('indicator-bind-form');
    fireEvent.click(screen.getByText('Room'));
    fireEvent.click(screen.getByPlaceholderText('Pick a room'));
    fireEvent.click(await screen.findByRole('option', { name: 'Wood Shop' }));
    fireEvent.click(screen.getByTestId('indicator-bind-btn'));

    await waitFor(() =>
      expect(mockApi.createIndicatorBinding).toHaveBeenCalledWith({
        device: 'ind-1',
        location: 5,
      }),
    );
  });

  it('sends a manual test with the chosen presentation', async () => {
    mockApi.indicatorTest.mockResolvedValue({
      data: { status: 'sent', device: 'AA:BB:CC:DD:EE:01', command_id: 'cmd-1', payload: { pattern: 'solid' } },
    } as any);
    renderCard();

    fireEvent.click(await screen.findByTestId('indicator-test-btn'));
    await waitFor(() =>
      expect(mockApi.indicatorTest).toHaveBeenCalledWith('ind-1', {
        brightness: 'high',
        pattern: 'solid',
        color: 'green',
      }),
    );
  });
});
