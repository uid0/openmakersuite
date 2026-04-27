/**
 * Tests for AdminDashboard page — modal-based flows.
 *
 * These tests cover the happy path for each action that previously used
 * native prompt()/alert(): approve, mark ordered (multi-field modal),
 * mark received, cancel, and update tracking (multi-field modal).
 */
import { MantineProvider } from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import { Notifications, notifications } from '@mantine/notifications';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import React from 'react';

import AdminDashboard from '../../pages/AdminDashboard';
import { assetsAPI, inventoryAPI, reorderAPI } from '../../services/api';
import { ReorderRequest } from '../../types';

jest.mock('../../services/api', () => ({
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
  it('opens a multi-field modal and posts the consolidated tracking data', async () => {
    mockReorderAPI.getPendingRequests.mockResolvedValue({
      data: [buildRequest({ id: 7, status: 'approved' })],
    } as any);
    mockReorderAPI.markOrdered.mockResolvedValue({ data: {} } as any);

    renderDashboard();

    const markOrderedBtn = await screen.findByRole('button', { name: /mark ordered/i });
    fireEvent.click(markOrderedBtn);

    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/order number/i), {
      target: { value: 'PO-123' },
    });
    fireEvent.change(within(dialog).getByLabelText(/estimated delivery date/i), {
      target: { value: '2026-05-01' },
    });
    fireEvent.change(within(dialog).getByLabelText(/actual cost/i), {
      target: { value: '42.50' },
    });

    fireEvent.click(within(dialog).getByRole('button', { name: /mark ordered/i }));

    await waitFor(() => {
      expect(mockReorderAPI.markOrdered).toHaveBeenCalledWith(7, {
        order_number: 'PO-123',
        estimated_delivery: '2026-05-01',
        actual_cost: 42.5,
      });
    });

    expect(
      await screen.findByText('Marked as ordered with tracking information'),
    ).toBeInTheDocument();
  });

  it('normalizes a non-ISO date entry to YYYY-MM-DD before submit', async () => {
    mockReorderAPI.getPendingRequests.mockResolvedValue({
      data: [buildRequest({ id: 8, status: 'approved' })],
    } as any);
    mockReorderAPI.markOrdered.mockResolvedValue({ data: {} } as any);

    renderDashboard();

    const markOrderedBtn = await screen.findByRole('button', { name: /mark ordered/i });
    fireEvent.click(markOrderedBtn);

    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/estimated delivery date/i), {
      target: { value: '05/01/2026' },
    });

    fireEvent.click(within(dialog).getByRole('button', { name: /mark ordered/i }));

    await waitFor(() => {
      expect(mockReorderAPI.markOrdered).toHaveBeenCalledWith(8, {
        estimated_delivery: '2026-05-01',
      });
    });

    expect(
      await screen.findByText('Marked as ordered with tracking information'),
    ).toBeInTheDocument();
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
