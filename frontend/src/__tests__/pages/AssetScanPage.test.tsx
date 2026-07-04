/**
 * Tests for AssetScanPage modal flows (confirmDelete / promptInput / showError).
 *
 * Focuses on verifying that the native alert/confirm/prompt popups have been
 * replaced with Mantine modals and notifications, per oms-13c AC-1..3.
 */
import { MantineProvider } from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import { Notifications } from '@mantine/notifications';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import AssetScanPage from '../../pages/AssetScanPage';
import * as api from '../../services/api';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

describe('AssetScanPage — modal flows', () => {
  const mockAsset = {
    id: 'asset-1',
    name: 'Test Asset',
    description: '',
    serial_number: '',
    asset_tag: '',
    inventory_item: null,
    inventory_item_name: null,
    manufacturer: null,
    manufacturer_name: null,
    display_manufacturer: null,
    date_received: null,
    amount_paid: null,
    is_donation: false,
    donor_name: '',
    acquisition_display: '',
    category: null,
    category_name: null,
    location: null,
    location_name: null,
    product_url: null,
    wiki_page_url: null,
    maintenance_plan: null,
    image: null,
    image_url: null,
    thumbnail_url: null,
    manual_pdf: null,
    manual_pdf_url: null,
    qr_code: null,
    qr_code_url: null,
    qr_code_scan_url: null,
    status: 'active',
    condition_notes: null,
    age_in_days: 0,
    is_active: true,
    report_only: false,
    notes: '',
    circuit: null,
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
    can_unlock: true,
    operational_status: 'available',
    parts: [],
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  };

  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    (api.assetsAPI.scanAsset as jest.Mock).mockResolvedValue({ data: mockAsset });
    (api.assetsAPI.getAssetChecklists as jest.Mock).mockResolvedValue({ data: [] });
    (api.assetsAPI.getMaintenanceItems as jest.Mock).mockResolvedValue({ data: [] });
  });

  const renderPage = () =>
    render(
      <MantineProvider>
        <ModalsProvider>
          <Notifications />
          <MemoryRouter initialEntries={['/scan/asset/asset-1']}>
            <Routes>
              <Route path="/scan/asset/:assetId" element={<AssetScanPage />} />
            </Routes>
          </MemoryRouter>
        </ModalsProvider>
      </MantineProvider>,
    );

  test('disable asset: clicking Disable opens confirm modal and calls API on confirm', async () => {
    localStorage.setItem('token', 'fake-token');
    (api.assetsAPI.disableAsset as jest.Mock).mockResolvedValue({ data: mockAsset });

    renderPage();

    const disableButton = await screen.findByRole('button', { name: /disable asset/i });
    fireEvent.click(disableButton);

    const confirmBtn = await screen.findByRole('button', { name: /^delete$/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(api.assetsAPI.disableAsset).toHaveBeenCalledWith('asset-1');
    });
  });

  test('disable asset: cancelling the confirm modal does not call the API', async () => {
    localStorage.setItem('token', 'fake-token');
    (api.assetsAPI.disableAsset as jest.Mock).mockResolvedValue({ data: mockAsset });

    renderPage();

    const disableButton = await screen.findByRole('button', { name: /disable asset/i });
    fireEvent.click(disableButton);

    const cancelBtn = await screen.findByRole('button', { name: /^cancel$/i });
    fireEvent.click(cancelBtn);

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /^cancel$/i })).not.toBeInTheDocument();
    });

    expect(api.assetsAPI.disableAsset).not.toHaveBeenCalled();
  });

  test('lock asset: clicking Lock opens confirm modal and calls API on confirm', async () => {
    localStorage.setItem('token', 'fake-token');
    (api.assetsAPI.lockAsset as jest.Mock).mockResolvedValue({ data: mockAsset });

    renderPage();

    const lockButton = await screen.findByRole('button', { name: /^lock asset$/i });
    fireEvent.click(lockButton);

    const confirmBtn = await screen.findByRole('button', { name: /^delete$/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(api.assetsAPI.lockAsset).toHaveBeenCalledWith('asset-1');
    });
  });

  test('unlock asset: locked asset shows Unlock button and confirm modal calls API on confirm', async () => {
    localStorage.setItem('token', 'fake-token');
    (api.assetsAPI.scanAsset as jest.Mock).mockResolvedValue({
      data: { ...mockAsset, is_locked: true },
    });
    (api.assetsAPI.unlockAsset as jest.Mock).mockResolvedValue({
      data: { ...mockAsset, is_locked: false },
    });

    renderPage();

    const unlockButton = await screen.findByRole('button', { name: /^unlock asset$/i });
    fireEvent.click(unlockButton);

    const confirmBtn = await screen.findByRole('button', { name: /^delete$/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(api.assetsAPI.unlockAsset).toHaveBeenCalledWith('asset-1');
    });
  });

  test('start checklist (anonymous): prompt modal captures the user name', async () => {
    (api.assetsAPI.getAssetChecklists as jest.Mock).mockResolvedValue({
      data: [
        {
          id: 'chk-1',
          name: 'Weekly Inspection',
          description: '',
          step_count: 3,
        },
      ],
    });
    (api.checklistsAPI.startChecklist as jest.Mock).mockResolvedValue({
      data: { id: 'completion-1' },
    });

    renderPage();

    const startButton = await screen.findByRole('button', { name: /weekly inspection/i });
    fireEvent.click(startButton);

    const input = await screen.findByRole('textbox');
    fireEvent.change(input, { target: { value: 'Alice' } });

    const submitBtn = screen.getByRole('button', { name: /^submit$/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(api.checklistsAPI.startChecklist).toHaveBeenCalledWith('chk-1', 'Alice');
    });
    expect(mockNavigate).toHaveBeenCalledWith('/checklist/chk-1/complete/completion-1');
  });

  test('start checklist (anonymous): cancelling the prompt modal does not start the checklist', async () => {
    (api.assetsAPI.getAssetChecklists as jest.Mock).mockResolvedValue({
      data: [
        {
          id: 'chk-1',
          name: 'Weekly Inspection',
          description: '',
          step_count: 3,
        },
      ],
    });
    (api.checklistsAPI.startChecklist as jest.Mock).mockResolvedValue({
      data: { id: 'completion-1' },
    });

    renderPage();

    const startButton = await screen.findByRole('button', { name: /weekly inspection/i });
    fireEvent.click(startButton);

    const cancelBtn = await screen.findByRole('button', { name: /^cancel$/i });
    fireEvent.click(cancelBtn);

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /^cancel$/i })).not.toBeInTheDocument();
    });

    expect(api.checklistsAPI.startChecklist).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  // --- R2 resilience (oms-sr1l4): asset scan journey ----------------------
  // The asset page is reached via the QR-encoded URL (/scan/asset/:assetId),
  // so it needs no in-page camera. These pin AC-19 loading/empty surfaces,
  // AC-16 offline/dependency-failure actionable states, and the AC-15
  // duplicate-submit guard on the report-problem form.

  test('AC-19: shows a loading state while the asset is fetched', async () => {
    (api.assetsAPI.scanAsset as jest.Mock).mockReturnValue(new Promise(() => {}));

    renderPage();

    expect(await screen.findByText(/loading asset details/i)).toBeInTheDocument();
  });

  test('AC-16: an offline asset load shows an actionable error with a way home', async () => {
    (api.assetsAPI.scanAsset as jest.Mock).mockRejectedValue(networkError());

    renderPage();

    expect(await screen.findByText(/failed to load asset/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /go home/i })).toBeInTheDocument();
  });

  test('AC-19: renders an empty "asset not found" state when the scan returns no asset', async () => {
    (api.assetsAPI.scanAsset as jest.Mock).mockResolvedValue({ data: null });

    renderPage();

    expect(await screen.findByText(/asset not found/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /go home/i })).toBeInTheDocument();
  });

  test('AC-16: a report-problem submitted offline surfaces an actionable error', async () => {
    localStorage.setItem('token', 'fake-token');
    (api.assetsAPI.reportProblem as jest.Mock).mockRejectedValue(networkError());

    renderPage();

    const textarea = await screen.findByPlaceholderText(/describe the problem/i);
    fireEvent.change(textarea, { target: { value: 'Belt is frayed' } });
    fireEvent.click(screen.getByRole('button', { name: /^report problem$/i }));

    expect(await screen.findByText(/failed to report problem/i)).toBeInTheDocument();
  });

  test('AC-15: a rapid double-tap on Report Problem only files one report', async () => {
    localStorage.setItem('token', 'fake-token');
    // Never resolves: holds the submitting state so the disabled guard shows.
    (api.assetsAPI.reportProblem as jest.Mock).mockReturnValue(new Promise(() => {}));

    renderPage();

    const textarea = await screen.findByPlaceholderText(/describe the problem/i);
    fireEvent.change(textarea, { target: { value: 'Belt is frayed' } });

    fireEvent.click(screen.getByRole('button', { name: /^report problem$/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /submitting/i })).toBeDisabled()
    );
    fireEvent.click(screen.getByRole('button', { name: /submitting/i }));

    expect(api.assetsAPI.reportProblem).toHaveBeenCalledTimes(1);
  });

  test('AC-16: an offline photo upload still saves the report and flags the photo', async () => {
    localStorage.setItem('token', 'fake-token');
    const createObjectURL = jest.fn().mockReturnValue('blob:preview');
    const revokeObjectURL = jest.fn();
    Object.defineProperty(window.URL, 'createObjectURL', {
      value: createObjectURL,
      configurable: true,
    });
    Object.defineProperty(window.URL, 'revokeObjectURL', {
      value: revokeObjectURL,
      configurable: true,
    });

    (api.assetsAPI.reportProblem as jest.Mock).mockResolvedValue({ data: { id: 'prob-1' } });
    (api.assetProblemsAPI.uploadPhoto as jest.Mock).mockRejectedValue(networkError());

    const { container } = renderPage();

    const textarea = await screen.findByPlaceholderText(/describe the problem/i);
    fireEvent.change(textarea, { target: { value: 'Cracked housing' } });

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['img'], 'broken.png', { type: 'image/png' });
    fireEvent.change(fileInput, { target: { files: [file] } });

    // The preview registers (Remove control appears) before we submit.
    await screen.findByRole('button', { name: /^remove$/i });
    fireEvent.click(screen.getByRole('button', { name: /^report problem$/i }));

    // The problem report is created even though the photo upload is offline…
    await waitFor(() =>
      expect(api.assetsAPI.reportProblem).toHaveBeenCalledWith('asset-1', 'Cracked housing')
    );
    await waitFor(() => expect(api.assetProblemsAPI.uploadPhoto).toHaveBeenCalled());
    // …and the offline photo is surfaced as a non-fatal, actionable failure.
    expect(await screen.findByText(/photo 1 failed to upload/i)).toBeInTheDocument();
  });

  // --- op-3ak: which-components-need-replace/fix multi-select ---------------

  const assetPart = {
    id: 'part-1',
    asset: 'asset-1',
    asset_name: 'Test Asset',
    asset_tag: '',
    part: 'p1',
    part_name: 'Drive Belt',
    part_sku: 'SKU1',
    quantity_needed: 2,
    is_required: true,
    maintenance_interval_days: null,
    last_replaced_at: null,
    days_since_replacement: null,
    needs_replacement: false,
    notes: '',
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  };

  test('renders an optional component multi-select from the asset parts', async () => {
    localStorage.setItem('token', 'fake-token');
    (api.assetsAPI.scanAsset as jest.Mock).mockResolvedValue({
      data: { ...mockAsset, parts: [assetPart] },
    });

    renderPage();

    expect(
      await screen.findByText(/which components need attention/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('checkbox', { name: /drive belt/i }),
    ).toBeInTheDocument();
  });

  test('sends checked part ids as part_ids when reporting a problem', async () => {
    localStorage.setItem('token', 'fake-token');
    (api.assetsAPI.scanAsset as jest.Mock).mockResolvedValue({
      data: { ...mockAsset, parts: [assetPart] },
    });
    (api.assetsAPI.reportProblem as jest.Mock).mockResolvedValue({
      data: { id: 'prob-1', affected_parts: [] },
    });

    renderPage();

    const textarea = await screen.findByPlaceholderText(/describe the problem/i);
    fireEvent.change(textarea, { target: { value: 'Belt is frayed' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /drive belt/i }));
    fireEvent.click(screen.getByRole('button', { name: /^report problem$/i }));

    await waitFor(() =>
      expect(api.assetsAPI.reportProblem).toHaveBeenCalledWith(
        'asset-1',
        'Belt is frayed',
        ['part-1'],
      ),
    );
  });

  test('reports description-only (no part_ids) when no component is checked', async () => {
    localStorage.setItem('token', 'fake-token');
    (api.assetsAPI.scanAsset as jest.Mock).mockResolvedValue({
      data: { ...mockAsset, parts: [assetPart] },
    });
    (api.assetsAPI.reportProblem as jest.Mock).mockResolvedValue({
      data: { id: 'prob-1', affected_parts: [] },
    });

    renderPage();

    const textarea = await screen.findByPlaceholderText(/describe the problem/i);
    fireEvent.change(textarea, { target: { value: 'Belt is frayed' } });
    fireEvent.click(screen.getByRole('button', { name: /^report problem$/i }));

    await waitFor(() =>
      expect(api.assetsAPI.reportProblem).toHaveBeenCalledWith('asset-1', 'Belt is frayed'),
    );
  });
});
