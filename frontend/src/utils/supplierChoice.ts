/**
 * The web's one reading of `InventoryItem.supplier_choice` (op-3xsp).
 *
 * `inventory/services/supplier_selection.py` decides WHICH supplier an item is
 * bought from; this file is the single place that READS that answer for the web
 * — which name is the chosen one, which others were on offer, and which caveats
 * an operator has to be told. Neither half is re-derived here: nothing below
 * ranks, filters or picks a link, and nothing below reads `is_active` /
 * `is_discontinued` / `is_primary` to work out what the server already said.
 *
 * Surfaces compose their own sentence around these values, because they cannot
 * share one: a scan row dims the alternatives in a separate span, a CSV puts
 * them in their own column, and a logged-out visitor gets a count instead of
 * the list. What must not be re-derived is the ANSWER, not the punctuation.
 *
 * The reason a shared reading is needed at all: the flat `item.supplier_name`
 * key these surfaces used to render is the same winner with the derivation
 * thrown away. It cannot say that four other suppliers were on offer, that the
 * scoring chose this one without knowing a price for it, or that the operator's
 * own flagged primary was skipped as unbuyable — so an item with three sources
 * rendered as an item with one on a scan screen, a reorder queue and an
 * exported CSV somebody then ordered from.
 */
import { SupplierChoice } from '../types';

/** What a surface says where the server sent no `supplier_choice` at all. */
export const SUPPLIER_CHOICE_UNKNOWN = 'Supplier information was not included in this response.';

/**
 * The two "nothing to buy from" answers, worded apart.
 *
 * They are different facts needing different actions — "nobody has said where
 * this comes from" versus "everyone who did is inactive or discontinued" — and
 * the whole point of `reason` is that the server distinguishes them.
 */
const NO_SUPPLIER_TEXT: Record<string, string> = {
  no_suppliers: 'No supplier is linked to this item.',
  none_orderable: 'No supplier here can be ordered from — every link is inactive or discontinued.',
};

/**
 * How the winner won, in words.
 *
 * The distinction matters to a purchaser: `flagged_primary` is somebody's
 * standing decision, `best_scored` is the system weighing price, lead time and
 * delivery record because nobody made one — and the remedy for disagreeing
 * with the second is to flag a primary on the item form.
 */
export const SUPPLIER_BASIS_LABELS: Record<string, string> = {
  flagged_primary: 'flagged primary',
  best_scored: 'price, lead time and delivery record',
};

/**
 * The chosen supplier's name, or null when there is none.
 *
 * Null covers both "the server told us nothing can be bought" and "the server
 * did not send the field"; a caller that must tell those apart asks
 * {@link supplierChoiceNote}, which words all three cases.
 */
export const chosenSupplierName = (choice: SupplierChoice | undefined): string | null =>
  choice?.supplier_name ?? null;

/**
 * The chosen supplier plus how many others were on offer — "Acme, or 2 others".
 *
 * The compact form, for a table cell or a list row with no room for a sentence.
 * Returns null where there is no supplier to name; the caller pairs that with
 * {@link supplierChoiceNote}.
 */
export const supplierChoiceSummary = (choice: SupplierChoice | undefined): string | null => {
  const name = chosenSupplierName(choice);
  if (name === null) return null;
  const others = choice?.alternatives?.length ?? 0;
  if (others === 0) return name;
  return others === 1 ? `${name}, or 1 other` : `${name}, or ${others} others`;
};

/**
 * The other suppliers' names, in the order they arrived.
 *
 * NOT a ranked runner-up order. The server preserves candidate arrival order,
 * which is `ItemSupplier.Meta.ordering` — `-is_primary`, then `unit_cost`
 * ascending — and never re-sorts the losers by score. The CSV's "Other
 * Suppliers" column and the scan page's "also available from" line both render
 * this list, and neither may present it as "the next best".
 */
export const alternativeSupplierNames = (choice: SupplierChoice | undefined): string[] =>
  (choice?.alternatives ?? []).map((alternative) => alternative.supplier_name);

/**
 * Everything qualifying the choice that an operator has to be told, as
 * separate sentences.
 *
 * Empty for a clean choice. Each entry is a fact the SERVER reported, never one
 * inferred here:
 *
 * - the scoring won with no price on file, so a blank cost cell means "we chose
 *   a supplier nobody has priced" rather than "there is no supplier";
 * - the scoring won with no delivery history, so the lead time beside it is a
 *   promise nothing has yet tested;
 * - an operator's flagged primary was skipped as unbuyable, which reads to them
 *   as their choice being ignored unless it is said out loud.
 */
export const supplierChoiceCaveats = (choice: SupplierChoice | undefined): string[] => {
  if (!choice || choice.supplier_name === null) return [];
  const caveats: string[] = [];
  if (choice.scored_without_price) {
    caveats.push('chosen without a price on file');
  }
  if (choice.scored_without_history) {
    caveats.push('chosen with no delivery history');
  }
  if (choice.flagged_primary_unorderable) {
    caveats.push('your flagged primary supplier cannot be ordered from and was skipped');
  }
  return caveats;
};

/**
 * One line covering whichever of the three states this item is in: no field on
 * the wire, no supplier to buy from, or a supplier with caveats.
 *
 * Returns null for the uneventful case — a supplier was chosen and nothing
 * qualifies it — so a caller can render nothing at all rather than noise.
 */
export const supplierChoiceNote = (choice: SupplierChoice | undefined): string | null => {
  if (!choice) return SUPPLIER_CHOICE_UNKNOWN;
  if (choice.supplier_name === null) {
    return choice.reason === null
      ? SUPPLIER_CHOICE_UNKNOWN
      : (NO_SUPPLIER_TEXT[choice.reason] ?? SUPPLIER_CHOICE_UNKNOWN);
  }
  const caveats = supplierChoiceCaveats(choice);
  return caveats.length === 0 ? null : caveats.join('; ');
};
