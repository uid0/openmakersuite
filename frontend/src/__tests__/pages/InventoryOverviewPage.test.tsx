/**
 * Tests for the inventory overview search box.
 *
 * The page already existed pre-this-PR; this file covers the live
 * search filter added so an operator can find items by name / SKU /
 * category without using the browser's ctrl-F.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import InventoryOverviewPage from '../../pages/InventoryOverviewPage';
import { inventoryAPI, reportsAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    inventoryAPI: {
      ...(actual as any).inventoryAPI,
      listItems: jest.fn(),
      listCategories: jest.fn(),
    },
    // The page mounts <DemandForecastPanel>, which reads these on mount (op-3).
    reportsAPI: {
      ...(actual as any).reportsAPI,
      getDemandForecast: jest.fn(),
      getReorderAlerts: jest.fn(),
    },
  };
});

const mockInv = inventoryAPI as jest.Mocked<typeof inventoryAPI>;
const mockReports = reportsAPI as jest.Mocked<typeof reportsAPI>;

const buildItem = (overrides: Partial<any> = {}) => ({
  id: 'item-1',
  name: 'M3 hex bolt',
  sku: 'SKU-BOLT',
  category_name: 'Fasteners',
  current_stock: 10,
  minimum_stock: 5,
  is_active: true,
  needs_reorder: false,
  has_pending_reorder: false,
  ...overrides,
});

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/inventory']}>
        <InventoryOverviewPage />
      </MemoryRouter>
    </MantineProvider>,
  );

describe('InventoryOverviewPage search', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockReports.getDemandForecast.mockResolvedValue({ data: [] } as any);
    mockReports.getReorderAlerts.mockResolvedValue({ data: [] } as any);
    mockInv.listCategories.mockResolvedValue({
      data: { results: [{ id: 1, name: 'Fasteners' }] },
    } as any);
    mockInv.listItems.mockResolvedValue({
      data: {
        count: 3,
        next: null,
        previous: null,
        results: [
          buildItem({ id: 'a', name: 'M3 hex bolt', sku: 'SKU-BOLT' }),
          buildItem({
            id: 'b',
            name: 'Wood glue',
            sku: 'SKU-GLUE',
            category_name: 'Adhesives',
          }),
          buildItem({
            id: 'c',
            name: 'Sandpaper 220',
            sku: 'SKU-SAND',
            category_name: 'Abrasives',
          }),
        ],
      },
    } as any);
  });

  test('search box renders once items load', async () => {
    renderPage();
    expect(
      await screen.findByTestId('inventory-overview-search'),
    ).toBeInTheDocument();
  });

  test('typing in the search filters by item name', async () => {
    renderPage();
    await screen.findByText('M3 hex bolt');
    const input = screen.getByLabelText('Search inventory');
    fireEvent.change(input, { target: { value: 'wood' } });

    await waitFor(() => {
      expect(screen.getByText('Wood glue')).toBeInTheDocument();
      expect(screen.queryByText('M3 hex bolt')).not.toBeInTheDocument();
      expect(screen.queryByText('Sandpaper 220')).not.toBeInTheDocument();
    });
  });

  test('search matches SKU', async () => {
    renderPage();
    await screen.findByText('M3 hex bolt');
    const input = screen.getByLabelText('Search inventory');
    fireEvent.change(input, { target: { value: 'sku-sand' } });

    await waitFor(() => {
      expect(screen.getByText('Sandpaper 220')).toBeInTheDocument();
      expect(screen.queryByText('Wood glue')).not.toBeInTheDocument();
    });
  });

  test('search matches category name', async () => {
    renderPage();
    await screen.findByText('M3 hex bolt');
    const input = screen.getByLabelText('Search inventory');
    fireEvent.change(input, { target: { value: 'adhes' } });

    await waitFor(() => {
      expect(screen.getByText('Wood glue')).toBeInTheDocument();
      expect(screen.queryByText('M3 hex bolt')).not.toBeInTheDocument();
    });
  });

  test('no-match state surfaces a clear-search link', async () => {
    renderPage();
    await screen.findByTestId('inventory-overview-search');
    const input = screen.getByLabelText('Search inventory');
    fireEvent.change(input, { target: { value: 'zzzzz' } });

    const empty = await screen.findByTestId(
      'inventory-overview-no-matches',
    );
    expect(empty).toBeInTheDocument();
    fireEvent.click(screen.getByText('Clear search'));
    await waitFor(() => {
      expect(screen.getByText('Wood glue')).toBeInTheDocument();
    });
  });

  test('pressing "/" focuses the search box from outside an input', async () => {
    renderPage();
    await screen.findByTestId('inventory-overview-search');
    const input = screen.getByLabelText('Search inventory');
    expect(document.activeElement).not.toBe(input);

    // Slash keypress on document.body — not an input — should focus
    // the search box.
    fireEvent.keyDown(window, { key: '/' });
    await waitFor(() => {
      expect(document.activeElement).toBe(input);
    });
  });
});
