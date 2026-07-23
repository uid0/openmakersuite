/**
 * Resilience tests for WorkOrderPage (#457 R4 — AC-17 / AC-19, safety-critical).
 *
 * WorkOrderPage had NO test before this slice. The maintenance work-order
 * review screen is safety-critical, so it must degrade gracefully:
 *  - loading shows a placeholder, never a blank screen (AC-19);
 *  - a failed load surfaces an actionable "not found" state, not a raw
 *    exception (AC-19);
 *  - a failed save (network/offline or a 401 session expiry) keeps the
 *    notes the user typed so they can retry after recovery, and re-enables
 *    the control (AC-17 — auth expiration preserves user context);
 *  - a double-click on Save does not fire two saves (duplicate-submit guard).
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AxiosError } from 'axios';
import { MemoryRouter } from 'react-router-dom';

import WorkOrderPage from '../../pages/WorkOrderPage';
import { workOrderAPI } from '../../services/api';
import { WorkOrder, WorkOrderMaterialUsage } from '../../types';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useParams: () => ({ id: 'wo-1' }),
  useNavigate: () => jest.fn(),
}));

const mockWorkOrderAPI = workOrderAPI as jest.Mocked<typeof workOrderAPI>;

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <WorkOrderPage />
      </MemoryRouter>
    </MantineProvider>,
  );

const buildWorkOrder = (overrides: Partial<WorkOrder> = {}): WorkOrder =>
  ({
    id: 'wo-1',
    short_id: 'wo-001',
    maintenance_item: 'mi-1',
    maintenance_item_title: 'Quarterly belt inspection',
    asset_name: 'Bandsaw',
    asset_tag: 'TAG001',
    asset_id: 'a-1',
    status: 'open',
    due_date: null,
    assigned_to: null,
    assigned_to_name: 'Alice',
    completed_by_name: null,
    completed_at: null,
    notes: '',
    loto_completion_note: '',
    is_overdue: false,
    task_completions: [],
    material_usage: [],
    loto_completions: [],
    submissions: [],
    photos: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }) as WorkOrder;

/** A 401 shaped like the real backend's "token expired" rejection. */
const sessionExpiredError = (): AxiosError =>
  new AxiosError('Request failed with status code 401', 'ERR_BAD_REQUEST', undefined, {}, {
    status: 401,
    statusText: 'Unauthorized',
    data: { detail: 'Authentication credentials were not provided.' },
    headers: {},
    config: {} as never,
  } as never);

const okResponse = <T,>(data: T) =>
  ({ data, status: 200, statusText: 'OK', headers: {}, config: {} as never }) as never;

beforeEach(() => {
  jest.clearAllMocks();
});

describe('WorkOrderPage resilience (#457 R4)', () => {
  it('shows a loading placeholder while the work order loads (AC-19)', () => {
    // getWorkOrder never resolves -> stays in the loading branch.
    mockWorkOrderAPI.getWorkOrder.mockImplementation(
      () => new Promise(() => undefined) as never,
    );

    renderPage();

    expect(screen.getByText(/loading work order/i)).toBeInTheDocument();
  });

  it('renders an actionable not-found state when the load fails (AC-19)', async () => {
    // A failed load (offline / dependency failure) must not blank the page.
    mockWorkOrderAPI.getWorkOrder.mockRejectedValue(networkError());

    renderPage();

    expect(await screen.findByText(/work order not found/i)).toBeInTheDocument();
    // A recovery affordance back to the dashboard is offered.
    expect(
      screen.getByRole('button', { name: /back to dashboard/i }),
    ).toBeInTheDocument();
  });

  it('keeps the typed notes and re-enables Save when the save fails (AC-19)', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder()));
    mockWorkOrderAPI.updateWorkOrder.mockRejectedValue(networkError());

    renderPage();

    const notes = (await screen.findByPlaceholderText(
      /add notes about this work order/i,
    )) as HTMLTextAreaElement;
    fireEvent.change(notes, { target: { value: 'Replaced the drive belt' } });

    fireEvent.click(screen.getByRole('button', { name: /save notes/i }));

    await waitFor(() => {
      expect(mockWorkOrderAPI.updateWorkOrder).toHaveBeenCalledWith('wo-1', {
        notes: 'Replaced the drive belt',
      });
    });

    // The save failed, but the user's unsaved notes survive and the button
    // re-enables so they can retry — no blank screen, no lost work.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /save notes/i })).not.toBeDisabled();
    });
    expect(
      (screen.getByPlaceholderText(/add notes about this work order/i) as HTMLTextAreaElement)
        .value,
    ).toBe('Replaced the drive belt');
  });

  it('preserves user context across a session expiry (401) on save (AC-17)', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder()));
    mockWorkOrderAPI.updateWorkOrder.mockRejectedValue(sessionExpiredError());

    renderPage();

    const notes = (await screen.findByPlaceholderText(
      /add notes about this work order/i,
    )) as HTMLTextAreaElement;
    fireEvent.change(notes, { target: { value: 'LOTO verified, zero energy' } });

    fireEvent.click(screen.getByRole('button', { name: /save notes/i }));

    await waitFor(() => {
      expect(mockWorkOrderAPI.updateWorkOrder).toHaveBeenCalledTimes(1);
    });

    // After the session expires mid-save the attempted work is preserved and
    // the control is usable again, so the user can re-submit after re-login.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /save notes/i })).not.toBeDisabled();
    });
    expect(
      (screen.getByPlaceholderText(/add notes about this work order/i) as HTMLTextAreaElement)
        .value,
    ).toBe('LOTO verified, zero energy');
  });

  it('does not fire a second save while one is in flight (duplicate-submit guard)', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder()));
    let resolveSave: (value: unknown) => void = () => undefined;
    mockWorkOrderAPI.updateWorkOrder.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSave = resolve;
        }) as never,
    );

    renderPage();

    const notes = await screen.findByPlaceholderText(/add notes about this work order/i);
    fireEvent.change(notes, { target: { value: 'first' } });

    const saveBtn = screen.getByRole('button', { name: /save notes/i });
    fireEvent.click(saveBtn);

    // Button enters its busy state while the save is pending.
    await waitFor(() => {
      expect(saveBtn).toHaveAttribute('data-loading', 'true');
    });

    // A second click while pending is ignored — exactly one save fires.
    fireEvent.click(saveBtn);
    expect(mockWorkOrderAPI.updateWorkOrder).toHaveBeenCalledTimes(1);

    // Let the in-flight save settle so no act() warning leaks.
    resolveSave(okResponse(buildWorkOrder({ notes: 'first' })));
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /save notes/i }),
      ).not.toBeDisabled();
    });
  });

  it('offers a single print button for the unified work order (OMR) PDF (op-3irz)', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder()));
    mockWorkOrderAPI.getPdfUrl.mockReturnValue(
      'http://localhost:8000/api/inventory/work-orders/wo-1/pdf/',
    );

    renderPage();

    const printBtn = await screen.findByLabelText('Print work order form');
    expect(printBtn).toHaveAttribute(
      'href',
      'http://localhost:8000/api/inventory/work-orders/wo-1/pdf/',
    );
    expect(mockWorkOrderAPI.getPdfUrl).toHaveBeenCalledWith('wo-1');
  });
});

describe('WorkOrderPage material usage → inventory decrement (op-uh8z)', () => {
  const buildMaterial = (
    overrides: Partial<WorkOrderMaterialUsage> = {},
  ): WorkOrderMaterialUsage => ({
    id: 'mu-1',
    work_order: 'wo-1',
    material: 'm-1',
    material_name: 'Air filter',
    quantity_planned: '2.00',
    quantity_used: '2.00',
    unit: 'ea',
    was_used: false,
    applied_quantity: null,
    stock_applied: false,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  });

  it('sends the quantity used when a material is checked off as used', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ material_usage: [buildMaterial()] })),
    );
    mockWorkOrderAPI.toggleMaterial.mockResolvedValue(okResponse({}));

    renderPage();

    const checkbox = await screen.findByRole('checkbox', { name: /air filter/i });
    fireEvent.click(checkbox);

    // Checking the box records the material as used AND carries the quantity,
    // which the backend decrements from the linked inventory item. The trailing
    // unit cost (op-768w) is undefined here — nobody typed a price, and an
    // omitted one leaves whatever the line already recorded alone.
    await waitFor(() => {
      expect(mockWorkOrderAPI.toggleMaterial).toHaveBeenCalledWith(
        'wo-1',
        'mu-1',
        true,
        '2.00',
        undefined,
      );
    });
  });

  it('shows the decremented-from-stock state and hides the quantity input once applied', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          material_usage: [
            buildMaterial({
              was_used: true,
              quantity_used: '3.00',
              applied_quantity: 3,
              stock_applied: true,
            }),
          ],
        }),
      ),
    );

    renderPage();

    expect(await screen.findByText(/3 from stock/i)).toBeInTheDocument();
    expect(screen.getByText(/usage logged/i)).toBeInTheDocument();
    // The quantity input is locked away once the decrement is applied.
    expect(screen.queryByLabelText(/qty used/i)).not.toBeInTheDocument();
  });
});
