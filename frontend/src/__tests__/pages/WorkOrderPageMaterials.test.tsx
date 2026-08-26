/**
 * Work-order actual materials & cost (op-xl80, web half of op-768w).
 *
 * The thing that was impossible before this slice: recording what a job
 * actually consumed or cost. A *corrective* work order has no PM template to
 * copy material rows from, so the add-material form is its only path to a
 * material at all — and any work order can now record something bought
 * mid-job, priced, with the receipt attached.
 *
 * Also covers the ordering-side view (op-bu80): the PO lines bought for this
 * job, which is what tells a tech the part is still in transit.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import WorkOrderPage from '../../pages/WorkOrderPage';
import { inventoryAPI, maintenanceAPI, workOrderAPI } from '../../services/api';
import { WorkOrder, WorkOrderMaterialUsage, WorkOrderPurchaseLine } from '../../types';

vi.mock('../../services/api');
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useParams: () => ({ id: 'wo-1' }),
  useNavigate: () => jest.fn(),
}));

const mockWorkOrderAPI = workOrderAPI as jest.Mocked<typeof workOrderAPI>;
const mockInventoryAPI = inventoryAPI as jest.Mocked<typeof inventoryAPI>;
const mockMaintenanceAPI = maintenanceAPI as jest.Mocked<typeof maintenanceAPI>;

const okResponse = <T,>(data: T) =>
  ({ data, status: 200, statusText: 'OK', headers: {}, config: {} as never }) as never;

// `env="test"` keeps Mantine's dropdowns out of their transition, which never
// completes in jsdom and would leave the Select's options display:none.
const renderPage = () =>
  render(
    <MantineProvider env="test">
      <MemoryRouter>
        <WorkOrderPage />
      </MemoryRouter>
    </MantineProvider>,
  );

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
  is_ad_hoc: false,
  inventory_item: null,
  inventory_item_name: null,
  purchase_order_item: null,
  unit_cost: null,
  actual_cost: null,
  receipt_url: null,
  created_at: '2026-01-01T00:00:00Z',
  ...overrides,
});

const buildPurchaseLine = (
  overrides: Partial<WorkOrderPurchaseLine> = {},
): WorkOrderPurchaseLine => ({
  id: 'poi-1',
  purchase_order_id: 'po-1',
  po_number: 'PO-2026-0007',
  po_status: 'ordered',
  supplier_name: 'Grainger',
  name: 'Drive belt A45',
  item_type: 'inventory',
  quantity_ordered: 4,
  quantity_received: 0,
  quantity_pending: 4,
  is_fully_received: false,
  is_settled: false,
  receipt_state: 'not_received',
  receipt_state_label: 'Not received',
  quantity_variance: -4,
  unit_cost: '11.25',
  expected_delivery_date: '2026-02-10',
  expected_shipment_date: null,
  ...overrides,
});

const buildWorkOrder = (overrides: Partial<WorkOrder> = {}): WorkOrder =>
  ({
    id: 'wo-1',
    short_id: 'wo-001',
    maintenance_item: null,
    maintenance_item_title: null,
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
    loto_completion_note: '',
    is_overdue: false,
    task_completions: [],
    material_usage: [],
    actual_material_cost: '0.00',
    loto_completions: [],
    photos: [],
    submissions: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }) as unknown as WorkOrder;

const materialsCard = async () =>
  (await screen.findByText('Materials')).closest('.mantine-Card-root') as HTMLElement;

/** Open the add-material form and wait for it to be on screen. */
const openAddMaterial = async () => {
  fireEvent.click(await screen.findByRole('button', { name: /add material/i }));
  await screen.findByRole('radio', { name: /out-of-pocket receipt/i });
};

beforeEach(() => {
  jest.clearAllMocks();
  mockWorkOrderAPI.addMaterial.mockResolvedValue(okResponse(buildMaterial({ id: 'mu-new' })));
  mockWorkOrderAPI.removeMaterial.mockResolvedValue(okResponse(undefined));
  mockWorkOrderAPI.toggleMaterial.mockResolvedValue(okResponse(buildMaterial()));
  mockInventoryAPI.listItems.mockResolvedValue(okResponse({ results: [] }));
});

describe('WorkOrderPage — adding a material (op-xl80)', () => {
  it('offers the add form on a corrective work order with no materials at all', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder()));

    renderPage();

    const card = await materialsCard();
    expect(within(card).getByText(/no materials recorded/i)).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: /add material/i })).toBeInTheDocument();
  });

  it('posts an ad-hoc line with the quantity, unit and price typed in', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder()));

    renderPage();
    await openAddMaterial();

    fireEvent.change(screen.getByLabelText(/material name/i), { target: { value: 'Coupling' } });
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '3' } });
    fireEvent.change(screen.getByLabelText('Unit'), { target: { value: 'ea' } });
    fireEvent.change(screen.getByLabelText('Unit cost'), { target: { value: '4.25' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() => expect(mockWorkOrderAPI.addMaterial).toHaveBeenCalled());
    const [workOrderId, payload] = mockWorkOrderAPI.addMaterial.mock.calls[0];
    expect(workOrderId).toBe('wo-1');
    expect(payload).toEqual({
      material_name: 'Coupling',
      quantity_used: 3,
      unit: 'ea',
      unit_cost: 4.25,
    });
    // No receipt attached -> a plain JSON body, not multipart.
    expect(payload).not.toBeInstanceOf(FormData);
    // The page reloads so the new line appears with its actual cost.
    expect(mockWorkOrderAPI.getWorkOrder).toHaveBeenCalledTimes(2);
  });

  it('defaults the unit cost from the inventory item that was picked', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder()));
    mockInventoryAPI.listItems.mockResolvedValue(
      okResponse({ results: [{ id: 'item-1', name: 'Bearing 6203', unit_cost: '7.50' }] }),
    );

    renderPage();
    await openAddMaterial();

    fireEvent.change(screen.getByLabelText(/material name/i), { target: { value: 'Bearing' } });
    // The picker is searched server-side (debounced), so wait for the options.
    await waitFor(() => expect(mockInventoryAPI.listItems).toHaveBeenCalled());
    fireEvent.click(screen.getByPlaceholderText('Search inventory…'));
    fireEvent.click(await screen.findByRole('option', { name: 'Bearing 6203' }));

    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() => expect(mockWorkOrderAPI.addMaterial).toHaveBeenCalled());
    const [, payload] = mockWorkOrderAPI.addMaterial.mock.calls[0];
    // Linking stock is what lets marking the line used decrement it, and the
    // item's current price seeds the cost so the tech types one only when the
    // real one differs.
    expect(payload).toMatchObject({ inventory_item: 'item-1', unit_cost: '7.50' });
  });

  it('records an out-of-pocket receipt as a priced line with the image attached', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder()));

    renderPage();
    await openAddMaterial();

    fireEvent.click(screen.getByRole('radio', { name: /out-of-pocket receipt/i }));
    fireEvent.change(screen.getByLabelText('Bought where'), {
      target: { value: 'Ace Hardware' },
    });
    fireEvent.change(screen.getByLabelText('Amount'), { target: { value: '18.40' } });

    const attach = screen.getByRole('button', { name: /attach receipt/i });
    const fileInput = attach.parentElement?.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [new File(['bytes'], 'receipt.jpg', { type: 'image/jpeg' })] },
    });

    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() => expect(mockWorkOrderAPI.addMaterial).toHaveBeenCalled());
    const [, body] = mockWorkOrderAPI.addMaterial.mock.calls[0];
    // A receipt rides along, so the body goes up multipart like a WO photo.
    expect(body).toBeInstanceOf(FormData);
    const formData = body as FormData;
    expect(formData.get('material_name')).toBe('Misc supplies — Ace Hardware');
    expect(Number(formData.get('unit_cost'))).toBe(18.4);
    expect(Number(formData.get('quantity_used'))).toBe(1);
    expect((formData.get('receipt_image') as File).name).toBe('receipt.jpg');
    // An out-of-pocket buy moves no stock — it is money spent, not inventory.
    expect(formData.get('inventory_item')).toBeNull();
  });
});

describe('WorkOrderPage — per-line cost and removal (op-xl80)', () => {
  it('sends an edited unit cost for a line that no checkbox click will carry', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          material_usage: [
            buildMaterial({ is_ad_hoc: true, was_used: true, material: null }),
          ],
        }),
      ),
    );

    renderPage();

    const cost = await screen.findByLabelText('Unit cost for Air filter');
    fireEvent.change(cost, { target: { value: '3.25' } });
    fireEvent.blur(cost);

    // `was_used` rides along unchanged — the toggle is the only write path for
    // the price, and an already-used line still has to be able to record one.
    await waitFor(() =>
      expect(mockWorkOrderAPI.toggleMaterial).toHaveBeenCalledWith(
        'wo-1',
        'mu-1',
        true,
        undefined,
        3.25,
      ),
    );
  });

  it('carries a freshly typed price when the line is checked off as used', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ material_usage: [buildMaterial({ is_ad_hoc: true })] })),
    );

    renderPage();

    fireEvent.change(await screen.findByLabelText('Unit cost for Air filter'), {
      target: { value: '5' },
    });
    fireEvent.click(screen.getByRole('checkbox', { name: /air filter/i }));

    await waitFor(() =>
      expect(mockWorkOrderAPI.toggleMaterial).toHaveBeenCalledWith(
        'wo-1',
        'mu-1',
        true,
        '2.00',
        5,
      ),
    );
  });

  it('locks the price away once the stock decrement is applied', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          material_usage: [
            buildMaterial({
              is_ad_hoc: true,
              was_used: true,
              applied_quantity: 2,
              stock_applied: true,
              unit_cost: '4.00',
              actual_cost: '8.00',
            }),
          ],
        }),
      ),
    );

    renderPage();

    const card = await materialsCard();
    expect(within(card).getByText(/2 from stock/i)).toBeInTheDocument();
    // Both editable fields are gone: changing either after stock moved would
    // desync the reversal from the spend it backs.
    expect(screen.queryByLabelText('Unit cost for Air filter')).not.toBeInTheDocument();
    expect(within(card).getByText(/\$8\.00/)).toBeInTheDocument();
    // Removing it would strand the units taken out of inventory.
    expect(within(card).getByRole('button', { name: /remove air filter/i })).toBeDisabled();
  });

  it('removes an ad-hoc line and leaves template lines alone', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          material_usage: [
            buildMaterial({ id: 'mu-tpl', material_name: 'Air filter' }),
            buildMaterial({
              id: 'mu-adhoc',
              material: null,
              is_ad_hoc: true,
              material_name: 'Zip ties',
            }),
          ],
        }),
      ),
    );

    renderPage();

    const card = await materialsCard();
    // A template row is the frozen copy of what the job was supposed to be —
    // it prints on the sign-off sheet, so it is never removable.
    expect(within(card).queryByRole('button', { name: /remove air filter/i })).toBeNull();

    fireEvent.click(within(card).getByRole('button', { name: /remove zip ties/i }));

    await waitFor(() =>
      expect(mockWorkOrderAPI.removeMaterial).toHaveBeenCalledWith('wo-1', 'mu-adhoc'),
    );
    expect(mockWorkOrderAPI.getWorkOrder).toHaveBeenCalledTimes(2);
  });

  it('shows a PO-sourced line as owned by its purchase order', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          material_usage: [
            buildMaterial({
              id: 'mu-po',
              material: null,
              is_ad_hoc: true,
              purchase_order_item: 'poi-1',
              material_name: 'Drive belt A45',
              was_used: true,
              quantity_used: '4.00',
              unit_cost: '11.25',
              actual_cost: '45.00',
            }),
          ],
        }),
      ),
    );

    renderPage();

    const card = await materialsCard();
    expect(within(card).getByText('from PO')).toBeInTheDocument();
    // The PO owns the quantity and the price — a later receipt re-writes both,
    // so the job page reports them rather than offering edits or a delete.
    expect(screen.queryByLabelText('Unit cost for Drive belt A45')).not.toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: /remove drive belt/i })).toBeNull();
    expect(within(card).getByText(/\$45\.00/)).toBeInTheDocument();
  });

  it('links the receipt backing an out-of-pocket line', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          material_usage: [
            buildMaterial({
              material: null,
              is_ad_hoc: true,
              material_name: 'Misc supplies — Ace Hardware',
              unit_cost: '18.40',
              actual_cost: '18.40',
              receipt_url: 'http://api.test/media/receipt.jpg',
            }),
          ],
        }),
      ),
    );

    renderPage();

    const card = await materialsCard();
    expect(within(card).getByRole('link', { name: 'Receipt' })).toHaveAttribute(
      'href',
      'http://api.test/media/receipt.jpg',
    );
  });
});

describe('WorkOrderPage — actual vs estimated material cost (op-xl80)', () => {
  it('measures the actual against the PM template estimate', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          maintenance_item: 'mi-1',
          actual_material_cost: '25.00',
          material_usage: [
            buildMaterial({ quantity_planned: '2.00', unit_cost: '12.50', actual_cost: '25.00', was_used: true }),
          ],
        }),
      ),
    );
    mockMaintenanceAPI.getItem.mockResolvedValue(
      okResponse({ id: 'mi-1', materials: [{ id: 'm-1', estimated_cost_per_unit: '10.00' }] }),
    );

    renderPage();

    const card = await materialsCard();
    expect(await within(card).findByTestId('wo-actual-material-cost')).toHaveTextContent('$25.00');
    // 2 planned × $10.00 estimated = $20.00, so the job ran $5.00 over.
    const estimated = within(card).getByTestId('wo-estimated-material-cost');
    expect(estimated).toHaveTextContent('$20.00');
    expect(estimated).toHaveTextContent('$5.00 over');
  });

  it('shows the actual alone on a corrective job, which nothing estimated', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          actual_material_cost: '18.40',
          material_usage: [
            buildMaterial({
              material: null,
              is_ad_hoc: true,
              was_used: true,
              unit_cost: '18.40',
              actual_cost: '18.40',
            }),
          ],
        }),
      ),
    );

    renderPage();

    const card = await materialsCard();
    expect(within(card).getByTestId('wo-actual-material-cost')).toHaveTextContent('$18.40');
    expect(within(card).getByText(/no estimate for this job/i)).toBeInTheDocument();
    // No template, so nothing is fetched to compare against.
    expect(mockMaintenanceAPI.getItem).not.toHaveBeenCalled();
  });

  it('falls back to summing the used lines when the payload has no server total', async () => {
    const legacy = buildWorkOrder({
      material_usage: [
        buildMaterial({ was_used: true, actual_cost: '6.00' }),
        // Planned but not used: it cost nothing, so it counts for nothing.
        buildMaterial({ id: 'mu-2', was_used: false, actual_cost: '99.00' }),
      ],
    });
    delete (legacy as Partial<WorkOrder>).actual_material_cost;
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(legacy));

    renderPage();

    const card = await materialsCard();
    expect(within(card).getByTestId('wo-actual-material-cost')).toHaveTextContent('$6.00');
  });
});

describe('WorkOrderPage — ordered for this work order (op-bu80)', () => {
  it('shows what is still in transit and what has landed', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          purchase_order_lines: [
            buildPurchaseLine(),
            buildPurchaseLine({
              id: 'poi-2',
              name: 'Filter cartridge',
              quantity_received: 2,
              quantity_pending: 0,
              is_fully_received: true,
              is_settled: true,
              receipt_state: 'received',
              receipt_state_label: 'Received in full',
              quantity_variance: 0,
              po_status: 'received',
            }),
          ],
        }),
      ),
    );

    renderPage();

    const card = (await screen.findByText('Ordered for this work order')).closest(
      '.mantine-Card-root',
    ) as HTMLElement;
    expect(within(card).getByText('Drive belt A45')).toBeInTheDocument();
    // The point of the section: the part is bought but has not shown up yet.
    expect(within(card).getByText('4 on order')).toBeInTheDocument();
    expect(within(card).getByText('received')).toBeInTheDocument();
    expect(within(card).getAllByRole('link', { name: 'PO-2026-0007' })[0]).toHaveAttribute(
      'href',
      '/purchasing/orders/po-1',
    );
  });

  it('does not present a line closed short as still on its way', async () => {
    // Receiving is finished with it — the missing units were written off, not
    // dispatched. A yellow "2 on order" badge with an expected date would tell
    // the tech to keep waiting for a part nobody is sending.
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          purchase_order_lines: [
            buildPurchaseLine({
              quantity_received: 2,
              quantity_pending: 2,
              is_fully_received: false,
              is_settled: true,
              receipt_state: 'closed_short',
              receipt_state_label: 'Closed short',
              quantity_variance: -2,
              expected_delivery_date: '2026-02-10',
            }),
          ],
        }),
      ),
    );

    renderPage();

    const card = (await screen.findByText('Ordered for this work order')).closest(
      '.mantine-Card-root',
    ) as HTMLElement;
    expect(within(card).queryByText('2 on order')).not.toBeInTheDocument();
    expect(within(card).queryByText(/^Expected /)).not.toBeInTheDocument();
    expect(within(card).getByText('closed short')).toBeInTheDocument();
    // What did arrive is still on the record.
    expect(within(card).getByText(/2\/4 received/)).toBeInTheDocument();
  });

  it('still shows an expected date for a line that really is outstanding', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ purchase_order_lines: [buildPurchaseLine()] })),
    );

    renderPage();

    const card = (await screen.findByText('Ordered for this work order')).closest(
      '.mantine-Card-root',
    ) as HTMLElement;
    expect(within(card).getByText('4 on order')).toBeInTheDocument();
    expect(within(card).getByText(/^Expected /)).toBeInTheDocument();
  });

  it('leaves the section out when nothing was ordered for the job', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder()));

    renderPage();

    await materialsCard();
    expect(screen.queryByText('Ordered for this work order')).not.toBeInTheDocument();
  });
});
