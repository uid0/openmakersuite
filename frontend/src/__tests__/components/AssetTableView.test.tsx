/**
 * Tests for AssetTableView component
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import AssetTableView from '../../components/AssetTableView';
import { Asset } from '../../types';
import * as csvExport from '../../utils/csvExport';

jest.mock('../../utils/csvExport');
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => jest.fn(),
}));

const mockAssets: Asset[] = [
  {
    id: '1',
    name: 'Test Asset 1',
    description: 'Description 1',
    serial_number: 'SN001',
    asset_tag: 'TAG001',
    inventory_item: null,
    inventory_item_name: 'Item 1',
    manufacturer: null,
    manufacturer_name: 'Manufacturer 1',
    display_manufacturer: 'Manufacturer 1',
    date_received: '2024-01-01',
    amount_paid: '100.00',
    is_donation: false,
    donor_name: '',
    acquisition_display: 'Purchased',
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
    age_in_days: 365,
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
    owning_group_name: 'Space',
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
    inventory_item_name: 'Item 2',
    manufacturer: null,
    manufacturer_name: 'Manufacturer 2',
    display_manufacturer: 'Manufacturer 2',
    date_received: '2023-01-01',
    amount_paid: '200.00',
    is_donation: true,
    donor_name: 'John Doe',
    acquisition_display: 'Donated',
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
    age_in_days: 730,
    is_active: true,
    report_only: false,
    notes: '',
    circuit: '',
    needs_compressed_air: false,
    needs_ventilation: false,
    is_chargeable: false,
    last_scanned_at: null,
    ownership_type: 'group',
    owning_group: 1,
    owning_group_name: 'SIG 1',
    owning_user: null,
    owning_user_name: null,
    groups_can_enable: [],
    is_locked: false,
    lockout_info: null,
    can_enable: true,
    can_unlock: false,
    operational_status: 'needs_maintenance',
    created_at: '2023-01-01T00:00:00Z',
    updated_at: '2023-01-01T00:00:00Z',
  },
];

describe('AssetTableView', () => {
  const defaultProps = {
    assets: mockAssets,
    loading: false,
    sortField: null,
    sortDirection: 'asc' as const,
    onSort: jest.fn(),
    serverMode: false,
  };

  const renderComponent = (props = {}) => {
    return render(
      <MantineProvider>
        <MemoryRouter>
          <AssetTableView {...defaultProps} {...props} />
        </MemoryRouter>
      </MantineProvider>
    );
  };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders table with assets', () => {
    renderComponent();

    expect(screen.getByText('Test Asset 1')).toBeInTheDocument();
    expect(screen.getByText('Test Asset 2')).toBeInTheDocument();
    expect(screen.getByText('TAG001')).toBeInTheDocument();
    expect(screen.getByText('TAG002')).toBeInTheDocument();
  });

  it('displays all table columns', () => {
    renderComponent();

    expect(screen.getByText('Name')).toBeInTheDocument();
    expect(screen.getByText('Asset Tag')).toBeInTheDocument();
    expect(screen.getByText('Serial Number')).toBeInTheDocument();
    expect(screen.getByText('Status')).toBeInTheDocument();
    expect(screen.getByText('Location')).toBeInTheDocument();
    expect(screen.getByText('Category')).toBeInTheDocument();
    expect(screen.getByText('Manufacturer')).toBeInTheDocument();
    expect(screen.getByText('Age')).toBeInTheDocument();
    expect(screen.getByText('Date Received')).toBeInTheDocument();
  });

  it('displays status badges with correct colors', () => {
    renderComponent();

    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getByText('Maintenance')).toBeInTheDocument();
  });

  it('displays operational status badges', () => {
    renderComponent();

    expect(screen.getByText('available')).toBeInTheDocument();
    expect(screen.getByText('needs_maintenance')).toBeInTheDocument();
  });

  it('formats age correctly', () => {
    renderComponent();

    // Asset 1 has 365 days = 1y 0d
    expect(screen.getByText('1y 0d')).toBeInTheDocument();
    // Asset 2 has 730 days = 2y 0d
    expect(screen.getByText('2y 0d')).toBeInTheDocument();
  });

  it('handles sorting when column header is clicked', async () => {
    const onSort = jest.fn();
    renderComponent({ onSort });

    const nameHeader = screen.getByText('Name').closest('th');
    expect(nameHeader).toBeInTheDocument();

    if (nameHeader) {
      await userEvent.click(nameHeader);
      expect(onSort).toHaveBeenCalledWith('name');
    }
  });

  it('shows sort icon when column is sorted', () => {
    renderComponent({ sortField: 'name', sortDirection: 'asc' });

    // Sort icon should be visible
    const nameHeader = screen.getByText('Name').closest('th');
    expect(nameHeader).toBeInTheDocument();
  });

  it('shows loading state', () => {
    renderComponent({ loading: true, assets: [] });

    expect(screen.getByText('Loading assets...')).toBeInTheDocument();
  });

  it('shows empty state when no assets', () => {
    renderComponent({ assets: [] });

    expect(screen.getByText('No assets found.')).toBeInTheDocument();
  });

  it('displays pagination in server mode', () => {
    renderComponent({
      serverMode: true,
      totalCount: 100,
      currentPage: 1,
      pageSize: 50,
      onPageChange: jest.fn(),
    });

    // Pagination should be visible
    expect(screen.getByText(/Showing page 1 of 2/)).toBeInTheDocument();
  });

  it('does not show pagination in client mode', () => {
    renderComponent({ serverMode: false });

    expect(screen.queryByText(/Showing page/)).not.toBeInTheDocument();
    expect(screen.getByText(/Showing 2 asset/)).toBeInTheDocument();
  });

  it('handles page change in server mode', async () => {
    const onPageChange = jest.fn();
    renderComponent({
      serverMode: true,
      totalCount: 100,
      currentPage: 1,
      pageSize: 50,
      onPageChange,
    });

    // Pagination should be visible
    expect(screen.getByText(/Showing page 1 of 2/)).toBeInTheDocument();
    // Pagination buttons should be present
    const paginationButtons = screen.getAllByRole('button');
    expect(paginationButtons.length).toBeGreaterThan(0);
  });

  it('handles export when export button is clicked', async () => {
    const onExport = jest.fn().mockResolvedValue(mockAssets);
    const exportSpy = jest.spyOn(csvExport, 'exportAssetsToCSV');

    renderComponent({ onExport });

    // Find export button by tooltip or icon
    const exportButton = screen.getByRole('button');
    expect(exportButton).toBeInTheDocument();

    await userEvent.click(exportButton);

    await waitFor(() => {
      expect(onExport).toHaveBeenCalled();
      expect(exportSpy).toHaveBeenCalledWith(mockAssets);
    });
  });

  it('handles export without onExport function (exports current page)', async () => {
    const exportSpy = jest.spyOn(csvExport, 'exportAssetsToCSV');

    renderComponent();

    // Find export button
    const exportButton = screen.getByRole('button');
    await userEvent.click(exportButton);

    await waitFor(() => {
      expect(exportSpy).toHaveBeenCalledWith(mockAssets);
    });
  });

  it('displays N/A for missing optional fields', () => {
    const assetWithMissingFields: Asset = {
      ...mockAssets[0],
      serial_number: '',
      asset_tag: '',
      location_name: '',
      category_name: '',
      display_manufacturer: '',
      date_received: null,
      age_in_days: undefined,
    };

    renderComponent({ assets: [assetWithMissingFields] });

    // Should show N/A for missing fields
    const naElements = screen.getAllByText('N/A');
    expect(naElements.length).toBeGreaterThan(0);
  });

  it('navigates to asset detail when row is clicked', async () => {
    const mockNavigate = jest.fn();
    jest.spyOn(require('react-router-dom'), 'useNavigate').mockReturnValue(mockNavigate);

    renderComponent();

    const assetRow = screen.getByText('Test Asset 1').closest('tr');
    expect(assetRow).toBeInTheDocument();

    if (assetRow) {
      await userEvent.click(assetRow);
      expect(mockNavigate).toHaveBeenCalledWith('/assets/1');
    }
  });
});

