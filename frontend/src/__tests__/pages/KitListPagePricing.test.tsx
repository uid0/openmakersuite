/**
 * The kit list's "Unit cost" column (op-9m2v).
 *
 * `KitSerializer` subclasses `InventoryItemSerializer`, so `unit_cost` reaches
 * it the same way: a model PROPERTY named in `Meta.fields` with no explicit
 * declaration becomes a `ReadOnlyField`, and DRF's `JSONEncoder` sends the raw
 * `Decimal` as a JSON NUMBER. `{kit.unit_cost ? ... : '—'}` therefore read a
 * donated kit's `0` as "nobody priced this" and printed the em-dash this table
 * reserves for a genuinely absent price. The same cell also interpolated the
 * number raw, so 5.1 read as "$5.1" rather than "$5.10".
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import KitListPage from '../../pages/KitListPage';
import { kitAPI } from '../../services/api';

vi.mock('../../services/api', () => ({
  kitAPI: { listKits: vi.fn() },
}));

const KIT = {
  id: 'k1',
  name: 'Ink Kit',
  sku: 'KIT-1',
  supplier_sku: 'SUP-1',
  component_count: 3,
  components: [],
  is_active: true,
  is_kit: true as const,
};

const renderWithCost = async (unitCost: number | null) => {
  (kitAPI.listKits as ReturnType<typeof vi.fn>).mockResolvedValue({
    data: { results: [{ ...KIT, unit_cost: unitCost }] },
  });
  render(
    <MantineProvider>
      <MemoryRouter>
        <KitListPage />
      </MemoryRouter>
    </MantineProvider>,
  );
  await waitFor(() => expect(screen.getByTestId('kit-row-k1')).toBeInTheDocument());
  return within(screen.getByTestId('kit-row-k1'));
};

describe('the kit list unit-cost column', () => {
  it('BEFORE/AFTER: prices a donated kit at $0.00 rather than dashing it', async () => {
    const row = await renderWithCost(0);

    expect(row.getByText('$0.00')).toBeInTheDocument();
    expect(row.queryByText('—')).toBeNull();
  });

  it('CONTROL: a kit nobody has priced still shows the em-dash', async () => {
    const row = await renderWithCost(null);

    expect(row.getByText('—')).toBeInTheDocument();
    expect(row.queryByText(/\$/)).toBeNull();
  });

  it('CONTROL: an ordinary price is unchanged', async () => {
    const row = await renderWithCost(89.99);

    expect(row.getByText('$89.99')).toBeInTheDocument();
  });

  it('writes a trailing zero cent in full', async () => {
    const row = await renderWithCost(5.1);

    expect(row.getByText('$5.10')).toBeInTheDocument();
    expect(row.queryByText('$5.1')).toBeNull();
  });
});
