/**
 * Tests for the storage_vision capture upload page (slice 8).
 *
 * Covers AC-9 phone upload happy path, the 2-second poll
 * transitioning the tracked capture through processing → processed,
 * the AC-15 no-markers surface, the AC-29 non-staff redirect, and
 * the recent-captures history rendering.
 */
import { MantineProvider } from '@mantine/core';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import StorageVisionCapturePage from '../../pages/StorageVisionCapturePage';
import { storageVisionAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    storageVisionAPI: {
      listAreas: jest.fn(),
      listCaptures: jest.fn(),
      uploadCapture: jest.fn(),
      getCapture: jest.fn(),
    },
  };
});

const mockVision = storageVisionAPI as jest.Mocked<typeof storageVisionAPI>;

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

const buildCapture = (overrides: Partial<any> = {}) => ({
  id: 42,
  area: 1,
  area_name: 'Bay 1',
  source: 'phone' as const,
  camera: null,
  uploaded_by: 7,
  original_image: null,
  captured_at: null,
  received_at: '2026-06-12T00:00:00Z',
  status: 'queued' as const,
  processor_version: '',
  markers_detected: [],
  failure_reason: '',
  failure_code: '',
  queued_at: '2026-06-12T00:00:00Z',
  processing_at: null,
  processed_at: null,
  failed_at: null,
  ...overrides,
});

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/facilities/storage-vision/capture']}>
        <Routes>
          <Route
            path="/facilities/storage-vision/capture"
            element={<StorageVisionCapturePage />}
          />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('StorageVisionCapturePage', () => {
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

  test('staff sees the area picker populated with active areas', async () => {
    localStorage.setItem('is_staff', 'true');
    mockVision.listAreas.mockResolvedValue({ data: [buildArea()] } as any);
    mockVision.listCaptures.mockResolvedValue({ data: [] } as any);

    renderPage();

    expect(
      await screen.findByTestId('capture-area-select'),
    ).toBeInTheDocument();
    // Submit button is disabled until area + file are chosen.
    expect(screen.getByTestId('capture-submit')).toBeDisabled();
  });

  test('upload posts multipart and shows the tracked capture panel', async () => {
    localStorage.setItem('is_staff', 'true');
    mockVision.listAreas.mockResolvedValue({ data: [buildArea()] } as any);
    mockVision.listCaptures.mockResolvedValue({ data: [] } as any);
    mockVision.uploadCapture.mockResolvedValue({
      data: buildCapture({ status: 'queued' }),
    } as any);
    mockVision.getCapture.mockResolvedValue({
      data: buildCapture({
        status: 'processed',
        markers_detected: [
          {
            marker_code: 'VIS-BAY1-M3HEX',
            bbox: [0, 0, 100, 100],
            confidence: 0.95,
            matched_slot_id: 99,
          },
        ],
        processor_version: 'slice4',
      }),
    } as any);

    renderPage();
    await screen.findByTestId('capture-area-select');

    // Pick the area via the Mantine combobox.
    fireEvent.click(screen.getByPlaceholderText(/Pick the area/));
    fireEvent.click(await screen.findByText(/Bay 1 — Shop floor/));

    // Attach a file. Mantine FileInput's button has the label
    // "Photo (JPEG or PNG)"; assigning to the underlying hidden
    // <input type="file"> is easier than driving the picker.
    const file = new File(['png-bytes'], 'frame.jpg', { type: 'image/jpeg' });
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByTestId('capture-submit')).not.toBeDisabled();
    });

    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(screen.getByTestId('capture-submit'));

    // Tracked panel appears after the 202.
    expect(await screen.findByTestId('tracked-capture-panel')).toBeInTheDocument();
    expect(mockVision.uploadCapture).toHaveBeenCalled();
    const formData = mockVision.uploadCapture.mock.calls[0][0] as FormData;
    expect(formData.get('area')).toBe('1');
    expect(formData.get('original_image')).toBe(file);

    // Advance to the poll, which transitions to processed.
    await act(async () => {
      vi.advanceTimersByTime(2_100);
    });

    await waitFor(() => {
      expect(mockVision.getCapture).toHaveBeenCalledWith(42);
      expect(
        screen.getByTestId('matched-markers-table'),
      ).toBeInTheDocument();
      expect(screen.getByText('VIS-BAY1-M3HEX')).toBeInTheDocument();
    });

    vi.useRealTimers();
  });

  test('no-markers result surfaces the AC-15 message', async () => {
    localStorage.setItem('is_staff', 'true');
    mockVision.listAreas.mockResolvedValue({ data: [buildArea()] } as any);
    mockVision.listCaptures.mockResolvedValue({ data: [] } as any);
    mockVision.uploadCapture.mockResolvedValue({
      data: buildCapture({ status: 'queued' }),
    } as any);
    mockVision.getCapture.mockResolvedValue({
      data: buildCapture({
        status: 'processed',
        failure_code: 'no_markers_detected',
        failure_reason: 'No storage-vision markers were readable in this image.',
        markers_detected: [],
      }),
    } as any);

    renderPage();
    await screen.findByTestId('capture-area-select');
    fireEvent.click(screen.getByPlaceholderText(/Pick the area/));
    fireEvent.click(await screen.findByText(/Bay 1 — Shop floor/));
    const file = new File(['png'], 'frame.jpg', { type: 'image/jpeg' });
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });
    await waitFor(() =>
      expect(screen.getByTestId('capture-submit')).not.toBeDisabled(),
    );

    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(screen.getByTestId('capture-submit'));
    await screen.findByTestId('tracked-capture-panel');
    await act(async () => {
      vi.advanceTimersByTime(2_100);
    });

    await waitFor(() => {
      expect(
        screen.getByText(/No storage-vision markers were readable/),
      ).toBeInTheDocument();
    });

    vi.useRealTimers();
  });

  test('recent captures table renders with status + marker count', async () => {
    localStorage.setItem('is_staff', 'true');
    mockVision.listAreas.mockResolvedValue({ data: [buildArea()] } as any);
    mockVision.listCaptures.mockResolvedValue({
      data: [
        buildCapture({
          id: 11,
          status: 'processed',
          markers_detected: [
            {
              marker_code: 'X',
              bbox: [0, 0, 1, 1],
              confidence: 1.0,
              matched_slot_id: 1,
            },
          ],
        }),
        buildCapture({
          id: 12,
          status: 'failed',
          failure_code: 'detection_error',
        }),
      ],
    } as any);

    renderPage();
    expect(
      await screen.findByTestId('recent-captures-table'),
    ).toBeInTheDocument();
    expect(screen.getByText('processed')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  test('error on listAreas surfaces the alert', async () => {
    localStorage.setItem('is_staff', 'true');
    mockVision.listAreas.mockRejectedValue(new Error('boom'));
    mockVision.listCaptures.mockResolvedValue({ data: [] } as any);

    renderPage();
    expect(await screen.findByText(/Failed to load areas/)).toBeInTheDocument();
  });
});
