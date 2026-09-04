/**
 * Tests for LeadTimeChart component
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import LeadTimeChart from '../../components/LeadTimeChart';

describe('LeadTimeChart', () => {
  const mockAnalytics = {
    average_lead_time: 7.5,
    min_lead_time: 5,
    max_lead_time: 10,
    average_variance: 0.5,
    total_orders: 10,
    on_time_percentage: 80.0,
    recent_logs: [
      {
        item_name: 'Test Item 1',
        order_date: '2024-01-01T00:00:00Z',
        expected_delivery_date: '2024-01-15T00:00:00Z',
        actual_delivery_date: '2024-01-16T00:00:00Z',
        estimated_lead_time_days: 14,
        actual_lead_time_days: 15,
        variance_days: 1,
        was_late: true,
      },
      {
        item_name: 'Test Item 2',
        order_date: '2024-01-02T00:00:00Z',
        expected_delivery_date: '2024-01-12T00:00:00Z',
        actual_delivery_date: '2024-01-10T00:00:00Z',
        estimated_lead_time_days: 10,
        actual_lead_time_days: 8,
        variance_days: -2,
        was_late: false,
      },
    ],
  };

  it('renders statistics cards', () => {
    render(
      <MantineProvider>
        <LeadTimeChart analytics={mockAnalytics} />
      </MantineProvider>
    );

    expect(screen.getByText('Average Lead Time')).toBeInTheDocument();
    expect(screen.getByText('Within Quoted Lead Time')).toBeInTheDocument();
    expect(screen.getByText('Min / Max Lead Time')).toBeInTheDocument();
    expect(screen.getByText('Avg Variance vs. Quoted Lead Time')).toBeInTheDocument();
  });

  it('displays correct statistics values', () => {
    render(
      <MantineProvider>
        <LeadTimeChart analytics={mockAnalytics} />
      </MantineProvider>
    );

    expect(screen.getByText('7.5 days')).toBeInTheDocument();
    expect(screen.getByText('80.0%')).toBeInTheDocument();
    expect(screen.getByText('5 / 10 days')).toBeInTheDocument();
  });

  it('renders chart with data', () => {
    render(
      <MantineProvider>
        <LeadTimeChart analytics={mockAnalytics} />
      </MantineProvider>
    );

    expect(screen.getByText('Quoted vs. Actual Lead Time (Recent Orders)')).toBeInTheDocument();
  });

  it('displays empty state when no data', () => {
    const emptyAnalytics = {
      average_lead_time: null,
      min_lead_time: null,
      max_lead_time: null,
      average_variance: null,
      total_orders: 0,
      on_time_percentage: null,
      recent_logs: [],
    };

    render(
      <MantineProvider>
        <LeadTimeChart analytics={emptyAnalytics} />
      </MantineProvider>
    );

    expect(screen.getByText('No lead time data available')).toBeInTheDocument();
  });
});
