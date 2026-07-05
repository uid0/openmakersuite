/**
 * Tests for AssetFormPage component
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AxiosError } from 'axios';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import AssetFormPage from '../../pages/AssetFormPage';
import { assetsAPI, inventoryAPI, sigAPI } from '../../services/api';
import { Asset, Category, InventoryItem, Location, SIG } from '../../types';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');

const mockUseParams = vi.fn(() => ({}));
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useParams: () => mockUseParams(),
  useNavigate: () => mockNavigate,
}));

const renderWithMantine = (component: React.ReactElement) => {
  return render(
    <MantineProvider env="test">
      {component}
    </MantineProvider>
  );
};

const mockAssetsAPI = assetsAPI as jest.Mocked<typeof assetsAPI>;
const mockInventoryAPI = inventoryAPI as jest.Mocked<typeof inventoryAPI>;
const mockSigAPI = sigAPI as jest.Mocked<typeof sigAPI>;

/** A 401 shaped like the backend's "session expired" rejection. */
const sessionExpiredError = (): AxiosError =>
  new AxiosError('Request failed with status code 401', 'ERR_BAD_REQUEST', undefined, {}, {
    status: 401,
    statusText: 'Unauthorized',
    data: { detail: 'Authentication credentials were not provided.' },
    headers: {},
    config: {} as never,
  } as never);

describe('AssetFormPage', () => {
  const mockCategories: Category[] = [
    { id: 1, name: 'Electronics', slug: 'electronics', parent: null, created_at: '', updated_at: '' },
    { id: 2, name: 'Tools', slug: 'tools', parent: null, created_at: '', updated_at: '' },
  ];

  const mockLocations: Location[] = [
    { id: 1, name: 'Workshop A', description: '', created_at: '', updated_at: '' },
    { id: 2, name: 'Workshop B', description: '', created_at: '', updated_at: '' },
  ];

  const mockInventoryItems: InventoryItem[] = [
    {
      id: 'item-1',
      name: 'Test Item',
      description: '',
      sku: 'ITEM001',
      current_stock: 10,
      minimum_stock: 5,
      reorder_quantity: 20,
      use_case_based_reorder: false,
      minimum_cases: null,
      reorder_cases: null,
      category: 1,
      category_name: 'Electronics',
      location: 1,
      location_name: 'Workshop A',
      shelf_position: '',
      is_hazardous: false,
      msds_url: '',
      nfpa_health_hazard: null,
      nfpa_fire_hazard: null,
      nfpa_instability_hazard: null,
      nfpa_special_hazards: '',
      ownership_type: 'space',
      is_active: true,
      notes: '',
      created_at: '',
      updated_at: '',
    },
  ];

  const mockSIGs: SIG[] = [
    { id: 1, name: 'SIG 1', member_count: 5, asset_count: 2, inventory_count: 3, admins: [], is_user_admin: false },
  ];

  beforeEach(() => {
    jest.clearAllMocks();
    mockInventoryAPI.listCategories.mockResolvedValue({
      data: { results: mockCategories },
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
    mockInventoryAPI.listItems.mockResolvedValue({
      data: { results: mockInventoryItems },
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

  it('renders create form', async () => {
    renderWithMantine(
      <MemoryRouter>
        <AssetFormPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByTestId('page-hero-title')).toBeInTheDocument();
    });

    expect(screen.getByLabelText(/Name/i)).toBeInTheDocument();
  });

  it('submits form with valid data', async () => {
    
    

    const mockCreatedAsset: Asset = {
      id: 'new-id',
      name: 'New Asset',
      description: 'New Description',
      serial_number: '',
      asset_tag: 'TAG001',
      inventory_item: null,
      inventory_item_name: '',
      manufacturer: null,
      manufacturer_name: '',
      display_manufacturer: '',
      date_received: null,
      amount_paid: '0.00',
      is_donation: false,
      donor_name: '',
      acquisition_display: '',
      category: null,
      category_name: '',
      location: null,
      location_name: '',
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
      age_in_days: 0,
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
      created_at: '',
      updated_at: '',
    };

    mockAssetsAPI.createAsset.mockResolvedValue({
      data: mockCreatedAsset,
      status: 201,
      statusText: 'Created',
      headers: {},
      config: {} as any,
    });

    renderWithMantine(
      <MemoryRouter>
        <AssetFormPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByLabelText(/Name/i)).toBeInTheDocument();
    });

    // Fill in required field (name is required) - use fireEvent like other form tests
    const nameInputs = screen.getAllByLabelText(/Name/i);
    expect(nameInputs.length).toBeGreaterThan(0);
    fireEvent.change(nameInputs[0], { target: { value: 'New Asset' } });

    // Wait for submit button to be available - try to find it by role first
    await waitFor(() => {
      const submitButtonByRole = screen.queryByRole('button', { name: /Create Asset/i });
      const submitButtonsByText = screen.queryAllByText('Create Asset');
      expect(submitButtonByRole || submitButtonsByText.length > 0).toBeTruthy();
    });

    // Find the submit button - try multiple methods
    let submitButton: HTMLElement | null = null;
    
    // Method 1: By role
    submitButton = screen.queryByRole('button', { name: /Create Asset/i });
    
    // Method 2: By text and button type
    if (!submitButton) {
      const submitButtons = screen.getAllByText('Create Asset');
      submitButton = submitButtons.find(btn => 
        btn.tagName === 'BUTTON' && (btn as HTMLButtonElement).type === 'submit'
      ) || submitButtons.find(btn => btn.tagName === 'BUTTON') || null;
    }
    
    // Method 3: Any button with "Create Asset" text
    if (!submitButton) {
      const allButtons = screen.getAllByRole('button');
      submitButton = allButtons.find(btn => btn.textContent?.includes('Create Asset')) || null;
    }
    
    // Verify button exists and click it
    expect(submitButton).toBeTruthy();
    if (submitButton) {
      fireEvent.click(submitButton);

      // Wait for form submission - API should be called if form is valid
      await waitFor(() => {
        expect(mockAssetsAPI.createAsset).toHaveBeenCalled();
      }, { timeout: 3000 });
    }
  });

  it('loads asset data in edit mode', async () => {
    mockUseParams.mockReturnValue({ id: 'test-id' });

    const mockAsset: Asset = {
      id: 'test-id',
      name: 'Test Asset',
      description: 'Test Description',
      serial_number: 'SN001',
      asset_tag: 'TAG001',
      inventory_item: null,
      inventory_item_name: '',
      manufacturer: null,
      manufacturer_name: '',
      display_manufacturer: '',
      date_received: '2024-01-01',
      amount_paid: '100.00',
      is_donation: false,
      donor_name: '',
      acquisition_display: '',
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
      created_at: '',
      updated_at: '',
    };

    mockAssetsAPI.getAsset.mockResolvedValue({
      data: mockAsset,
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });

    renderWithMantine(
      <MemoryRouter>
        <AssetFormPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByTestId('page-hero-title')).toHaveTextContent(/edit asset/i);
    });

    await waitFor(() => {
      expect(screen.getByDisplayValue('Test Asset')).toBeInTheDocument();
    });
  });

  it('shows loading state', () => {
    mockAssetsAPI.getAsset.mockImplementation(
      () =>
        new Promise(() => {
          // Never resolves
        })
    );

    mockUseParams.mockReturnValue({ id: 'test-id' });

    renderWithMantine(
      <MemoryRouter>
        <AssetFormPage />
      </MemoryRouter>
    );

    expect(screen.getByText(/Loading asset/)).toBeInTheDocument();
  });

  it('handles API responses with missing results property gracefully', async () => {
    // Ensure useParams returns an empty object (for create mode)
    mockUseParams.mockReturnValue({});

    // Mock API responses where results is missing or undefined
    // This simulates the bug where .map() would be called on undefined
    // The fix ensures arrays are always arrays (empty if results is missing)
    mockInventoryAPI.listCategories.mockResolvedValue({
      data: {} as any, // Missing results property
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
    mockInventoryAPI.listLocations.mockResolvedValue({
      data: { results: undefined } as any, // results is explicitly undefined
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
    mockInventoryAPI.listItems.mockResolvedValue({
      data: {} as any, // Missing results property
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
    mockSigAPI.listMySIGs.mockResolvedValue({
      data: { results: undefined } as any, // results is explicitly undefined
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });

    // Component should render without crashing (no error thrown)
    renderWithMantine(
      <MemoryRouter>
        <AssetFormPage />
      </MemoryRouter>
    );

    // Should render the form (not crash) - arrays should be empty, not undefined
    await waitFor(() => {
      expect(screen.getByTestId('page-hero-title')).toBeInTheDocument();
    });

    // Form fields should be accessible (arrays should be empty, not undefined)
    expect(screen.getByLabelText(/Name/i)).toBeInTheDocument();
    
    // No error message should be shown since API calls succeeded (just missing results)
    expect(screen.queryByText(/Failed to load form data/i)).not.toBeInTheDocument();
  });

  it('handles API errors gracefully without crashing on .map() calls', async () => {
    // Ensure useParams returns an empty object (for create mode)
    mockUseParams.mockReturnValue({});

    // Mock API calls that reject/fail
    mockInventoryAPI.listCategories.mockRejectedValue(new Error('Network error'));
    mockInventoryAPI.listLocations.mockRejectedValue(new Error('Network error'));
    mockInventoryAPI.listItems.mockRejectedValue(new Error('Network error'));
    mockSigAPI.listMySIGs.mockRejectedValue(new Error('Network error'));

    // Component should render without crashing
    renderWithMantine(
      <MemoryRouter>
        <AssetFormPage />
      </MemoryRouter>
    );

    // Should show error message
    await waitFor(() => {
      expect(screen.getByText(/Failed to load form data/i)).toBeInTheDocument();
    });

    // Should still render the form (not crash)
    await waitFor(() => {
      expect(screen.getByTestId('page-hero-title')).toBeInTheDocument();
    });

    // Form fields should be accessible (arrays should be empty, not undefined)
    expect(screen.getByLabelText(/Name/i)).toBeInTheDocument();
  });

  it('handles edit mode with missing API results', async () => {
    mockUseParams.mockReturnValue({ id: 'test-id' });

    // Mock API responses with missing results
    mockInventoryAPI.listCategories.mockResolvedValue({
      data: {} as any,
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
    mockInventoryAPI.listLocations.mockResolvedValue({
      data: { results: undefined } as any,
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
    mockInventoryAPI.listItems.mockResolvedValue({
      data: {} as any,
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
    mockSigAPI.listMySIGs.mockResolvedValue({
      data: { results: undefined } as any,
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });

    const mockAsset: Asset = {
      id: 'test-id',
      name: 'Test Asset',
      description: 'Test Description',
      serial_number: 'SN001',
      asset_tag: 'TAG001',
      inventory_item: null,
      inventory_item_name: '',
      manufacturer: null,
      manufacturer_name: '',
      display_manufacturer: '',
      date_received: '2024-01-01',
      amount_paid: '100.00',
      is_donation: false,
      donor_name: '',
      acquisition_display: '',
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
      created_at: '',
      updated_at: '',
    };

    mockAssetsAPI.getAsset.mockResolvedValue({
      data: mockAsset,
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });

    // Component should render without crashing
    renderWithMantine(
      <MemoryRouter>
        <AssetFormPage />
      </MemoryRouter>
    );

    // Should render the edit form (not crash) - arrays should be empty, not undefined
    await waitFor(() => {
      expect(screen.getByTestId('page-hero-title')).toHaveTextContent(/edit asset/i);
    });

    // Asset data should still be loaded
    await waitFor(() => {
      expect(screen.getByDisplayValue('Test Asset')).toBeInTheDocument();
    });

    // No error message should be shown since API calls succeeded (just missing results)
    expect(screen.queryByText(/Failed to load form data/i)).not.toBeInTheDocument();
  });

  describe('resilience (#457 R4)', () => {
    it('does not double-submit while a create is in flight (AC-15)', async () => {
      mockUseParams.mockReturnValue({});
      let resolveCreate: (value: unknown) => void = () => undefined;
      mockAssetsAPI.createAsset.mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveCreate = resolve;
          }) as never,
      );

      renderWithMantine(
        <MemoryRouter>
          <AssetFormPage />
        </MemoryRouter>,
      );

      const name = (await screen.findAllByLabelText(/Name/i))[0];
      fireEvent.change(name, { target: { value: 'Drill press' } });

      const submit = screen.getByRole('button', { name: /create asset/i });
      fireEvent.click(submit);

      await waitFor(() => {
        expect(submit).toHaveAttribute('data-loading', 'true');
      });

      // A second click while the create is pending is ignored.
      fireEvent.click(submit);
      expect(mockAssetsAPI.createAsset).toHaveBeenCalledTimes(1);

      resolveCreate({
        data: { id: 'new-id' },
        status: 201,
        statusText: 'Created',
        headers: {},
        config: {} as never,
      });
      await waitFor(() => {
        expect(mockNavigate).toHaveBeenCalled();
      });
    });

    it('keeps the form and re-enables Save when the create fails offline (AC-19)', async () => {
      mockUseParams.mockReturnValue({});
      mockAssetsAPI.createAsset.mockRejectedValue(networkError());

      renderWithMantine(
        <MemoryRouter>
          <AssetFormPage />
        </MemoryRouter>,
      );

      const name = (await screen.findAllByLabelText(/Name/i))[0] as HTMLInputElement;
      fireEvent.change(name, { target: { value: 'Drill press' } });

      fireEvent.click(screen.getByRole('button', { name: /create asset/i }));

      await waitFor(() => {
        expect(mockAssetsAPI.createAsset).toHaveBeenCalledTimes(1);
      });

      // The form (which carries the photo upload) is preserved, an actionable
      // error is shown, and the button re-enables so the user can retry.
      expect(await screen.findByText(/failed to save asset/i)).toBeInTheDocument();
      expect((screen.getAllByLabelText(/Name/i)[0] as HTMLInputElement).value).toBe(
        'Drill press',
      );
      await waitFor(() => {
        expect(
          screen.getByRole('button', { name: /create asset/i }),
        ).not.toBeDisabled();
      });
    });

    it('preserves the form across a session expiry (401) on save (AC-17)', async () => {
      mockUseParams.mockReturnValue({});
      mockAssetsAPI.createAsset.mockRejectedValue(sessionExpiredError());

      renderWithMantine(
        <MemoryRouter>
          <AssetFormPage />
        </MemoryRouter>,
      );

      const name = (await screen.findAllByLabelText(/Name/i))[0] as HTMLInputElement;
      fireEvent.change(name, { target: { value: 'Lathe' } });

      fireEvent.click(screen.getByRole('button', { name: /create asset/i }));

      await waitFor(() => {
        expect(mockAssetsAPI.createAsset).toHaveBeenCalledTimes(1);
      });

      // After a session expiry the entered asset details are not lost and the
      // user can retry the save once they sign back in.
      expect((screen.getAllByLabelText(/Name/i)[0] as HTMLInputElement).value).toBe(
        'Lathe',
      );
      await waitFor(() => {
        expect(
          screen.getByRole('button', { name: /create asset/i }),
        ).not.toBeDisabled();
      });
    });
  });
});
