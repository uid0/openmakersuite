/**
 * Tests for the storage_vision setup page (slice 7).
 *
 * Covers AC-29 (non-staff/non-Logistics users blocked) and the
 * AC-28 setup half: listing areas/slots/cameras, downloading marker
 * labels, and the AC-7 one-time-reveal camera token UX.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import StorageVisionSetupPage from '../../pages/StorageVisionSetupPage';
import { inventoryAPI, storageVisionAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    storageVisionAPI: {
      listAreas: jest.fn(),
      createArea: jest.fn(),
      updateArea: jest.fn(),
      deleteArea: jest.fn(),
      listSlots: jest.fn(),
      createSlot: jest.fn(),
      updateSlot: jest.fn(),
      deleteSlot: jest.fn(),
      downloadSlotMarker: jest.fn(),
      listCameras: jest.fn(),
      createCamera: jest.fn(),
      updateCamera: jest.fn(),
      deleteCamera: jest.fn(),
      rotateCameraToken: jest.fn(),
    },
    inventoryAPI: {
      ...(actual as any).inventoryAPI,
      listLocations: jest.fn(),
      listItems: jest.fn(),
      listCategories: jest.fn(),
    },
  };
});

const mockVision = storageVisionAPI as jest.Mocked<typeof storageVisionAPI>;
const mockInv = inventoryAPI as jest.Mocked<typeof inventoryAPI>;

const buildArea = (overrides: Partial<any> = {}) => ({
  id: 1,
  name: 'Bay 1',
  location: 10,
  location_name: 'Shop floor',
  description: '',
  is_active: true,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  ...overrides,
});

const buildSlot = (overrides: Partial<any> = {}) => ({
  id: 11,
  area: 1,
  area_name: 'Bay 1',
  item: 'item-uuid',
  item_name: 'M3 hex bolt',
  marker_code: 'VIS-BAY1-M3HEX',
  empty_low_confidence_threshold: '0.50',
  notes: '',
  is_active: true,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  ...overrides,
});

const buildCamera = (overrides: Partial<any> = {}) => ({
  id: 21,
  name: 'bay1-cam',
  area: 1,
  area_name: 'Bay 1',
  token_fingerprint: 'abc1234567890def',
  last_seen_at: null,
  last_seen_status: {},
  is_active: true,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  ...overrides,
});

const seedAll = () => {
  mockVision.listAreas.mockResolvedValue({ data: [buildArea()] } as any);
  mockVision.listSlots.mockResolvedValue({ data: [buildSlot()] } as any);
  mockVision.listCameras.mockResolvedValue({ data: [buildCamera()] } as any);
  mockInv.listLocations.mockResolvedValue({
    data: { results: [{ id: 10, name: 'Shop floor' }] },
  } as any);
  mockInv.listItems.mockResolvedValue({
    data: {
      count: 1,
      next: null,
      previous: null,
      results: [{ id: 'item-uuid', name: 'M3 hex bolt' }],
    },
  } as any);
  mockInv.listCategories.mockResolvedValue({
    data: { results: [{ id: 1, name: 'Fasteners' }] },
  } as any);
};

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/facilities/storage-vision']}>
        <Routes>
          <Route
            path="/facilities/storage-vision"
            element={<StorageVisionSetupPage />}
          />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('StorageVisionSetupPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
  });

  test('non-staff users are redirected (AC-29)', async () => {
    localStorage.setItem('is_staff', 'false');
    localStorage.setItem('is_superuser', 'false');

    renderPage();

    expect(await screen.findByText('HOME')).toBeInTheDocument();
    expect(mockVision.listAreas).not.toHaveBeenCalled();
  });

  test('staff sees the area / slot / camera tabs and rows', async () => {
    localStorage.setItem('is_staff', 'true');
    seedAll();

    renderPage();

    expect(await screen.findByText(/Areas \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Slots \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Cameras \(1\)/)).toBeInTheDocument();
    expect(screen.getAllByText('Bay 1').length).toBeGreaterThan(0);
    expect(screen.getByText('Shop floor')).toBeInTheDocument();
  });

  test('Logistics group members can also access (AC-29 inclusive)', async () => {
    localStorage.setItem('is_staff', 'false');
    localStorage.setItem('groups', 'Logistics,Members');
    seedAll();

    renderPage();
    expect(await screen.findByText(/Areas \(1\)/)).toBeInTheDocument();
  });

  test('marker download triggers a blob fetch (AC-6)', async () => {
    localStorage.setItem('is_staff', 'true');
    seedAll();
    mockVision.downloadSlotMarker.mockResolvedValue({
      data: new Blob(['png-bytes'], { type: 'image/png' }),
    } as any);

    renderPage();

    // Switch to the Slots tab.
    fireEvent.click(await screen.findByRole('tab', { name: /Slots/ }));
    const button = await screen.findByTestId('marker-download-11');
    fireEvent.click(button);

    await waitFor(() => {
      expect(mockVision.downloadSlotMarker).toHaveBeenCalledWith(11);
    });
  });

  test('camera create surfaces the raw token exactly once (AC-7)', async () => {
    localStorage.setItem('is_staff', 'true');
    seedAll();
    mockVision.createCamera.mockResolvedValue({
      data: {
        ...buildCamera({ id: 99, name: 'newcam' }),
        raw_token: 'TOP-SECRET-BEARER',
      },
    } as any);

    renderPage();

    // Cameras tab.
    fireEvent.click(await screen.findByRole('tab', { name: /Cameras/ }));
    fireEvent.click(await screen.findByTestId('new-camera-button'));

    // Fill the name field in the create modal.
    const nameInput = await screen.findByLabelText(/^Name/);
    fireEvent.change(nameInput, { target: { value: 'newcam' } });

    // Save.
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    // Token reveal modal shows the raw bearer.
    expect(await screen.findByTestId('revealed-token')).toHaveTextContent(
      'TOP-SECRET-BEARER',
    );
    expect(
      screen.getByText(/only time newcam's token will be displayed/i),
    ).toBeInTheDocument();

    // Dismiss the reveal modal — the table row from the in-memory state
    // must keep showing only the fingerprint, never the raw token.
    fireEvent.click(screen.getByRole('button', { name: /I've copied/i }));
    await waitFor(() => {
      expect(screen.queryByTestId('revealed-token')).not.toBeInTheDocument();
    });
    expect(screen.queryByText('TOP-SECRET-BEARER')).not.toBeInTheDocument();
  });

  test('error from listAreas surfaces an alert', async () => {
    localStorage.setItem('is_staff', 'true');
    mockVision.listAreas.mockRejectedValue(new Error('boom'));
    mockVision.listSlots.mockResolvedValue({ data: [] } as any);
    mockVision.listCameras.mockResolvedValue({ data: [] } as any);
    mockInv.listLocations.mockResolvedValue({ data: { results: [] } } as any);
    mockInv.listItems.mockResolvedValue({
      data: { count: 0, next: null, previous: null, results: [] },
    } as any);
    mockInv.listCategories.mockResolvedValue({
      data: { results: [] },
    } as any);

    renderPage();

    expect(
      await screen.findByText(/Failed to load storage vision setup/),
    ).toBeInTheDocument();
  });
});
