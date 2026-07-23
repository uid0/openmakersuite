/**
 * Tests for AssetCostRecoveryPage — the cost-recovery report generator
 * (op-wjmm; all-assets + ownership filters op-ilnl).
 *
 * Covers: options load on mount; asset select + default preset build the right
 * params and render per-asset line items + grand total; the custom date range
 * sends start_date/end_date instead of a period; the All-assets toggle and the
 * ownership-type / owning-group (committee) selects drive the params and stand
 * in as a selection on their own; CSV/PDF buttons hit the format= endpoint and
 * trigger a download; empty-state; and error surfacing.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import AssetCostRecoveryPage from '../../pages/AssetCostRecoveryPage';
import { assetsAPI, inventoryAPI, reportsAPI, sigAPI } from '../../services/api';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');

const mockAssetsAPI = assetsAPI as jest.Mocked<typeof assetsAPI>;
const mockInventoryAPI = inventoryAPI as jest.Mocked<typeof inventoryAPI>;
const mockReportsAPI = reportsAPI as jest.Mocked<typeof reportsAPI>;
const mockSigAPI = sigAPI as jest.Mocked<typeof sigAPI>;

const okResponse = <T,>(data: T) =>
  ({ data, status: 200, statusText: 'OK', headers: {}, config: {} as never }) as never;

const ASSETS = [
  { id: 'a1', name: 'Laser Cutter', asset_tag: 'LC-1', serial_number: 'SN-1' },
  { id: 'a2', name: 'CNC Mill', asset_tag: '', serial_number: 'SN-2' },
];

const CATEGORIES = [{ id: 3, name: 'Fabrication' }];

const SIGS = [
  { id: 11, name: 'Woodshop Committee' },
  { id: 12, name: 'Electronics Committee' },
];

const SAMPLE_REPORT = {
  period: 'past_month',
  start_date: '2026-06-16',
  end_date: '2026-07-16',
  asset_ids: ['a1'],
  category_ids: [],
  asset_count: 1,
  service_count: 2,
  grand_total_estimated: '40.00',
  grand_total_internal: '40.00',
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
      is_cost_recoverable: false,
      services: [
        {
          date: '2026-06-20',
          source: 'vendor',
          description: 'Tube replacement (Acme)',
          estimated_cost: null,
          internal_cost: null,
          actual_cost: '250.00',
        },
        {
          date: '2026-06-25',
          source: 'pm',
          description: 'Lens cleaning',
          estimated_cost: '40.00',
          internal_cost: '40.00',
          actual_cost: null,
        },
      ],
      subtotal_estimated: '40.00',
      subtotal_internal: '40.00',
      subtotal_actual: '250.00',
    },
  ],
};

// A flagged asset next to an unflagged one — the B5 split the statement exists
// to show. Same in-house PM cost on both; only the flagged one bills it.
const SPLIT_REPORT = {
  ...SAMPLE_REPORT,
  asset_ids: ['a1', 'a2'],
  asset_count: 2,
  service_count: 2,
  grand_total_estimated: '150.00',
  grand_total_internal: '150.00',
  grand_total_actual: '75.00',
  assets: [
    {
      ...SAMPLE_REPORT.assets[0],
      asset_id: 'a1',
      name: 'Rooftop Unit',
      asset_tag: 'HV-1',
      is_cost_recoverable: true,
      services: [
        {
          date: '2026-06-20',
          source: 'pm',
          description: 'Belt replacement',
          estimated_cost: '80.00',
          internal_cost: '75.00',
          actual_cost: '75.00',
        },
      ],
      subtotal_estimated: '80.00',
      subtotal_internal: '75.00',
      subtotal_actual: '75.00',
    },
    {
      ...SAMPLE_REPORT.assets[0],
      asset_id: 'a2',
      name: 'CNC Mill',
      asset_tag: 'CM-1',
      is_cost_recoverable: false,
      services: [
        {
          date: '2026-06-22',
          source: 'pm',
          description: 'Spindle service',
          estimated_cost: '70.00',
          internal_cost: '75.00',
          actual_cost: null,
        },
      ],
      subtotal_estimated: '70.00',
      subtotal_internal: '75.00',
      subtotal_actual: '0.00',
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
  mockSigAPI.listMySIGs.mockResolvedValue(okResponse({ results: SIGS }));
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

// Pick an option from one of the ownership <Select>s. Mantine renders both a
// visible and a hidden input per Select, so target the testid (which lands on
// the visible one) rather than the shared label.
const pickFromSelect = async (testId: string, option: RegExp) => {
  const input = screen.getByTestId(testId);
  await waitFor(() => expect(input).not.toBeDisabled());
  fireEvent.click(input);
  fireEvent.click(await screen.findByRole('option', { name: option }));
};

const allAssetsToggle = () => screen.getByTestId('cost-recovery-all-assets');

describe('AssetCostRecoveryPage', () => {
  it('loads asset + category + committee pickers and shows the initial prompt', async () => {
    renderPage();

    expect(
      await screen.findByText(/choose assets or categories .* plus a period/i),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Generate' })).toBeInTheDocument();

    await waitFor(() => {
      expect(mockAssetsAPI.listAssets).toHaveBeenCalledWith({ page_size: 500 });
    });
    expect(mockInventoryAPI.listCategories).toHaveBeenCalled();
    // Committees come from the same source as the asset ownership editor.
    expect(mockSigAPI.listMySIGs).toHaveBeenCalled();
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

  it('All assets is a selection on its own and sends all_assets=true', async () => {
    renderPage();
    const generate = screen.getByRole('button', { name: 'Generate' });
    expect(generate).toBeDisabled();

    fireEvent.click(allAssetsToggle());
    await waitFor(() => expect(generate).not.toBeDisabled());
    fireEvent.click(generate);

    await waitFor(() => expect(mockReportsAPI.getAssetCostRecovery).toHaveBeenCalled());
    const params = mockReportsAPI.getAssetCostRecovery.mock.calls[0][0];
    expect(params.all_assets).toBe(true);
    expect(params.period).toBe('past_month');
    // The pickers are ignored in this mode, so no stale ids ride along.
    expect(params.asset_ids).toEqual([]);
    expect(params.category_ids).toEqual([]);
  });

  it('All assets disables the asset + category pickers', async () => {
    renderPage();
    const assetInput = screen.getByPlaceholderText('Search assets');
    await waitFor(() => expect(assetInput).not.toBeDisabled());

    fireEvent.click(allAssetsToggle());

    await waitFor(() => expect(assetInput).toBeDisabled());
    expect(screen.getByPlaceholderText('Search categories')).toBeDisabled();
  });

  it('an ownership type alone enables Generate and sends ownership_type', async () => {
    renderPage();
    const generate = screen.getByRole('button', { name: 'Generate' });
    expect(generate).toBeDisabled();

    await pickFromSelect('cost-recovery-ownership-type', /^Space$/);
    await waitFor(() => expect(generate).not.toBeDisabled());
    fireEvent.click(generate);

    await waitFor(() => expect(mockReportsAPI.getAssetCostRecovery).toHaveBeenCalled());
    const params = mockReportsAPI.getAssetCostRecovery.mock.calls[0][0];
    expect(params.ownership_type).toBe('space');
    expect(params.all_assets).toBeUndefined();
  });

  it('picking a committee sends owning_group and combines with All assets', async () => {
    renderPage();

    fireEvent.click(allAssetsToggle());
    await pickFromSelect('cost-recovery-owning-group', /Woodshop Committee/i);

    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));

    await waitFor(() => expect(mockReportsAPI.getAssetCostRecovery).toHaveBeenCalled());
    const params = mockReportsAPI.getAssetCostRecovery.mock.calls[0][0];
    expect(params.all_assets).toBe(true);
    expect(params.owning_group).toBe(11);
  });

  it('shows the recoverable / internal split for a flagged vs unflagged asset', async () => {
    mockReportsAPI.getAssetCostRecovery.mockResolvedValue(okResponse(SPLIT_REPORT));

    renderPage();
    await selectLaserCutter();
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));

    const results = await screen.findByTestId('cost-recovery-results');

    // The flag itself, per asset.
    expect(within(results).getByTestId('cost-recoverable-badge-a1')).toHaveTextContent(
      'Cost-recoverable',
    );
    expect(within(results).getByTestId('cost-recoverable-badge-a2')).toHaveTextContent(
      /not billable/i,
    );

    // Same $75 of in-house work on both — only the flagged asset bills it.
    expect(within(results).getByText(/Recoverable: \$75\.00/)).toBeInTheDocument();
    expect(within(results).getByText(/Recoverable: \$0\.00/)).toBeInTheDocument();
    expect(within(results).getByText(/In-house cost: \$75\.00 \(internal only\)/)).toBeInTheDocument();

    // The grand total bills only the flagged asset's in-house work.
    expect(within(results).getByText(/grand total recoverable: \$75\.00/i)).toBeInTheDocument();
    expect(within(results).getByTestId('cost-recovery-grand-internal')).toHaveTextContent(
      /In-house cost across the selection: \$150\.00/,
    );
  });

  it('renders the in-house cost column alongside Estimated and Actual', async () => {
    renderPage();
    await selectLaserCutter();
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));

    const results = await screen.findByTestId('cost-recovery-results');
    expect(within(results).getByRole('columnheader', { name: /in-house cost/i })).toBeInTheDocument();
    expect(
      within(results).getByRole('columnheader', { name: /actual \(recoverable\)/i }),
    ).toBeInTheDocument();
    // A vendor line is not in-house work: estimated and in-house are both "—",
    // and only the actual carries money.
    const vendorRow = within(results).getByText('Tube replacement (Acme)').closest('tr')!;
    expect(within(vendorRow).getAllByText('—')).toHaveLength(2);
    expect(within(vendorRow).getByText('$250.00')).toBeInTheDocument();
    // The PM line reports the same figure as estimate and in-house cost, and
    // bills nothing — this asset is not flagged.
    const pmRow = within(results).getByText('Lens cleaning').closest('tr')!;
    expect(within(pmRow).getAllByText('$40.00')).toHaveLength(2);
    expect(within(pmRow).getByText('—')).toBeInTheDocument();
  });

  it('carries the ownership scope into the CSV export', async () => {
    Object.defineProperty(window.URL, 'createObjectURL', {
      value: vi.fn(() => 'blob:mock'),
      writable: true,
    });
    Object.defineProperty(window.URL, 'revokeObjectURL', { value: vi.fn(), writable: true });

    renderPage();
    await pickFromSelect('cost-recovery-owning-group', /Electronics Committee/i);

    fireEvent.click(screen.getByRole('button', { name: /export csv/i }));

    await waitFor(() => {
      expect(mockReportsAPI.downloadAssetCostRecovery).toHaveBeenCalledWith(
        expect.objectContaining({ owning_group: 12, period: 'past_month' }),
        'csv',
      );
    });
  });
});
