/**
 * Tests for AdminDashboard page — modal-based flows.
 *
 * These tests cover the happy path for each action that previously used
 * native prompt()/alert(): approve, mark ordered (multi-field modal),
 * mark received, cancel, and update tracking (multi-field modal).
 *
 * The "reactive contract" describes below assert (gh-453):
 *   - mutation responses patch the row in place (no second GET, no full reload)
 *   - "Loading requests…" placeholder does NOT replace the table after submit
 *   - duplicate submit is prevented while a row mutation is in flight
 *   - mutation failures leave the row visible and the action re-enabled
 */
import { MantineProvider } from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import { Notifications, notifications } from '@mantine/notifications';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import React from 'react';

import AdminDashboard from '../../pages/AdminDashboard';
import { assetsAPI, inventoryAPI, reorderAPI } from '../../services/api';
import { ReorderRequest } from '../../types';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api', () => ({
  assetsAPI: {
    getNotCheckedIn: jest.fn(),
  },
  inventoryAPI: {
    listItems: jest.fn(),
  },
  reorderAPI: {
    getPendingRequests: jest.fn(),
    listRequests: jest.fn(),
    getBySupplier: jest.fn(),
    approveRequest: jest.fn(),
    markOrdered: jest.fn(),
    markReceived: jest.fn(),
    cancelRequest: jest.fn(),
    updateTracking: jest.fn(),
  },
}));

const mockAssetsAPI = assetsAPI as jest.Mocked<typeof assetsAPI>;
const mockInventoryAPI = inventoryAPI as jest.Mocked<typeof inventoryAPI>;
const mockReorderAPI = reorderAPI as jest.Mocked<typeof reorderAPI>;

const renderDashboard = () =>
  render(
    <MantineProvider>
      <ModalsProvider>
        <Notifications />
        <AdminDashboard />
      </ModalsProvider>
    </MantineProvider>,
  );

const buildRequest = (overrides: Partial<ReorderRequest>): ReorderRequest => ({
  id: 1,
  item: 'item-1',
  item_details: {
    id: 'item-1',
    name: 'Widget',
    description: '',
    sku: 'W-1',
    current_stock: 0,
    minimum_stock: 0,
    reorder_quantity: 1,
    use_case_based_reorder: false,
    minimum_cases: null,
    reorder_cases: null,
    category: 1,
    category_name: '',
    location: 1,
    location_name: '',
    shelf_position: '',
    is_hazardous: false,
    msds_url: '',
    nfpa_health_hazard: null,
    nfpa_fire_hazard: null,
    nfpa_instability_hazard: null,
    nfpa_special_hazards: '',
    ownership_type: 'space',
    is_active: true,
    notes: '',
    created_at: '',
    updated_at: '',
  } as any,
  quantity: 1,
  status: 'pending',
  priority: 'normal',
  requested_by: 'alice',
  request_notes: '',
  requested_at: '2026-04-01T00:00:00Z',
  reviewed_by: null,
  reviewed_by_username: null,
  reviewed_at: null,
  admin_notes: '',
  ordered_at: null,
  estimated_delivery: null,
  actual_delivery: null,
  order_number: '',
  actual_cost: null,
  estimated_cost: null,
  days_pending: 0,
  updated_at: '2026-04-01T00:00:00Z',
  ...overrides,
});

beforeEach(() => {
  jest.clearAllMocks();
  notifications.clean();
  mockAssetsAPI.getNotCheckedIn.mockResolvedValue({ data: [] } as any);
  mockInventoryAPI.listItems.mockResolvedValue({ data: { results: [] } } as any);
  mockReorderAPI.listRequests.mockResolvedValue({ data: { results: [] } } as any);
});

describe('AdminDashboard — approve flow', () => {
  it('approves a pending request and shows a success notification', async () => {
    mockReorderAPI.getPendingRequests.mockResolvedValue({
      data: [buildRequest({ id: 42, status: 'pending' })],
    } as any);
    mockReorderAPI.approveRequest.mockResolvedValue({ data: {} } as any);

    renderDashboard();

    const approveBtn = await screen.findByTitle('Approve');
    fireEvent.click(approveBtn);

    await waitFor(() => {
      expect(mockReorderAPI.approveRequest).toHaveBeenCalledWith(42);
    });

    expect(await screen.findByText('Request approved')).toBeInTheDocument();
  });
});

describe('AdminDashboard — mark ordered flow', () => {
  it('marks a request ordered in one click, without prompting for an order number', async () => {
    mockReorderAPI.getPendingRequests.mockResolvedValue({
      data: [buildRequest({ id: 7, status: 'approved' })],
    } as any);
    mockReorderAPI.markOrdered.mockResolvedValue({ data: {} } as any);

    renderDashboard();

    const markOrderedBtn = await screen.findByRole('button', { name: /mark ordered/i });
    fireEvent.click(markOrderedBtn);

    // No modal is opened — the order/PO number lives in the Purchase Order
    // domain and is not re-typed here, so the operator is not interrupted.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    await waitFor(() => {
      expect(mockReorderAPI.markOrdered).toHaveBeenCalledWith(7);
    });

    expect(await screen.findByText('Marked as ordered')).toBeInTheDocument();
  });

  it('surfaces an error when marking ordered fails', async () => {
    mockReorderAPI.getPendingRequests.mockResolvedValue({
      data: [buildRequest({ id: 8, status: 'approved' })],
    } as any);
    mockReorderAPI.markOrdered.mockRejectedValue(new Error('boom'));

    renderDashboard();

    const markOrderedBtn = await screen.findByRole('button', { name: /mark ordered/i });
    fireEvent.click(markOrderedBtn);

    expect(await screen.findByText('Failed to mark as ordered')).toBeInTheDocument();
  });
});

describe('AdminDashboard — mark received flow', () => {
  it('prompts for actual delivery date and posts it to the API', async () => {
    mockReorderAPI.getPendingRequests.mockResolvedValue({
      data: [buildRequest({ id: 9, status: 'ordered' })],
    } as any);
    mockReorderAPI.markReceived.mockResolvedValue({ data: {} } as any);

    renderDashboard();

    const markReceivedBtn = await screen.findByRole('button', { name: /mark received/i });
    fireEvent.click(markReceivedBtn);

    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/actual delivery date/i), {
      target: { value: '2026-05-02' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: /submit/i }));

    await waitFor(() => {
      expect(mockReorderAPI.markReceived).toHaveBeenCalledWith(9, '2026-05-02');
    });

    expect(
      await screen.findByText('Marked as received and inventory updated'),
    ).toBeInTheDocument();
  });
});

describe('AdminDashboard — cancel flow', () => {
  it('prompts for cancellation reason and posts it to the API', async () => {
    mockReorderAPI.getPendingRequests.mockResolvedValue({
      data: [buildRequest({ id: 11, status: 'pending' })],
    } as any);
    mockReorderAPI.cancelRequest.mockResolvedValue({ data: {} } as any);

    renderDashboard();

    const cancelBtn = await screen.findByTitle('Cancel');
    fireEvent.click(cancelBtn);

    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/reason for cancellation/i), {
      target: { value: 'Duplicate order' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: /submit/i }));

    await waitFor(() => {
      expect(mockReorderAPI.cancelRequest).toHaveBeenCalledWith(11, 'Duplicate order');
    });

    expect(await screen.findByText('Request cancelled')).toBeInTheDocument();
  });
});

describe('AdminDashboard — reactive contract (gh-453)', () => {
  it('patches the row in place from the API response — no full reload', async () => {
    const pending = buildRequest({ id: 100, status: 'pending' });
    mockReorderAPI.getPendingRequests.mockResolvedValue({ data: [pending] } as any);
    // Backend returns the full updated representation; the table should
    // reflect that without a second GET.
    mockReorderAPI.approveRequest.mockResolvedValue({
      data: { ...pending, status: 'approved' },
    } as any);

    renderDashboard();

    const approveBtn = await screen.findByTitle('Approve');
    fireEvent.click(approveBtn);

    // Status badge flips from 'pending' to 'approved' without the table
    // ever flashing back to "Loading requests…".
    await waitFor(() => {
      expect(screen.getByText('approved')).toBeInTheDocument();
    });
    expect(screen.queryByText(/loading requests/i)).not.toBeInTheDocument();
    // Crucially: no follow-up reload of the requests list.
    expect(mockReorderAPI.getPendingRequests).toHaveBeenCalledTimes(1);
    expect(mockReorderAPI.listRequests).not.toHaveBeenCalled();
  });

  it('disables the row action while pending and prevents duplicate submit', async () => {
    const pending = buildRequest({ id: 200, status: 'pending' });
    mockReorderAPI.getPendingRequests.mockResolvedValue({ data: [pending] } as any);

    // Hold the mutation in flight so we can observe the pending UI.
    let resolveApprove: (value: { data: any }) => void = () => undefined;
    mockReorderAPI.approveRequest.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveApprove = resolve;
        }) as any,
    );

    renderDashboard();

    const approveBtn = await screen.findByTitle('Approve');
    fireEvent.click(approveBtn);

    await waitFor(() => {
      expect(approveBtn).toBeDisabled();
    });
    expect(screen.queryByText(/loading requests/i)).not.toBeInTheDocument();

    // A second click while the row is busy must not fire the API again.
    fireEvent.click(approveBtn);
    expect(mockReorderAPI.approveRequest).toHaveBeenCalledTimes(1);

    // Resolve so the test cleanly drains.
    resolveApprove({ data: { ...pending, status: 'approved' } });
    await waitFor(() => {
      expect(screen.getByText('approved')).toBeInTheDocument();
    });
  });

  it('preserves row state and re-enables actions when the mutation fails', async () => {
    const pending = buildRequest({ id: 300, status: 'pending' });
    mockReorderAPI.getPendingRequests.mockResolvedValue({ data: [pending] } as any);
    mockReorderAPI.approveRequest.mockRejectedValue(new Error('network down'));

    renderDashboard();

    const approveBtn = await screen.findByTitle('Approve');
    fireEvent.click(approveBtn);

    await waitFor(() => {
      expect(mockReorderAPI.approveRequest).toHaveBeenCalledWith(300);
    });

    // Table content and the row stay visible; the page never flips to the
    // initial loading placeholder.
    expect(screen.queryByText(/loading requests/i)).not.toBeInTheDocument();
    expect(screen.getByText(pending.item_details.name)).toBeInTheDocument();
    // The row's status badge still says 'pending' (the optimistic patch
    // that would have changed it never landed).
    expect(screen.getByText('pending')).toBeInTheDocument();

    // The action is re-enabled so the operator can retry.
    await waitFor(() => {
      expect(approveBtn).not.toBeDisabled();
    });
  });

  it('patches the row from a mark-received response without reloading the table', async () => {
    const ordered = buildRequest({ id: 400, status: 'ordered' });
    mockReorderAPI.getPendingRequests.mockResolvedValue({ data: [ordered] } as any);
    mockReorderAPI.markReceived.mockResolvedValue({
      data: { ...ordered, status: 'received', actual_delivery: '2026-05-02' },
    } as any);

    renderDashboard();

    const markReceivedBtn = await screen.findByRole('button', { name: /mark received/i });
    fireEvent.click(markReceivedBtn);

    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/actual delivery date/i), {
      target: { value: '2026-05-02' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: /submit/i }));

    await waitFor(() => {
      expect(screen.getByText('received')).toBeInTheDocument();
    });
    expect(screen.queryByText(/loading requests/i)).not.toBeInTheDocument();
    expect(mockReorderAPI.getPendingRequests).toHaveBeenCalledTimes(1);
    expect(mockReorderAPI.listRequests).not.toHaveBeenCalled();
  });
});

describe('AdminDashboard — update tracking flow', () => {
  it('opens a multi-field modal and posts the consolidated tracking fields', async () => {
    mockReorderAPI.getPendingRequests.mockResolvedValue({
      data: [buildRequest({ id: 5, status: 'ordered' })],
    } as any);
    mockReorderAPI.updateTracking.mockResolvedValue({ data: {} } as any);

    renderDashboard();

    const updateTrackingBtn = await screen.findByTitle('Update Tracking');
    fireEvent.click(updateTrackingBtn);

    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/tracking number/i), {
      target: { value: 'TRK-1' },
    });
    fireEvent.change(within(dialog).getByLabelText(/carrier \/ shipper/i), {
      target: { value: 'UPS' },
    });
    fireEvent.change(within(dialog).getByLabelText(/expected delivery date/i), {
      target: { value: '2026-05-10' },
    });
    fireEvent.change(within(dialog).getByLabelText(/tracking url/i), {
      target: { value: 'https://ups.example/TRK-1' },
    });

    fireEvent.click(within(dialog).getByRole('button', { name: /update tracking/i }));

    await waitFor(() => {
      expect(mockReorderAPI.updateTracking).toHaveBeenCalledWith(5, {
        tracking_number: 'TRK-1',
        carrier: 'UPS',
        expected_delivery_date: '2026-05-10',
        delivery_tracking_url: 'https://ups.example/TRK-1',
      });
    });

    expect(await screen.findByText('Tracking information updated')).toBeInTheDocument();
  });
});

describe('AdminDashboard — reorder-triage resilience (#457 R3)', () => {
  it('shows the empty state when there are no pending requests', async () => {
    mockReorderAPI.getPendingRequests.mockResolvedValue({ data: [] } as any);

    renderDashboard();

    // The triage table renders its empty-state row once the load settles.
    expect(await screen.findByText('No requests found')).toBeInTheDocument();
    expect(screen.queryByText(/loading requests/i)).not.toBeInTheDocument();
  });

  it('surfaces an error notification and does not hang when the load is forbidden (403)', async () => {
    mockReorderAPI.getPendingRequests.mockRejectedValue({
      response: { status: 403, data: { detail: 'You do not have permission.' } },
    } as any);

    renderDashboard();

    // No per-403 messaging exists; the page degrades to its generic load error
    // toast and never gets stuck on the loading placeholder.
    expect(
      await screen.findByText('Failed to load requests. Please log in.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/loading requests/i)).not.toBeInTheDocument();
  });

  it('keeps the row and re-enables the action when a mutation hits a network error (offline)', async () => {
    const pending = buildRequest({ id: 77, status: 'pending' });
    mockReorderAPI.getPendingRequests.mockResolvedValue({ data: [pending] } as any);
    // Offline signature: code ERR_NETWORK, request set, no response.
    mockReorderAPI.approveRequest.mockRejectedValue(networkError('Network Error'));

    renderDashboard();

    const approveBtn = await screen.findByTitle('Approve');
    fireEvent.click(approveBtn);

    expect(await screen.findByText('Failed to approve request')).toBeInTheDocument();
    // The row survives the failed mutation and the action re-enables for retry.
    expect(screen.getByText(pending.item_details.name)).toBeInTheDocument();
    expect(screen.getByText('pending')).toBeInTheDocument();
    await waitFor(() => {
      expect(approveBtn).not.toBeDisabled();
    });
  });
});


/**
 * The "Requests by Supplier" modal renders `total_estimated_cost` as a
 * bulk-ordering total. It is the sum of the requests the server COULD price
 * (op-9m2v), so where it could not price them all the screen has to say so —
 * otherwise a purchaser reads a confident figure that silently omits a line.
 */
describe('AdminDashboard — requests by supplier', () => {
  const group = (overrides: Record<string, unknown> = {}) => ({
    supplier: 'Acme',
    supplier_type: 'local',
    requests: [],
    item_count: 2,
    total_estimated_cost: 10,
    unpriced_item_count: 0,
    estimated_total_is_partial: false,
    ...overrides,
  });

  const openModal = async (groups: Record<string, unknown>[]) => {
    mockReorderAPI.getPendingRequests.mockResolvedValue({ data: [] } as any);
    mockReorderAPI.getBySupplier.mockResolvedValue({ data: groups } as any);

    renderDashboard();
    fireEvent.click(await screen.findByRole('button', { name: /view by supplier/i }));
    return (await screen.findByText('Acme')).closest('.supplier-group')!;
  };

  it('says the total is partial when a request could not be priced', async () => {
    const card = await openModal([
      group({ unpriced_item_count: 1, estimated_total_is_partial: true }),
    ]);

    // The number itself is unchanged; the count beside it is what is new.
    expect(card).toHaveTextContent('$10.00');
    expect(card).toHaveTextContent('1 unpriced');
  });

  it('claims nothing extra when every request was priced — the invariant', async () => {
    const card = await openModal([group()]);

    expect(card).toHaveTextContent('$10.00');
    expect(card).not.toHaveTextContent('unpriced');
  });

  it('treats a group of free requests as fully priced, not as unknown', async () => {
    const card = await openModal([group({ total_estimated_cost: 0 })]);

    expect(card).toHaveTextContent('$0.00');
    expect(card).not.toHaveTextContent('unpriced');
  });
});

// --- One supplier is not THE supplier (op-3xsp) ---------------------------
// The reorder queue is the screen where the ordering decision is taken. Its
// item cell rendered `item_details.supplier_name` — the read-only legacy
// accessor — so an item stocked by three vendors showed one name with nothing
// saying there were others, and the Lead Time column rendered that same
// supplier's quoted wait as though it were the item's own. Neither said when
// the choice had been made without a price, or when the operator's own flagged
// primary had been skipped as unbuyable.

describe('AdminDashboard — which supplier a queued request would go to', () => {
  const choice = (overrides: Record<string, unknown> = {}) => ({
    item_supplier_id: 1,
    supplier_name: 'Acme Supplies',
    basis: 'best_scored',
    reason: null,
    flagged_primary_unorderable: false,
    scored_without_price: false,
    scored_without_history: false,
    alternatives: [],
    ...overrides,
  });

  const withChoice = (id: number, supplierChoice: unknown, extra: Record<string, unknown> = {}) => {
    const request = buildRequest({ id, status: 'pending' });
    return {
      ...request,
      item_details: {
        ...(request.item_details as any),
        average_lead_time: 7,
        supplier_choice: supplierChoice,
        ...extra,
      },
    } as any;
  };

  const showRow = async (request: unknown) => {
    mockReorderAPI.getPendingRequests.mockResolvedValue({ data: [request] } as any);
    renderDashboard();
    return screen.findByTestId('reorder-row-1');
  };

  it('BEFORE/AFTER: names the derived supplier, not the legacy accessor', async () => {
    await showRow(
      withChoice(1, choice({ supplier_name: 'Derived Supply Co.' }), {
        // Set apart so the assertion can only pass by reading the right key.
        supplier_name: 'Legacy Accessor Co.',
      }),
    );

    expect(await screen.findByTestId('reorder-supplier-1')).toHaveTextContent(
      'Derived Supply Co.',
    );
    expect(screen.queryByText('Legacy Accessor Co.')).not.toBeInTheDocument();
  });

  it('BEFORE/AFTER: an item with three suppliers does not read as having one', async () => {
    await showRow(
      withChoice(
        1,
        choice({
          alternatives: [
            { id: 2, supplier_name: 'Beta Parts' },
            { id: 3, supplier_name: 'Gamma Wholesale' },
          ],
        }),
      ),
    );

    expect(await screen.findByTestId('reorder-supplier-1')).toHaveTextContent(
      'Acme Supplies, or 2 others',
    );
  });

  it('warns when the choice was made without a price on file', async () => {
    await showRow(withChoice(1, choice({ scored_without_price: true })));

    expect(await screen.findByTestId('reorder-supplier-note-1')).toHaveTextContent(
      'chosen without a price on file',
    );
  });

  it('warns when the operator’s own flagged primary was skipped as unbuyable', async () => {
    await showRow(withChoice(1, choice({ flagged_primary_unorderable: true })));

    expect(await screen.findByTestId('reorder-supplier-note-1')).toHaveTextContent(
      /flagged primary supplier cannot be ordered from/i,
    );
  });

  it('CONTROL: a clean single-supplier request carries no warning', async () => {
    await showRow(withChoice(1, choice()));

    expect(await screen.findByTestId('reorder-supplier-1')).toHaveTextContent('Acme Supplies');
    expect(screen.queryByTestId('reorder-supplier-note-1')).not.toBeInTheDocument();
  });

  it('BEFORE/AFTER: quotes no lead time where there is no supplier to quote it from', async () => {
    await showRow(
      withChoice(1, choice({ supplier_name: null, item_supplier_id: null, basis: null, reason: 'none_orderable' })),
    );

    // The cell used to render a bare " days" for an item nothing can be bought
    // from — a wait attributed to a vendor that does not exist.
    expect(await screen.findByTestId('reorder-lead-time-1')).toHaveTextContent('—');
    expect(screen.getByTestId('reorder-lead-time-1')).not.toHaveTextContent('days');
    expect(screen.getByTestId('reorder-supplier-note-1')).toHaveTextContent(
      /inactive or discontinued/i,
    );
  });

  it('CONTROL: a real supplier still shows its quoted lead time', async () => {
    await showRow(withChoice(1, choice()));

    expect(await screen.findByTestId('reorder-lead-time-1')).toHaveTextContent('7 days');
  });
});
