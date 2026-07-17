/**
 * Tests for AssetCostRecoveryPage — the cost-recovery report generator (op-wjmm).
 *
 * Covers: options load on mount; asset select + default preset build the right
 * params and render per-asset line items + grand total; the custom date range
 * sends start_date/end_date instead of a period; CSV/PDF buttons hit the
 * format= endpoint and trigger a download; empty-state; and error surfacing.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import AssetCostRecoveryPage from '../../pages/AssetCostRecoveryPage';
import { assetsAPI, inventoryAPI, reportsAPI } from '../../services/api';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');

const mockAssetsAPI = assetsAPI as jest.Mocked<typeof assetsAPI>;
const mockInventoryAPI = inventoryAPI as jest.Mocked<typeof inventoryAPI>;
const mockReportsAPI = reportsAPI as jest.Mocked<typeof reportsAPI>;

const okResponse = <T,>(data: T) =>
  ({ data, status: 200, statusText: 'OK', headers: {}, config: {} as never }) as never;

const ASSETS = [
  { id: 'a1', name: 'Laser Cutter', asset_tag: 'LC-1', serial_number: 'SN-1' },
  { id: 'a2', name: 'CNC Mill', asset_tag: '', serial_number: 'SN-2' },
];

const CATEGORIES = [{ id: 3, name: 'Fabrication' }];

const SAMPLE_REPORT = {
  period: 'past_month',
  start_date: '2026-06-16',
  end_date: '2026-07-16',
  asset_ids: ['a1'],
  category_ids: [],
  asset_count: 1,
  service_count: 2,
  grand_total_estimated: '40.00',
  grand_total_actual: '250.00',
  assets: [
    {
      asset_id: 'a1',
      asset_tag: 'LC-1',
      name: 'Laser Cutter',
      serial_number: 'SN-1',
      date_received: '2025-01-01',
      status: 'operational',
      status_display: 'Operational',
      category: 'Fabrication',
      services: [
        {
          date: '2026-06-20',
          source: 'vendor',
          description: 'Tube replacement (Acme)',
          estimated_cost: null,
          actual_cost: '250.00',
        },
        {
          date: '2026-06-25',
          source: 'pm',
          description: 'Lens cleaning',
          estimated_cost: '40.00',
          actual_cost: null,
        },
      ],
      subtotal_estimated: '40.00',
      subtotal_actual: '250.00',
    },
  ],
};

const renderPage = () =>
  render(
    <MantineProvider env="test">
      <AssetCostRecoveryPage />
    </MantineProvider>,
  );

// Load the pickers so the selects enable; individual tests override the report
// calls as needed.
beforeEach(() => {
  jest.clearAllMocks();
  mockAssetsAPI.listAssets.mockResolvedValue(
    okResponse({ count: ASSETS.length, next: null, previous: null, results: ASSETS }),
  );
  mockInventoryAPI.listCategories.mockResolvedValue(okResponse({ results: CATEGORIES }));
  mockReportsAPI.getAssetCostRecovery.mockResolvedValue(okResponse(SAMPLE_REPORT));
  mockReportsAPI.downloadAssetCostRecovery.mockResolvedValue(okResponse(new Blob(['x'])));
});

// Wait for the asset picker to finish loading, then select "Laser Cutter".
const selectLaserCutter = async () => {
  const assetInput = screen.getByPlaceholderText('Search assets');
  await waitFor(() => expect(assetInput).not.toBeDisabled());
  fireEvent.click(assetInput);
  fireEvent.click(await screen.findByRole('option', { name: /Laser Cutter/i }));
};

describe('AssetCostRecoveryPage', () => {
  it('loads asset + category pickers and shows the initial prompt', async () => {
    renderPage();

    expect(
      await screen.findByText(/choose one or more assets or categories and a period/i),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Generate' })).toBeInTheDocument();

    await waitFor(() => {
      expect(mockAssetsAPI.listAssets).toHaveBeenCalledWith({ page_size: 500 });
    });
    expect(mockInventoryAPI.listCategories).toHaveBeenCalled();
  });

  it('generates with the default preset and renders per-asset line items + grand total', async () => {
    renderPage();
    await selectLaserCutter();

    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));

    await waitFor(() => {
      expect(mockReportsAPI.getAssetCostRecovery).toHaveBeenCalledWith(
        expect.objectContaining({ asset_ids: ['a1'], period: 'past_month' }),
      );
    });

    const results = await screen.findByTestId('cost-recovery-results');
    expect(within(results).getByText('Tube replacement (Acme)')).toBeInTheDocument();
    expect(within(results).getByText('Lens cleaning')).toBeInTheDocument();
    expect(
      within(results).getByText(/grand total recoverable: \$250\.00/i),
    ).toBeInTheDocument();
  });

  it('sends start_date/end_date (not a period) when "These dates" is selected', async () => {
    renderPage();
    await selectLaserCutter();

    fireEvent.click(screen.getByRole('radio', { name: /these dates/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));

    await waitFor(() => expect(mockReportsAPI.getAssetCostRecovery).toHaveBeenCalled());
    const params = mockReportsAPI.getAssetCostRecovery.mock.calls[0][0];
    expect(params.period).toBeUndefined();
    expect(params.start_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(params.end_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(params.asset_ids).toEqual(['a1']);
  });

  it('Export CSV hits the csv endpoint and triggers a download', async () => {
    const createObjectURL = vi.fn(() => 'blob:mock');
    Object.defineProperty(window.URL, 'createObjectURL', {
      value: createObjectURL,
      writable: true,
    });
    Object.defineProperty(window.URL, 'revokeObjectURL', { value: vi.fn(), writable: true });

    renderPage();
    await selectLaserCutter();

    fireEvent.click(screen.getByRole('button', { name: /export csv/i }));

    await waitFor(() => {
      expect(mockReportsAPI.downloadAssetCostRecovery).toHaveBeenCalledWith(
        expect.objectContaining({ asset_ids: ['a1'], period: 'past_month' }),
        'csv',
      );
    });
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
  });

  it('Export PDF hits the pdf endpoint', async () => {
    Object.defineProperty(window.URL, 'createObjectURL', {
      value: vi.fn(() => 'blob:mock'),
      writable: true,
    });
    Object.defineProperty(window.URL, 'revokeObjectURL', { value: vi.fn(), writable: true });

    renderPage();
    await selectLaserCutter();

    fireEvent.click(screen.getByRole('button', { name: /export pdf/i }));

    await waitFor(() => {
      expect(mockReportsAPI.downloadAssetCostRecovery).toHaveBeenCalledWith(
        expect.objectContaining({ asset_ids: ['a1'] }),
        'pdf',
      );
    });
  });

  it('shows an empty state when the selection matches no assets', async () => {
    mockReportsAPI.getAssetCostRecovery.mockResolvedValue(
      okResponse({
        ...SAMPLE_REPORT,
        asset_count: 0,
        service_count: 0,
        grand_total_actual: '0.00',
        grand_total_estimated: '0.00',
        assets: [],
      }),
    );

    renderPage();
    await selectLaserCutter();
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));

    expect(
      await screen.findByText(/no assets matched your selection for this period/i),
    ).toBeInTheDocument();
  });

  it('surfaces an error when report generation fails', async () => {
    mockReportsAPI.getAssetCostRecovery.mockRejectedValue(networkError());

    renderPage();
    await selectLaserCutter();
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));

    expect(await screen.findByText(/report error/i)).toBeInTheDocument();
  });

  it('keeps Generate disabled until an asset or category is chosen', async () => {
    renderPage();
    const generate = screen.getByRole('button', { name: 'Generate' });
    expect(generate).toBeDisabled();
    // Enable once loaded + a selection is made.
    await selectLaserCutter();
    await waitFor(() => expect(generate).not.toBeDisabled());
  });
});
