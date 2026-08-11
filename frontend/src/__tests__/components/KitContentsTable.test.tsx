/**
 * KitContentsTable (op-8n0) — the shared read-only "what's in this kit" view,
 * and the live "N kits -> M units" arithmetic behind AC-40.
 *
 * Rendered as a real table rather than a tooltip so the breakdown is reachable
 * on touch and exposed to screen readers as tabular data.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, within } from '@testing-library/react';
import React from 'react';

import KitContentsTable, {
  KitContentsRow,
  perKitQuantity,
  totalUnits,
} from '../../components/inventory/KitContentsTable';

const ROWS: KitContentsRow[] = [
  { component: 'c1', component_name: 'Cyan', component_sku: 'SKU-C', quantity: 1, component_current_stock: 0, component_needs_reorder: true },
  { component: 'c2', component_name: 'Magenta', component_sku: 'SKU-M', quantity: 1, component_current_stock: 9 },
  { component: 'c3', component_name: 'Yellow', quantity: 1 },
  { component: 'c4', component_name: 'Black', quantity: 1 },
  { component: 'c5', component_name: 'Cleaning Kit', quantity: 1 },
];

const renderTable = (props: Partial<React.ComponentProps<typeof KitContentsTable>> = {}) =>
  render(
    <MantineProvider>
      <KitContentsTable rows={ROWS} {...props} />
    </MantineProvider>,
  );

describe('KitContentsTable', () => {
  it('renders one accessible row per component', () => {
    renderTable();
    const table = screen.getByRole('table', { name: /kit contents/i });
    expect(within(table).getAllByRole('row')).toHaveLength(ROWS.length + 1); // + header
    expect(screen.getByText('Cyan')).toBeInTheDocument();
    expect(screen.getByText('Cleaning Kit')).toBeInTheDocument();
  });

  it('omits the "You get" column until a kit quantity is supplied', () => {
    renderTable();
    expect(screen.queryByText('You get')).not.toBeInTheDocument();
  });

  it('shows per-component "You get" totals for the ordered quantity (AC-40)', () => {
    renderTable({ kitQuantity: 2 });
    expect(screen.getByText('You get')).toBeInTheDocument();
    ROWS.forEach((row) => {
      expect(screen.getByTestId(`kit-you-get-${row.component}`)).toHaveTextContent('+2');
    });
  });

  it('flags low components when stock is shown', () => {
    renderTable({ showStock: true });
    const cyanRow = screen.getByTestId('kit-component-row-c1');
    expect(within(cyanRow).getByText('Low')).toBeInTheDocument();
    const magentaRow = screen.getByTestId('kit-component-row-c2');
    expect(within(magentaRow).queryByText('Low')).not.toBeInTheDocument();
  });

  it('explains an empty bill of materials rather than rendering an empty table', () => {
    render(
      <MantineProvider>
        <KitContentsTable rows={[]} />
      </MantineProvider>,
    );
    expect(screen.getByTestId('kit-contents-table-empty')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  describe('arithmetic helpers', () => {
    it('reads the per-kit count from either payload shape', () => {
      // Kit API rows call it `quantity`; PO line rows call it `quantity_per_kit`.
      expect(perKitQuantity({ component: 'x', component_name: 'X', quantity: 3 })).toBe(3);
      expect(
        perKitQuantity({ component: 'x', component_name: 'X', quantity_per_kit: 4 }),
      ).toBe(4);
    });

    it('totals base units across the kit — "2 kits -> 10 units"', () => {
      expect(totalUnits(ROWS, 2)).toBe(10);
      expect(totalUnits(ROWS, 0)).toBe(0);
      expect(totalUnits([], 5)).toBe(0);
    });

    it('scales a mixed bill of materials correctly', () => {
      const mixed: KitContentsRow[] = [
        { component: 'a', component_name: 'A', quantity: 2 },
        { component: 'b', component_name: 'B', quantity: 3 },
      ];
      expect(totalUnits(mixed, 4)).toBe(20);
    });
  });
});
