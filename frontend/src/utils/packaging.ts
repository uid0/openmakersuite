/**
 * Unit-of-measure / packaging-chain helpers for the web UI (op-lkxl, phase 3).
 *
 * The client-side twin of `backend/inventory/services/packaging.py`: the same
 * chain rules, so the item form rejects an impossible chain before the request
 * instead of round-tripping a 400, and the same "is this item counted in
 * packs?" predicate every mode-aware surface branches on.
 *
 * The backend stays the authority — anything these helpers let through is still
 * validated server-side, and `on_hand_display` / `reorder_display` text is
 * rendered by the server. Nothing here converts stock: `current_stock` remains
 * the canonical base-unit count.
 */
import { InventoryItem, ItemCountMode, PackagingLevel, PackSummary } from '../types';

/** The two `count_mode`s that count whole packs rather than base units. */
export const PACK_COUNT_MODES: ItemCountMode[] = ['by_level', 'open_closed'];

export const COUNT_MODE_LABELS: Record<ItemCountMode, string> = {
  each: 'Each (count base units)',
  by_level: 'By packaging level (count whole packs)',
  open_closed: 'Sealed + open (count sealed packs, track the open one)',
};

/**
 * A chain rung as the form edits it. `sort_order` is deliberately absent: the
 * editor keeps rows largest-first and derives `sort_order` from the row's index
 * on save, which is exactly the backend's convention (0 = outermost). `key` is
 * a client-only stable identity so the count-level selection survives reorders
 * and inserts, and `base_units` allows '' for a half-typed row.
 */
export interface PackagingRow {
  key: string;
  id?: number;
  name: string;
  base_units: number | '';
}

let rowKeySeq = 0;

/** Mint a stable client-side key for a new chain row. */
export const newPackagingRowKey = (): string => `pkg-${++rowKeySeq}`;

/** An empty editor row, ready to type into. */
export const blankPackagingRow = (): PackagingRow => ({
  key: newPackagingRowKey(),
  name: '',
  base_units: '',
});

/** Server chain → editor rows, ordered outermost-first. */
export const toPackagingRows = (levels?: PackagingLevel[] | null): PackagingRow[] =>
  [...(levels ?? [])]
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((level) => ({
      key: newPackagingRowKey(),
      id: level.id,
      name: level.name,
      base_units: level.base_units,
    }));

/**
 * Editor rows → the nested `packaging_levels` write payload. `sort_order` is
 * the row index, so "largest first" in the UI is "sort_order 0 is outermost" on
 * the wire. The pk is deliberately not sent: the serializer upserts on
 * `(item, sort_order)`, so a rung that keeps its position keeps its pk.
 */
export const toPackagingPayload = (
  rows: PackagingRow[]
): { name: string; sort_order: number; base_units: number }[] =>
  rows.map((row, index) => ({
    name: row.name.trim(),
    sort_order: index,
    base_units: row.base_units === '' ? 0 : Number(row.base_units),
  }));

/**
 * Validate a chain the way `validate_packaging_chain` does, returning every
 * problem found (empty array = valid). An empty chain is valid: an item with no
 * packaging levels is simply counted in base units.
 */
export const validatePackagingChain = (rows: PackagingRow[]): string[] => {
  if (rows.length === 0) return [];

  const errors: string[] = [];

  if (rows.some((row) => !row.name.trim())) {
    errors.push('Every packaging level needs a name.');
  }
  if (rows.some((row) => row.base_units === '' || Number(row.base_units) < 1)) {
    errors.push('Every packaging level must hold at least one base unit.');
  }
  if (errors.length > 0) return errors;

  const sizes = rows.map((row) => Number(row.base_units));
  const baseRungs = sizes.filter((size) => size === 1);
  if (baseRungs.length !== 1) {
    errors.push(
      'Exactly one packaging level must be the base unit (holding 1 base unit); ' +
        `found ${baseRungs.length}.`
    );
  } else if (sizes[sizes.length - 1] !== 1) {
    errors.push('The base packaging level must be the innermost (listed last).');
  }

  rows.forEach((row, index) => {
    if (index === 0) return;
    if (sizes[index] >= sizes[index - 1]) {
      errors.push(
        `Packaging level '${row.name.trim()}' must hold fewer base units than ` +
          `'${rows[index - 1].name.trim()}' that contains it.`
      );
    }
  });

  return errors;
};

/**
 * Why `countLevelKey` is wrong for `countMode`, or null if the pair fits —
 * the client twin of `resolve_count_level_error`.
 */
export const resolveCountLevelError = (
  countMode: ItemCountMode,
  countLevelKey: string | null,
  rows: PackagingRow[]
): string | null => {
  if (countMode === 'each') return null;
  if (!countLevelKey) {
    return 'Choose which packaging level this item is counted in.';
  }
  if (!rows.some((row) => row.key === countLevelKey)) {
    return 'The counting level must be one of the packaging levels above.';
  }
  return null;
};

/**
 * How many of the next rung down fit in this one — the "1 case = 10 reams"
 * number, computed the way `PackagingLevelSerializer.get_per_parent` does.
 * Null for the base (last) rung, which has nothing below it.
 */
export const perParent = (rows: PackagingRow[], index: number): number | null => {
  const own = rows[index]?.base_units;
  const below = rows[index + 1]?.base_units;
  if (own === '' || own === undefined || below === '' || below === undefined) return null;
  if (Number(below) < 1) return null;
  const ratio = Number(own) / Number(below);
  return Number.isFinite(ratio) ? ratio : null;
};

/**
 * `unit` pluralised for `count`. Handles the sibilant ending the naive `+ "s"`
 * gets wrong ("box" → "boxes", not "boxs"), because packaging levels are named
 * by hand and "box" is one of the commonest ones. Deliberately a shade better
 * than the backend's own `_plural`, which only ever appends "s" — nothing
 * compares the two strings, and no server-rendered text is re-pluralised here.
 */
export const pluralizeUnit = (unit: string, count: number): string => {
  if (count === 1) return unit;
  return /(s|x|z|ch|sh)$/i.test(unit) ? `${unit}es` : `${unit}s`;
};

/** The item's base unit, defaulting to the backend's own default. */
export const baseUnitOf = (item: Pick<InventoryItem, 'base_unit'>): string =>
  item.base_unit?.trim() || 'unit';

/** The rung an item is counted in, or null. */
export const countLevelOf = (item: InventoryItem): PackagingLevel | null =>
  (item.packaging_levels ?? []).find((level) => level.id === item.count_level) ?? null;

/**
 * True when the item is counted in whole packs of a usable counting level —
 * the client twin of `counts_in_packs`, and deliberately just as conservative:
 * a half-configured item (a pack mode with no resolvable `count_level`) reads
 * false and keeps today's base-unit behaviour.
 */
export const countsInPacks = (item: InventoryItem): boolean => {
  if (!item.count_mode || item.count_mode === 'each') return false;
  const level = countLevelOf(item);
  return level !== null && level.base_units >= 1;
};

/**
 * The noun a quantity for this item is entered/reported in — the counting
 * rung's name for a pack-counting item, the base unit otherwise. Mirrors
 * `count_unit`, so the UI labels an input with the unit the server will read it
 * in.
 */
export const countUnitOf = (item: InventoryItem): string => {
  const level = countsInPacks(item) ? countLevelOf(item) : null;
  return level ? level.name : baseUnitOf(item);
};

/**
 * The on-hand label for an item.
 *
 * Pack-counting items render the server's `on_hand_display.text` verbatim, so
 * web, ScanTTY and the index card all say the same thing. Each-mode items keep
 * today's base-unit rendering (pluralised `base_unit`, which is "unit" for
 * every item that has not opted in) — the phase invariant is that an each item
 * looks exactly as it did before the packaging matrix existed.
 */
export const onHandLabel = (item: InventoryItem): string => {
  const display = item.on_hand_display;
  if (display && display.mode !== 'each' && display.text) {
    return display.text;
  }
  const unit = baseUnitOf(item);
  return `${item.current_stock} ${pluralizeUnit(unit, item.current_stock)}`;
};

/**
 * The rung an item is *bought* in: the outermost rung of its chain — the client
 * twin of `order_level`. Null for an item that is not counted in packs, which
 * is what keeps every legacy item ordering in its supplier's case.
 */
export const orderLevelOf = (item: InventoryItem): PackagingLevel | null => {
  if (!countsInPacks(item)) return null;
  const ordered = [...(item.packaging_levels ?? [])].sort((a, b) => a.sort_order - b.sort_order);
  return ordered[0] ?? null;
};

/** A rung reduced to the wire shape the order pad sends, or null. */
export const toPackSummary = (level: PackagingLevel | null): PackSummary | null =>
  level ? { name: level.name, base_units: level.base_units } : null;

/** The pack a purchase-order line is ordered in, and which side declared it. */
export interface OrderPack {
  /** Base units in one ordered pack. Always > 1 — below that there is no pack. */
  baseUnits: number;
  /** What one pack is called ("case" for a supplier's, else the rung's name). */
  name: string;
  source: 'supplier' | 'item';
}

/**
 * Which pack a PO line is ordered in — the client twin of
 * `order_packages_for_line`, and deliberately the same precedence:
 *
 * * the SUPPLIER's case wins whenever that vendor declares one, because you buy
 *   what the vendor ships and their package cost is quoted against it;
 * * the item's own outermost rung fills in when the supplier declares none;
 * * neither → null, i.e. the line is ordered in plain base units, exactly as
 *   every `each` item with a supplier case size of 1 always has been.
 *
 * Keeping this in step with the server matters: the form sends the pack count
 * as `order_in_packages`, and a client that picked a different pack would
 * record a line whose package count contradicts its own quantity.
 */
export const resolveOrderPack = (
  supplierQuantityPerPackage: number | null | undefined,
  itemOrderPack?: PackSummary | null
): OrderPack | null => {
  const supplierPack = supplierQuantityPerPackage || 1;
  if (supplierPack > 1) {
    return { baseUnits: supplierPack, name: 'case', source: 'supplier' };
  }
  if (itemOrderPack && itemOrderPack.base_units > 1) {
    return { baseUnits: itemOrderPack.base_units, name: itemOrderPack.name, source: 'item' };
  }
  return null;
};

/**
 * One "1 case = 10 reams" line per non-base rung, innermost rung excluded.
 * Empty for an item with no chain (or a single base rung, which describes
 * nothing).
 */
export const describePackChain = (levels?: PackagingLevel[] | null): string[] => {
  const ordered = [...(levels ?? [])].sort((a, b) => a.sort_order - b.sort_order);
  const lines: string[] = [];
  ordered.forEach((level, index) => {
    const below = ordered[index + 1];
    if (!below || below.base_units < 1) return;
    const ratio = level.base_units / below.base_units;
    lines.push(`1 ${level.name} = ${ratio} ${pluralizeUnit(below.name, ratio)}`);
  });
  return lines;
};
