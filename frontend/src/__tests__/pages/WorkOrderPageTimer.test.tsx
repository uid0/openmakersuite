/**
 * Work-order + per-step stopwatch on the detail page (op-m3so).
 *
 * The point is measurement: a tech starts the clock, works, and the recorded
 * total gets compared against the template's `estimated_time_minutes`. The
 * server owns the total — `elapsed_seconds` already includes any segment still
 * running — so the page only ticks a display over it and re-anchors on every
 * response.
 */
import { MantineProvider } from '@mantine/core';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import WorkOrderPage, { formatElapsed, formatElapsedSummary } from '../../pages/WorkOrderPage';
import { workOrderAPI } from '../../services/api';
import { WorkOrder, WorkOrderTaskCompletion } from '../../types';

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

const buildStep = (overrides: Partial<WorkOrderTaskCompletion> = {}): WorkOrderTaskCompletion =>
  ({
    id: 'tc-1',
    work_order: 'wo-1',
    task: 'st-1',
    task_title: 'Disconnect power',
    task_order: 0,
    is_required: true,
    is_completed: false,
    completed_by: null,
    completed_by_name: null,
    completed_at: null,
    notes: '',
    evidence_photos: [],
    elapsed_seconds: 0,
    is_timing: false,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }) as WorkOrderTaskCompletion;

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
    completed_by_name: '',
    started_at: null,
    completed_at: null,
    elapsed_seconds: 0,
    is_timing: false,
    estimated_time_minutes: 30,
    notes: '',
    loto_completion_note: '',
    is_overdue: false,
    task_completions: [],
    material_usage: [],
    loto_completions: [],
    photos: [],
    submissions: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }) as WorkOrder;

const clock = () => screen.getByTestId('wo-timer-clock');
const stepsCard = async () =>
  (await screen.findByText('Task Steps')).closest('.mantine-Card-root') as HTMLElement;

beforeEach(() => {
  jest.clearAllMocks();
});

describe('formatting helpers', () => {
  it.each([
    [0, '00:00'],
    [9, '00:09'],
    [65, '01:05'],
    [3600, '1:00:00'],
    [4325, '1:12:05'],
  ])('formats %i seconds as %s', (seconds, expected) => {
    expect(formatElapsed(seconds)).toBe(expected);
  });

  it('puts the elapsed minutes next to the estimate', () => {
    expect(formatElapsedSummary(18 * 60, 30)).toBe('18m / est 30m');
  });

  it('omits the comparison when the template carries no estimate', () => {
    expect(formatElapsedSummary(18 * 60, null)).toBe('18m on job');
  });
});

describe('WorkOrderPage — work-order timer (op-m3so)', () => {
  it('shows the running total against the estimate', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ elapsed_seconds: 18 * 60 })),
    );

    renderPage();

    expect(await screen.findByTestId('wo-timer-clock')).toHaveTextContent('18:00');
    expect(screen.getByTestId('wo-timer-summary')).toHaveTextContent('18m / est 30m');
    expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled();
  });

  it('starts the clock and renders the server response without a refetch', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder()));
    mockWorkOrderAPI.timer.mockResolvedValue(
      okResponse(buildWorkOrder({ is_timing: true, elapsed_seconds: 0 })),
    );

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Start' }));

    await waitFor(() => expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument());
    expect(mockWorkOrderAPI.timer).toHaveBeenCalledWith('wo-1', 'start');
    // The response IS the refreshed work order, so no second GET.
    expect(mockWorkOrderAPI.getWorkOrder).toHaveBeenCalledTimes(1);
  });

  it('pauses a running clock', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ is_timing: true, elapsed_seconds: 120 })),
    );
    mockWorkOrderAPI.timer.mockResolvedValue(
      okResponse(buildWorkOrder({ is_timing: false, elapsed_seconds: 125 })),
    );

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Pause' }));

    await waitFor(() => expect(screen.getByRole('button', { name: 'Start' })).toBeInTheDocument());
    expect(mockWorkOrderAPI.timer).toHaveBeenCalledWith('wo-1', 'pause');
    expect(clock()).toHaveTextContent('02:05');
  });

  it('ticks once a second while running', async () => {
    vi.useFakeTimers();
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ is_timing: true, elapsed_seconds: 65 })),
    );

    const view = renderPage();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(clock()).toHaveTextContent('01:05');

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(clock()).toHaveTextContent('01:08');

    // Unmount before restoring real timers so the interval is cleared by the
    // component's own cleanup — a leaked interval reddens the whole run.
    view.unmount();
    vi.useRealTimers();
  });

  it('does not tick while paused', async () => {
    vi.useFakeTimers();
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ is_timing: false, elapsed_seconds: 65 })),
    );

    const view = renderPage();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(clock()).toHaveTextContent('01:05');

    view.unmount();
    vi.useRealTimers();
  });

  it('locks the control once the work order is completed', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ status: 'completed', elapsed_seconds: 47 * 60 })),
    );

    renderPage();

    expect(await screen.findByTestId('wo-timer-clock')).toHaveTextContent('47:00');
    // Still shown — actual-vs-estimate is the record — but no longer runnable.
    expect(screen.getByTestId('wo-timer-summary')).toHaveTextContent('47m / est 30m');
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled();
  });

  it('renders an older payload that omits the timer keys', async () => {
    const legacy = buildWorkOrder();
    delete (legacy as Partial<WorkOrder>).elapsed_seconds;
    delete (legacy as Partial<WorkOrder>).is_timing;
    delete (legacy as Partial<WorkOrder>).estimated_time_minutes;
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(legacy));

    renderPage();

    expect(await screen.findByTestId('wo-timer-clock')).toHaveTextContent('00:00');
    expect(screen.getByTestId('wo-timer-summary')).toHaveTextContent('0m on job');
  });
});

describe('WorkOrderPage — per-step timer (op-m3so)', () => {
  it('starts a step and reloads so a paused sibling is reflected', async () => {
    mockWorkOrderAPI.getWorkOrder
      .mockResolvedValueOnce(
        okResponse(
          buildWorkOrder({
            task_completions: [
              buildStep(),
              buildStep({ id: 'tc-2', task_title: 'Check belt', task_order: 1, is_timing: true }),
            ],
          }),
        ),
      )
      .mockResolvedValue(
        okResponse(
          buildWorkOrder({
            task_completions: [
              buildStep({ is_timing: true }),
              buildStep({
                id: 'tc-2',
                task_title: 'Check belt',
                task_order: 1,
                is_timing: false,
                elapsed_seconds: 90,
              }),
            ],
          }),
        ),
      );
    mockWorkOrderAPI.taskTimer.mockResolvedValue(okResponse(buildStep({ is_timing: true })));

    renderPage();

    const card = await stepsCard();
    fireEvent.click(
      within(card).getByRole('button', { name: 'Start timer for Disconnect power' }),
    );

    await waitFor(() =>
      expect(mockWorkOrderAPI.taskTimer).toHaveBeenCalledWith('wo-1', 'tc-1', 'start'),
    );
    // The sibling stopped and kept its time — one step runs at a time.
    await waitFor(() =>
      expect(
        within(card).getByRole('button', { name: 'Pause timer for Disconnect power' }),
      ).toBeInTheDocument(),
    );
    expect(
      within(card).getByRole('button', { name: 'Start timer for Check belt' }),
    ).toBeInTheDocument();
    expect(within(card).getByText('01:30')).toBeInTheDocument();
  });

  it('pauses a running step', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({ task_completions: [buildStep({ is_timing: true, elapsed_seconds: 30 })] }),
      ),
    );
    mockWorkOrderAPI.taskTimer.mockResolvedValue(okResponse(buildStep()));

    renderPage();

    const card = await stepsCard();
    fireEvent.click(
      within(card).getByRole('button', { name: 'Pause timer for Disconnect power' }),
    );

    await waitFor(() =>
      expect(mockWorkOrderAPI.taskTimer).toHaveBeenCalledWith('wo-1', 'tc-1', 'pause'),
    );
  });

  it('keeps a completed step read-only, showing what it took', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          task_completions: [
            buildStep({ is_completed: true, elapsed_seconds: 75, completed_at: '2026-01-02T00:00:00Z' }),
          ],
        }),
      ),
    );

    renderPage();

    const card = await stepsCard();
    expect(within(card).getByText('01:15')).toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: /timer for Disconnect power/i })).toBeNull();
  });

  it('shows no step control at all on a completed work order', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ status: 'completed', task_completions: [buildStep()] })),
    );

    renderPage();

    const card = await stepsCard();
    expect(within(card).queryByRole('button', { name: /timer for Disconnect power/i })).toBeNull();
  });
});
