/**
 * The inventory card's threshold and reorder badge name ONE unit (op-c1ke).
 *
 * This card is the third surface that used to re-derive a reorder threshold and
 * its unit from raw columns. For a case-based item whose case size is unknown it
 * printed "Current Cases: —" and then a threshold and a badge in cases — a unit
 * the same card had just said it could not compute — while the server flagged
 * the item on base units. Every number that names a unit now reads the single
 * owner, `reorderThresholdLabel` / `reorderQuantityLabel`.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import InventoryList from '../../components/InventoryList';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

// The DISAGREEING side, and also the DEFAULT configuration: `minimum_stock`
// defaults to 0 and `minimum_cases` to 1, so a case-based item is normally
// configured in cases with `minimum_stock` left at 0. The threshold the flag
// uses here is max(0, 3) = 3, NOT the bare `minimum_stock` of 0 — which 2 units
// on hand would clear, contradicting the reorder badge on the same card.
const unknownCaseItem = {
  id: 'item-1',
  name: 'Solvent',
  sku: 'SOL-1',
  description: 'A case-based item whose only vendor is gone',
  use_case_based_reorder: true,
  current_cases: null,
  current_stock: 2,
  minimum_stock: 0,
  minimum_cases: 3,
  reorder_quantity: 40,
  reorder_cases: 2,
  needs_reorder: true,
  has_pending_reorder: false,
  location: '',
  category_name: '',
  thumbnail: null,
  unit_cost: null,
};

const serverDisplay = {
  mode: 'each',
  unit: 'unit',
  threshold: 3,
  current: 2,
  reorder_quantity: 40,
  needs_reorder: true,
  text: '2 units on hand · reorder at 3 units',
};

const renderWith = async (item: Record<string, unknown>) => {
  (api.inventoryAPI.listItems as jest.Mock).mockResolvedValue({
    data: { results: [item] },
  });
  render(
    <MemoryRouter>
      <InventoryList />
    </MemoryRouter>
  );
  await waitFor(() => {
    expect(screen.getByText('Solvent')).toBeInTheDocument();
  });
};

describe('InventoryList card units', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('names the threshold the flag uses, in base units, when the case size is unknown', async () => {
    await renderWith({ ...unknownCaseItem, reorder_display: serverDisplay });

    expect(screen.getByText('3 units')).toBeInTheDocument();
    expect(screen.queryByText(/3 cases/)).toBeNull();
    // The badge agrees with the threshold beside it: both base units.
    expect(screen.getByText(/Needs Reorder/)).toHaveTextContent('40 units');
    expect(screen.getByText(/Needs Reorder/)).not.toHaveTextContent(/case/i);
  });

  it('falls back to the flag threshold when reorder_display is absent', async () => {
    await renderWith(unknownCaseItem);

    expect(screen.getByText('3 units')).toBeInTheDocument();
    expect(screen.getByText(/Needs Reorder/)).toHaveTextContent('40 units');
  });

  it('still names cases when the case size IS known', async () => {
    await renderWith({
      ...unknownCaseItem,
      current_cases: 2.5,
      current_stock: 30,
      reorder_display: {
        ...serverDisplay,
        unit: 'case',
        threshold: 3,
        current: 2.5,
        reorder_quantity: 2,
        text: '2.5 cases on hand · reorder at 3 cases',
      },
    });

    expect(screen.getByText('3 cases')).toBeInTheDocument();
    expect(screen.getByText(/Needs Reorder/)).toHaveTextContent('2 cases');
  });
});
