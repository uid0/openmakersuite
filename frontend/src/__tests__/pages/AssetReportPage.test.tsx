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
import { render, screen, waitFor } from '@testing-library/react';

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
