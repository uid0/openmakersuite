/**
 * Tests for the storage_vision review queue page (slice 9).
 *
 * Covers AC-20 (filterable list), AC-21 + AC-22 (approve creates
 * reconciliation + may create a reorder), AC-23 (409 conflict on
 * already-resolved), AC-24 (reject requires a reason), AC-25 (bulk
 * approve partial results with per-id skip reasons), AC-29 (non-staff
 * redirect), plus the slice-8 ?capture=N deep link.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import StorageVisionReviewPage from '../../pages/StorageVisionReviewPage';
import { storageVisionAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    storageVisionAPI: {
      listObservations: jest.fn(),
      listAreas: jest.fn(),
      approveObservation: jest.fn(),
      rejectObservation: jest.fn(),
      bulkApprove: jest.fn(),
    },
  };
});

const mockVision = storageVisionAPI as jest.Mocked<typeof storageVisionAPI>;

const buildObs = (overrides: Partial<any> = {}) => ({
  id: 11,
  capture: 100,
  capture_thumbnail: null,
  slot: 1,
  slot_marker_code: 'VIS-BAY1-M3HEX',
  area_id: 1,
  area_name: 'Bay 1',
  item_id: 'item-uuid',
  item_name: 'M3 hex bolt',
  classification: 'empty' as const,
  confidence: '0.800',
  evidence_crop: 'https://example.com/crop.jpg',
  model_version: 'heuristic-v1',
  suggested_action: 'reconcile_empty' as const,
  status: 'pending' as const,
  duplicate_count: 0,
  last_duplicate_at: null,
  age_seconds: 120,
  created_at: '2026-06-12T00:00:00Z',
  updated_at: '2026-06-12T00:00:00Z',
  ...overrides,
});

const renderPage = (initialEntry = '/facilities/storage-vision/review') =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route
            path="/facilities/storage-vision/review"
            element={<StorageVisionReviewPage />}
          />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('StorageVisionReviewPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    mockVision.listAreas.mockResolvedValue({
      data: [
        {
          id: 1,
          name: 'Bay 1',
          location: 10,
          location_name: 'Shop floor',
          description: '',
          is_active: true,
          created_at: '2026-06-01T00:00:00Z',
          updated_at: '2026-06-01T00:00:00Z',
        },
      ],
    } as any);
  });

  test('non-staff users are redirected (AC-29)', async () => {
    localStorage.setItem('is_staff', 'false');
    renderPage();
    expect(await screen.findByText('HOME')).toBeInTheDocument();
    expect(mockVision.listObservations).not.toHaveBeenCalled();
  });

  test('staff sees a pending-by-default queue (AC-20)', async () => {
    localStorage.setItem('is_staff', 'true');
    mockVision.listObservations.mockResolvedValue({
      data: [buildObs()],
    } as any);

    renderPage();

    expect(
      await screen.findByTestId('observations-table'),
    ).toBeInTheDocument();
    expect(screen.getByText('M3 hex bolt')).toBeInTheDocument();
    expect(screen.getByText('VIS-BAY1-M3HEX')).toBeInTheDocument();
    expect(mockVision.listObservations).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'pending' }),
    );
  });

  test('approve calls API, updates row, and surfaces reorder badge (AC-21, AC-22)', async () => {
    localStorage.setItem('is_staff', 'true');
    mockVision.listObservations.mockResolvedValue({
      data: [buildObs()],
    } as any);
    mockVision.approveObservation.mockResolvedValue({
      data: {
        ...buildObs(),
        status: 'approved',
        reconciliation_id: 42,
        reorder_created: true,
      },
    } as any);

    renderPage();

    fireEvent.click(await screen.findByTestId('approve-11'));

    await waitFor(() => {
      expect(mockVision.approveObservation).toHaveBeenCalledWith(11);
    });
    await waitFor(() => {
      expect(screen.getByTestId('review-resolution-alert')).toHaveTextContent(
        /Approved observation #11/,
      );
      expect(screen.getByTestId('review-resolution-alert')).toHaveTextContent(
        /reconciliation #42/,
      );
      expect(screen.getByTestId('review-resolution-alert')).toHaveTextContent(
        /reorder created/,
      );
    });
    // Approve button disabled after status → approved.
    expect(screen.getByTestId('approve-11')).toBeDisabled();
  });

  test('409 conflict surfaces an AC-23 warning', async () => {
    localStorage.setItem('is_staff', 'true');
    mockVision.listObservations.mockResolvedValue({
      data: [buildObs()],
    } as any);
    mockVision.approveObservation.mockRejectedValue({
      response: { status: 409, data: { code: 'already_resolved' } },
    });

    renderPage();
    fireEvent.click(await screen.findByTestId('approve-11'));

    await waitFor(() => {
      expect(screen.getByTestId('review-resolution-alert')).toHaveTextContent(
        /already resolved/i,
      );
    });
  });

  test('reject requires a reason (AC-24)', async () => {
    localStorage.setItem('is_staff', 'true');
    mockVision.listObservations.mockResolvedValue({
      data: [buildObs()],
    } as any);
    mockVision.rejectObservation.mockResolvedValue({
      data: { ...buildObs(), status: 'rejected' },
    } as any);

    renderPage();
    fireEvent.click(await screen.findByTestId('reject-11'));

    // Modal opens asynchronously — await its confirm button.
    const confirm = await screen.findByTestId('reject-confirm');
    expect(confirm).toBeDisabled();

    const reason = screen.getByTestId('reject-reason') as HTMLTextAreaElement;
    fireEvent.change(reason, { target: { value: 'glare on the bin' } });
    await waitFor(() => expect(confirm).not.toBeDisabled());

    fireEvent.click(confirm);

    await waitFor(() => {
      expect(mockVision.rejectObservation).toHaveBeenCalledWith(
        11,
        'glare on the bin',
      );
      expect(screen.getByTestId('review-resolution-alert')).toHaveTextContent(
        /Rejected observation #11/,
      );
    });
  });

  test('bulk approve sends selected ids + renders skipped panel (AC-25)', async () => {
    localStorage.setItem('is_staff', 'true');
    mockVision.listObservations.mockResolvedValue({
      data: [
        buildObs({ id: 1 }),
        buildObs({ id: 2 }),
        // review_only rows shouldn't even be selectable
        buildObs({ id: 3, suggested_action: 'review_only' }),
      ],
    } as any);
    mockVision.bulkApprove.mockResolvedValue({
      data: {
        approved: [
          { id: 1, reconciliation_id: 9, reorder_created: true },
        ],
        skipped: [{ id: 2, reason: 'internal_error' }],
        counts: { requested: 2, approved: 1, skipped: 1 },
      },
    } as any);

    renderPage();
    await screen.findByTestId('observations-table');

    // Master-toggle picks up only the two reconcile_empty pending rows
    // — the review_only row stays unselected per the disabled-checkbox guard.
    fireEvent.click(screen.getByTestId('select-all'));

    expect(
      screen.getByTestId('bulk-approve-button'),
    ).toHaveTextContent(/Approve selected \(2\)/);

    fireEvent.click(screen.getByTestId('bulk-approve-button'));

    await waitFor(() => {
      expect(mockVision.bulkApprove).toHaveBeenCalledWith([1, 2], undefined);
    });

    await waitFor(() => {
      const skipped = screen.getByTestId('bulk-skipped-alert');
      expect(skipped).toHaveTextContent(/#2 — internal_error/);
      expect(screen.getByTestId('review-resolution-alert')).toHaveTextContent(
        /Approved 1 of 2/,
      );
    });
  });

  test('?capture=N scopes the queue and offers a Clear filter button', async () => {
    localStorage.setItem('is_staff', 'true');
    mockVision.listObservations.mockResolvedValue({
      data: [
        buildObs({ id: 1, capture: 100 }),
        buildObs({ id: 2, capture: 200 }),
      ],
    } as any);

    renderPage('/facilities/storage-vision/review?capture=100');
    await screen.findByTestId('observations-table');

    // Only observation #1 (capture 100) should be in the rendered table.
    expect(screen.getByTestId('approve-1')).toBeInTheDocument();
    expect(screen.queryByTestId('approve-2')).not.toBeInTheDocument();
    expect(screen.getByTestId('clear-capture-filter')).toBeInTheDocument();
  });
});
