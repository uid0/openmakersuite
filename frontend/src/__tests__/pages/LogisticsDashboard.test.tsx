import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import LogisticsDashboard from '../../pages/LogisticsDashboard';
import { analyticsAPI } from '../../services/api';

jest.mock('../../services/api', () => ({
  analyticsAPI: {
    getLogisticsDashboard: jest.fn(),
  },
}));

const mockDashboardData = {
  summary: {
    pending_requests: 2,
    urgent_requests: 1,
    awaiting_approval: 1,
    pending_orders: 1,
    open_order_lines: 4,
  },
  refill_requests: [
    {
      id: 1,
      item_name: 'Nitrile Gloves',
      location: 'Logistics Closet',
      category: 'PPE',
      quantity_requested: 120,
      priority: 'urgent',
      priority_label: 'Urgent',
      status: 'pending',
      status_label: 'Pending',
      requested_at: '2024-02-01T12:00:00Z',
      days_open: 2,
      requested_by: 'Safety Committee',
      public_notes: '',
      request_notes: 'Running low on size L.',
    },
  ],
  pending_orders: [
    {
      id: 10,
      po_number: 'PO-2024-02',
      supplier_name: 'Maker Supply Company',
      status: 'sent',
      status_label: 'Sent to Supplier',
      sent_at: '2024-01-31T18:30:00Z',
      expected_delivery_date: '2024-02-05',
      days_since_ordered: 2,
      total_items: 3,
      total_quantity: 75,
      received_quantity: 15,
      progress_percent: 20.0,
      estimated_total: 450.0,
      updated_at: '2024-02-01T19:30:00Z',
    },
  ],
  last_updated: '2024-02-01T19:45:00Z',
};

describe('LogisticsDashboard', () => {
  afterEach(() => {
    jest.clearAllMocks();
  });

  it('renders headline metrics and lists requests and orders', async () => {
    const getLogisticsDashboardMock = analyticsAPI.getLogisticsDashboard as jest.Mock;
    getLogisticsDashboardMock.mockResolvedValue({ data: mockDashboardData });

    render(<LogisticsDashboard />);

    await waitFor(() => {
      expect(getLogisticsDashboardMock).toHaveBeenCalledTimes(1);
    });

    // Wait for loading to complete and content to render
    expect(await screen.findByText('Logistics Command Center')).toBeInTheDocument();
    expect(screen.getByText('Open Requests')).toBeInTheDocument();
    // "2" appears multiple times, so check for it in the context of "Open Requests"
    const openRequestsSection = screen.getByText('Open Requests').closest('.summary-card');
    expect(openRequestsSection).toHaveTextContent('2');
    expect(screen.getByText('Nitrile Gloves')).toBeInTheDocument();
    expect(screen.getByText('Logistics Closet')).toBeInTheDocument();
    expect(screen.getByText('Running low on size L.')).toBeInTheDocument();
    expect(screen.getByText('PO-2024-02')).toBeInTheDocument();
    expect(screen.getByText('Maker Supply Company')).toBeInTheDocument();
  });
});
