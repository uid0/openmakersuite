import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import LogisticsDashboard from '../../pages/LogisticsDashboard';
import { analyticsAPI, locationCheckinAPI } from '../../services/api';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');

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
    const openItemRequestsLabel = await screen.findByText('Open Item Requests');
    expect(openItemRequestsLabel).toBeInTheDocument();
    expect(screen.getByText('Locations with Problems')).toBeInTheDocument();
    expect(screen.getByText('Assets Overdue Maintenance')).toBeInTheDocument();
    expect(screen.getByText('QR Scans (Last 7 Days)')).toBeInTheDocument();
    
    // Check metric values - find them in the document using getAllByText and verify they exist
    // The values are displayed in separate divs with class metric-value
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('45')).toBeInTheDocument();
    
    // Check that time is displayed in footer
    expect(screen.getByText(/\d{1,2}:\d{2}:\d{2}/)).toBeInTheDocument();
  });

  it('shows the urgent alert banner when alert_active is true', async () => {
    const getLogisticsDashboardMock = analyticsAPI.getLogisticsDashboard as jest.Mock;
    getLogisticsDashboardMock.mockResolvedValue({
      data: {
        ...mockDashboardData,
        urgent_location_problems: 2,
        alert_active: true,
      },
    });

    const { container } = render(<LogisticsDashboard />);

    await waitFor(() => {
      expect(getLogisticsDashboardMock).toHaveBeenCalledTimes(1);
    });

    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/URGENT LOCATION PROBLEMS \(2\) OPEN/)).toBeInTheDocument();
    expect(container.querySelector('.logistics-dashboard--alert')).not.toBeNull();
  });

  it('omits the urgent alert banner when no urgent problems are open', async () => {
    const getLogisticsDashboardMock = analyticsAPI.getLogisticsDashboard as jest.Mock;
    getLogisticsDashboardMock.mockResolvedValue({
      data: {
        ...mockDashboardData,
        urgent_location_problems: 0,
        alert_active: false,
      },
    });

    const { container } = render(<LogisticsDashboard />);

    await waitFor(() => {
      expect(getLogisticsDashboardMock).toHaveBeenCalledTimes(1);
    });

    await screen.findByText('Open Item Requests');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(container.querySelector('.logistics-dashboard--alert')).toBeNull();
  });

  it('renders a readable offline state when the dashboard API is unreachable', async () => {
    // AC-16: a network failure surfaces an actionable card with retry guidance,
    // not a blank screen or a raw exception.
    (analyticsAPI.getLogisticsDashboard as jest.Mock).mockRejectedValue(networkError());

    render(<LogisticsDashboard />);

    expect(await screen.findByText(/Unable to load logistics data/i)).toBeInTheDocument();
    expect(screen.getByText(/Ensure the backend is reachable/i)).toBeInTheDocument();
  });

  it('shows per-panel empty states without crashing when there is no data', async () => {
    // AC-19: each panel renders a readable empty state rather than a blank.
    (analyticsAPI.getLogisticsDashboard as jest.Mock).mockResolvedValue({
      data: {
        open_item_requests: 0,
        open_locations_with_problems: 0,
        urgent_location_problems: 0,
        alert_active: false,
        assets_overdue_maintenance: 0,
        pm_overdue: 0,
        pm_due_this_week: 0,
        qr_scans_total: 0,
        qr_scans_by_day: [],
        last_updated: '2024-02-01T19:45:00Z',
      },
    });
    (locationCheckinAPI.getTopLocations as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    const { container } = render(<LogisticsDashboard />);

    // Top-locations panel: readable empty message.
    expect(await screen.findByText('No traffic recorded')).toBeInTheDocument();
    // QR panel: an empty 7-day series renders no sparkline rather than crashing.
    expect(container.querySelector('.sparkline')).toBeNull();
    // Headline panels still render their labels.
    expect(screen.getByText('QR Scans (Last 7 Days)')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
