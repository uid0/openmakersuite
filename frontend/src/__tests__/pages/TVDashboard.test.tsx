/**
 * Resilience tests for the TV Dashboard (#457 R5, AC-16/19/20).
 *
 * The TV Dashboard is an unattended Chromecast/TV display, so it must:
 *  - render a readable dependency-failure state (not a blank screen or raw
 *    exception) when the backend is unreachable — AC-16/19;
 *  - keep polling on its 30-second auto-refresh and recover once the backend
 *    comes back — AC-16;
 *  - expose accessible, readable kiosk states — AC-20.
 *
 * The page builds its own auth-less axios instance via `axios.create()` at
 * module load, so we hoist a single mocked instance whose `get` we drive per
 * test. `../services/api` is stubbed down to `resolveApiBaseUrl` and the site
 * settings hook is stubbed so the test exercises only the dashboard.
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { InventoryItem } from '../../types';
import { expectNoA11yViolations } from '../helpers/axe';

const { tvGet } = vi.hoisted(() => ({ tvGet: vi.fn() }));

vi.mock('axios', () => ({
  default: { create: () => ({ get: tvGet }) },
}));

vi.mock('../../services/api', () => ({
  resolveApiBaseUrl: () => 'http://test.local/api',
}));

vi.mock('../../hooks/useSiteSettings', () => ({
  useSiteSettings: () => ({ settings: null, loading: false, error: null }),
}));

// eslint-disable-next-line import/first
import TVDashboard from '../../pages/TVDashboard';

const buildItem = (overrides: Partial<InventoryItem> = {}): InventoryItem =>
  ({
    id: 'item-1',
    name: 'Widget',
    description: '',
    sku: 'WIDGET-1',
    image: null,
    thumbnail: null,
    category: null,
    category_name: '',
    location: 'Main Workshop',
    reorder_quantity: 5,
    current_stock: 1,
    minimum_stock: 10,
    use_case_based_reorder: false,
    minimum_cases: 0,
    reorder_cases: 0,
    current_cases: 0,
    supplier: null,
    supplier_name: '',
    supplier_sku: '',
    supplier_url: '',
    unit_cost: null,
    average_lead_time: 0,
    qr_code: null,
    is_active: true,
    notes: '',
    needs_reorder: true,
    total_value: '0',
    created_at: '',
    updated_at: '',
    ownership_type: 'space',
    owning_user: null,
    owning_group: null,
    reorder_status: 'pending',
    has_pending_reorder: true,
    expected_delivery_date: null,
    active_reorder_request: null,
    is_hazardous: false,
    msds_url: null,
    nfpa_health_hazard: null,
    nfpa_fire_hazard: null,
    nfpa_instability_hazard: null,
    nfpa_special_hazards: '',
    nfpa_fire_diamond_display: '',
    hazmat_compliance_status: '',
    has_complete_nfpa_data: false,
    ...overrides,
  }) as InventoryItem;

const renderTV = () =>
  render(
    <MemoryRouter>
      <TVDashboard />
    </MemoryRouter>,
  );

describe('TVDashboard resilience', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows a readable, accessible loading state before data arrives', async () => {
    // Never resolves: the dashboard stays in its loading state.
    tvGet.mockReturnValue(new Promise(() => {}));
    const { container } = renderTV();

    expect(screen.getByText('Loading Inventory Dashboard...')).toBeInTheDocument();
    expect(screen.getByTestId('loading-spinner')).toBeInTheDocument();
    await expectNoA11yViolations(container);
  });

  it('shows a readable backend-error state when the server returns 5xx', async () => {
    tvGet.mockRejectedValue({ response: { status: 503, statusText: 'Service Unavailable' } });
    renderTV();

    expect(await screen.findByText('Connection Error')).toBeInTheDocument();
    expect(
      screen.getByText('Backend server error - Check if the server is running'),
    ).toBeInTheDocument();
    // The unattended display tells the viewer it will recover on its own.
    expect(screen.getByText('Retrying automatically...')).toBeInTheDocument();
  });

  it('shows a readable network-error state when the backend is unreachable', async () => {
    tvGet.mockRejectedValue({ code: 'ERR_NETWORK', message: 'Network Error' });
    renderTV();

    expect(
      await screen.findByText('Network error - Cannot connect to backend server'),
    ).toBeInTheDocument();
  });

  it('has no accessibility violations in the dependency-failure state', async () => {
    tvGet.mockRejectedValue({ code: 'ERR_NETWORK', message: 'Network Error' });
    const { container } = renderTV();

    await screen.findByText('Connection Error');
    await expectNoA11yViolations(container);
  });

  it('renders a readable empty state when nothing is on order', async () => {
    tvGet.mockResolvedValue({ data: [] });
    renderTV();

    expect(await screen.findByText('No Items on Order')).toBeInTheDocument();
  });

  it('auto-refreshes every 30s and recovers content after a transient failure', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      tvGet
        .mockRejectedValueOnce({ code: 'ERR_NETWORK', message: 'Network Error' })
        .mockResolvedValue({ data: [buildItem({ id: 'i1', name: 'Recovered Widget' })] });

      renderTV();

      // Initial fetch (called once, from the mount effect) fails: the display
      // shows the readable connection-error state, not a blank screen.
      expect(await screen.findByText('Connection Error')).toBeInTheDocument();
      expect(tvGet).toHaveBeenCalledTimes(1);

      // After 30s the auto-refresh fires again and the backend is back.
      await act(async () => {
        vi.advanceTimersByTime(30000);
      });
      expect(await screen.findByText('Recovered Widget')).toBeInTheDocument();
      expect(tvGet.mock.calls.length).toBeGreaterThanOrEqual(2);

      // It keeps polling on the same 30s cadence.
      const callsSoFar = tvGet.mock.calls.length;
      await act(async () => {
        vi.advanceTimersByTime(30000);
      });
      await waitFor(() => {
        expect(tvGet.mock.calls.length).toBeGreaterThan(callsSoFar);
      });
    } finally {
      vi.useRealTimers();
    }
  });
});
