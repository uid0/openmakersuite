/**
 * OMR scan review UI (op-6pc8, bead-2).
 *
 * A flatbed-scan submission surfaces each detected mark as its own review row
 * with a warped crop and per-row Accept/Reject, and the work order is only
 * closed by an explicit human "Confirm & complete" — a scan never auto-closes.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import WorkOrderPage from '../../pages/WorkOrderPage';
import { workOrderAPI } from '../../services/api';
import { WorkOrder } from '../../types';

vi.mock('../../services/api');
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useParams: () => ({ id: 'wo-1' }),
  useNavigate: () => jest.fn(),
}));

const mockWorkOrderAPI = workOrderAPI as jest.Mocked<typeof workOrderAPI>;

const okResponse = <T,>(data: T) =>
  ({ data, status: 200, statusText: 'OK', headers: {}, config: {} as never }) as never;

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <WorkOrderPage />
      </MemoryRouter>
    </MantineProvider>,
  );

const scanWorkOrder = (overrides: Partial<WorkOrder> = {}): WorkOrder =>
  ({
    id: 'wo-1',
    short_id: 'wo-001',
    maintenance_item: 'mi-1',
    maintenance_item_title: 'Quarterly belt inspection',
    asset_name: 'Bandsaw',
    asset_tag: 'TAG001',
    asset_id: 'a-1',
    status: 'in_progress',
    due_date: null,
    assigned_to: null,
    assigned_to_name: 'Alice',
    completed_by_name: null,
    completed_at: null,
    notes: '',
    is_overdue: false,
    task_completions: [],
    material_usage: [],
    photos: [],
    submissions: [
      {
        id: 'sub-1',
        pdf_url: null,
        received_at: '2026-07-08T00:00:00Z',
        status: 'pending_review',
        source: 'scan',
        from_email: 'scanner@example.com',
        subject: 'scan.png',
        submitted_by: null,
        submitted_by_name: null,
        parse_error: '',
        pending_changes: [
          {
            kind: 'checkbox',
            target_id: 'task_aaa',
            value: true,
            confidence: 1.0,
            label: 'Inspect belt tension',
            crop_url: '/api/inventory/work-orders/wo-1/submissions/sub-1/mark-crop/task_aaa/',
            auto_applied: true,
          },
          {
            kind: 'checkbox',
            target_id: 'task_bbb',
            value: false,
            confidence: 0.62,
            label: 'Lubricate bearings',
            crop_url: '/api/inventory/work-orders/wo-1/submissions/sub-1/mark-crop/task_bbb/',
            auto_applied: false,
          },
        ],
      },
    ],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }) as WorkOrder;

beforeEach(() => {
  jest.clearAllMocks();
  // OmrMarkCrop fetches an authed PNG blob → object URL. Stub both.
  (URL as unknown as { createObjectURL: () => string }).createObjectURL = vi.fn(() => 'blob:mock');
  (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = vi.fn();
  mockWorkOrderAPI.getMarkCrop.mockResolvedValue(okResponse(new Blob(['png'])));
  // op-o6rs: the review panel also fetches the full scanned page (authed blob).
  mockWorkOrderAPI.getScanImage.mockResolvedValue(okResponse(new Blob(['png'])));
  mockWorkOrderAPI.applyPendingChanges.mockResolvedValue(okResponse({ work_order_completed: false }));
  mockWorkOrderAPI.discardPendingChanges.mockResolvedValue(okResponse({}));
});

describe('OMR scan review (bead-2)', () => {
  it('renders each scanned mark as a review row with a scan badge', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(scanWorkOrder()));
    renderPage();

    expect(await screen.findByText(/detected from paper form/i)).toBeInTheDocument();
    expect(screen.getByText(/flatbed scan/i)).toBeInTheDocument();
    expect(screen.getByText('Inspect belt tension')).toBeInTheDocument();
    expect(screen.getByText('Lubricate bearings')).toBeInTheDocument();
    // the high-confidence mark shows it was pre-checked
    expect(screen.getByText(/marked · pre-checked/i)).toBeInTheDocument();
  });

  it('shows the full scanned page for paper-form verification', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(scanWorkOrder()));
    renderPage();

    // the whole scanned page is rendered (not just the per-mark crops) and is
    // sourced from the authed scan-image endpoint.
    expect(
      await screen.findByRole('img', { name: /scanned work order page/i }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(mockWorkOrderAPI.getScanImage).toHaveBeenCalledWith('wo-1', 'sub-1');
    });
  });

  it('accepts a single mark via its per-row button', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(scanWorkOrder()));
    renderPage();

    const acceptBtn = await screen.findByRole('button', { name: /accept Lubricate bearings/i });
    fireEvent.click(acceptBtn);

    await waitFor(() => {
      expect(mockWorkOrderAPI.applyPendingChanges).toHaveBeenCalledWith('wo-1', 'sub-1', {
        target_ids: ['task_bbb'],
      });
    });
  });

  it('rejects a single mark via its per-row button', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(scanWorkOrder()));
    renderPage();

    const rejectBtn = await screen.findByRole('button', { name: /reject Inspect belt tension/i });
    fireEvent.click(rejectBtn);

    await waitFor(() => {
      expect(mockWorkOrderAPI.discardPendingChanges).toHaveBeenCalledWith('wo-1', 'sub-1', {
        target_ids: ['task_aaa'],
      });
    });
  });

  it('only closes the work order on the explicit human confirm', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(scanWorkOrder()));
    renderPage();

    const confirmBtn = await screen.findByRole('button', { name: /confirm & complete work order/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(mockWorkOrderAPI.applyPendingChanges).toHaveBeenCalledWith('wo-1', 'sub-1', {
        confirm_complete: true,
      });
    });
  });

  it('surfaces a scan review error banner (bad alignment / template drift)', async () => {
    const wo = scanWorkOrder();
    wo.submissions[0].pending_changes = [];
    wo.submissions[0].parse_error = 'Could not align the scanned form. Please review by hand.';
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(wo));
    renderPage();

    // Shown in the review-card Alert (and echoed in the submissions history).
    const banners = await screen.findAllByText(/could not align the scanned form/i);
    expect(banners.length).toBeGreaterThanOrEqual(1);
    // no per-row marks, but the confirm affordance is still available
    expect(
      screen.getByRole('button', { name: /confirm & complete work order/i }),
    ).toBeInTheDocument();
  });
});
