/**
 * A newly added supplier row asserts no lead time at all
 * (oms-new-supplier-row-lead-time-zero).
 *
 * `ItemSupplier.average_lead_time` is NOT NULL with a default of 7, and `0`
 * on that column is a RECORDED answer — a counter-pickup vendor who hands the
 * part over the same day. Seeding a blank row with `0` therefore did not leave
 * the field neutral: it made an operator who added a supplier and never
 * touched the box assert same-day delivery, and lead time feeds planning and
 * reorder scoring.
 *
 * Every other create path — the kit form's `supplier_terms`, the item form's
 * `_sync_primary_supplier`, a bare POST to `/inventory/item-suppliers/` —
 * omits the key and takes the model's own default. This row now does the same,
 * and the editor shows the absence rather than a number nobody supplied.
 */

// ResizeObserver is mocked in setupTests.ts

import { MantineProvider } from '@mantine/core';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import SupplierRelationshipForm, {
  SupplierRelationship,
} from '../../components/SupplierRelationshipForm';
import { relationshipFromSaved, relationshipPayload } from '../../utils/supplierRelationships';
import { ItemSupplier, LeadTimeSource, Supplier } from '../../types';

const renderWithProvider = (component: React.ReactElement) =>
  render(<MantineProvider>{component}</MantineProvider>);

const suppliers: Supplier[] = [
  {
    id: 1,
    name: 'Test Supplier',
    supplier_type: 'local',
    website: 'https://example.com',
    notes: '',
    tax_free_paperwork_filed: false,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
];

/** Click "Add Supplier" and return the row the editor handed back. */
const addedRow = (): SupplierRelationship => {
  const onChange = jest.fn();
  renderWithProvider(
    <SupplierRelationshipForm suppliers={suppliers} relationships={[]} onChange={onChange} />
  );
  fireEvent.click(screen.getByText('Add Supplier'));
  return onChange.mock.calls[0][0][0];
};

const leadTimeBox = () => screen.getByLabelText(/Average Lead Time/i) as HTMLInputElement;

beforeEach(() => {
  jest.clearAllMocks();
});

describe('a supplier row the operator has just added', () => {
  test('does not assert same-day delivery', () => {
    expect(addedRow().average_lead_time).not.toBe(0);
  });

  test('records no lead time at all, so the model default applies', () => {
    expect(addedRow().average_lead_time).toBeNull();
  });

  test('sends no lead time to the server rather than a fabricated one', () => {
    const payload = relationshipPayload({ ...addedRow(), supplier: 1 }, 'item-1');

    expect('average_lead_time' in payload).toBe(false);
  });

  test('shows the absence in the box, and names what will be used instead', () => {
    // The editor is controlled, so the row it just handed back has to be fed
    // in before the box it produces can be read.
    const row = addedRow();
    cleanup();
    renderWithProvider(
      <SupplierRelationshipForm
        suppliers={suppliers}
        relationships={[row]}
        onChange={jest.fn()}
      />
    );

    expect(leadTimeBox().value).toBe('');
    expect(screen.getByText(/default of 7 days/i)).toBeInTheDocument();
  });
});

describe('the lead-time box on an existing row', () => {
  const recorded = (days: number | null): SupplierRelationship => ({
    supplier: 1,
    supplier_sku: 'SKU-001',
    supplier_url: '',
    unit_cost: '10.99',
    package_cost: null,
    quantity_per_package: 1,
    average_lead_time: days,
    is_primary: true,
  });

  test('shows a recorded same-day quote as 0, not as an empty box', () => {
    const onChange = jest.fn();
    renderWithProvider(
      <SupplierRelationshipForm
        suppliers={suppliers}
        relationships={[recorded(0)]}
        onChange={onChange}
      />
    );

    expect(leadTimeBox().value).toBe('0');
  });

  test('keeps a typed 0 as a real same-day quote', () => {
    const onChange = jest.fn();
    renderWithProvider(
      <SupplierRelationshipForm
        suppliers={suppliers}
        relationships={[recorded(7)]}
        onChange={onChange}
      />
    );

    fireEvent.change(leadTimeBox(), { target: { value: '0' } });

    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({ average_lead_time: 0 }),
    ]);
  });

  test('clearing the box records no lead time rather than same-day', () => {
    const onChange = jest.fn();
    renderWithProvider(
      <SupplierRelationshipForm
        suppliers={suppliers}
        relationships={[recorded(7)]}
        onChange={onChange}
      />
    );

    fireEvent.change(leadTimeBox(), { target: { value: '' } });

    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({ average_lead_time: null }),
    ]);
  });
});

/**
 * An edit that leaves the lead-time box alone re-sends the loaded number, with
 * the version it was loaded at.
 *
 * The server keeps a row's lead-time source when a PATCH echoes the stored
 * number (`inventory/services/lead_time_source.py`). The number this page holds
 * is the one it LOADED, and it can differ from what is stored only when the row
 * was written after the page loaded it — the measuring task, or another person.
 * That case used to be dodged by leaving the lead time out (while every other
 * field was still overwritten). It is now refused for the whole row: the PATCH
 * carries `version`, and the server answers a stale one with a 409
 * (`inventory/services/link_version.py`; the refusal is exercised end to end in
 * `InventoryItemFormPage.suppliers.test.tsx`).
 */
describe('an edit that does not touch the lead time', () => {
  const loaded = (days: number, source: LeadTimeSource): ItemSupplier => ({
    id: 91,
    item: 'item-1',
    item_name: 'Hex bolt',
    supplier: 1,
    supplier_name: 'Test Supplier',
    supplier_sku: 'SKU-001',
    supplier_url: '',
    package_upc: '',
    unit_upc: '',
    quantity_per_package: 1,
    package_height: null,
    package_width: null,
    package_length: null,
    package_weight: null,
    package_volume: null,
    unit_weight: null,
    package_dimensions_display: '',
    unit_cost: '10.99',
    package_cost: null,
    average_lead_time: days,
    average_lead_time_source: source,
    is_primary: true,
    is_active: true,
    is_discontinued: false,
    notes: '',
    created_at: '2026-09-13T00:00:00Z',
    updated_at: '2026-09-13T00:00:00Z',
    version: 4,
  });

  test.each([
    [7, 'default'],
    [12, 'measured'],
    [7, 'unknown'],
  ] as const)(
    'an SKU-only edit of a %i-day %s lead time echoes it with the loaded version',
    (days, source) => {
      const saved = loaded(days, source);
      const edited = { ...relationshipFromSaved(saved), supplier_sku: 'SKU-002' };

      expect(relationshipPayload(edited, undefined, saved)).toMatchObject({
        supplier_sku: 'SKU-002',
        average_lead_time: days,
        version: 4,
      });
    }
  );

  test('a create carries no version, having loaded nothing', () => {
    const edited = { ...relationshipFromSaved(loaded(7, 'default')), id: undefined };

    expect('version' in relationshipPayload(edited, 'item-1')).toBe(false);
  });

  test.each([
    [7, 9],
    [7, 0],
  ])('a lead time moved from %i to %i is sent as the operator typed it', (from, to) => {
    const saved = loaded(from, 'default');
    const edited = { ...relationshipFromSaved(saved), average_lead_time: to };

    expect(relationshipPayload(edited, undefined, saved)).toMatchObject({
      average_lead_time: to,
    });
  });
});
