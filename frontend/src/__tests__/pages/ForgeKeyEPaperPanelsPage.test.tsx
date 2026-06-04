/**
 * Tests for the ForgeKey e-paper panels management page.
 *
 * Covers: non-staff redirect, the panel table (binding + battery + status),
 * and the retire/reactivate toggle.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ForgeKeyEPaperPanelsPage from '../../pages/ForgeKeyEPaperPanelsPage';
import { assetsAPI, forgekeyAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    forgekeyAPI: {
      listEPaperDisplays: jest.fn(),
      setEPaperActive: jest.fn(),
      setEPaperRotation: jest.fn(),
      bindEPaper: jest.fn(),
    },
    assetsAPI: { ...(actual as any).assetsAPI, listAssets: jest.fn() },
  };
});

const mockApi = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;
const mockAssets = assetsAPI as jest.Mocked<typeof assetsAPI>;

const buildPanel = (overrides: Partial<any> = {}) => ({
  id: 'p1',
  device: null,
  device_mac_address: null,
  asset: 'a1',
  asset_name: 'Lathe',
  asset_tag: 'TAG-1',
  battery_percent: 8,
  is_low_battery: true,
  last_battery_at: null,
  last_image_etag: '',
  last_image_at: null,
  firmware_version: '',
  target_firmware_version: null,
  target_firmware_version_string: null,
  is_active: true,
  event_face_weight: 2,
  pm_face_weight: 1,
  rotation_counter: 0,
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-01T00:00:00Z',
  ...overrides,
});

const renderPage = (initialEntry = '/facilities/forgekey-epaper') =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/facilities/forgekey-epaper" element={<ForgeKeyEPaperPanelsPage />} />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ForgeKeyEPaperPanelsPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    mockAssets.listAssets.mockResolvedValue({
      data: { results: [{ id: 'a9', name: 'Drill' }] },
    } as any);
  });

  test('non-staff users are redirected', async () => {
    localStorage.setItem('is_staff', 'false');
    localStorage.setItem('is_superuser', 'false');

    renderPage();

    expect(await screen.findByText('HOME')).toBeInTheDocument();
    expect(mockApi.listEPaperDisplays).not.toHaveBeenCalled();
  });

  test('staff sees panels with binding, battery, and status', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.listEPaperDisplays.mockResolvedValue({ data: [buildPanel()] } as any);

    renderPage();

    expect(await screen.findByText('Lathe')).toBeInTheDocument();
    expect(screen.getByText('TAG-1')).toBeInTheDocument();
    expect(screen.getByText('8%')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  test('retire toggles the panel to retired', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.listEPaperDisplays.mockResolvedValue({ data: [buildPanel()] } as any);
    mockApi.setEPaperActive.mockResolvedValue({
      data: buildPanel({ is_active: false }),
    } as any);

    renderPage();

    const retireBtn = await screen.findByText('Retire');
    fireEvent.click(retireBtn);

    await waitFor(() => {
      expect(mockApi.setEPaperActive).toHaveBeenCalledWith('p1', false);
    });
    await waitFor(() => {
      expect(screen.getByText('Retired')).toBeInTheDocument();
    });
    expect(screen.getByText('Reactivate')).toBeInTheDocument();
  });

  test('renders an inline asset rebind picker per panel', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.listEPaperDisplays.mockResolvedValue({ data: [buildPanel()] } as any);

    renderPage();

    // The inline picker means rebinding happens from this page (not a bounce
    // to the QR flow). Asset options come from the assets feed.
    expect(await screen.findByTestId('bind-select-p1')).toBeInTheDocument();
  });

  test('asset list pulls all active assets, not just the first page', async () => {
    // Default DRF PAGE_SIZE=50 means a plain listAssets() call would hide
    // any asset past the first page from the rebind dropdown — request a
    // page big enough to cover any realistic makerspace asset count.
    localStorage.setItem('is_staff', 'true');
    mockApi.listEPaperDisplays.mockResolvedValue({ data: [buildPanel()] } as any);

    renderPage();

    await waitFor(() => {
      expect(mockAssets.listAssets).toHaveBeenCalledWith({
        is_active: true,
        page_size: 1000,
        ordering: 'name',
      });
    });
  });

  test('editing rotation weights posts to setEPaperRotation on blur', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.listEPaperDisplays.mockResolvedValue({ data: [buildPanel()] } as any);
    mockApi.setEPaperRotation.mockResolvedValue({
      data: buildPanel({ event_face_weight: 5, pm_face_weight: 1 }),
    } as any);

    renderPage();

    const eventInput = await screen.findByTestId('rotation-event-p1');
    fireEvent.change(eventInput, { target: { value: '5' } });
    fireEvent.blur(eventInput);

    await waitFor(() => {
      expect(mockApi.setEPaperRotation).toHaveBeenCalledWith('p1', {
        event_face_weight: 5,
        pm_face_weight: 1,
      });
    });
    await waitFor(() => {
      expect(screen.getByText('ratio 5:1')).toBeInTheDocument();
    });
  });

  test('blur with unchanged weights does not POST', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.listEPaperDisplays.mockResolvedValue({ data: [buildPanel()] } as any);

    renderPage();

    const eventInput = await screen.findByTestId('rotation-event-p1');
    fireEvent.blur(eventInput);

    await new Promise((r) => setTimeout(r, 30));
    expect(mockApi.setEPaperRotation).not.toHaveBeenCalled();
  });

  test('shows reported firmware version and pending rollout target', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.listEPaperDisplays.mockResolvedValue({
      data: [
        buildPanel({
          id: 'reported',
          asset_name: 'Lathe',
          firmware_version: '1.5.0',
        }),
        buildPanel({
          id: 'pending',
          asset_name: 'Drill',
          firmware_version: '1.5.0',
          target_firmware_version: 'fv-2',
          target_firmware_version_string: '2.0.0',
        }),
      ],
    } as any);

    renderPage();

    await screen.findByText('Lathe');
    expect(screen.getAllByText('1.5.0')).toHaveLength(2);
    expect(screen.getByText('→ 2.0.0')).toBeInTheDocument();
  });
});
