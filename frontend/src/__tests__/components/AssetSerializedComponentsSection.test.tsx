/**
 * Tests for AssetSerializedComponentsSection (#818) — the "installed now" +
 * "serial history" surface on the asset detail page.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';

import AssetSerializedComponentsSection from '../../components/inventory/AssetSerializedComponentsSection';
import {
  ComponentUsageEvent,
  SerializedComponent,
  serializedComponentsAPI,
} from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    serializedComponentsAPI: {
      list: jest.fn(),
      listUsageEvents: jest.fn(),
    },
  };
});

const mockAPI = serializedComponentsAPI as jest.Mocked<typeof serializedComponentsAPI>;

const ASSET_ID = 'asset-1';

const buildUnit = (overrides: Partial<SerializedComponent> = {}): SerializedComponent => ({
  id: 'unit-1',
  item: 'item-1',
  item_name: 'Cutting blade',
  item_sku: 'BLD-1',
  serial_number: 'SN-INSTALLED',
  lot: '',
  status: 'installed',
  status_display: 'Installed',
  tracking_mode: 'reusable',
  available_actions: ['remove', 'retire'],
  installed_in_asset: ASSET_ID,
  installed_in_asset_name: 'Laser cutter',
  received_at: '2026-06-01T00:00:00Z',
  installed_at: '2026-06-02T00:00:00Z',
  disposed_at: null,
  provenance_delivery_item: null,
  provenance_purchase_order_item: null,
  disposal_reason: '',
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-02T00:00:00Z',
  ...overrides,
});

const buildEvent = (overrides: Partial<ComponentUsageEvent> = {}): ComponentUsageEvent => ({
  id: 'ev-1',
  component: 'unit-9',
  component_serial: 'SN-PAST',
  component_item_name: 'Old blade',
  asset: ASSET_ID,
  asset_name: 'Laser cutter',
  action: 'remove',
  action_display: 'Remove',
  at: '2026-05-10T10:00:00Z',
  actor: 1,
  actor_username: 'alice',
  notes: '',
  created_at: '2026-05-10T10:00:00Z',
  ...overrides,
});

const paginated = <T,>(results: T[]) =>
  ({ data: { count: results.length, next: null, previous: null, results } }) as never;

const renderSection = () =>
  render(
    <MantineProvider>
      <AssetSerializedComponentsSection assetId={ASSET_ID} />
    </MantineProvider>,
  );

describe('AssetSerializedComponentsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows installed-now units and the serial history for the machine', async () => {
    mockAPI.list.mockResolvedValue(paginated([buildUnit()]));
    mockAPI.listUsageEvents.mockResolvedValue(paginated([buildEvent()]));

    renderSection();

    expect(await screen.findByText('SN-INSTALLED')).toBeInTheDocument();
    expect(screen.getByText('SN-PAST')).toBeInTheDocument();
    expect(screen.getByText('Old blade')).toBeInTheDocument();

    // Installed-now is scoped to this asset + status=installed.
    expect(mockAPI.list).toHaveBeenCalledWith({
      installed_in_asset: ASSET_ID,
      status: 'installed',
    });
    // History is every serial this machine has used (?asset=).
    expect(mockAPI.listUsageEvents).toHaveBeenCalledWith({ asset: ASSET_ID });
  });

  it('renders nothing when the asset has no serialized history at all', async () => {
    mockAPI.list.mockResolvedValue(paginated([]));
    mockAPI.listUsageEvents.mockResolvedValue(paginated([]));

    renderSection();

    await waitFor(() => expect(mockAPI.listUsageEvents).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByTestId('asset-serialized-section')).not.toBeInTheDocument(),
    );
  });

  it('shows the installed-now empty message when only history exists', async () => {
    mockAPI.list.mockResolvedValue(paginated([]));
    mockAPI.listUsageEvents.mockResolvedValue(paginated([buildEvent()]));

    renderSection();

    expect(
      await screen.findByText('No serialized components are currently installed in this asset.'),
    ).toBeInTheDocument();
    expect(screen.getByText('SN-PAST')).toBeInTheDocument();
  });
});
