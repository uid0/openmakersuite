/**
 * Tests for InventoryListPage component
 */
import { MantineProvider } from '@mantine/core';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import SessionExpiredBanner, {
  consumePendingReturnTo,
} from '../../components/SessionExpiredBanner';
import InventoryListPage from '../../pages/InventoryListPage';
import * as api from '../../services/api';
import { showError, showInfo, showSuccess } from '../../utils/dialogs';

// Mock the API
vi.mock('../../services/api');
vi.mock('../../utils/csvExport', async () => ({
  exportInventoryItemsToCSV: jest.fn(),
}));

vi.mock('../../utils/dialogs', async () => ({
  showError: jest.fn(),
  showInfo: jest.fn(),
  showSuccess: jest.fn(),
}));

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

// Capture the IntersectionObserver callback so tests can simulate the
// infinite-scroll sentinel coming into view.
let intersectionCallback: IntersectionObserverCallback | null = null;

describe('InventoryListPage', () => {
  const mockItems = [
    {
      id: '1',
      name: 'Test Item 1',
      sku: 'TEST-001',
      category: 1,
      category_name: 'Tools',
      location: 'Shelf A',
      current_stock: 10,
      minimum_stock: 5,
      reorder_quantity: 20,
      // A NUMBER on the wire — a property-backed `ReadOnlyField` (op-9m2v).
      unit_cost: 15.99,
      supplier_name: 'Test Supplier',
      needs_reorder: false,
      has_pending_reorder: false,
      is_active: true,
      is_retired: false,
      retired_at: null,
      description: 'Test description',
      image: null,
      thumbnail: null,
      qr_code: null,
      use_case_based_reorder: false,
      minimum_cases: 0,
      reorder_cases: 0,
      current_cases: 0,
      supplier: null,
      supplier_sku: '',
      supplier_url: '',
      average_lead_time: 7,
      notes: '',
      total_value: '159.90',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
      ownership_type: 'space' as const,
      owning_user: null,
      owning_group: null,
      reorder_status: '',
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
    },
    {
      id: '2',
      name: 'Low Stock Item',
      sku: 'LOW-001',
      category: 1,
      category_name: 'Tools',
      location: 'Shelf B',
      current_stock: 2,
      minimum_stock: 5,
      reorder_quantity: 20,
      unit_cost: 10.99,
      supplier_name: 'Test Supplier',
      needs_reorder: true,
      has_pending_reorder: false,
      is_active: true,
      is_retired: false,
      retired_at: null,
      description: 'Low stock item',
      image: null,
      thumbnail: null,
      qr_code: null,
      use_case_based_reorder: false,
      minimum_cases: 0,
      reorder_cases: 0,
      current_cases: 0,
      supplier: null,
      supplier_sku: '',
      supplier_url: '',
      average_lead_time: 7,
      notes: '',
      total_value: '21.98',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
      ownership_type: 'space' as const,
      owning_user: null,
      owning_group: null,
      reorder_status: '',
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
    },
  ];

  const mockCategories = [
    { id: 1, name: 'Tools', slug: 'tools', description: 'Tools category', parent: null },
  ];

  const mockLocations = [
    { id: 1, name: 'Shelf A', description: 'Shelf A location', is_active: true },
    { id: 2, name: 'Shelf B', description: 'Shelf B location', is_active: true },
  ];

  // A full page of results: no further pages to fetch.
  const fullPage = (results: typeof mockItems) => ({
    data: { count: results.length, next: null, previous: null, results },
  });

  beforeEach(() => {
    jest.clearAllMocks();

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

    (api.inventoryAPI.listItems as jest.Mock).mockResolvedValue(fullPage(mockItems));
    (api.inventoryAPI.listCategories as jest.Mock).mockResolvedValue({
      data: { results: mockCategories },
    });
    (api.inventoryAPI.listLocations as jest.Mock).mockResolvedValue({
      data: { results: mockLocations },
    });
  });

  const renderPage = () => {
    return render(
      <MantineProvider env="test">
        <MemoryRouter>
          <InventoryListPage />
        </MemoryRouter>
      </MantineProvider>
    );
  };

  it('renders loading state initially', async () => {
    (api.inventoryAPI.listItems as jest.Mock).mockReturnValue(new Promise(() => {}));
    renderPage();

    expect(screen.getByText(/Loading inventory/)).toBeInTheDocument();
  });

  it('displays inventory items after loading', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
      expect(screen.getByText('Low Stock Item')).toBeInTheDocument();
    });
  });

  it('requests page 1 with default ordering on first load', async () => {
    renderPage();

    await waitFor(() => {
      expect(api.inventoryAPI.listItems).toHaveBeenCalledWith(
        expect.objectContaining({ page: 1, ordering: 'name' })
      );
    });
  });

  it('requests server-side search filtering (debounced)', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });
    (api.inventoryAPI.listItems as jest.Mock).mockClear();

    const searchInput = screen.getByPlaceholderText(/Search by name/);
    fireEvent.change(searchInput, { target: { value: 'Low' } });

    await waitFor(() => {
      expect(api.inventoryAPI.listItems).toHaveBeenCalledWith(
        expect.objectContaining({ search: 'Low', page: 1 })
      );
    });
  });

  it('requests server-side category filtering', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });
    (api.inventoryAPI.listItems as jest.Mock).mockClear();

    const categorySelect = screen.getByPlaceholderText('All Categories');
    fireEvent.click(categorySelect);
    const option = await screen.findByRole('option', { name: 'Tools' });
    fireEvent.click(option);

    await waitFor(() => {
      expect(api.inventoryAPI.listItems).toHaveBeenCalledWith(
        expect.objectContaining({ category: 1, page: 1 })
      );
    });
  });

  it('requests server-side low-stock filtering', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });
    (api.inventoryAPI.listItems as jest.Mock).mockClear();

    const stockSelect = screen.getByPlaceholderText('Stock Status');
    fireEvent.click(stockSelect);
    const option = await screen.findByRole('option', { name: 'Low Stock' });
    fireEvent.click(option);

    await waitFor(() => {
      expect(api.inventoryAPI.listItems).toHaveBeenCalledWith(
        expect.objectContaining({ low_stock: true, page: 1 })
      );
    });
  });

  it('requests include_retired when the Show Retired filter is selected', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });
    (api.inventoryAPI.listItems as jest.Mock).mockClear();

    const retiredSelect = screen.getByPlaceholderText('Retired');
    fireEvent.click(retiredSelect);
    const option = await screen.findByRole('option', { name: 'Show Retired' });
    fireEvent.click(option);

    await waitFor(() => {
      expect(api.inventoryAPI.listItems).toHaveBeenCalledWith(
        expect.objectContaining({ include_retired: true, page: 1 })
      );
    });
  });

  it('renders a Retired badge for retired items', async () => {
    (api.inventoryAPI.listItems as jest.Mock).mockResolvedValue(
      fullPage([{ ...mockItems[0], is_retired: true }])
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });
    expect(screen.getByText('Retired')).toBeInTheDocument();
  });

  it('does not render a Retired badge for non-retired items', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });
    // Neither seed item is retired (is_retired is absent/falsy).
    expect(screen.queryByText('Retired')).not.toBeInTheDocument();
  });

  it('requests server-side sorting when a column header is clicked', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });
    (api.inventoryAPI.listItems as jest.Mock).mockClear();

    fireEvent.click(screen.getByText('SKU'));

    await waitFor(() => {
      expect(api.inventoryAPI.listItems).toHaveBeenCalledWith(
        expect.objectContaining({ ordering: 'sku', page: 1 })
      );
    });
  });

  it('loads the next page when the scroll sentinel intersects', async () => {
    (api.inventoryAPI.listItems as jest.Mock)
      .mockResolvedValueOnce({
        data: {
          count: 2,
          next: 'http://test/api/inventory/items/?page=2',
          previous: null,
          results: [mockItems[0]],
        },
      })
      .mockResolvedValueOnce({
        data: { count: 2, next: null, previous: null, results: [mockItems[1]] },
      });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });
    // Second page not loaded yet.
    expect(screen.queryByText('Low Stock Item')).not.toBeInTheDocument();

    // Simulate the sentinel scrolling into view.
    await act(async () => {
      intersectionCallback?.(
        [{ isIntersecting: true } as IntersectionObserverEntry],
        null as unknown as IntersectionObserver
      );
    });

    await waitFor(() => {
      expect(screen.getByText('Low Stock Item')).toBeInTheDocument();
    });
    expect(api.inventoryAPI.listItems).toHaveBeenCalledWith(expect.objectContaining({ page: 2 }));
  });

  it('handles item selection', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });

    const checkboxes = screen.getAllByRole('checkbox');
    // First checkbox is "select all", second is for first item
    fireEvent.click(checkboxes[1]);

    expect(screen.getByText(/1 item\(s\) selected/)).toBeInTheDocument();
  });

  it('navigates to item detail on row click', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });

    const itemRow = screen.getByText('Test Item 1').closest('tr');
    if (itemRow) {
      fireEvent.click(itemRow);
      expect(mockNavigate).toHaveBeenCalledWith('/inventory/items/1');
    }
  });

  it('navigates to create page when clicking add button', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Add new item')).toBeInTheDocument();
    });

    const addButton = screen.getByText('Add new item');
    fireEvent.click(addButton);

    expect(mockNavigate).toHaveBeenCalledWith('/inventory/items/new');
  });

  it('shows error notification when bulk QR generation fails', async () => {
    (api.inventoryAPI.generateQR as jest.Mock).mockRejectedValue(new Error('boom'));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });

    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[1]);

    const qrButton = await screen.findByText('Generate QR Codes');
    fireEvent.click(qrButton);

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith('Failed to generate QR codes. Please try again.');
    });
  });

  it('shows info notification when generating QR with no items selected', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });

    // Bulk QR button is only visible when items are selected, so with no
    // selection there is nothing to fire an info notification yet.
    expect(showInfo).not.toHaveBeenCalled();
  });

  it('shows success notification after bulk QR generation', async () => {
    (api.inventoryAPI.generateQR as jest.Mock).mockResolvedValue({});

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });

    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[1]);

    const qrButton = await screen.findByText('Generate QR Codes');
    fireEvent.click(qrButton);

    await waitFor(() => {
      expect(showSuccess).toHaveBeenCalledWith('Successfully generated QR codes for 1 items.');
    });
  });

  it('updates stock inline and patches the row in place', async () => {
    (api.inventoryAPI.updateStock as jest.Mock).mockResolvedValue({
      data: { ...mockItems[0], current_stock: 15 },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });

    // Click the first item's stock value to open the inline editor.
    fireEvent.click(screen.getByText('10'));

    // Editor reveals Save/Cancel and a number input pre-filled with the value.
    await screen.findByText('Save');
    const numberInput = screen.getByDisplayValue('10');
    fireEvent.change(numberInput, { target: { value: '15' } });
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => {
      expect(api.inventoryAPI.updateStock).toHaveBeenCalledWith('1', 15);
    });
    // Row reflects the new value without a full reload.
    await waitFor(() => {
      expect(screen.getByText('15')).toBeInTheDocument();
    });
  });

  // ---- #457 R3 resilience ----

  it('shows the empty state when no items match the filters', async () => {
    (api.inventoryAPI.listItems as jest.Mock).mockResolvedValue(fullPage([]));

    renderPage();

    expect(
      await screen.findByText('No items found matching your filters.')
    ).toBeInTheDocument();
  });

  it('degrades to the empty state without crashing when the load is forbidden (403)', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    (api.inventoryAPI.listItems as jest.Mock).mockRejectedValue({
      response: { status: 403, data: { detail: 'You do not have permission.' } },
    });

    renderPage();

    // The list has no error banner; a forbidden load logs and leaves it empty.
    expect(
      await screen.findByText('No items found matching your filters.')
    ).toBeInTheDocument();
    // The page chrome is still interactive — this is graceful, not a crash.
    expect(screen.getByPlaceholderText(/Search by name/)).toBeInTheDocument();
    consoleError.mockRestore();
  });

  it('surfaces an error and keeps the inline editor open when a stock save fails', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    (api.inventoryAPI.updateStock as jest.Mock).mockRejectedValue(new Error('boom'));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('10'));
    await screen.findByText('Save');
    fireEvent.change(screen.getByDisplayValue('10'), { target: { value: '15' } });
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith('Failed to update stock. Please try again.');
    });
    // The editor stays open with the entered value so the user can retry.
    expect(screen.getByText('Save')).toBeInTheDocument();
    expect(screen.getByDisplayValue('15')).toBeInTheDocument();
    consoleError.mockRestore();
  });

  it('shows the re-login banner with a return path on session expiry, keeping the list', async () => {
    sessionStorage.clear();
    render(
      <MantineProvider env="test">
        <MemoryRouter>
          <SessionExpiredBanner />
          <InventoryListPage />
        </MemoryRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });

    // The API client raises this on a 401 once token refresh fails.
    act(() => {
      window.dispatchEvent(
        new CustomEvent('oms:session-expired', {
          detail: { pathname: '/inventory/items' },
        })
      );
    });

    expect(screen.getByTestId('session-expired-banner')).toBeInTheDocument();
    expect(screen.getByText(/where you left off/i)).toBeInTheDocument();
    expect(consumePendingReturnTo()).toBe('/inventory/items');
    // The list survived the interruption.
    expect(screen.getByText('Test Item 1')).toBeInTheDocument();
  });

  /**
   * The table's Cost column (op-9m2v). `item.unit_cost ? ... : '-'` read the
   * number `0` a donated item sends as "no price recorded" and printed the
   * dash this table reserves for a genuinely absent one.
   */
  describe('the Cost column', () => {
    const renderPriced = async (unitCost: number | null) => {
      (api.inventoryAPI.listItems as jest.Mock).mockResolvedValue(
        fullPage([{ ...mockItems[0], unit_cost: unitCost }] as typeof mockItems)
      );
      renderPage();
      await screen.findByText('Test Item 1');
    };

    const costCell = () =>
      screen.getByText('Test Item 1').closest('tr')?.querySelectorAll('td')[
        Array.from(
          screen.getByText('Test Item 1').closest('tr')!.querySelectorAll('td')
        ).findIndex((td) => /^\$|^-$/.test(td.textContent ?? ''))
      ] as HTMLElement;

    it('BEFORE/AFTER: prices a donated item at $0.00 rather than dashing it', async () => {
      await renderPriced(0);

      expect(costCell()).toHaveTextContent('$0.00');
    });

    it('CONTROL: an item nobody priced still shows the dash', async () => {
      await renderPriced(null);

      expect(costCell()).toHaveTextContent('-');
      expect(costCell()).not.toHaveTextContent('$');
    });

    it('CONTROL: an ordinary price is unchanged', async () => {
      await renderPriced(15.99);

      expect(costCell()).toHaveTextContent('$15.99');
    });
  });

  describe('a caller the server withheld the vendor block from', () => {
    /**
     * REGRESSION (op-anonymous-read-posture). `/inventory/items` is not behind
     * RequireAuth and its list endpoint is AllowAny, so a logged-out visitor
     * reaches this table — and their rows carry no `unit_cost` key at all.
     * `item.unit_cost != null ? ... : '-'` therefore printed '-' in every row,
     * which in THIS table means "no price recorded": a claim about the ITEM
     * where the truth is a fact about the READER. The three CONTROL cases just
     * above are what make that dash unusable for anything else.
     *
     * `csvExport` already drops the same column for the same audience ("an
     * absent COLUMN cannot be misread as an empty VALUE"), so the screen and
     * the file exported from it have to agree.
     */
    const anonymousRows = () =>
      mockItems.map((item) => {
        const row: Record<string, unknown> = { ...item, vendor_data_withheld: true };
        for (const key of ['unit_cost', 'supplier_name', 'supplier_sku', 'supplier_url', 'total_value']) {
          delete row[key];
        }
        return row;
      });

    it('drops the Unit Cost column rather than dashing every row', async () => {
      (api.inventoryAPI.listItems as jest.Mock).mockResolvedValue(
        fullPage(anonymousRows() as unknown as typeof mockItems)
      );
      renderPage();

      await screen.findByText('Test Item 1');

      expect(screen.queryByRole('columnheader', { name: 'Unit Cost' })).not.toBeInTheDocument();
      // ...and the row loses the cell with it, so the remaining columns do not
      // slide one place left under their own headers.
      const headers = screen.getAllByRole('columnheader').length;
      const cells = screen.getByText('Test Item 1').closest('tr')!.querySelectorAll('td').length;
      expect(cells).toBe(headers);
    });

    it('CONTROL: a signed-in caller still gets the column', async () => {
      renderPage();

      await screen.findByText('Test Item 1');

      expect(screen.getByRole('columnheader', { name: 'Unit Cost' })).toBeInTheDocument();
      expect(screen.getByText('$15.99')).toBeInTheDocument();
    });
  });
});
