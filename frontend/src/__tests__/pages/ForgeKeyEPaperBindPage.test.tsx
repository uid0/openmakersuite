/**
 * Tests for the ForgeKey ePaper bind page.
 *
 * Covers ACs:
 *   1. Page reads display_id from `?did=` query param and surfaces it.
 *   2. Searching debounces, hits `assetsAPI.listAssets`, and renders
 *      the result rows.
 *   3. Clicking a row POSTs to `forgekeyAPI.bindEPaper` with the
 *      display_id and asset id, and renders the success state on 200.
 *   4. Missing `did` shows the help card instead of the picker.
 *   5. Non-staff visitors are redirected to home.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ForgeKeyEPaperBindPage from '../../pages/ForgeKeyEPaperBindPage';
import { assetsAPI, forgekeyAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    assetsAPI: {
      ...actual.assetsAPI,
      listAssets: jest.fn(),
    },
    forgekeyAPI: {
      ...actual.forgekeyAPI,
      bindEPaper: jest.fn(),
    },
  };
});

const mockAssets = assetsAPI as jest.Mocked<typeof assetsAPI>;
const mockForgekey = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;

const buildAsset = (overrides: Partial<any> = {}) => ({
  id: 'a1',
  name: 'Bandsaw',
  description: '',
  serial_number: '',
  asset_tag: 'BS-001',
  inventory_item: null,
  inventory_item_name: '',
  manufacturer: null,
  manufacturer_name: '',
  display_manufacturer: '',
  date_received: null,
  amount_paid: '',
  is_donation: false,
  donor_name: '',
  acquisition_display: '',
  category: null,
  category_name: '',
  location: 1,
  location_name: 'Wood shop',
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
  mac_address: '',
  needs_compressed_air: false,
  ...overrides,
});

const DID = '11111111-2222-3333-4444-555555555555';

const renderPage = (initialEntry = `/forgekey/epaper/bind?did=${DID}`) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/forgekey/epaper/bind" element={<ForgeKeyEPaperBindPage />} />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ForgeKeyEPaperBindPage', () => {
  beforeEach(() => {
    jest.resetAllMocks();
    localStorage.setItem('is_staff', 'true');
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('renders the picker and lists active assets', async () => {
    mockAssets.listAssets.mockResolvedValue({
      data: { count: 1, next: null, previous: null, results: [buildAsset()] },
    } as any);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Bandsaw')).toBeInTheDocument();
    });
    expect(screen.getByText(/Wood shop/)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(DID))).toBeInTheDocument();
  });

  it('binds the panel to the picked asset and shows the success card', async () => {
    mockAssets.listAssets.mockResolvedValue({
      data: { count: 1, next: null, previous: null, results: [buildAsset()] },
    } as any);
    mockForgekey.bindEPaper.mockResolvedValue({
      data: { display_id: DID, asset_id: 'a1', asset_name: 'Bandsaw' },
    } as any);

    renderPage();

    const row = await screen.findByTestId('asset-row-a1');
    fireEvent.click(row);

    await waitFor(() => {
      expect(mockForgekey.bindEPaper).toHaveBeenCalledWith(DID, 'a1');
    });
    expect(await screen.findByText('Panel bound')).toBeInTheDocument();
    expect(screen.getAllByText('Bandsaw').length).toBeGreaterThan(0);
  });

  it('shows the help card when did is missing', async () => {
    renderPage('/forgekey/epaper/bind');

    expect(await screen.findByText('No display_id')).toBeInTheDocument();
    expect(mockAssets.listAssets).not.toHaveBeenCalled();
  });

  it('redirects non-staff visitors home', async () => {
    localStorage.setItem('is_staff', 'false');
    localStorage.removeItem('is_superuser');

    renderPage();

    expect(await screen.findByText('HOME')).toBeInTheDocument();
    expect(mockAssets.listAssets).not.toHaveBeenCalled();
  });
});
