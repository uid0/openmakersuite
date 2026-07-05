/**
 * Tests for SerializedComponentsPanel (#818) — the per-unit "instances" view
 * on the inventory item detail page: unit list, lifecycle action buttons,
 * add-unit, and per-unit usage history.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import SerializedComponentsPanel from '../../components/inventory/SerializedComponentsPanel';
import {
  assetsAPI,
  SerializedComponent,
  serializedComponentsAPI,
} from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    serializedComponentsAPI: {
      list: jest.fn(),
      create: jest.fn(),
      receive: jest.fn(),
      install: jest.fn(),
      remove: jest.fn(),
      consume: jest.fn(),
      retire: jest.fn(),
      dispose: jest.fn(),
      listUsageEvents: jest.fn(),
    },
    assetsAPI: {
      listAssets: jest.fn(),
    },
  };
});

const mockAPI = serializedComponentsAPI as jest.Mocked<typeof serializedComponentsAPI>;
const mockAssets = assetsAPI as jest.Mocked<typeof assetsAPI>;

const ITEM_ID = 'item-1';

const buildUnit = (overrides: Partial<SerializedComponent> = {}): SerializedComponent => ({
  id: 'unit-1',
  item: ITEM_ID,
  item_name: 'Cutting blade',
  item_sku: 'BLD-1',
  serial_number: 'SN-0001',
  lot: '',
  status: 'received',
  status_display: 'Received',
  tracking_mode: 'consumable',
  available_actions: ['receive'],
  installed_in_asset: null,
  installed_in_asset_name: null,
  received_at: null,
  installed_at: null,
  disposed_at: null,
  provenance_delivery_item: null,
  provenance_purchase_order_item: null,
  disposal_reason: '',
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  ...overrides,
});

const listResponse = (units: SerializedComponent[]) =>
  ({ data: { count: units.length, next: null, previous: null, results: units } }) as never;

const renderPanel = () =>
  render(
    <MantineProvider>
      <SerializedComponentsPanel itemId={ITEM_ID} trackingMode="consumable" />
    </MantineProvider>,
  );

describe('SerializedComponentsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('lists units with their serial, status and available actions', async () => {
    mockAPI.list.mockResolvedValue(listResponse([buildUnit()]));
    renderPanel();

    expect(await screen.findByText('SN-0001')).toBeInTheDocument();
    expect(screen.getByText('Received')).toBeInTheDocument();
    expect(screen.getByTestId('serialized-action-receive-unit-1')).toBeInTheDocument();
    expect(mockAPI.list).toHaveBeenCalledWith({ item: ITEM_ID });
  });

  it('shows an empty state when there are no units', async () => {
    mockAPI.list.mockResolvedValue(listResponse([]));
    renderPanel();
    expect(
      await screen.findByText('No serialized units recorded for this item yet.'),
    ).toBeInTheDocument();
  });

  it('runs a simple lifecycle action and reflects the new status', async () => {
    mockAPI.list.mockResolvedValue(listResponse([buildUnit()]));
    mockAPI.receive.mockResolvedValue({
      data: {
        ...buildUnit({ status: 'in_stock', status_display: 'In Stock', available_actions: ['install'] }),
        event: { id: 'e1' },
      },
    } as never);

    renderPanel();
    fireEvent.click(await screen.findByTestId('serialized-action-receive-unit-1'));

    await waitFor(() => expect(mockAPI.receive).toHaveBeenCalledWith('unit-1'));
    expect(await screen.findByText('In Stock')).toBeInTheDocument();
    // The next legal action (install) is now offered.
    expect(screen.getByTestId('serialized-action-install-unit-1')).toBeInTheDocument();
  });

  it('adds a new unit by serial number', async () => {
    mockAPI.list.mockResolvedValue(listResponse([]));
    mockAPI.create.mockResolvedValue({
      data: buildUnit({ id: 'unit-new', serial_number: 'SN-9999' }),
    } as never);

    renderPanel();
    await screen.findByText('No serialized units recorded for this item yet.');

    fireEvent.click(screen.getByTestId('serialized-add-toggle'));
    fireEvent.change(screen.getByTestId('serialized-add-serial'), {
      target: { value: 'SN-9999' },
    });
    fireEvent.click(screen.getByTestId('serialized-add-submit'));

    await waitFor(() =>
      expect(mockAPI.create).toHaveBeenCalledWith({
        item: ITEM_ID,
        serial_number: 'SN-9999',
        lot: undefined,
      }),
    );
    expect(await screen.findByText('SN-9999')).toBeInTheDocument();
  });

  it('shows an enable-tracking CTA (not a dead panel) when the item is not serialized', async () => {
    const onEnableTracking = jest.fn();
    render(
      <MantineProvider>
        <SerializedComponentsPanel
          itemId={ITEM_ID}
          isSerialized={false}
          onEnableTracking={onEnableTracking}
        />
      </MantineProvider>,
    );

    // The state is explained rather than silently empty...
    expect(await screen.findByTestId('serialized-not-tracked')).toBeInTheDocument();
    // ...the add-unit form is not offered for a non-serialized item...
    expect(screen.queryByTestId('serialized-add-toggle')).not.toBeInTheDocument();
    expect(screen.queryByTestId('serialized-add-serial')).not.toBeInTheDocument();
    // ...no units are fetched (nothing to list)...
    expect(mockAPI.list).not.toHaveBeenCalled();

    // ...and the CTA hands off to enabling serial tracking.
    fireEvent.click(screen.getByTestId('serialized-enable-tracking'));
    expect(onEnableTracking).toHaveBeenCalledTimes(1);
  });

  it('loads and shows per-unit usage history on expand', async () => {
    mockAPI.list.mockResolvedValue(listResponse([buildUnit()]));
    mockAPI.listUsageEvents.mockResolvedValue({
      data: {
        count: 1,
        next: null,
        previous: null,
        results: [
          {
            id: 'ev-1',
            component: 'unit-1',
            component_serial: 'SN-0001',
            component_item_name: 'Cutting blade',
            asset: null,
            asset_name: null,
            action: 'receive',
            action_display: 'Receive',
            at: '2026-06-02T10:00:00Z',
            actor: 1,
            actor_username: 'alice',
            notes: 'first receipt',
            created_at: '2026-06-02T10:00:00Z',
          },
        ],
      },
    } as never);

    renderPanel();
    fireEvent.click(await screen.findByLabelText('Show history'));

    await waitFor(() =>
      expect(mockAPI.listUsageEvents).toHaveBeenCalledWith({ component: 'unit-1' }),
    );
    const panel = screen.getByTestId('serialized-components-panel');
    expect(await within(panel).findByText('first receipt')).toBeInTheDocument();
    expect(within(panel).getByText('alice')).toBeInTheDocument();
  });

  it('opens the dispose modal and disposes with a reason', async () => {
    mockAPI.list.mockResolvedValue(
      listResponse([
        buildUnit({
          status: 'consumed',
          status_display: 'Consumed',
          available_actions: ['dispose'],
        }),
      ]),
    );
    mockAPI.dispose.mockResolvedValue({
      data: {
        ...buildUnit({ status: 'disposed', status_display: 'Disposed', available_actions: [] }),
        event: { id: 'e2' },
      },
    } as never);

    renderPanel();
    fireEvent.click(await screen.findByTestId('serialized-action-dispose-unit-1'));

    // Reason is required — the reason field appears in the modal.
    fireEvent.change(await screen.findByTestId('serialized-dispose-reason'), {
      target: { value: 'worn out' },
    });
    fireEvent.click(screen.getByTestId('serialized-dispose-submit'));

    await waitFor(() =>
      expect(mockAPI.dispose).toHaveBeenCalledWith('unit-1', { disposal_reason: 'worn out' }),
    );
    expect(await screen.findByText('Disposed')).toBeInTheDocument();
  });
});
