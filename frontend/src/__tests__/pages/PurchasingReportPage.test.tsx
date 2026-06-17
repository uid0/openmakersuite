/**
 * Resilience tests for PurchasingReportPage (#457 R3).
 *
 * The report page loads "spend by supplier" on mount. It has no dedicated
 * error UI — a failed load is logged and leaves the table empty — so the
 * resilience contract is: show a loading row while in flight, an empty row
 * when there is no data, and never crash on a failed request.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor, within } from '@testing-library/react';
import PurchasingReportPage from '../../pages/PurchasingReportPage';
import * as api from '../../services/api';
import { expectNoA11yViolations } from '../helpers/axe';

vi.mock('../../services/api');
vi.mock('../../utils/csvExport', async () => ({
  exportPurchasingReportToCSV: jest.fn(),
}));

const renderPage = () =>
  render(
    <MantineProvider>
      <PurchasingReportPage />
    </MantineProvider>
  );

describe('PurchasingReportPage', () => {
  const mockSupplier = {
    supplier_id: 1,
    supplier_name: 'Acme Supplies',
    total_orders: 5,
    total_spend: 1234.56,
    avg_order_value: 246.91,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  // Mantine Tabs keeps every panel mounted (keepMounted defaults to true), so
  // "Loading..." / "No data available" render in all four (hidden) panels.
  // Scope shared body text to the single visible tab panel.
  const activePanel = () => screen.getByRole('tabpanel');

  it('shows a loading row while the report is in flight', async () => {
    (api.reportsAPI.getPurchasingSpendBySupplier as jest.Mock).mockReturnValue(
      new Promise(() => {})
    );

    renderPage();

    expect(screen.getByText('Purchasing Reports')).toBeInTheDocument();
    expect(await within(activePanel()).findByText('Loading...')).toBeInTheDocument();
  });

  it('shows the empty state when there is no spend data', async () => {
    (api.reportsAPI.getPurchasingSpendBySupplier as jest.Mock).mockResolvedValue({
      data: [],
    });

    renderPage();

    expect(await within(activePanel()).findByText('No data available')).toBeInTheDocument();
  });

  it('renders supplier spend rows once loaded', async () => {
    (api.reportsAPI.getPurchasingSpendBySupplier as jest.Mock).mockResolvedValue({
      data: [mockSupplier],
    });

    renderPage();

    expect(await screen.findByText('Acme Supplies')).toBeInTheDocument();
    expect(screen.getByText('$1234.56')).toBeInTheDocument();
  });

  it('degrades to the empty state without crashing on a failed load', async () => {
    (api.reportsAPI.getPurchasingSpendBySupplier as jest.Mock).mockRejectedValue(
      new Error('boom')
    );

    renderPage();

    // No error banner exists; a failed load simply leaves the table empty.
    expect(await within(activePanel()).findByText('No data available')).toBeInTheDocument();
    expect(screen.getByText('Purchasing Reports')).toBeInTheDocument();
  });

  it('has no accessibility violations with data loaded', async () => {
    (api.reportsAPI.getPurchasingSpendBySupplier as jest.Mock).mockResolvedValue({
      data: [mockSupplier],
    });

    const { container } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('Acme Supplies')).toBeInTheDocument();
    });
    await expectNoA11yViolations(container);
  });
});
