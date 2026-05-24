/**
 * Tests for InventoryReconciliationPage (oms-90k)
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { NotificationProvider } from '../../contexts/NotificationContext';
import InventoryReconciliationPage from '../../pages/InventoryReconciliationPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

vi.mock('../../components/QRScanner', () => {
  const MockScanner = () => <div data-testid="qr-scanner-mock" />;
  return { __esModule: true, default: MockScanner };
});

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

const renderPage = () =>
  render(
    <MantineProvider>
      <NotificationProvider>
        <MemoryRouter initialEntries={['/inventory/locations/1/reconcile']}>
          <Routes>
            <Route
              path="/inventory/locations/:id/reconcile"
              element={<InventoryReconciliationPage />}
            />
          </Routes>
        </MemoryRouter>
      </NotificationProvider>
    </MantineProvider>,
  );

const gridItems = [
  {
    item_id: 'item-1',
    name: 'Widget A',
    sku: 'SKU-1',
    projected: 20,
    minimum_stock: 5,
    reorder_quantity: 10,
    owning_group_name: '',
  },
  {
    item_id: 'item-2',
    name: 'Widget B',
    sku: 'SKU-2',
    projected: 3,
    minimum_stock: 5,
    reorder_quantity: 10,
    owning_group_name: '',
  },
];

describe('InventoryReconciliationPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (api.inventoryAPI.listLocationReconcileGrid as jest.Mock).mockResolvedValue({
      data: {
        location_id: '1',
        location_name: 'Main Workshop',
        items: gridItems,
      },
    });
    (api.inventoryAPI.submitReconciliationBatch as jest.Mock).mockResolvedValue({
      data: { reconciled: 1, reorders_created: 0, reconciliations: [] },
    });
  });

  it('renders the grid with projected counts', async () => {
    renderPage();
    expect(await screen.findByText(/Reconcile: Main Workshop/)).toBeInTheDocument();
    expect(screen.getByText('Widget A')).toBeInTheDocument();
    expect(screen.getByText('Widget B')).toBeInTheDocument();
    expect(screen.getByText('SKU-1')).toBeInTheDocument();
  });

  it('submits only rows with actual_count entered', async () => {
    renderPage();
    expect(await screen.findByText('Widget A')).toBeInTheDocument();

    const actualInput = screen.getByLabelText('Actual count for Widget A');
    fireEvent.change(actualInput, { target: { value: '18' } });

    const submitBtn = screen.getByTestId('submit-reconciliation');
    expect(submitBtn).toHaveTextContent('Submit (1)');
    fireEvent.click(submitBtn);

    await waitFor(() =>
      expect(api.inventoryAPI.submitReconciliationBatch).toHaveBeenCalledTimes(1),
    );
    const payload = (api.inventoryAPI.submitReconciliationBatch as jest.Mock).mock
      .calls[0][0];
    expect(payload).toHaveLength(1);
    expect(payload[0].item_id).toBe('item-1');
    expect(payload[0].actual_count).toBe(18);
    expect(payload[0].skip_reorder).toBe(false);
  });

  it('shows CSV export/upload controls and renders upload result modal', async () => {
    (api.inventoryAPI.uploadReconcileCsv as jest.Mock).mockResolvedValue({
      data: { created: 2, skipped: 1, errors: [{ row: 4, error: 'bad reason' }] },
    });
    renderPage();
    expect(await screen.findByTestId('export-csv-template')).toBeInTheDocument();
    const uploadBtn = screen.getByTestId('upload-filled-csv');
    expect(uploadBtn).toBeInTheDocument();

    const fileInput = screen.getByTestId('csv-file-input') as HTMLInputElement;
    const file = new File(['item_id,actual_count,reason\n'], 'r.csv', {
      type: 'text/csv',
    });
    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() =>
      expect(api.inventoryAPI.uploadReconcileCsv).toHaveBeenCalledWith(file, false),
    );
    expect(await screen.findByTestId('csv-upload-summary')).toHaveTextContent(
      'Created: 2',
    );
    expect(await screen.findByTestId('csv-upload-errors')).toHaveTextContent(
      'Row 4: bad reason',
    );
  });

  it('honors the skip-reorder checkbox', async () => {
    renderPage();
    expect(await screen.findByText('Widget B')).toBeInTheDocument();

    const actualInput = screen.getByLabelText('Actual count for Widget B');
    fireEvent.change(actualInput, { target: { value: '1' } });

    const skipCheckbox = screen.getByLabelText(
      'Skip reorder for Widget B',
    ) as HTMLInputElement;
    fireEvent.click(skipCheckbox);
    expect(skipCheckbox.checked).toBe(true);

    fireEvent.click(screen.getByTestId('submit-reconciliation'));

    await waitFor(() =>
      expect(api.inventoryAPI.submitReconciliationBatch).toHaveBeenCalledTimes(1),
    );
    const payload = (api.inventoryAPI.submitReconciliationBatch as jest.Mock).mock
      .calls[0][0];
    expect(payload[0].skip_reorder).toBe(true);
  });
});
