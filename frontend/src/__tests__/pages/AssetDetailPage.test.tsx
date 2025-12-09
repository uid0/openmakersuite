/**
 * Tests for AssetDetailPage component
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import AssetDetailPage from '../../pages/AssetDetailPage';
import { assetPartsAPI, assetsAPI } from '../../services/api';
import { Asset, AssetProblem } from '../../types';

jest.mock('../../services/api');
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useParams: () => ({ id: 'test-id' }),
  useNavigate: () => jest.fn(),
}));

const mockAssetsAPI = assetsAPI as jest.Mocked<typeof assetsAPI>;
const mockAssetPartsAPI = assetPartsAPI as jest.Mocked<typeof assetPartsAPI>;

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
});
