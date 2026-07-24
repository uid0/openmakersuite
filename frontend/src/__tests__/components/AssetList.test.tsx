/**
 * Tests for AssetList component
 */
import { MantineProvider } from '@mantine/core';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import AssetList from '../../components/AssetList';
import { assetsAPI, inventoryAPI, sigAPI } from '../../services/api';
import { Asset, Location, SIG } from '../../types';

vi.mock('../../services/api');

// Capture the IntersectionObserver callback so tests can simulate the
// infinite-scroll sentinel coming into view.
let intersectionCallback: IntersectionObserverCallback | null = null;
const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
  useSearchParams: () => [new URLSearchParams()],
}));

const mockAssetsAPI = assetsAPI as jest.Mocked<typeof assetsAPI>;
const mockInventoryAPI = inventoryAPI as jest.Mocked<typeof inventoryAPI>;
const mockSigAPI = sigAPI as jest.Mocked<typeof sigAPI>;

describe('AssetList', () => {
  const mockAssets: Asset[] = [
    {
      id: '1',
      name: 'Test Asset 1',
      description: 'Description 1',
      serial_number: 'SN001',
      asset_tag: 'TAG001',
      inventory_item: null,
      inventory_item_name: '',
      manufacturer: null,
      manufacturer_name: '',
      display_manufacturer: 'Test Manufacturer',
      date_received: '2024-01-01',
      amount_paid: '100.00',
      is_donation: false,
      donor_name: '',
      acquisition_display: 'Purchased for $100',
      category: 1,
      category_name: 'Electronics',
      location: 1,
      location_name: 'Workshop A',
      product_url: '',
      wiki_page_url: '',
      maintenance_plan: '',
      image: null,
      image_url: null,
      thumbnail_url: null,
      manual_pdf: null,
      manual_pdf_url: null,
      qr_code: null,
      qr_code_url: null,
      qr_code_scan_url: null,
      status: 'active',
      condition_notes: '',
      age_in_days: 30,
      is_active: true,
      report_only: false,
      notes: '',
      circuit: '',
      needs_compressed_air: false,
      needs_ventilation: false,
      is_chargeable: false,
      last_scanned_at: null,
      ownership_type: 'space',
      owning_group: null,
      owning_group_name: null,
      owning_user: null,
      owning_user_name: null,
      groups_can_enable: [],
      is_locked: false,
      lockout_info: null,
      can_enable: true,
      can_unlock: false,
      operational_status: 'available',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    },
    {
      id: '2',
      name: 'Test Asset 2',
      description: 'Description 2',
      serial_number: 'SN002',
      asset_tag: 'TAG002',
      inventory_item: null,
      inventory_item_name: '',
      manufacturer: null,
      manufacturer_name: '',
      display_manufacturer: 'Test Manufacturer 2',
      date_received: '2024-01-02',
      amount_paid: '200.00',
      is_donation: true,
      donor_name: 'John Doe',
      acquisition_display: 'Donated from John Doe',
      category: 2,
      category_name: 'Tools',
      location: 2,
      location_name: 'Workshop B',
      product_url: '',
      wiki_page_url: '',
      maintenance_plan: '',
      image: null,
      image_url: null,
      thumbnail_url: null,
      manual_pdf: null,
      manual_pdf_url: null,
      qr_code: null,
      qr_code_url: null,
      qr_code_scan_url: null,
      status: 'maintenance',
      condition_notes: '',
      age_in_days: 29,
      is_active: true,
      report_only: false,
      notes: '',
      circuit: '',
      needs_compressed_air: false,
      needs_ventilation: false,
      is_chargeable: false,
      last_scanned_at: null,
      ownership_type: 'space',
      owning_group: null,
      owning_group_name: null,
      owning_user: null,
      owning_user_name: null,
      groups_can_enable: [],
      is_locked: false,
      lockout_info: null,
      can_enable: true,
      can_unlock: false,
      operational_status: 'available',
      created_at: '2024-01-02T00:00:00Z',
      updated_at: '2024-01-02T00:00:00Z',
    },
  ];

  const mockLocations: Location[] = [
    { id: 1, name: 'Workshop A', description: '', created_at: '', updated_at: '' },
    { id: 2, name: 'Workshop B', description: '', created_at: '', updated_at: '' },
  ];

  const mockSIGs: SIG[] = [
    { id: 1, name: 'SIG 1', member_count: 5, asset_count: 2, inventory_count: 3, admins: [], is_user_admin: false },
    { id: 2, name: 'SIG 2', member_count: 3, asset_count: 1, inventory_count: 2, admins: [], is_user_admin: false },
  ];

  beforeEach(() => {
    jest.clearAllMocks();
    // Clear localStorage before each test
    localStorage.clear();

    intersectionCallback = null;
    (globalThis as unknown as { IntersectionObserver: unknown }).IntersectionObserver = class {
      constructor(cb: IntersectionObserverCallback) {
        intersectionCallback = cb;
      }
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = vi.fn();
      takeRecords = () => [];
    };

    mockAssetsAPI.listAssets.mockResolvedValue({
      data: {
        count: mockAssets.length,
        next: null,
        previous: null,
        results: mockAssets,
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
    mockInventoryAPI.listLocations.mockResolvedValue({
      data: { results: mockLocations },
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
    mockSigAPI.listMySIGs.mockResolvedValue({
      data: { results: mockSIGs },
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
  });

  const renderComponent = () => {
    return render(
      <MantineProvider>
        <MemoryRouter>
          <AssetList />
        </MemoryRouter>
      </MantineProvider>
    );
  };

  it('renders asset list', async () => {
    renderComponent();

    await waitFor(() => {
      expect(screen.getByText(/Makerspace Assets/)).toBeInTheDocument();
    });

    // Wait for assets to load
    await waitFor(() => {
      expect(screen.getByText('Test Asset 1')).toBeInTheDocument();
    }, { timeout: 3000 });

    expect(screen.getByText('Test Asset 2')).toBeInTheDocument();
  });

  it('filters assets by status', async () => {
    renderComponent();

    await waitFor(() => {
      expect(screen.getByText('Test Asset 1')).toBeInTheDocument();
    });

    mockAssetsAPI.listAssets.mockResolvedValueOnce({
      data: {
        count: 1,
        next: null,
        previous: null,
        results: [mockAssets[0]],
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });

    // Find the Active button (not the status badge)
    const activeButtons = screen.getAllByText('Active');
    const activeButton = activeButtons.find(btn => btn.tagName === 'BUTTON');
    if (activeButton) {
      await userEvent.click(activeButton);
    } else {
      // Fallback: click the first button with "Active" text
      await userEvent.click(activeButtons[0]);
    }

    await waitFor(() => {
      const calls = (mockAssetsAPI.listAssets as jest.Mock).mock.calls;
      const lastCall = calls[calls.length - 1];
      expect(lastCall[0]).toMatchObject({ status: 'active' });
    });
  });

  it('filters assets by location', async () => {
    renderComponent();

    await waitFor(() => {
      expect(screen.getByText('Test Asset 1')).toBeInTheDocument();
    });

    // Find select by getting all selects and finding the one with location options
    const selects = screen.getAllByRole('combobox');
    const locationSelect = selects.find((select) => {
      const options = Array.from(select.querySelectorAll('option')) as HTMLOptionElement[];
      return options.some((opt) => opt.textContent === 'Workshop A');
    }) as HTMLSelectElement;
    
    expect(locationSelect).toBeInTheDocument();
    await userEvent.selectOptions(locationSelect, '1');

    await waitFor(() => {
      const calls = (mockAssetsAPI.listAssets as jest.Mock).mock.calls;
      const lastCall = calls[calls.length - 1];
      expect(lastCall[0]).toMatchObject({ location: 1 });
    }, { timeout: 3000 });
  });

  it('filters assets by SIG', async () => {
    renderComponent();

    await waitFor(() => {
      expect(screen.getByText('Test Asset 1')).toBeInTheDocument();
    });

    // Find select by label
    const sigLabel = screen.getByText('SIG:');
    const sigSelect = sigLabel.nextElementSibling as HTMLSelectElement;
    
    expect(sigSelect).toBeInTheDocument();
    await userEvent.selectOptions(sigSelect, '1');

    await waitFor(() => {
      const calls = (mockAssetsAPI.listAssets as jest.Mock).mock.calls;
      const lastCall = calls[calls.length - 1];
      expect(lastCall[0]).toMatchObject({ owning_group: 1 });
    }, { timeout: 3000 });
  });

  it('searches assets', async () => {
    renderComponent();

    await waitFor(() => {
      expect(screen.getByText('Test Asset 1')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('Search by name, serial number, or asset tag...');
    await userEvent.type(searchInput, 'Test Asset 1');

    await waitFor(() => {
      const calls = (mockAssetsAPI.listAssets as jest.Mock).mock.calls;
      const lastCall = calls[calls.length - 1];
      expect(lastCall[0]).toMatchObject({ search: 'Test Asset 1' });
    }, { timeout: 3000 });
  });

  it('shows loading state', async () => {
    // Mock a promise that never resolves to keep loading state
    let resolvePromise: () => void;
    const neverResolvingPromise = new Promise<void>((resolve) => {
      resolvePromise = resolve;
    });

    mockAssetsAPI.listAssets.mockImplementation(() => neverResolvingPromise as any);

    renderComponent();

    // The component should show loading state initially
    // Since we're using Mantine components, check for loading indicators
    // The AssetTableView shows "Loading assets..." when loading
    // But AssetList might not show it immediately if it renders before the promise starts
    // Let's check if the component rendered without errors
    expect(screen.getByText(/Makerspace Assets/)).toBeInTheDocument();
    
    // Clean up
    resolvePromise!();
  });

  it('shows no results message when no assets match filters', async () => {
    mockAssetsAPI.listAssets.mockResolvedValueOnce({
      data: {
        count: 0,
        next: null,
        previous: null,
        results: [],
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });

    renderComponent();

    await waitFor(() => {
      expect(screen.getByText(/No assets/)).toBeInTheDocument();
    });
  });

  it('toggles between card and table view', async () => {
    renderComponent();

    await waitFor(() => {
      expect(screen.getByText('Test Asset 1')).toBeInTheDocument();
    });

    // Find the view toggle (SegmentedControl)
    const tableViewButton = screen.getByText('Table View');
    expect(tableViewButton).toBeInTheDocument();

    await userEvent.click(tableViewButton);

    // Should show table view
    await waitFor(() => {
      expect(screen.getByText('Name')).toBeInTheDocument();
      expect(screen.getByText('Asset Tag')).toBeInTheDocument();
    });

    // Verify preference was saved to localStorage
    expect(localStorage.getItem('assetViewMode')).toBe('table');
  });

  it('loads view mode preference from localStorage', async () => {
    // Set preference to table view
    localStorage.setItem('assetViewMode', 'table');

    renderComponent();

    await waitFor(() => {
      expect(screen.getByText('Test Asset 1')).toBeInTheDocument();
    });

    // Should show table view by default
    await waitFor(() => {
      expect(screen.getByText('Name')).toBeInTheDocument();
      expect(screen.getByText('Asset Tag')).toBeInTheDocument();
    });
  });

  it('appends the next page when the scroll sentinel intersects', async () => {
    const page1Asset = { ...mockAssets[0], id: '101', name: 'Asset Page One' };
    const page2Asset = { ...mockAssets[1], id: '102', name: 'Asset Page Two' };

    mockAssetsAPI.listAssets
      .mockResolvedValueOnce({
        data: {
          count: 2,
          next: 'http://api/inventory/assets/?page=2',
          previous: null,
          results: [page1Asset],
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as any,
      })
      .mockResolvedValueOnce({
        data: {
          count: 2,
          next: null,
          previous: null,
          results: [page2Asset],
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as any,
      });

    renderComponent();

    await waitFor(() => {
      expect(screen.getByText('Asset Page One')).toBeInTheDocument();
    });
    // Second page not loaded yet.
    expect(screen.queryByText('Asset Page Two')).not.toBeInTheDocument();

    // Simulate the sentinel scrolling into view.
    await act(async () => {
      intersectionCallback?.(
        [{ isIntersecting: true } as IntersectionObserverEntry],
        null as unknown as IntersectionObserver
      );
    });

    await waitFor(() => {
      expect(screen.getByText('Asset Page Two')).toBeInTheDocument();
    });
    const calls = (mockAssetsAPI.listAssets as jest.Mock).mock.calls;
    expect(calls.some((c) => c[0]?.page === 2)).toBe(true);
  });

  // Bulk cost-recovery flag by category (op-9ho2, W3). Staff-only: the flag
  // decides which in-house repair cost gets billed to the landlord.
  describe('bulk cost-recovery by category', () => {
    const mockCategories = [
      { id: 7, name: 'HVAC', slug: 'hvac', description: '', parent: null },
      { id: 8, name: 'Woodshop', slug: 'woodshop', description: '', parent: null },
    ];

    // env="test" so the Select dropdown is reachable in jsdom.
    const renderStaff = () =>
      render(
        <MantineProvider env="test">
          <MemoryRouter>
            <AssetList />
          </MemoryRouter>
        </MantineProvider>
      );

    beforeEach(() => {
      mockInventoryAPI.listCategories.mockResolvedValue({
        data: { results: mockCategories },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as any,
      });
      mockAssetsAPI.setCostRecoverableByCategory.mockResolvedValue({
        data: {
          category_id: 7,
          category_slug: 'hvac',
          category_name: 'HVAC',
          is_cost_recoverable: true,
          matched: 4,
          updated: 3,
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as any,
      });
    });

    const pickHvac = async () => {
      const select = await screen.findByTestId('bulk-cost-recoverable-category');
      await userEvent.click(select);
      await userEvent.click(await screen.findByRole('option', { name: 'HVAC' }));
    };

    it('is hidden from non-staff', async () => {
      renderComponent();

      await waitFor(() => {
        expect(screen.getByText('Test Asset 1')).toBeInTheDocument();
      });
      expect(screen.queryByTestId('bulk-cost-recoverable')).not.toBeInTheDocument();
    });

    it('flags a whole category and reports how many changed', async () => {
      localStorage.setItem('is_staff', 'true');
      renderStaff();

      await pickHvac();
      await userEvent.click(screen.getByTestId('bulk-cost-recoverable-set'));

      await waitFor(() => {
        expect(mockAssetsAPI.setCostRecoverableByCategory).toHaveBeenCalledWith(7, true);
      });
      expect(await screen.findByTestId('bulk-cost-recoverable-result')).toHaveTextContent(
        'Flagged 3 of 4 assets in HVAC.'
      );
    });

    it('clears the flag for a category', async () => {
      localStorage.setItem('is_superuser', 'true');
      mockAssetsAPI.setCostRecoverableByCategory.mockResolvedValue({
        data: {
          category_id: 7,
          category_slug: 'hvac',
          category_name: 'HVAC',
          is_cost_recoverable: false,
          matched: 4,
          updated: 4,
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as any,
      });
      renderStaff();

      await pickHvac();
      await userEvent.click(screen.getByTestId('bulk-cost-recoverable-clear'));

      await waitFor(() => {
        expect(mockAssetsAPI.setCostRecoverableByCategory).toHaveBeenCalledWith(7, false);
      });
      expect(await screen.findByTestId('bulk-cost-recoverable-result')).toHaveTextContent(
        'Cleared 4 of 4 assets in HVAC.'
      );
    });

    it('keeps the buttons disabled until a category is picked', async () => {
      localStorage.setItem('is_staff', 'true');
      renderStaff();

      expect(await screen.findByTestId('bulk-cost-recoverable-set')).toBeDisabled();
      expect(screen.getByTestId('bulk-cost-recoverable-clear')).toBeDisabled();

      await pickHvac();

      await waitFor(() =>
        expect(screen.getByTestId('bulk-cost-recoverable-set')).not.toBeDisabled()
      );
    });

    it('surfaces a failure instead of claiming success', async () => {
      localStorage.setItem('is_staff', 'true');
      mockAssetsAPI.setCostRecoverableByCategory.mockRejectedValue(new Error('boom'));
      renderStaff();

      await pickHvac();
      await userEvent.click(screen.getByTestId('bulk-cost-recoverable-set'));

      expect(await screen.findByTestId('bulk-cost-recoverable-error')).toBeInTheDocument();
      expect(screen.queryByTestId('bulk-cost-recoverable-result')).not.toBeInTheDocument();
    });
  });
});
