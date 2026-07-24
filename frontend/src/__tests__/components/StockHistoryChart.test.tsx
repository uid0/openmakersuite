/**
 * Tests for StockHistoryChart component (op-2dqu).
 *
 * The chart is driven by the stock-history endpoint DTO: a stock line built
 * from `series` + `cycle_counts`, reorder-event markers, cycle-count markers,
 * and reorder/desired threshold lines. recharts is mocked; the mock forwards
 * the props we assert on (marker `fill`, line `label`/`y`).
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import React from 'react';
import StockHistoryChart from '../../components/StockHistoryChart';
import { StockHistory } from '../../types';

// Ensure matchMedia is available (jsdom)
if (typeof window !== 'undefined' && !window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: jest.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    })),
    configurable: true,
  });
}

const renderWithProvider = (component: React.ReactElement) => {
  return render(<MantineProvider>{component}</MantineProvider>);
};

// Mock recharts; forward the props the assertions key off.
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: any) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  LineChart: ({ children }: any) => <div data-testid="line-chart">{children}</div>,
  Line: (props: any) => <div data-testid="line" data-key={props.dataKey} />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  Tooltip: () => <div data-testid="tooltip" />,
  ReferenceLine: (props: any) => (
    <div
      data-testid="reference-line"
      data-label={
        typeof props.label === 'object' && props.label ? props.label.value : String(props.label ?? '')
      }
      data-y={String(props.y)}
    />
  ),
  ReferenceDot: (props: any) => (
    <div data-testid="reference-dot" data-fill={props.fill} data-x={String(props.x)} />
  ),
}));

const REORDER_MARKER = '#fd7e14';
const CYCLE_MARKER = '#7048e8';

const mockData: StockHistory = {
  series: [
    { date: '2026-06-01', count: 30 },
    { date: '2026-06-08', count: 25 },
    { date: '2026-06-15', count: 22 },
  ],
  reorder_events: [{ date: '2026-06-15' }, { date: '2026-07-01' }],
  cycle_counts: [{ date: '2026-06-20', count: 28 }],
  thresholds: { reorder_point: 20, desired: 35 },
  current_stock: 28,
};

describe('StockHistoryChart Component', () => {
  it('renders the stock series line (not the empty state)', () => {
    renderWithProvider(<StockHistoryChart data={mockData} />);

    expect(screen.getByText('Stock History')).toBeInTheDocument();
    expect(screen.getByTestId('line-chart')).toBeInTheDocument();
    expect(screen.getByTestId('line')).toHaveAttribute('data-key', 'stock');
    expect(screen.queryByText(/No stock history data available/i)).not.toBeInTheDocument();
  });

  it('renders the line from cycle_counts alone (populated before weekly snapshots accumulate)', () => {
    const cycleOnly: StockHistory = {
      series: [],
      reorder_events: [],
      cycle_counts: [{ date: '2026-06-20', count: 28 }],
      thresholds: { reorder_point: 20, desired: 35 },
      current_stock: 28,
    };

    renderWithProvider(<StockHistoryChart data={cycleOnly} />);

    expect(screen.getByTestId('line-chart')).toBeInTheDocument();
    expect(screen.queryByText(/No stock history data available/i)).not.toBeInTheDocument();
  });

  it('renders both reorder-event and cycle-count markers', () => {
    renderWithProvider(<StockHistoryChart data={mockData} />);

    const dots = screen.getAllByTestId('reference-dot');
    const reorderDots = dots.filter((d) => d.getAttribute('data-fill') === REORDER_MARKER);
    const cycleDots = dots.filter((d) => d.getAttribute('data-fill') === CYCLE_MARKER);

    expect(reorderDots).toHaveLength(2);
    expect(cycleDots).toHaveLength(1);
  });

  it('renders both threshold lines (reorder point + desired)', () => {
    renderWithProvider(<StockHistoryChart data={mockData} />);

    const lines = screen.getAllByTestId('reference-line');
    expect(lines).toHaveLength(2);

    const labels = lines.map((l) => l.getAttribute('data-label'));
    expect(labels).toContain('Reorder');
    expect(labels).toContain('Desired');

    const ys = lines.map((l) => l.getAttribute('data-y'));
    expect(ys).toContain('20'); // reorder_point
    expect(ys).toContain('35'); // desired
  });

  it('renders a legend distinguishing stock / reorder / desired / event markers', () => {
    renderWithProvider(<StockHistoryChart data={mockData} />);

    expect(screen.getByText('Stock')).toBeInTheDocument();
    expect(screen.getByText('Reorder point')).toBeInTheDocument();
    expect(screen.getByText('Desired')).toBeInTheDocument();
    expect(screen.getByText('Reorder requested')).toBeInTheDocument();
    expect(screen.getByText('Cycle count')).toBeInTheDocument();
  });

  it('renders a friendly empty state when data is null', () => {
    renderWithProvider(<StockHistoryChart data={null} />);

    expect(screen.getByText(/No stock history data available/i)).toBeInTheDocument();
    expect(screen.queryByTestId('line-chart')).not.toBeInTheDocument();
  });

  it('renders the empty state when there are no stock data points', () => {
    const emptyData: StockHistory = {
      series: [],
      // reorder events with no stock line should still show the empty state
      reorder_events: [{ date: '2026-07-01' }],
      cycle_counts: [],
      thresholds: { reorder_point: 20, desired: 35 },
      current_stock: 0,
    };

    renderWithProvider(<StockHistoryChart data={emptyData} />);

    expect(screen.getByText(/No stock history data available/i)).toBeInTheDocument();
    expect(screen.queryByTestId('line-chart')).not.toBeInTheDocument();
  });
});
