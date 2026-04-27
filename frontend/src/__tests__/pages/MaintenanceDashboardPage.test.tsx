/**
 * Tests for the new /maintenance overview page (oms-95o).
 *
 * Covers AC #1, #4, #5: page renders header even with no data, renders rows
 * when data is present, and shows graceful empty states.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import MaintenanceDashboardPage from '../../pages/MaintenanceDashboardPage';
import { maintenanceAPI, MaintenanceDashboardData } from '../../services/api';

jest.mock('../../services/api', () => {
  const actual = jest.requireActual('../../services/api');
  return {
    ...actual,
    maintenanceAPI: {
      ...actual.maintenanceAPI,
      getDashboard: jest.fn(),
    },
  };
});

const mockMaintenanceAPI = maintenanceAPI as jest.Mocked<typeof maintenanceAPI>;

const emptyDashboard = (): MaintenanceDashboardData => ({
  scheduled_pm: [],
  unscheduled: [],
  costs: {
    per_period: {
      today: '0.00',
      this_week: '0.00',
      this_month: '0.00',
      this_year: '0.00',
      all_time: '0.00',
    },
    by_asset: [],
  },
});

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <MaintenanceDashboardPage />
      </MemoryRouter>
    </MantineProvider>
  );

describe('MaintenanceDashboardPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('renders title and graceful empty states when no data exists', async () => {
    mockMaintenanceAPI.getDashboard.mockResolvedValue({ data: emptyDashboard() } as any);

    renderPage();

    expect(await screen.findByRole('heading', { name: /maintenance/i })).toBeInTheDocument();
    expect(await screen.findByTestId('empty-scheduled')).toBeInTheDocument();
    expect(screen.getByTestId('empty-unscheduled')).toBeInTheDocument();
    expect(screen.getByTestId('empty-by-asset')).toBeInTheDocument();
    // All cost cards render even with zero data.
    for (const key of ['today', 'this_week', 'this_month', 'this_year', 'all_time']) {
      expect(screen.getByTestId(`cost-card-${key}`)).toBeInTheDocument();
    }
  });

  test('renders rows when scheduled_pm, unscheduled, and by_asset are populated', async () => {
    const data: MaintenanceDashboardData = {
      scheduled_pm: [
        {
          asset_id: 'asset-1',
          asset_name: 'Lathe-A',
          maintenance_item_id: 'mi-1',
          title: 'Quarterly oil change',
          interval_days: 90,
          next_due: new Date(Date.now() + 5 * 86_400_000).toISOString(),
          days_until: 5,
          last_completed_at: null,
          is_overdue: false,
        },
      ],
      unscheduled: [
        {
          workorder_id: 'wo-1',
          short_id: 'WO-AAA',
          asset_id: 'asset-2',
          asset_name: 'Mill-B',
          problem: 'Belt replacement',
          opened_at: new Date().toISOString(),
          status: 'open',
        },
      ],
      costs: {
        per_period: {
          today: '12.50',
          this_week: '50.00',
          this_month: '200.00',
          this_year: '1000.00',
          all_time: '5000.00',
        },
        by_asset: [
          {
            asset_id: 'asset-1',
            asset_name: 'Lathe-A',
            total_cost: '160.00',
            days_in_maintenance_90d: 4,
          },
        ],
      },
    };
    mockMaintenanceAPI.getDashboard.mockResolvedValue({ data } as any);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('scheduled-row')).toBeInTheDocument();
    });
    expect(screen.getByText('Quarterly oil change')).toBeInTheDocument();
    expect(screen.getByTestId('unscheduled-row')).toBeInTheDocument();
    expect(screen.getByText('Belt replacement')).toBeInTheDocument();
    expect(screen.getByTestId('by-asset-row')).toBeInTheDocument();
    expect(screen.getByText('$5,000.00')).toBeInTheDocument();
  });

  test('shows error alert when the dashboard API fails', async () => {
    mockMaintenanceAPI.getDashboard.mockRejectedValue({
      response: { data: { detail: 'Server exploded' } },
    });

    renderPage();

    expect(await screen.findByText(/server exploded/i)).toBeInTheDocument();
  });
});
