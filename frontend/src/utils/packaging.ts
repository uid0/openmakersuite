/**
 * Unit-of-measure / packaging-chain helpers for the web UI (op-lkxl, phase 3).
 *
 * The client-side twin of `backend/inventory/services/packaging.py`: the same
 * chain rules, so the item form rejects an impossible chain before the request
 * instead of round-tripping a 400, and the same "is this item counted in
 * packs?" predicate every mode-aware surface branches on.
 *
 * The backend stays the authority — anything these helpers let through is still
 * validated server-side, and `on_hand_display` / `reorder_display` are the
 * server's own renderings, which the helpers below prefer where they apply.
 * Both fields are optional on the wire, so those helpers also carry a client
 * twin of the server's branch for payloads that omit them. Nothing here
 * converts stock: `current_stock` remains the canonical base-unit count.
 */
import { InventoryItem, ItemCountMode, PackagingLevel } from '../types';

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
 * by hand and "box" is one of the commonest ones.
 *
 * The SAME rule as the backend's `inventory.services.packaging._plural`, which
 * now shares it. That matters because server-rendered wording and this one meet
 * on the same screen: `reorderFiling` renders the server's `order_text`
 * verbatim beside labels built here, so a unit that pluralised one way on the
 * server and another way in the browser read as two different nouns.
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
 * The server's own reorder presentation, or the client twin when it is absent.
 *
 * `reorder_display` is OPTIONAL on the wire — a narrowed list payload may omit
 * it — so the fallback has to be correct on its own. It mirrors
 * `inventory.services.packaging.reorder_threshold` / `reorder_display` branch
 * for branch, and the unknown-case-size branch is the one that matters: the
 * threshold there is `max(minimum_stock, minimum_cases)`, which is exactly the
 * boundary `needs_reorder`'s disjunction uses. The bare `minimum_stock` is
 * WRONG and understates it whenever `minimum_cases > minimum_stock` — which is
 * the default configuration, since `minimum_stock` defaults to 0 and
 * `minimum_cases` to 1. A card that flags an item LOW and then names a
 * threshold that item clears is the same defect this branch exists to close.
 */
const reorderPresentation = (
  item: InventoryItem
): { unit: string; threshold: number; quantity: number } => {
  const display = item.reorder_display;
  if (display) {
    return {
      unit: display.unit,
      threshold: display.threshold,
      quantity: display.reorder_quantity,
    };
  }
  if (countsInPacks(item)) {
    return {
      unit: countUnitOf(item),
      threshold: item.minimum_stock,
      quantity: item.reorder_quantity,
    };
  }
  if (item.use_case_based_reorder) {
    // A case count we cannot compute cannot carry a threshold either: judge it
    // in the unit that CAN be counted, at the boundary the flag actually uses.
    if (typeof item.current_cases !== 'number') {
      return {
        unit: baseUnitOf(item),
        threshold: Math.max(item.minimum_stock, item.minimum_cases),
        quantity: item.reorder_quantity,
      };
    }
    return { unit: 'case', threshold: item.minimum_cases, quantity: item.reorder_cases };
  }
  return {
    unit: baseUnitOf(item),
    threshold: item.minimum_stock,
    quantity: item.reorder_quantity,
  };
};

/**
 * "3 units" / "2 cases" — the reorder POINT with the unit it is measured in.
 *
 * THE one place the web answers "what is this item's threshold, and in what
 * unit?". Every surface that names a threshold reads this, so a page can no
 * longer print a number the flag does not use, or a unit the same card has just
 * said it cannot compute. Three surfaces used to re-derive it from raw columns
 * and the set of them had to be recalled every time the rule moved.
 */
export const reorderThresholdLabel = (item: InventoryItem): string => {
  const { unit, threshold } = reorderPresentation(item);
  return `${threshold} ${pluralizeUnit(unit, threshold)}`;
};

/**
 * "40 units" / "2 cases" — the item's CONFIGURED reorder amount, with the unit
 * it is counted in. The quantity twin of {@link reorderThresholdLabel}, same
 * single owner.
 *
 * NOT what a reorder for this item would actually order — that is
 * {@link reorderFiling}, and the two are different numbers for a pack-counting
 * item and for any item well below its minimum. Read this wherever the reader
 * is not being promised what will be filed: a surface that only DESCRIBES an
 * item, or one whose own form states and sends its number. Read
 * `reorderFiling` on a surface that files a reorder for the reader, so it
 * cannot show one number and send another.
 */
export const reorderQuantityLabel = (item: InventoryItem): string => {
  const { unit, quantity } = reorderPresentation(item);
  return `${quantity} ${pluralizeUnit(unit, quantity)}`;
};

/**
 * What filing a reorder for this item right now would order, and how to say it.
 *
 * THE one thing a surface that BOTH shows a reorder quantity AND files one may
 * read. `reorderQuantityLabel` above answers a different question — the item's
 * CONFIGURED reorder amount in its own counting unit — and the two are not the
 * same number: they differ by the pack size for a pack-counting item (3 cases
 * = 36 bottles) and by the server's shortage top-up for any item well below its
 * minimum. ScanPage printed the first and POSTed a third derivation of its own,
 * so a member read "3 cases" off a shelf label and had 3 bottles ordered.
 *
 * `quantity` is BASE units, which is what a `ReorderRequest.quantity` is stored
 * in — `mark-received` adds it straight to `current_stock`. Both halves come
 * from the SERVER's `base_reorder_quantity`, the same derivation that fills a
 * purchase-order pad, so no client re-derives it.
 *
 * Returns null when `reorder_display` is absent — it is optional on the wire,
 * and a page that cannot learn what it would file must say so rather than
 * guess. Deliberately NOT given a client twin: the shortage top-up needs the
 * server's count-at-level maths, and a twin that silently dropped it would file
 * less than the page promised, which is the defect this function exists to
 * close.
 */
export const reorderFiling = (
  item: InventoryItem
): { quantity: number; text: string } | null => {
  const display = item.reorder_display;
  if (!display || typeof display.order_quantity !== 'number' || !display.order_text) {
    return null;
  }
  return { quantity: display.order_quantity, text: display.order_text };
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
