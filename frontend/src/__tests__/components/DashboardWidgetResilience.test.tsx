/**
 * Dashboard widget resilience tests (op-8lhv).
 *
 * One widget-data endpoint 500'ing used to blank the whole dashboard via two
 * independent defects:
 *   1. Widgets stored the raw `{code, message}` error envelope in state and
 *      rendered it as a JSX child, throwing React #31.
 *   2. There was no per-widget error isolation, so that throw bubbled to the
 *      app-level boundary and took down the entire page.
 *
 * These tests lock in the fixes: a 500 surfaces a friendly per-widget string,
 * and a widget that crashes at render is isolated so its siblings keep
 * rendering.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { BrowserRouter } from 'react-router-dom';
import DashboardWidget from '../../components/dashboard/DashboardWidget';
import DeliveriesWidget from '../../components/dashboard/DeliveriesWidget';
import WidgetErrorBoundary from '../../components/dashboard/WidgetErrorBoundary';
import { dashboardAPI } from '../../services/api';

vi.mock('../../services/api', () => ({
  dashboardAPI: {
    getDeliveriesData: vi.fn(),
  },
}));

const renderWithProviders = (ui: React.ReactElement) =>
  render(
    <MantineProvider>
      <BrowserRouter>{ui}</BrowserRouter>
    </MantineProvider>
  );

// The standardized 500 envelope the backend emits from error_response(): axios
// rejects with this shape on the deliveries endpoint.
const serverErrorEnvelope = {
  response: {
    status: 500,
    data: { error: { code: 'server_error', message: 'Internal server error.' } },
  },
};

describe('Dashboard widget resilience (op-8lhv)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a friendly string (not the raw {code,message} object) when a widget fetch 500s', async () => {
    vi.mocked(dashboardAPI.getDeliveriesData).mockRejectedValueOnce(serverErrorEnvelope);

    renderWithProviders(<DeliveriesWidget />);

    // The widget surfaces the envelope's message as a string. If it rendered the
    // raw {code,message} object it would throw React #31 before this resolves.
    await waitFor(() => {
      expect(screen.getByText('Internal server error.')).toBeInTheDocument();
    });
    // The widget itself is still on screen — the failure is contained to its own
    // per-widget error state, not a blank page.
    expect(screen.getByText('Recent Deliveries')).toBeInTheDocument();
  });

  it('isolates a crashing widget so sibling widgets keep rendering', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const Boom: React.FC = () => {
      throw new Error('kaboom');
    };

    renderWithProviders(
      <>
        <WidgetErrorBoundary title="Recent Deliveries">
          <Boom />
        </WidgetErrorBoundary>
        <WidgetErrorBoundary title="Low Stock">
          <div>healthy widget content</div>
        </WidgetErrorBoundary>
      </>
    );

    // The crashed widget shows its compact fallback...
    expect(screen.getByText(/couldn't load/i)).toBeInTheDocument();
    // ...and the sibling still renders. One widget did not take down the rest.
    expect(screen.getByText('healthy widget content')).toBeInTheDocument();

    consoleError.mockRestore();
  });

  it('coerces a {code,message} object error to a string instead of throwing React #31', () => {
    // Defense-in-depth: even if a widget forwards the raw envelope object, the
    // shared base component must never render it as a JSX child.
    const envelope = { code: 'server_error', message: 'Internal server error.' };

    expect(() =>
      renderWithProviders(
        <DashboardWidget title="Recent Deliveries" error={envelope as unknown as string}>
          <div>content</div>
        </DashboardWidget>
      )
    ).not.toThrow();

    expect(screen.getByText('Internal server error.')).toBeInTheDocument();
  });
});
