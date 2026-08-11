/**
 * Work-order-level tools on the detail page (op-0v4).
 *
 * op-67q5 gave the page a read-only "Tools Required" panel fed by the PM
 * template, so a *corrective* work order — which has no template — could never
 * list a tool, and staging a tool for one job meant rewriting the recurring
 * template. The panel now renders the work order's OWN rows when it has any:
 * a location editable for this job alone, ad-hoc rows addable mid-job, and
 * removal offered only for the ad-hoc ones.
 *
 * One describe block per acceptance criterion, named for it. The template
 * fallback (a work order generated before per-job tools) stays covered by
 * WorkOrderPageTools.test.tsx.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import WorkOrderPage from '../../pages/WorkOrderPage';
import { workOrderAPI } from '../../services/api';
import { WorkOrder, WorkOrderTool, WorkOrderToolRow } from '../../types';

vi.mock('../../services/api');
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useParams: () => ({ id: 'wo-1' }),
  useNavigate: () => jest.fn(),
}));

const mockWorkOrderAPI = workOrderAPI as jest.Mocked<typeof workOrderAPI>;

const okResponse = <T,>(data: T, status = 200) =>
  ({ data, status, statusText: 'OK', headers: {}, config: {} as never }) as never;

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <WorkOrderPage />
      </MemoryRouter>
    </MantineProvider>,
  );

/** A row the work order owns — `is_ad_hoc: false` is a frozen template copy. */
const toolRow = (overrides: Partial<WorkOrderToolRow> = {}): WorkOrderToolRow => ({
  id: 'wot-1',
  work_order: 'wo-1',
  tool: 'mt-1',
  inventory_item: null,
  inventory_item_name: null,
  is_ad_hoc: false,
  name: 'Torque wrench',
  quantity: 1,
  location_hint: 'Tool crib, drawer 3',
  resolved_location: 'Tool crib, drawer 3',
  is_required: true,
  notes: '',
  ...overrides,
});

const adHocRow = (overrides: Partial<WorkOrderToolRow> = {}): WorkOrderToolRow =>
  toolRow({
    id: 'wot-adhoc',
    tool: null,
    is_ad_hoc: true,
    name: 'Bearing puller',
    location_hint: 'Bench 2',
    resolved_location: 'Bench 2',
    ...overrides,
  });

/**
 * `maintenance_item: null` is what makes a work order corrective — no PM
 * template, so `tool_rows` is the only place a tool can come from.
 */
const buildWorkOrder = (
  overrides: Partial<WorkOrder> & { tool_rows?: WorkOrderToolRow[]; tools?: WorkOrderTool[] } = {},
): WorkOrder =>
  ({
    id: 'wo-1',
    short_id: 'wo-001',
    maintenance_item: null,
    maintenance_item_title: 'Bandsaw will not start',
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
    photos: [],
    submissions: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
    // The display payload the server derives from the rows — kept in step so
    // the fixture cannot describe a state the API could not produce.
    tools:
      overrides.tools ??
      (overrides.tool_rows ?? []).map((row) => ({
        id: row.id,
        name: row.name,
        quantity: row.quantity,
        location_hint: row.resolved_location,
        is_required: row.is_required,
        notes: row.notes,
      })),
  }) as WorkOrder;

/** The Tools Required card, scoped so sibling panels cannot answer a query. */
const toolsPanel = async (): Promise<HTMLElement> =>
  (await screen.findByText('Tools Required')).closest('.mantine-Card-root') as HTMLElement;

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

describe('AC-20: WorkOrderPage shows corrective tools', () => {
  it('renders a corrective work order’s tools instead of the empty state', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          tool_rows: [
            adHocRow(),
            adHocRow({
              id: 'wot-2',
              name: 'Feeler gauge',
              is_required: false,
              location_hint: 'Tool crib',
              resolved_location: 'Tool crib',
            }),
          ],
        }),
      ),
    );

    renderPage();

    const panel = await toolsPanel();
    expect(within(panel).getByText('Bearing puller')).toBeInTheDocument();
    expect(within(panel).getByText('Bench 2')).toBeInTheDocument();
    expect(within(panel).getByText('Feeler gauge')).toBeInTheDocument();
    expect(within(panel).queryByText('No tools specified.')).not.toBeInTheDocument();
  });

  it('still shows the empty state when a corrective work order has no tools', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder({ tool_rows: [] })));

    renderPage();

    expect(within(await toolsPanel()).getByText('No tools specified.')).toBeInTheDocument();
  });

  it('shows the resolved location when the row leans on its inventory item', async () => {
    // A blank per-job hint means "wherever the linked item lives" — the server
    // resolves that into `resolved_location`, and the panel reads only that.
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          tool_rows: [
            adHocRow({
              location_hint: '',
              resolved_location: 'Shelf A',
              inventory_item: 'inv-1',
              inventory_item_name: 'Bearing puller set',
            }),
          ],
        }),
      ),
    );

    renderPage();

    expect(within(await toolsPanel()).getByText('Shelf A')).toBeInTheDocument();
  });
});

describe('AC-21: WorkOrderPage edits per-job locations', () => {
  beforeEach(() => {
    localStorage.setItem('is_staff', 'true');
  });

  it('shows the returned per-job location after restaging a template-derived row', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ maintenance_item: 'mi-1', tool_rows: [toolRow()] })),
    );
    mockWorkOrderAPI.updateToolLocation.mockResolvedValue(
      okResponse(toolRow({ location_hint: 'Bench 2', resolved_location: 'Bench 2' })),
    );

    renderPage();

    const input = await screen.findByLabelText('Location for this job — Torque wrench');
    fireEvent.change(input, { target: { value: 'Bench 2' } });
    fireEvent.blur(input);

    await waitFor(() => expect(mockWorkOrderAPI.updateToolLocation).toHaveBeenCalled());
    expect(mockWorkOrderAPI.updateToolLocation).toHaveBeenCalledWith('wo-1', 'wot-1', 'Bench 2');

    const panel = await toolsPanel();
    expect(await within(panel).findByText('Bench 2')).toBeInTheDocument();
    expect(within(panel).queryByText('Tool crib, drawer 3')).not.toBeInTheDocument();
  });

  it('restages an ad-hoc row the same way', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ tool_rows: [adHocRow()] })),
    );
    mockWorkOrderAPI.updateToolLocation.mockResolvedValue(
      okResponse(adHocRow({ location_hint: 'Cart 4', resolved_location: 'Cart 4' })),
    );

    renderPage();

    const input = await screen.findByLabelText('Location for this job — Bearing puller');
    fireEvent.change(input, { target: { value: 'Cart 4' } });
    fireEvent.blur(input);

    await waitFor(() => expect(mockWorkOrderAPI.updateToolLocation).toHaveBeenCalled());
    expect(await within(await toolsPanel()).findByText('Cart 4')).toBeInTheDocument();
  });

  it('never touches the maintenance-template tool editor', async () => {
    // Restaging writes to the work order alone — the PM template's tool API is
    // a different endpoint, and this page must not reach for it.
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ maintenance_item: 'mi-1', tool_rows: [toolRow()] })),
    );
    mockWorkOrderAPI.updateToolLocation.mockResolvedValue(
      okResponse(toolRow({ location_hint: 'Bench 2', resolved_location: 'Bench 2' })),
    );

    renderPage();

    const input = await screen.findByLabelText('Location for this job — Torque wrench');
    fireEvent.change(input, { target: { value: 'Bench 2' } });
    fireEvent.blur(input);

    await waitFor(() => expect(mockWorkOrderAPI.updateToolLocation).toHaveBeenCalled());
    expect(mockWorkOrderAPI.updateWorkOrder).not.toHaveBeenCalled();
  });

  it('does not call the API when the location is left unchanged', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ tool_rows: [adHocRow()] })),
    );

    renderPage();

    fireEvent.blur(await screen.findByLabelText('Location for this job — Bearing puller'));

    await waitFor(() => expect(mockWorkOrderAPI.getWorkOrder).toHaveBeenCalled());
    expect(mockWorkOrderAPI.updateToolLocation).not.toHaveBeenCalled();
  });

  it('keeps the old location when the save fails', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ tool_rows: [adHocRow()] })),
    );
    mockWorkOrderAPI.updateToolLocation.mockRejectedValue(new Error('boom'));

    renderPage();

    const input = await screen.findByLabelText('Location for this job — Bearing puller');
    fireEvent.change(input, { target: { value: 'Cart 4' } });
    fireEvent.blur(input);

    await waitFor(() => expect(mockWorkOrderAPI.updateToolLocation).toHaveBeenCalled());
    expect(within(await toolsPanel()).getByText('Bench 2')).toBeInTheDocument();
  });

  it('offers no location editor to a volunteer', async () => {
    localStorage.clear();
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ tool_rows: [adHocRow()] })),
    );

    renderPage();

    const panel = await toolsPanel();
    expect(within(panel).getByText('Bearing puller')).toBeInTheDocument();
    expect(
      screen.queryByLabelText('Location for this job — Bearing puller'),
    ).not.toBeInTheDocument();
  });
});

describe('AC-22: WorkOrderPage adds ad-hoc tools', () => {
  beforeEach(() => {
    localStorage.setItem('is_staff', 'true');
  });

  /** Open the add-tool form and wait for it to be on screen. */
  const openAddTool = async () => {
    fireEvent.click(await screen.findByRole('button', { name: /add tool/i }));
    await screen.findByLabelText(/tool name/i);
  };

  it('adds a tool from the Tools Required section and shows it', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder({ tool_rows: [] })));
    mockWorkOrderAPI.addTool.mockResolvedValue(
      okResponse(
        adHocRow({
          id: 'wot-new',
          name: 'Bearing puller',
          quantity: 2,
          location_hint: 'Bench 2',
          resolved_location: 'Bench 2',
          notes: 'Three-jaw',
        }),
        201,
      ),
    );

    renderPage();
    await openAddTool();

    fireEvent.change(screen.getByLabelText(/tool name/i), {
      target: { value: 'Bearing puller' },
    });
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText('Location for this job'), {
      target: { value: 'Bench 2' },
    });
    fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Three-jaw' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() => expect(mockWorkOrderAPI.addTool).toHaveBeenCalled());
    expect(mockWorkOrderAPI.addTool).toHaveBeenCalledWith('wo-1', {
      name: 'Bearing puller',
      quantity: 2,
      is_required: true,
      location_hint: 'Bench 2',
      notes: 'Three-jaw',
    });

    const panel = await toolsPanel();
    expect(await within(panel).findByText('Bearing puller')).toBeInTheDocument();
    expect(within(panel).getByText('×2')).toBeInTheDocument();
    expect(within(panel).getByText('Bench 2')).toBeInTheDocument();
    expect(within(panel).getByText('Three-jaw')).toBeInTheDocument();
    expect(within(panel).getByText('Required')).toBeInTheDocument();
    expect(within(panel).queryByText('No tools specified.')).not.toBeInTheDocument();
  });

  it('sends an optional tool as not required', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder({ tool_rows: [] })));
    mockWorkOrderAPI.addTool.mockResolvedValue(
      okResponse(adHocRow({ id: 'wot-new', name: 'Shop vacuum', is_required: false }), 201),
    );

    renderPage();
    await openAddTool();

    fireEvent.change(screen.getByLabelText(/tool name/i), { target: { value: 'Shop vacuum' } });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Required' }));
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() => expect(mockWorkOrderAPI.addTool).toHaveBeenCalled());
    const [, payload] = mockWorkOrderAPI.addTool.mock.calls[0];
    expect(payload.is_required).toBe(false);
    expect(payload.location_hint).toBeUndefined();
  });

  it('joins the existing rows in server order — required first, then by name', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          tool_rows: [toolRow({ id: 'wot-1', name: 'Torque wrench' })],
        }),
      ),
    );
    mockWorkOrderAPI.addTool.mockResolvedValue(
      okResponse(adHocRow({ id: 'wot-new', name: 'Allen keys' }), 201),
    );

    renderPage();
    await openAddTool();
    fireEvent.change(screen.getByLabelText(/tool name/i), { target: { value: 'Allen keys' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() => expect(mockWorkOrderAPI.addTool).toHaveBeenCalled());
    const panel = await toolsPanel();
    const added = await within(panel).findByText('Allen keys');
    expect(
      added.compareDocumentPosition(within(panel).getByText('Torque wrench')) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('leaves the list alone when the add fails', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder({ tool_rows: [] })));
    mockWorkOrderAPI.addTool.mockRejectedValue(new Error('boom'));

    renderPage();
    await openAddTool();
    fireEvent.change(screen.getByLabelText(/tool name/i), { target: { value: 'Bearing puller' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() => expect(mockWorkOrderAPI.addTool).toHaveBeenCalled());
    expect(within(await toolsPanel()).getByText('No tools specified.')).toBeInTheDocument();
  });

  it('offers no add control to a volunteer', async () => {
    localStorage.clear();
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder({ tool_rows: [] })));

    renderPage();

    await toolsPanel();
    expect(screen.queryByRole('button', { name: /add tool/i })).not.toBeInTheDocument();
  });
});

describe('AC-23: WorkOrderPage removes only ad-hoc tools', () => {
  beforeEach(() => {
    localStorage.setItem('is_staff', 'true');
  });

  it('offers remove on the ad-hoc row and not on the template-derived one', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          maintenance_item: 'mi-1',
          tool_rows: [toolRow(), adHocRow()],
        }),
      ),
    );

    renderPage();

    const panel = await toolsPanel();
    expect(within(panel).getByRole('button', { name: 'Remove Bearing puller' })).toBeInTheDocument();
    expect(
      within(panel).queryByRole('button', { name: 'Remove Torque wrench' }),
    ).not.toBeInTheDocument();
  });

  it('drops the ad-hoc row from the section once the API succeeds', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ maintenance_item: 'mi-1', tool_rows: [toolRow(), adHocRow()] })),
    );
    mockWorkOrderAPI.removeTool.mockResolvedValue(okResponse(undefined, 204));

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Remove Bearing puller' }));

    await waitFor(() => expect(mockWorkOrderAPI.removeTool).toHaveBeenCalledWith('wo-1', 'wot-adhoc'));

    const panel = await toolsPanel();
    await waitFor(() =>
      expect(within(panel).queryByText('Bearing puller')).not.toBeInTheDocument(),
    );
    // The template-derived row is untouched.
    expect(within(panel).getByText('Torque wrench')).toBeInTheDocument();
  });

  it('asks the server what to show once the last row is gone', async () => {
    // The `tools` payload was derived from the row just deleted, so it cannot
    // stand in for the empty state — a preventive work order with no rows of
    // its own falls back to its PM template, and only the server knows that
    // list. Without the refetch the deleted tool would reappear.
    mockWorkOrderAPI.getWorkOrder
      .mockResolvedValueOnce(okResponse(buildWorkOrder({ tool_rows: [adHocRow()] })))
      .mockResolvedValueOnce(
        okResponse(
          buildWorkOrder({
            maintenance_item: 'mi-1',
            tool_rows: [],
            tools: [
              {
                id: 'mt-1',
                name: 'Torque wrench',
                quantity: 1,
                location_hint: 'Tool crib, drawer 3',
                is_required: true,
                notes: '',
              },
            ],
          }),
        ),
      );
    mockWorkOrderAPI.removeTool.mockResolvedValue(okResponse(undefined, 204));

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Remove Bearing puller' }));

    await waitFor(() => expect(mockWorkOrderAPI.getWorkOrder).toHaveBeenCalledTimes(2));
    const panel = await toolsPanel();
    await waitFor(() =>
      expect(within(panel).queryByText('Bearing puller')).not.toBeInTheDocument(),
    );
    expect(within(panel).getByText('Torque wrench')).toBeInTheDocument();
  });

  it('keeps the row when the removal fails', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ tool_rows: [adHocRow()] })),
    );
    mockWorkOrderAPI.removeTool.mockRejectedValue(new Error('boom'));

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Remove Bearing puller' }));

    await waitFor(() => expect(mockWorkOrderAPI.removeTool).toHaveBeenCalled());
    expect(within(await toolsPanel()).getByText('Bearing puller')).toBeInTheDocument();
  });

  it('offers no remove control to a volunteer', async () => {
    localStorage.clear();
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ tool_rows: [adHocRow()] })),
    );

    renderPage();

    await toolsPanel();
    expect(screen.queryByRole('button', { name: 'Remove Bearing puller' })).not.toBeInTheDocument();
  });
});
