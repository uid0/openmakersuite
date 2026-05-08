/**
 * Smoke tests for AnalyticsDashboardPage. Covers:
 * - happy path render with mocked pulse response
 * - error path
 * - shared mode forwards token from query param and hides Share button
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import AnalyticsDashboardPage from '../../pages/AnalyticsDashboardPage';
import * as api from '../../services/api';

jest.mock('../../services/api');

// Recharts is heavy — render placeholders so the smoke test stays focused.
jest.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: any) => <div data-testid="responsive-container">{children}</div>,
  LineChart: () => <div data-testid="line-chart" />,
  Line: () => null,
  BarChart: () => <div data-testid="bar-chart" />,
  Bar: () => null,
  RadialBarChart: () => <div data-testid="radial-bar-chart" />,
  RadialBar: () => null,
  PolarAngleAxis: () => null,
  CartesianGrid: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
  Legend: () => null,
}));

const renderAt = (path: string) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/analytics" element={<AnalyticsDashboardPage />} />
          <Route path="/analytics/shared" element={<AnalyticsDashboardPage shared />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>
  );

const mockPulse: api.AnalyticsPulseResponse = {
  summary: {
    period_start: '2026-04-01',
    period_end: '2026-05-01',
    internal_completed_count: 12,
    internal_estimated_external_cost: '3000.00',
    internal_estimated_internal_cost: '500.00',
    internal_net_value: '2500.00',
    external_closed_count: 2,
    external_actual_cost: '950.00',
    external_estimated_cost: '1200.00',
    total_value_to_makerspace: '1550.00',
  },
  wo_volume_trend: [
    { period: '2026-04-01', count: 12 },
  ],
  top_users: [
    { user_id: 1, username: 'alice', display_name: 'Alice Wonder', completed_wo_count: 5 },
  ],
  utilization: [
    { asset_id: 'a1', asset_name: 'CNC Mill', category: 'CNC', hours_used: 150, completed_wo_count: 3, status: 'active' },
  ],
  category_spend: [
    { category_id: 1, category_name: 'CNC', internal_estimated: '300.00', external_estimated: '600.00', external_actual: '950.00', internal_wo_count: 2, external_wo_count: 1 },
  ],
  maintenance_forecast: [
    { asset_id: 'a1', asset_name: 'CNC Mill', category: 'CNC', status: 'active', hours_used: 150, last_completed_wo_at: null, interval_hours: 100, interval_days: null, days_since_last_wo: null, due_reason: 'hours' },
  ],
  monthly_budget: '5000.00',
};

describe('AnalyticsDashboardPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders all panels with mocked pulse data', async () => {
    (api.pulseAPI.getPulse as jest.Mock).mockResolvedValue({ data: mockPulse });

    renderAt('/analytics');

    await waitFor(() => {
      expect(screen.getByText('Makerspace pulse')).toBeInTheDocument();
    });

    // Summary stats
    expect(screen.getByText('Net value created')).toBeInTheDocument();
    // Panels rendered
    expect(screen.getByTestId('budget-gauge')).toBeInTheDocument();
    expect(screen.getByTestId('volume-trend-chart')).toBeInTheDocument();
    expect(screen.getByTestId('category-spend-chart')).toBeInTheDocument();
    expect(screen.getByTestId('top-users-table')).toBeInTheDocument();
    expect(screen.getByTestId('maintenance-forecast-list')).toBeInTheDocument();
    expect(screen.getByTestId('equipment-status-grid')).toBeInTheDocument();
    // Top user surfaces
    expect(screen.getByText('Alice Wonder')).toBeInTheDocument();
    // Share button visible in default (non-shared) mode
    expect(screen.getByRole('button', { name: /share with board/i })).toBeInTheDocument();
  });

  it('renders error state on API failure', async () => {
    (api.pulseAPI.getPulse as jest.Mock).mockRejectedValue(new Error('boom'));

    renderAt('/analytics');

    await waitFor(() => {
      // Mantine Alert renders title + body. Pick whichever instance is present.
      const matches = screen.getAllByText(/Could not load analytics/);
      expect(matches.length).toBeGreaterThan(0);
    });
  });

  it('shared mode forwards token and hides share button', async () => {
    (api.pulseAPI.getPulse as jest.Mock).mockResolvedValue({ data: mockPulse });

    renderAt('/analytics/shared?token=signed.token.here');

    await waitFor(() => {
      expect(screen.getByText(/shared link/i)).toBeInTheDocument();
    });

    expect(api.pulseAPI.getPulse).toHaveBeenCalledWith({ token: 'signed.token.here' });
    expect(screen.queryByRole('button', { name: /share with board/i })).not.toBeInTheDocument();
  });

  it('hides budget gauge when monthly_budget is null', async () => {
    (api.pulseAPI.getPulse as jest.Mock).mockResolvedValue({
      data: { ...mockPulse, monthly_budget: null },
    });

    renderAt('/analytics');

    await waitFor(() => {
      expect(screen.getByTestId('budget-gauge-disabled')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('budget-gauge')).not.toBeInTheDocument();
  });
});
