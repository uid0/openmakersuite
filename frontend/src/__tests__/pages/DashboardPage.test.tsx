/**
 * Tests for DashboardPage component
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import DashboardPage from '../../pages/DashboardPage';
import { dashboardAPI } from '../../services/api';

// Mock CSS imports
vi.mock('react-grid-layout/css/styles.css', () => ({}));
vi.mock('react-resizable/css/styles.css', () => ({}));

// Mock the dashboard API
vi.mock('../../services/api', () => ({
  dashboardAPI: {
    getWidgets: jest.fn(),
    saveWidgets: jest.fn(),
    getLowStockData: jest.fn(),
    getPendingReordersData: jest.fn(),
    getAssetProblemsData: jest.fn(),
    getQRScansData: jest.fn(),
    getDeliveriesData: jest.fn(),
  },
}));

// Mock react-grid-layout
vi.mock('react-grid-layout', () => {
  return {
    __esModule: true,
    default: ({ children, onLayoutChange }: any) => (
      <div data-testid="grid-layout" onClick={() => onLayoutChange && onLayoutChange([])}>
        {children}
      </div>
    ),
  };
});

// Mock the widget components
vi.mock('../../components/dashboard/LowStockWidget', () => {
  return { default: function LowStockWidget() {
    return <div data-testid="low-stock-widget">Low Stock Widget</div>;
  } };
});

vi.mock('../../components/dashboard/PendingReordersWidget', () => {
  return { default: function PendingReordersWidget() {
    return <div data-testid="pending-reorders-widget">Pending Reorders Widget</div>;
  } };
});

vi.mock('../../components/dashboard/AssetProblemsWidget', () => {
  return { default: function AssetProblemsWidget() {
    return <div data-testid="asset-problems-widget">Asset Problems Widget</div>;
  } };
});

vi.mock('../../components/dashboard/QRScansWidget', () => {
  return { default: function QRScansWidget() {
    return <div data-testid="qr-scans-widget">QR Scans Widget</div>;
  } };
});

vi.mock('../../components/dashboard/DeliveriesWidget', () => {
  return { default: function DeliveriesWidget() {
    return <div data-testid="deliveries-widget">Deliveries Widget</div>;
  } };
});

describe('DashboardPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders loading state initially', () => {
    (dashboardAPI.getWidgets as jest.Mock).mockImplementation(() => 
      new Promise(() => {}) // Never resolves to keep loading state
    );

    render(
      <MantineProvider>
        <BrowserRouter>
          <DashboardPage />
        </BrowserRouter>
      </MantineProvider>
    );

    // Check that grid layout is not rendered while loading
    expect(screen.queryByTestId('grid-layout')).not.toBeInTheDocument();
    // The loader should be present in the DOM
    const loader = document.querySelector('.mantine-Loader-root');
    expect(loader).toBeInTheDocument();
  });

  it('renders error state when API fails', async () => {
    (dashboardAPI.getWidgets as jest.Mock).mockRejectedValueOnce({
      response: { data: { error: 'Failed to load' } },
    });

    render(
      <MantineProvider>
        <BrowserRouter>
          <DashboardPage />
        </BrowserRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getByText(/Failed to load/i)).toBeInTheDocument();
    });
  });

  it('renders widgets when loaded successfully', async () => {
    const mockWidgets = [
      {
        id: 1,
        widget_type: 'low_stock',
        position_x: 0,
        position_y: 0,
        width: 2,
        height: 2,
        is_visible: true,
        order: 0,
        settings: {},
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      },
      {
        id: 2,
        widget_type: 'pending_reorders',
        position_x: 2,
        position_y: 0,
        width: 2,
        height: 2,
        is_visible: true,
        order: 1,
        settings: {},
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      },
    ];

    (dashboardAPI.getWidgets as jest.Mock).mockResolvedValueOnce({ data: mockWidgets });

    render(
      <MantineProvider>
        <BrowserRouter>
          <DashboardPage />
        </BrowserRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('low-stock-widget')).toBeInTheDocument();
      expect(screen.getByTestId('pending-reorders-widget')).toBeInTheDocument();
    });
  });

  it('shows message when no widgets are visible', async () => {
    (dashboardAPI.getWidgets as jest.Mock).mockResolvedValueOnce({ data: [] });

    render(
      <MantineProvider>
        <BrowserRouter>
          <DashboardPage />
        </BrowserRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getByText(/No widgets configured/i)).toBeInTheDocument();
    });
  });

  it('renders the empty state (not a crash) when getWidgets returns a non-array', async () => {
    // A mis-deployed gateway/login HTML page or a serializer shape change
    // can make getWidgets resolve to a non-array. The page must coerce that
    // to the empty state rather than let `widgets.filter` throw into the
    // app-level error boundary (op-j1f).
    (dashboardAPI.getWidgets as jest.Mock).mockResolvedValueOnce({
      data: { detail: 'Authentication credentials were not provided.' },
    });

    render(
      <MantineProvider>
        <BrowserRouter>
          <DashboardPage />
        </BrowserRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getByText(/No widgets configured/i)).toBeInTheDocument();
    });
    // The grid never mounts — a non-array must not reach react-grid-layout.
    expect(screen.queryByTestId('grid-layout')).not.toBeInTheDocument();
  });

  it('unwraps a paginated { results: [...] } envelope into widgets', async () => {
    // Defensive fallback: if the endpoint ever returns a DRF-paginated
    // envelope instead of a bare array, the widgets in `results` still render.
    const mockWidgets = [
      {
        id: 1,
        widget_type: 'low_stock',
        position_x: 0,
        position_y: 0,
        width: 2,
        height: 2,
        is_visible: true,
        order: 0,
        settings: {},
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      },
    ];

    (dashboardAPI.getWidgets as jest.Mock).mockResolvedValueOnce({
      data: { count: 1, next: null, previous: null, results: mockWidgets },
    });

    render(
      <MantineProvider>
        <BrowserRouter>
          <DashboardPage />
        </BrowserRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('low-stock-widget')).toBeInTheDocument();
    });
  });
});
