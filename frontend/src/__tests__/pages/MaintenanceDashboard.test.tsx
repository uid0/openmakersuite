/**
 * Tests for MaintenanceDashboard low-stock warning flow.
 *
 * Covers AC #4: the work-order creation form displays low-stock alerts with a
 * reorder CTA when GET check_material_stock returns any alerts.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import MaintenanceDashboard from '../../pages/MaintenanceDashboard';
import { reorderAPI, workOrderAPI } from '../../services/api';
import { MaintenanceItem } from '../../types';

vi.mock('../../services/api');

const mockWorkOrderAPI = workOrderAPI as jest.Mocked<typeof workOrderAPI>;
const mockReorderAPI = reorderAPI as jest.Mocked<typeof reorderAPI>;

const buildItem = (overrides: Partial<MaintenanceItem> = {}): MaintenanceItem => ({
  id: 'mi-1',
  asset: 'asset-1',
  asset_name: 'Test Asset',
  asset_tag: 'TAG-1',
  title: 'Monthly inspection',
  description: '',
  instructions: '',
  estimated_time_minutes: null,
  estimated_cost: '0',
  interval_days: 30,
  last_completed_at: null,
  is_active: true,
  is_overdue: true,
  days_overdue: 2,
  next_due_at: null,
  materials: [],
  tasks: [],
  created_at: '',
  updated_at: '',
  ...overrides,
});

const renderDashboard = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <MaintenanceDashboard />
      </MemoryRouter>
    </MantineProvider>
  );

const mockListsEmpty = () => {
  // No due items by default — the single overdue item is injected per-test.
  mockWorkOrderAPI.getDueThisMonth.mockResolvedValue({ data: [] } as any);
  mockWorkOrderAPI.listWorkOrders.mockResolvedValue({ data: { results: [] } } as any);
};

describe('MaintenanceDashboard low-stock flow', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockListsEmpty();
  });

  test('generates work order directly when no low-stock alerts', async () => {
    const item = buildItem();
    mockWorkOrderAPI.getDueThisWeek.mockResolvedValue({ data: [item] } as any);
    mockWorkOrderAPI.checkMaterialStock.mockResolvedValue({
      data: { low_stock_alerts: [] },
    } as any);
    mockWorkOrderAPI.generateWorkOrder.mockResolvedValue({ data: {} } as any);

    renderDashboard();
    await waitFor(() => expect(mockWorkOrderAPI.getDueThisWeek).toHaveBeenCalled());

    const [generateBtn] = await screen.findAllByRole('button', { name: /generate work order/i });
    await userEvent.click(generateBtn);

    await waitFor(() => {
      expect(mockWorkOrderAPI.checkMaterialStock).toHaveBeenCalledWith('mi-1');
      expect(mockWorkOrderAPI.generateWorkOrder).toHaveBeenCalledWith('mi-1');
    });
    expect(screen.queryByTestId('low-stock-banner')).not.toBeInTheDocument();
  });

  test('shows low-stock banner with reorder CTA when alerts returned', async () => {
    const item = buildItem();
    mockWorkOrderAPI.getDueThisWeek.mockResolvedValue({ data: [item] } as any);
    mockWorkOrderAPI.checkMaterialStock.mockResolvedValue({
      data: {
        low_stock_alerts: [
          {
            material_id: 'mat-1',
            item_id: 'inv-1',
            name: 'Air Filter',
            current: 1,
            minimum: 5,
            reorder_qty: 10,
          },
        ],
      },
    } as any);

    renderDashboard();
    await waitFor(() => expect(mockWorkOrderAPI.getDueThisWeek).toHaveBeenCalled());

    const [generateBtn] = await screen.findAllByRole('button', { name: /generate work order/i });
    await userEvent.click(generateBtn);

    const banner = await screen.findByTestId('low-stock-banner');
    expect(banner).toBeInTheDocument();
    expect(screen.getByTestId('low-stock-alert')).toHaveTextContent('Low stock: Air Filter: 1/5');
    expect(
      screen.getByRole('button', { name: /create reorder requests.*continue/i })
    ).toBeInTheDocument();
    expect(mockWorkOrderAPI.generateWorkOrder).not.toHaveBeenCalled();
  });

  test('reorder CTA creates reorder requests and then generates work order', async () => {
    const item = buildItem();
    mockWorkOrderAPI.getDueThisWeek.mockResolvedValue({ data: [item] } as any);
    mockWorkOrderAPI.checkMaterialStock.mockResolvedValue({
      data: {
        low_stock_alerts: [
          {
            material_id: 'mat-1',
            item_id: 'inv-1',
            name: 'Air Filter',
            current: 1,
            minimum: 5,
            reorder_qty: 10,
          },
        ],
      },
    } as any);
    mockReorderAPI.createRequest.mockResolvedValue({ data: {} } as any);
    mockWorkOrderAPI.generateWorkOrder.mockResolvedValue({ data: {} } as any);

    renderDashboard();
    await waitFor(() => expect(mockWorkOrderAPI.getDueThisWeek).toHaveBeenCalled());

    const [generateBtn] = await screen.findAllByRole('button', { name: /generate work order/i });
    await userEvent.click(generateBtn);

    const reorderBtn = await screen.findByRole('button', {
      name: /create reorder requests.*continue/i,
    });
    await userEvent.click(reorderBtn);

    await waitFor(() => {
      expect(mockReorderAPI.createRequest).toHaveBeenCalledWith(
        expect.objectContaining({ item: 'inv-1', quantity: 10 })
      );
      expect(mockWorkOrderAPI.generateWorkOrder).toHaveBeenCalledWith('mi-1');
    });
  });
});

describe('MaintenanceDashboard manual PDF upload', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockListsEmpty();
    mockWorkOrderAPI.getDueThisWeek.mockResolvedValue({ data: [] } as any);
    localStorage.setItem('is_staff', 'true');
  });

  afterEach(() => {
    localStorage.removeItem('is_staff');
  });

  test('staff can upload a PDF and result modal lists completed steps', async () => {
    mockWorkOrderAPI.uploadPdf.mockResolvedValue({
      data: {
        submission_id: 'sub-1',
        status: 'applied',
        work_order_id: 'wo-9',
        completed_items: [
          { id: 'tc-1', task_title: 'Step 1' },
          { id: 'tc-2', task_title: 'Step 2' },
        ],
        errors: [],
      },
    } as any);

    renderDashboard();
    await waitFor(() => expect(mockWorkOrderAPI.getDueThisWeek).toHaveBeenCalled());

    const uploadBtn = await screen.findByRole('button', {
      name: /upload completed work order pdf/i,
    });
    expect(uploadBtn).toBeInTheDocument();

    // FileButton hides a real <input type="file"> behind the button. Drive it
    // directly so we don't depend on Mantine's click-bridging.
    const fileInput = uploadBtn.parentElement?.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement;
    expect(fileInput).toBeTruthy();
    const file = new File(['%PDF-1.4 fake'], 'completed.pdf', { type: 'application/pdf' });
    await userEvent.upload(fileInput, file);

    await waitFor(() =>
      expect(mockWorkOrderAPI.uploadPdf).toHaveBeenCalledWith(expect.any(File))
    );

    const modal = await screen.findByTestId('upload-result-modal');
    expect(modal).toHaveTextContent(/PDF applied successfully/i);
    const items = await screen.findAllByTestId('upload-completed-item');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('Step 1');
    expect(items[1]).toHaveTextContent('Step 2');
  });

  test('upload button is hidden for non-staff users', async () => {
    localStorage.setItem('is_staff', 'false');
    renderDashboard();
    await waitFor(() => expect(mockWorkOrderAPI.getDueThisWeek).toHaveBeenCalled());

    expect(
      screen.queryByRole('button', { name: /upload completed work order pdf/i })
    ).not.toBeInTheDocument();
  });
});
