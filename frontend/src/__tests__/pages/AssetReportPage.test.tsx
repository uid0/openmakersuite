/**
 * Resilience tests for AssetReportPage (#457 R4 — AC-19).
 *
 * The asset reports screen had no test. Its default tab loads
 * "assets by status" on mount. AC-19 requires a loading placeholder, a clear
 * empty state, and a non-blank state on failure. The page logs fetch errors
 * and leaves the table empty, so a failed/offline load lands on the same
 * readable "No data available" state rather than a blank panel or a crash.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import AssetReportPage from '../../pages/AssetReportPage';
import { reportsAPI } from '../../services/api';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');

const mockReportsAPI = reportsAPI as jest.Mocked<typeof reportsAPI>;

const renderPage = () =>
  render(
    <MantineProvider>
      <AssetReportPage />
    </MantineProvider>,
  );

const okResponse = <T,>(data: T) =>
  ({ data, status: 200, statusText: 'OK', headers: {}, config: {} as never }) as never;

beforeEach(() => {
  jest.clearAllMocks();
});

describe('AssetReportPage resilience (#457 R4)', () => {
  it('shows a loading placeholder while the report loads (AC-19)', () => {
    mockReportsAPI.getAssetAssetsByStatus.mockImplementation(
      () => new Promise(() => undefined) as never,
    );

    renderPage();

    // Mantine Tabs mounts every panel (inactive ones hidden via CSS), so the
    // shared "Loading..." placeholder appears in each tab's table.
    expect(screen.getAllByText(/loading\.\.\./i).length).toBeGreaterThan(0);
  });

  it('shows a clear empty state when the report has no rows (AC-19)', async () => {
    mockReportsAPI.getAssetAssetsByStatus.mockResolvedValue(okResponse([]));

    renderPage();

    expect(await screen.findByText(/no data available/i)).toBeInTheDocument();
  });

  it('renders a readable state (not a blank table) when the load fails (AC-19)', async () => {
    mockReportsAPI.getAssetAssetsByStatus.mockRejectedValue(networkError());

    renderPage();

    // The fetch failed but the table shows the consistent "no data" state and
    // stops loading — no blank panel, no uncaught exception.
    expect(await screen.findByText(/no data available/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText(/loading\.\.\./i)).not.toBeInTheDocument();
    });
  });
});

describe('AssetReportPage Supplies Used tab (op-ib81)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // Mount tab loads on render; keep it quiet so the panel under test is clean.
    mockReportsAPI.getAssetAssetsByStatus.mockResolvedValue(okResponse([]));
  });

  it('loads and renders serialized + consumable rows when the tab is selected', async () => {
    mockReportsAPI.getAssetSuppliesUsed.mockResolvedValue(
      okResponse([
        {
          asset_id: 'a1',
          asset_name: 'Laser Cutter',
          source: 'serialized',
          item_name: 'Lens Assembly',
          serial_number: 'SN-1001',
          action: 'install',
          action_display: 'Installed',
          used_at: '2026-06-01T10:00:00Z',
          actor: 'alice',
        },
        {
          asset_id: 'a1',
          asset_name: 'Laser Cutter',
          source: 'consumable',
          item_name: 'Cutting Oil',
          quantity: '2',
          unit: 'L',
          work_order_id: 'wo-9',
          estimated_cost: '15.50',
          used_at: '2026-06-05T12:00:00Z',
        },
      ]),
    );

    renderPage();

    // Mantine Tabs keeps every panel mounted; the loader only fires once the
    // Supplies Used tab is active, honoring the shared date range.
    fireEvent.click(screen.getByRole('tab', { name: /supplies used/i }));

    const panel = await screen.findByRole('tabpanel');
    // Serialized row: serial number + human action, no cost column value.
    expect(await within(panel).findByText('Lens Assembly')).toBeInTheDocument();
    expect(within(panel).getByText('SN-1001')).toBeInTheDocument();
    expect(within(panel).getByText('Installed')).toBeInTheDocument();
    // Consumable row: qty+unit under Serial/Qty and a formatted cost.
    expect(within(panel).getByText('Cutting Oil')).toBeInTheDocument();
    expect(within(panel).getByText('2 L')).toBeInTheDocument();
    expect(within(panel).getByText('$15.50')).toBeInTheDocument();

    expect(mockReportsAPI.getAssetSuppliesUsed).toHaveBeenCalledWith(
      expect.objectContaining({
        start_date: expect.any(String),
        end_date: expect.any(String),
      }),
    );
  });

  it('shows a clear empty state on the tab when there are no rows', async () => {
    mockReportsAPI.getAssetSuppliesUsed.mockResolvedValue(okResponse([]));

    renderPage();

    fireEvent.click(screen.getByRole('tab', { name: /supplies used/i }));

    const panel = await screen.findByRole('tabpanel');
    expect(await within(panel).findByText(/no supplies used in this period/i)).toBeInTheDocument();
  });
});
