/**
 * Tests for the packaging / unit-of-measure helpers (op-lkxl, phase 3).
 *
 * These are the client twins of `inventory/services/packaging.py`, so the cases
 * mirror the backend's rules: a chain shrinks toward exactly one base rung, a
 * pack-counting item needs a resolvable counting level, and anything
 * half-configured degrades to base units rather than throwing.
 */
import { InventoryItem, PackagingLevel } from '../../types';
import {
  PackagingRow,
  baseUnitOf,
  blankPackagingRow,
  countLevelOf,
  countUnitOf,
  countsInPacks,
  describePackChain,
  onHandLabel,
  perParent,
  pluralizeUnit,
  reorderFiling,
  resolveCountLevelError,
  toPackagingPayload,
  toPackagingRows,
  validatePackagingChain,
} from '../../utils/packaging';

const row = (name: string, base_units: number | ''): PackagingRow => ({
  ...blankPackagingRow(),
  name,
  base_units,
});

const level = (
  id: number,
  name: string,
  sort_order: number,
  base_units: number
): PackagingLevel => ({
  id,
  name,
  sort_order,
  base_units,
  per_parent: null,
});

const PAPER_CHAIN = [
  level(1, 'case', 0, 1000),
  level(2, 'ream', 1, 100),
  level(3, 'sheet', 2, 1),
];

const makeItem = (overrides: Partial<InventoryItem> = {}): InventoryItem =>
  ({
    id: 'item-1',
    name: 'Copy paper',
    current_stock: 10,
    minimum_stock: 2,
    reorder_quantity: 4,
    ...overrides,
  }) as InventoryItem;

describe('validatePackagingChain', () => {
  it('accepts an empty chain — such an item is simply counted in base units', () => {
    expect(validatePackagingChain([])).toEqual([]);
  });

  it('accepts a strictly shrinking chain ending in the base rung', () => {
    expect(
      validatePackagingChain([row('case', 1000), row('ream', 100), row('sheet', 1)])
    ).toEqual([]);
  });

  it('requires every level to be named', () => {
    expect(validatePackagingChain([row('case', 12), row('', 1)])).toContain(
      'Every packaging level needs a name.'
    );
  });

  it('requires every level to hold at least one base unit', () => {
    expect(validatePackagingChain([row('case', 12), row('sheet', '')])).toContain(
      'Every packaging level must hold at least one base unit.'
    );
  });

  it('requires exactly one base rung', () => {
    const errors = validatePackagingChain([row('case', 12), row('half', 1), row('sheet', 1)]);
    expect(errors.join(' ')).toMatch(/Exactly one packaging level must be the base unit/);
  });

  it('rejects a chain with no base rung at all', () => {
    const errors = validatePackagingChain([row('case', 12), row('ream', 6)]);
    expect(errors.join(' ')).toMatch(/found 0/);
  });

  it('requires the base rung to be listed last', () => {
    const errors = validatePackagingChain([row('sheet', 1), row('case', 12)]);
    expect(errors).toContain('The base packaging level must be the innermost (listed last).');
  });

  it('requires each level to be smaller than the one containing it', () => {
    const errors = validatePackagingChain([row('case', 10), row('ream', 10), row('sheet', 1)]);
    expect(errors.join(' ')).toMatch(/'ream' must hold fewer base units than 'case'/);
  });
});

describe('resolveCountLevelError', () => {
  const rows = [row('case', 12), row('unit', 1)];

  it('is silent for each-mode, which must have no counting level', () => {
    expect(resolveCountLevelError('each', null, rows)).toBeNull();
    expect(resolveCountLevelError('each', rows[0].key, rows)).toBeNull();
  });

  it('requires a counting level for the pack-counting modes', () => {
    expect(resolveCountLevelError('by_level', null, rows)).toMatch(/Choose which packaging level/);
    expect(resolveCountLevelError('open_closed', null, rows)).toMatch(
      /Choose which packaging level/
    );
  });

  it('requires the counting level to be one of the rows being saved', () => {
    expect(resolveCountLevelError('by_level', 'pkg-does-not-exist', rows)).toMatch(
      /must be one of the packaging levels above/
    );
  });

  it('accepts a level that is in the chain', () => {
    expect(resolveCountLevelError('by_level', rows[0].key, rows)).toBeNull();
  });
});

describe('toPackagingPayload / toPackagingRows', () => {
  it('derives sort_order from row position, outermost first', () => {
    expect(toPackagingPayload([row('case', 1000), row('ream', 100), row('sheet', 1)])).toEqual([
      { name: 'case', sort_order: 0, base_units: 1000 },
      { name: 'ream', sort_order: 1, base_units: 100 },
      { name: 'sheet', sort_order: 2, base_units: 1 },
    ]);
  });

  it('trims names and does not send a pk — the serializer upserts on sort_order', () => {
    const payload = toPackagingPayload([row('  case  ', 12), row('unit', 1)]);
    expect(payload[0]).toEqual({ name: 'case', sort_order: 0, base_units: 12 });
    expect(payload[0]).not.toHaveProperty('id');
  });

  it('orders server levels outermost-first regardless of arrival order', () => {
    const rows = toPackagingRows([PAPER_CHAIN[2], PAPER_CHAIN[0], PAPER_CHAIN[1]]);
    expect(rows.map((r) => r.name)).toEqual(['case', 'ream', 'sheet']);
    expect(rows.map((r) => r.id)).toEqual([1, 2, 3]);
  });

  it('round-trips an empty chain', () => {
    expect(toPackagingRows(undefined)).toEqual([]);
    expect(toPackagingRows(null)).toEqual([]);
  });
});

describe('perParent', () => {
  const rows = [row('case', 1000), row('ream', 100), row('sheet', 1)];

  it('is how many of the next rung down fit in this one', () => {
    expect(perParent(rows, 0)).toBe(10);
    expect(perParent(rows, 1)).toBe(100);
  });

  it('is null for the base rung, which contains nothing', () => {
    expect(perParent(rows, 2)).toBeNull();
  });

  it('is null while a row is half-typed', () => {
    expect(perParent([row('case', ''), row('unit', 1)], 0)).toBeNull();
  });
});

describe('countsInPacks / countUnitOf', () => {
  it('is false for an each-mode item, which keeps base units', () => {
    const item = makeItem({ count_mode: 'each', packaging_levels: PAPER_CHAIN });
    expect(countsInPacks(item)).toBe(false);
    expect(countUnitOf(item)).toBe('unit');
  });

  it('is false for an item that has not opted in at all', () => {
    expect(countsInPacks(makeItem())).toBe(false);
  });

  it('is true for a by_level item with a resolvable counting level', () => {
    const item = makeItem({
      count_mode: 'by_level',
      count_level: 2,
      packaging_levels: PAPER_CHAIN,
    });
    expect(countsInPacks(item)).toBe(true);
    expect(countLevelOf(item)?.name).toBe('ream');
    expect(countUnitOf(item)).toBe('ream');
  });

  it('degrades to base units for a half-configured pack item', () => {
    const item = makeItem({
      count_mode: 'by_level',
      count_level: null,
      packaging_levels: PAPER_CHAIN,
      base_unit: 'sheet',
    });
    expect(countsInPacks(item)).toBe(false);
    expect(countUnitOf(item)).toBe('sheet');
  });
});

describe('onHandLabel', () => {
  it('renders base units for an each-mode item, exactly as before the matrix existed', () => {
    const item = makeItem({
      current_stock: 10,
      count_mode: 'each',
      on_hand_display: { mode: 'each', base_units: 10, unit: 'unit', text: '10 unit' },
    });
    expect(onHandLabel(item)).toBe('10 units');
  });

  it('renders base units for an item with no packaging payload at all', () => {
    expect(onHandLabel(makeItem({ current_stock: 3 }))).toBe('3 units');
  });

  it('honours a custom base unit and singular counts', () => {
    expect(onHandLabel(makeItem({ current_stock: 1, base_unit: 'sheet' }))).toBe('1 sheet');
    expect(onHandLabel(makeItem({ current_stock: 5, base_unit: 'sheet' }))).toBe('5 sheets');
  });

  it("renders the server's text for a by_level item", () => {
    const item = makeItem({
      current_stock: 450,
      count_mode: 'by_level',
      on_hand_display: {
        mode: 'by_level',
        level: 'ream',
        level_count: 4,
        remainder_base: 50,
        text: '4 ream(s)',
      },
    });
    expect(onHandLabel(item)).toBe('4 ream(s)');
  });

  it("renders the server's sealed + open text for an open_closed item", () => {
    const item = makeItem({
      current_stock: 36,
      count_mode: 'open_closed',
      on_hand_display: {
        mode: 'open_closed',
        level: 'box',
        sealed: 3,
        open: 1,
        text: '3 sealed + 1 open',
      },
    });
    expect(onHandLabel(item)).toBe('3 sealed + 1 open');
  });
});

describe('describePackChain', () => {
  it('describes one line per non-base rung', () => {
    expect(describePackChain(PAPER_CHAIN)).toEqual([
      '1 case = 10 reams',
      '1 ream = 100 sheets',
    ]);
  });

  it('says nothing about a bare base rung or an absent chain', () => {
    expect(describePackChain([level(1, 'unit', 0, 1)])).toEqual([]);
    expect(describePackChain(undefined)).toEqual([]);
  });

  it('keeps a singular ratio singular', () => {
    expect(describePackChain([level(1, 'pair', 0, 2), level(2, 'glove', 1, 1)])).toEqual([
      '1 pair = 2 gloves',
    ]);
    expect(describePackChain([level(1, 'sleeve', 0, 1), level(2, 'cup', 1, 1)])).toEqual([
      '1 sleeve = 1 cup',
    ]);
  });
});

describe('pluralizeUnit / baseUnitOf', () => {
  it('pluralises everything but one', () => {
    expect(pluralizeUnit('case', 1)).toBe('case');
    expect(pluralizeUnit('case', 0)).toBe('cases');
    expect(pluralizeUnit('case', 2)).toBe('cases');
  });

  it('pluralises a sibilant ending properly — "box" is a common level name', () => {
    expect(pluralizeUnit('box', 2)).toBe('boxes');
    expect(pluralizeUnit('box', 1)).toBe('box');
    expect(pluralizeUnit('brush', 3)).toBe('brushes');
    expect(pluralizeUnit('glass', 3)).toBe('glasses');
  });

  it("defaults to the backend's own default base unit", () => {
    expect(baseUnitOf({ base_unit: undefined })).toBe('unit');
    expect(baseUnitOf({ base_unit: '   ' })).toBe('unit');
    expect(baseUnitOf({ base_unit: 'sheet' })).toBe('sheet');
  });
});

describe('reorderFiling', () => {
  // The one thing a surface that BOTH shows a reorder quantity and files one
  // may read. It is deliberately a pass-through of the server's answer and not
  // a client twin: a twin would have to reproduce the shortage top-up, and one
  // that quietly dropped it would file less than the page had promised.

  it("hands back the server's base-unit quantity and its wording", () => {
    const item = makeItem({
      reorder_quantity: 3,
      reorder_display: {
        mode: 'by_level',
        unit: 'case',
        threshold: 2,
        current: 2,
        reorder_quantity: 3,
        order_quantity: 36,
        order_text: '3 cases (36 bottles)',
        needs_reorder: true,
        text: '2 cases on hand · reorder at 2 cases',
      },
    });

    expect(reorderFiling(item)).toEqual({ quantity: 36, text: '3 cases (36 bottles)' });
  });

  it('never returns the raw reorder_quantity column for a pack-counting item', () => {
    const item = makeItem({
      reorder_quantity: 3,
      reorder_display: {
        mode: 'by_level',
        unit: 'case',
        threshold: 2,
        current: 2,
        reorder_quantity: 3,
        order_quantity: 36,
        order_text: '3 cases (36 bottles)',
        needs_reorder: true,
        text: '2 cases on hand · reorder at 2 cases',
      },
    });

    expect(reorderFiling(item)?.quantity).not.toBe(item.reorder_quantity);
  });

  it('refuses rather than guessing when the payload carries no answer', () => {
    expect(reorderFiling(makeItem())).toBeNull();
  });

  it('refuses a half-present block rather than filing part of one', () => {
    const partial = makeItem({
      reorder_display: {
        mode: 'each',
        unit: 'unit',
        threshold: 2,
        current: 10,
        reorder_quantity: 4,
        needs_reorder: false,
        text: '10 units on hand · reorder at 2 units',
      } as never,
    });

    expect(reorderFiling(partial)).toBeNull();
  });
});
