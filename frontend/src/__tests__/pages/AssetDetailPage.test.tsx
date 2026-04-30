/**
 * Tests for AssetDetailPage component
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import AssetDetailPage from '../../pages/AssetDetailPage';
import {
  AssetLOTORequirements,
  assetPartsAPI,
  assetsAPI,
  lotoAPI,
  maintenanceAPI,
  workOrderAPI,
} from '../../services/api';
import { Asset, AssetProblem, MaintenanceItem, WorkOrder } from '../../types';

jest.mock('../../services/api');
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useParams: () => ({ id: 'test-id' }),
  useNavigate: () => jest.fn(),
}));

const mockAssetsAPI = assetsAPI as jest.Mocked<typeof assetsAPI>;
const mockAssetPartsAPI = assetPartsAPI as jest.Mocked<typeof assetPartsAPI>;
const mockMaintenanceAPI = maintenanceAPI as jest.Mocked<typeof maintenanceAPI>;
const mockWorkOrderAPI = workOrderAPI as jest.Mocked<typeof workOrderAPI>;
const mockLotoAPI = lotoAPI as jest.Mocked<typeof lotoAPI>;

describe('AssetDetailPage', () => {
  const mockAsset: Asset = {
    id: 'test-id',
    name: 'Test Asset',
    description: 'Test Description',
    serial_number: 'SN001',
    asset_tag: 'TAG001',
    inventory_item: null,
    inventory_item_name: 'Test Item',
    manufacturer: null,
    manufacturer_name: 'Test Manufacturer',
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
    product_url: 'https://example.com/product',
    wiki_page_url: 'https://example.com/wiki',
    maintenance_plan: 'Monthly maintenance required',
    image: null,
    image_url: 'https://example.com/image.jpg',
    thumbnail_url: null,
    manual_pdf: null,
    manual_pdf_url: 'https://example.com/manual.pdf',
    qr_code: null,
    qr_code_url: 'https://example.com/qr.png',
    qr_code_scan_url: 'https://example.com/scan/test-id',
    status: 'active',
    condition_notes: 'Good condition',
    age_in_days: 30,
    is_active: true,
    report_only: false,
    notes: 'Test notes',
    circuit: 'Circuit A',
    needs_compressed_air: true,
    needs_ventilation: false,
    is_chargeable: true,
    last_scanned_at: '2024-01-15T00:00:00Z',
    ownership_type: 'space',
    owning_group: null,
    owning_group_name: 'Logistics',
    owning_user: null,
    owning_user_name: null,
    groups_can_enable: [],
    is_locked: false,
    lockout_info: null,
    can_enable: true,
    can_unlock: true,
    operational_status: 'available',
    parts: [
      {
        id: 'part-1',
        asset: 'test-id',
        asset_name: 'Test Asset',
        asset_tag: 'TAG001',
        part: 'part-id',
        part_name: 'Test Part',
        part_sku: 'PART001',
        quantity_needed: 2,
        is_required: true,
        maintenance_interval_days: 90,
        last_replaced_at: '2024-01-01T00:00:00Z',
        days_since_replacement: 30,
        needs_replacement: false,
        notes: 'Part notes',
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      },
    ],
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  };

  const mockProblems: AssetProblem[] = [
    {
      id: '1',
      asset: 'test-id',
      asset_name: 'Test Asset',
      asset_tag: 'TAG001',
      reported_by: 'user1',
      description: 'Test problem',
      status: 'reported',
      resolution_notes: '',
      created_at: '2024-01-10T00:00:00Z',
      updated_at: '2024-01-10T00:00:00Z',
      resolved_at: null,
    },
  ];

  beforeEach(() => {
    jest.clearAllMocks();
    mockAssetsAPI.getAsset.mockResolvedValue({
      data: mockAsset,
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
    mockAssetsAPI.getAssetProblems.mockResolvedValue({
      data: mockProblems,
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
    mockAssetsAPI.getMaintenanceItems.mockResolvedValue({
      data: [],
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
    mockWorkOrderAPI.listWorkOrders.mockResolvedValue({
      data: { results: [] },
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    } as any);
    mockLotoAPI.getAssetRequirements.mockResolvedValue({
      data: {
        asset_id: 'test-id',
        asset_name: 'Test Asset',
        lockout_type: '',
        lockout_type_display: '',
        lockout_instructions: '',
        lockout_responsible: '',
        is_required: false,
        energy_sources: [],
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
    mockAssetsAPI.getTagUrl.mockImplementation(
      (id: string, size: 'standard' | 'large' = 'standard', download = false) => {
        const params = new URLSearchParams({ size });
        if (download) params.set('download', '1');
        return `http://test/api/inventory/assets/${id}/tag/?${params.toString()}`;
      }
    );
  });

  it('renders asset details', async () => {
    render(
      <MemoryRouter>
        <AssetDetailPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Test Asset')).toBeInTheDocument();
    });

    expect(screen.getByText('Test Description')).toBeInTheDocument();
    expect(screen.getByText('TAG001')).toBeInTheDocument();
    expect(screen.getByText('SN001')).toBeInTheDocument();
  });

  it('displays part replacement tracking', async () => {
    render(
      <MemoryRouter>
        <AssetDetailPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Part Replacement Tracking')).toBeInTheDocument();
    });

    // Check for part name in table
    const partCells = screen.getAllByText('Test Part');
    expect(partCells.length).toBeGreaterThan(0);
    // Check for quantity needed
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('displays problem history', async () => {
    render(
      <MemoryRouter>
        <AssetDetailPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Problem History')).toBeInTheDocument();
    });

    expect(screen.getByText('Test problem')).toBeInTheDocument();
    expect(screen.getByText(/Reported by: user1/)).toBeInTheDocument();
  });

  it('filters problems by status', async () => {
    render(
      <MemoryRouter>
        <AssetDetailPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Problem History')).toBeInTheDocument();
    });

    const statusSelect = screen.getByRole('combobox', { name: /filter by status/i });
    await userEvent.selectOptions(statusSelect, 'reported');

    expect(screen.getByText('Test problem')).toBeInTheDocument();
  });

  it('marks part as replaced', async () => {
    mockAssetPartsAPI.markReplaced.mockResolvedValue({
      data: {
        id: 'part-1',
        asset: 'test-id',
        asset_name: 'Test Asset',
        asset_tag: 'TAG001',
        part: 'part-id',
        part_name: 'Test Part',
        part_sku: 'PART001',
        quantity_needed: 2,
        is_required: true,
        maintenance_interval_days: 90,
        last_replaced_at: new Date().toISOString(),
        days_since_replacement: 0,
        needs_replacement: false,
        notes: '',
        created_at: '2024-01-01T00:00:00Z',
        updated_at: new Date().toISOString(),
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });

    render(
      <MemoryRouter>
        <AssetDetailPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Part Replacement Tracking')).toBeInTheDocument();
    });

    const markReplacedButtons = screen.getAllByText('Mark Replaced');
    await userEvent.click(markReplacedButtons[0]);

    await waitFor(() => {
      expect(mockAssetPartsAPI.markReplaced).toHaveBeenCalledWith('part-1');
    });
  });

  it('displays QR code when available', async () => {
    render(
      <MemoryRouter>
        <AssetDetailPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('QR Code')).toBeInTheDocument();
    });

    const qrImage = screen.getByAltText('QR Code');
    expect(qrImage).toHaveAttribute('src', 'https://example.com/qr.png');
  });

  it('generates QR code when not available', async () => {
    const assetWithoutQR = { ...mockAsset, qr_code_url: null };
    mockAssetsAPI.getAsset.mockResolvedValueOnce({
      data: assetWithoutQR,
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
    mockAssetsAPI.generateQR.mockResolvedValueOnce({
      data: {},
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });

    render(
      <MemoryRouter>
        <AssetDetailPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('QR code not generated yet.')).toBeInTheDocument();
    });

    const generateButton = screen.getByText('Generate QR Code');
    await userEvent.click(generateButton);

    await waitFor(() => {
      expect(mockAssetsAPI.generateQR).toHaveBeenCalledWith('test-id');
    });
  });

  it('hides asset tag section when not logged in', async () => {
    render(
      <MemoryRouter>
        <AssetDetailPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Test Asset')).toBeInTheDocument();
    });

    expect(screen.queryByTestId('asset-tag-section')).not.toBeInTheDocument();
  });

  describe('asset tag (logged in)', () => {
    beforeEach(() => {
      localStorage.setItem('token', 'fake-token');
    });

    afterEach(() => {
      localStorage.removeItem('token');
    });

    it('renders preview, size selector, and download link', async () => {
      render(
        <MemoryRouter>
          <AssetDetailPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByTestId('asset-tag-section')).toBeInTheDocument();
      });

      const preview = screen.getByAltText(/Asset tag/);
      expect(preview).toHaveAttribute(
        'src',
        expect.stringContaining('/inventory/assets/test-id/tag/?size=standard')
      );

      const downloadLink = screen.getByRole('link', { name: 'Download PNG' });
      expect(downloadLink).toHaveAttribute(
        'href',
        expect.stringContaining('size=standard&download=1')
      );

      const sizeSelect = screen.getByLabelText(/Size:/);
      await userEvent.selectOptions(sizeSelect, 'large');

      const updatedPreview = screen.getByAltText(/Asset tag/);
      expect(updatedPreview).toHaveAttribute(
        'src',
        expect.stringContaining('size=large')
      );
    });
  });

  it('shows loading state', () => {
    mockAssetsAPI.getAsset.mockImplementation(
      () =>
        new Promise(() => {
          // Never resolves
        })
    );

    render(
      <MemoryRouter>
        <AssetDetailPage />
      </MemoryRouter>
    );

    expect(screen.getByText('Loading asset details...')).toBeInTheDocument();
  });

  it('shows error state', async () => {
    mockAssetsAPI.getAsset.mockRejectedValueOnce(new Error('Failed to load'));

    render(
      <MemoryRouter>
        <AssetDetailPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Error:/)).toBeInTheDocument();
    });
  });

  describe('Maintenance section (single source)', () => {
    const scheduledItem: MaintenanceItem = {
      id: 'mi-1',
      asset: 'test-id',
      asset_name: 'Test Asset',
      asset_tag: 'TAG001',
      title: 'Quarterly belt inspection',
      description: '',
      instructions: '',
      estimated_time_minutes: 15,
      estimated_cost: '0',
      interval_days: 90,
      last_completed_at: null,
      is_active: true,
      is_overdue: false,
      days_overdue: null,
      next_due_at: '2025-12-01T00:00:00Z',
      materials: [],
      tasks: [],
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    };

    const recentWorkOrder: WorkOrder = {
      id: 'wo-1',
      short_id: 'wo-001',
      maintenance_item: 'mi-1',
      maintenance_item_title: 'Quarterly belt inspection',
      asset_name: 'Test Asset',
      asset_tag: 'TAG001',
      asset_id: 'test-id',
      status: 'completed',
      due_date: null,
      assigned_to: null,
      assigned_to_name: 'Alice',
      completed_by_name: 'Alice',
      completed_at: '2024-02-01T00:00:00Z',
      notes: '',
      is_overdue: false,
      task_completions: [],
      material_usage: [],
      photos: [],
      created_at: '2024-01-15T00:00:00Z',
      updated_at: '2024-02-01T00:00:00Z',
    };

    it('renders exactly one Maintenance section sourced from MaintenanceItem and WorkOrder', async () => {
      mockAssetsAPI.getMaintenanceItems.mockResolvedValueOnce({
        data: [scheduledItem],
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as any,
      });
      mockWorkOrderAPI.listWorkOrders.mockResolvedValueOnce({
        data: { results: [recentWorkOrder] },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as any,
      } as any);

      render(
        <MemoryRouter>
          <AssetDetailPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: 'Maintenance', level: 2 })).toBeInTheDocument();
      });

      expect(
        screen.queryByRole('heading', { name: 'Preventive Maintenance' })
      ).not.toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: 'Maintenance Log' })).not.toBeInTheDocument();

      expect(screen.getByRole('heading', { name: 'Scheduled', level: 3 })).toBeInTheDocument();
      expect(
        screen.getByRole('heading', { name: 'Recent Work Orders', level: 3 })
      ).toBeInTheDocument();

      expect(screen.getAllByText('Quarterly belt inspection').length).toBeGreaterThan(0);
      expect(screen.getByText(/Completed/)).toBeInTheDocument();
    });

    it('does not render legacy maintenance_plan or condition_notes on the detail page', async () => {
      render(
        <MemoryRouter>
          <AssetDetailPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: 'Maintenance', level: 2 })).toBeInTheDocument();
      });

      expect(screen.queryByText('Monthly maintenance required')).not.toBeInTheDocument();
      expect(screen.queryByText('Good condition')).not.toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: 'Maintenance Plan' })).not.toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: 'Condition Notes' })).not.toBeInTheDocument();
      expect(
        screen.queryByRole('heading', { name: 'Part Replacement History' })
      ).not.toBeInTheDocument();
    });
  });

  describe('Clone maintenance item', () => {
    const mockMaintenanceItem: MaintenanceItem = {
      id: 'mi-1',
      asset: 'test-id',
      asset_name: 'Test Asset',
      asset_tag: 'TAG001',
      title: 'Monthly inspection',
      description: '',
      instructions: '',
      estimated_time_minutes: 30,
      estimated_cost: '5.00',
      interval_days: 30,
      last_completed_at: null,
      is_active: true,
      is_overdue: false,
      days_overdue: null,
      next_due_at: null,
      materials: [],
      tasks: [],
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    };

    const otherAsset: Asset = {
      ...mockAsset,
      id: 'target-id',
      name: 'Target Asset',
      asset_tag: 'TAG002',
    };

    beforeEach(() => {
      localStorage.setItem('token', 'fake-token');
      mockAssetsAPI.getMaintenanceItems.mockResolvedValue({
        data: [mockMaintenanceItem],
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as any,
      });
      mockAssetsAPI.listAssets.mockResolvedValue({
        data: {
          count: 2,
          next: null,
          previous: null,
          results: [mockAsset, otherAsset],
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as any,
      });
    });

    afterEach(() => {
      localStorage.removeItem('token');
    });

    it('opens clone modal when Clone button is clicked', async () => {
      render(
        <MemoryRouter>
          <AssetDetailPage />
        </MemoryRouter>
      );

      const cloneButton = await screen.findByText('Clone to asset...');
      await userEvent.click(cloneButton);

      await waitFor(() => {
        expect(
          screen.getByText('Clone maintenance plan to another asset')
        ).toBeInTheDocument();
      });

      await waitFor(() => {
        expect(mockAssetsAPI.listAssets).toHaveBeenCalled();
      });
    });

    it('submits clone and shows success toast', async () => {
      mockMaintenanceAPI.cloneItem.mockResolvedValue({
        data: { ...mockMaintenanceItem, id: 'mi-cloned', asset: 'target-id' },
        status: 201,
        statusText: 'Created',
        headers: {},
        config: {} as any,
      });

      render(
        <MemoryRouter>
          <AssetDetailPage />
        </MemoryRouter>
      );

      const cloneButton = await screen.findByText('Clone to asset...');
      await userEvent.click(cloneButton);

      const select = await screen.findByLabelText('Target asset');
      await waitFor(() => {
        expect(screen.getByRole('option', { name: /Target Asset/ })).toBeInTheDocument();
      });
      await userEvent.selectOptions(select, 'target-id');

      const submit = screen.getByRole('button', { name: 'Clone' });
      await userEvent.click(submit);

      await waitFor(() => {
        expect(mockMaintenanceAPI.cloneItem).toHaveBeenCalledWith('mi-1', 'target-id');
      });
      await waitFor(() => {
        expect(screen.getByText(/Cloned "Monthly inspection"/)).toBeInTheDocument();
      });
    });
  });

  describe('LOTO section (oms-78j AC-4)', () => {
    it('shows "No LOTO required" when no lockout configured', async () => {
      render(
        <MemoryRouter>
          <AssetDetailPage />
        </MemoryRouter>
      );
      await waitFor(() => {
        expect(screen.getByText(/Lockout \/ Tagout/)).toBeInTheDocument();
      });
      expect(screen.getByTestId('asset-loto-empty')).toBeInTheDocument();
    });

    it('renders LOTO details and energy sources when required', async () => {
      const requirements: AssetLOTORequirements = {
        asset_id: 'test-id',
        asset_name: 'Test Asset',
        lockout_type: 'loto',
        lockout_type_display: 'LOTO (Lockout / Tagout)',
        lockout_instructions: 'Lock breaker, tag valve, verify zero energy.',
        lockout_responsible: 'Maintenance lead',
        is_required: true,
        energy_sources: [
          {
            id: 1,
            asset: 'test-id',
            asset_name: 'Test Asset',
            source_type: 'electrical',
            source_type_display: 'Electrical',
            magnitude: '240V',
            isolation_point: 'Panel A / 12',
            required_devices: [10],
            required_devices_detail: [
              {
                id: 10,
                device_type: 'padlock',
                device_type_display: 'Padlock',
                label: 'PAD-001',
                status: 'available',
              },
            ],
            notes: '',
            created_at: '2026-04-30T00:00:00Z',
            updated_at: '2026-04-30T00:00:00Z',
          },
        ],
      };
      mockLotoAPI.getAssetRequirements.mockResolvedValueOnce({
        data: requirements,
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as any,
      });

      render(
        <MemoryRouter>
          <AssetDetailPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('LOTO (Lockout / Tagout)')).toBeInTheDocument();
      });
      expect(screen.getByText('Maintenance lead')).toBeInTheDocument();
      expect(
        screen.getByText('Lock breaker, tag valve, verify zero energy.')
      ).toBeInTheDocument();
      expect(screen.getByText('Energy Sources')).toBeInTheDocument();
      expect(screen.getByText('Electrical')).toBeInTheDocument();
      expect(screen.getByText('Panel A / 12')).toBeInTheDocument();
      expect(screen.getByText('Padlock PAD-001')).toBeInTheDocument();
    });
  });
});
