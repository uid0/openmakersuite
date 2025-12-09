/**
 * Tests for PriceTrendChart component
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import PriceTrendChart from '../../components/PriceTrendChart';

describe('PriceTrendChart', () => {
  const mockPriceTrends = {
    trends: [
      {
        item_id: '1',
        item_name: 'Test Item 1',
        price_history: [
          {
            recorded_at: '2024-01-01T00:00:00Z',
            unit_cost: 10.0,
            package_cost: 100.0,
            change_type: 'initial',
            price_change_percentage: null,
          },
          {
            recorded_at: '2024-02-01T00:00:00Z',
            unit_cost: 11.0,
            package_cost: 110.0,
            change_type: 'updated',
            price_change_percentage: 10.0,
          },
        ],
      },
    ],
    summary: {
      average_unit_cost: 10.5,
      min_unit_cost: 10.0,
      max_unit_cost: 11.0,
      price_changes_count: 2,
    },
  };

  it('renders summary statistics', () => {
    render(
      <MantineProvider>
        <PriceTrendChart priceTrends={mockPriceTrends} />
      </MantineProvider>
    );

    expect(screen.getByText('Price Summary')).toBeInTheDocument();
    expect(screen.getByText(/Average Unit Cost/i)).toBeInTheDocument();
    expect(screen.getByText(/Price Range/i)).toBeInTheDocument();
    expect(screen.getByText(/Total Price Changes/i)).toBeInTheDocument();
  });

  it('displays correct summary values', () => {
    render(
      <MantineProvider>
        <PriceTrendChart priceTrends={mockPriceTrends} />
      </MantineProvider>
    );

    expect(screen.getByText('$10.50')).toBeInTheDocument(); // average
    expect(screen.getByText('$10.00 - $11.00')).toBeInTheDocument(); // range
    expect(screen.getByText('2')).toBeInTheDocument(); // changes count
  });

  it('renders chart with data', () => {
    render(
      <MantineProvider>
        <PriceTrendChart priceTrends={mockPriceTrends} />
      </MantineProvider>
    );

    expect(screen.getByText('Price Trends Over Time')).toBeInTheDocument();
  });

  it('displays empty state when no data', () => {
    const emptyTrends = {
      trends: [],
      summary: {
        average_unit_cost: null,
        min_unit_cost: null,
        max_unit_cost: null,
        price_changes_count: 0,
      },
    };

    render(
      <MantineProvider>
        <PriceTrendChart priceTrends={emptyTrends} />
      </MantineProvider>
    );

    expect(screen.getByText('No price trend data available')).toBeInTheDocument();
  });
});
