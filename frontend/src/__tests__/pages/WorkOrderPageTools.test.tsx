/**
 * "Tools Required" panel on the work-order detail page (op-67q5).
 *
 * The tech must see what to gather — and where it lives — before walking to
 * the machine, so the panel renders near the top of the page (above the
 * electrical / lockout cards, matching the printed form's running order) and
 * accounts for itself even when the template names no tools.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import WorkOrderPage from '../../pages/WorkOrderPage';
import { workOrderAPI } from '../../services/api';
import { WorkOrder, WorkOrderTool } from '../../types';

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

const buildWorkOrder = (tools?: WorkOrderTool[]): WorkOrder =>
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
    photos: [],
    submissions: [],
    tools,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }) as WorkOrder;

const tool = (overrides: Partial<WorkOrderTool> = {}): WorkOrderTool => ({
  id: 't-1',
  name: 'Torque wrench',
  quantity: 1,
  location_hint: 'Tool crib, drawer 3',
  is_required: true,
  notes: '',
  ...overrides,
});

beforeEach(() => {
  jest.clearAllMocks();
});

describe('WorkOrderPage — Tools Required (op-67q5)', () => {
  it('lists each tool with its location and a Required badge', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder([
          tool(),
          tool({ id: 't-2', name: 'Shop vacuum', location_hint: 'Under bench 2' }),
        ]),
      ),
    );

    renderPage();

    const heading = await screen.findByText('Tools Required');
    const panel = heading.closest('.mantine-Card-root') as HTMLElement;
    expect(within(panel).getByText('Torque wrench')).toBeInTheDocument();
    expect(within(panel).getByText('Tool crib, drawer 3')).toBeInTheDocument();
    expect(within(panel).getByText('Shop vacuum')).toBeInTheDocument();
    expect(within(panel).getAllByText('Required')).toHaveLength(2);
  });

  it('shows the quantity only when more than one is needed', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder([tool({ quantity: 2 }), tool({ id: 't-2', name: 'Pry bar' })])),
    );

    renderPage();

    const panel = (await screen.findByText('Tools Required')).closest(
      '.mantine-Card-root',
    ) as HTMLElement;
    expect(within(panel).getByText('×2')).toBeInTheDocument();
    expect(within(panel).queryByText('×1')).not.toBeInTheDocument();
  });

  it('does not badge an optional tool as Required', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder([tool({ name: 'Shop vacuum', is_required: false })])),
    );

    renderPage();

    const panel = (await screen.findByText('Tools Required')).closest(
      '.mantine-Card-root',
    ) as HTMLElement;
    expect(within(panel).getByText('Shop vacuum')).toBeInTheDocument();
    expect(within(panel).queryByText('Required')).not.toBeInTheDocument();
  });

  it('accounts for itself when the template names no tools', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder([])));

    renderPage();

    const panel = (await screen.findByText('Tools Required')).closest(
      '.mantine-Card-root',
    ) as HTMLElement;
    expect(within(panel).getByText('No tools specified.')).toBeInTheDocument();
  });

  it('renders on an older payload that omits the tools key', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder(undefined)));

    renderPage();

    expect(await screen.findByText('No tools specified.')).toBeInTheDocument();
  });

  it('places the tools panel above the lockout card, as on the printed form', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder([tool()])));

    renderPage();

    const toolsHeading = await screen.findByText('Tools Required');
    const lotoHeading = screen.getByText('Lockout / Tagout');
    expect(
      toolsHeading.compareDocumentPosition(lotoHeading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
