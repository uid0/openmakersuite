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
  open_item_requests: 2,
  open_locations_with_problems: 1,
  assets_overdue_maintenance: 3,
  qr_scans_total: 45,
  qr_scans_by_day: [
    { date: '2024-02-01', count: 8 },
    { date: '2024-02-02', count: 12 },
    { date: '2024-02-03', count: 6 },
    { date: '2024-02-04', count: 10 },
    { date: '2024-02-05', count: 9 },
  ],
  last_updated: '2024-02-01T19:45:00Z',
};

describe('LogisticsDashboard', () => {
  afterEach(() => {
    jest.clearAllMocks();
  });

  it('renders headline metrics and shows summary cards', async () => {
    const getLogisticsDashboardMock = analyticsAPI.getLogisticsDashboard as jest.Mock;
    getLogisticsDashboardMock.mockResolvedValue({ data: mockDashboardData });

    render(<LogisticsDashboard />);

    await waitFor(() => {
      expect(getLogisticsDashboardMock).toHaveBeenCalledTimes(1);
    });

    // Wait for loading to complete and content to render
    expect(await screen.findByText('Open Item Requests')).toBeInTheDocument();
    expect(screen.getByText('Locations with Problems')).toBeInTheDocument();
    expect(screen.getByText('Assets Overdue Maintenance')).toBeInTheDocument();
    expect(screen.getByText('QR Scans (Last 7 Days)')).toBeInTheDocument();
    
    // Check metric values
    expect(screen.getByText('Open Item Requests').parentElement).toHaveTextContent('2');
    expect(screen.getByText('Locations with Problems').parentElement).toHaveTextContent('1');
    expect(screen.getByText('Assets Overdue Maintenance').parentElement).toHaveTextContent('3');
    expect(screen.getByText('QR Scans (Last 7 Days)').parentElement).toHaveTextContent('45');
    
    // Check that time is displayed in footer
    expect(screen.getByText(/\d{1,2}:\d{2}:\d{2}/)).toBeInTheDocument();
  });
});
