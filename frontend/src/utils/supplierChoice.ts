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
 * Surfaces frame these values in their own layout, because they cannot share
 * one: a scan row dims the alternatives in a separate span and a CSV puts them
 * in their own column. What a surface supplies is the label and the container;
 * every name, count and sentence about the choice comes from here.
 *
 * AUDIENCE lives here too, and is not the caller's to word. `/inventory/scan`,
 * `/inventory/items` and both kit routes are reachable logged out, and most of
 * what this field carries is addressed to whoever maintains the supplier links
 * — "your flagged primary supplier cannot be ordered from" means nothing to a
 * member who has no flagged primary and no way to order. So a caller decides
 * WHO is reading ({@link SupplierAudience}) and asks for that reader's wording
 * — {@link supplierChoiceNote} / {@link alternativeSupplierNamesText} for an
 * operator, {@link publicSupplierChoiceNote} / {@link anonymousAlternativesNote}
 * for a visitor — and renders what comes back. It never assembles, joins,
 * counts or trims the wording itself.
 *
 * What each audience is granted is a decision this module records, not one it
 * makes: an operator gets the alternatives BY NAME; an anonymous visitor gets
 * a count on the item detail page and NOTHING about the alternatives anywhere
 * else, because widening anonymous disclosure is the requester's to authorise.
 * A surface that wants a count therefore has to ask for one by name.
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

/**
 * Who is reading a surface, which decides which wording it may render.
 *
 * The caller resolves this — a route component from `isAuthenticated()`, a pure
 * function such as the CSV export from its own caller — and passes it in. This
 * module never reads auth state, and nothing that takes an audience may guess
 * one by defaulting.
 */
export type SupplierAudience = 'operator' | 'anonymous';

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
 * The same two facts for a visitor who is not signed in.
 *
 * Still two distinct answers, because "we never recorded where this comes from"
 * and "we did, and none of it can be bought right now" are different things to
 * be told. What is dropped is the LINK STATE: an anonymous reader learns that
 * the item cannot be ordered, not how many vendors exist or why each was
 * rejected. Naming no vendor and describing no link is the whole difference.
 */
const PUBLIC_NO_SUPPLIER_TEXT: Record<string, string> = {
  no_suppliers: 'No supplier is listed for this item.',
  none_orderable: 'This item cannot currently be ordered.',
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
 * The other suppliers as one readable run of names — "Beta Parts, Gamma
 * Wholesale" — or null where there were none.
 *
 * FOR OPERATORS. Null rather than an empty string so a caller renders nothing
 * at all instead of a dangling "also available from"; the caller supplies that
 * lead-in and the markup around it, and never the joining or the emptiness
 * test, which is how the same list came to be joined three different ways.
 *
 * A public surface asks {@link anonymousAlternativesNote} instead, and gets a
 * count or nothing — never these names.
 */
export const alternativeSupplierNamesText = (choice: SupplierChoice | undefined): string | null => {
  const names = alternativeSupplierNames(choice);
  return names.length === 0 ? null : names.join(', ');
};

/**
 * How many OTHER suppliers stock this item, in words — "2 other suppliers also
 * stock this item." — or null where there were none.
 *
 * FOR A VISITOR WHO IS NOT SIGNED IN, and granted on ONE surface: the item
 * detail page. It says there were others without saying who, which is the
 * furthest anonymous disclosure has been authorised. The scan page is not
 * granted it — a logged-out scanner sees the chosen name, the lead time and
 * the price, exactly what they saw before `supplier_choice` existed, and no
 * indication that any other vendor exists.
 *
 * It lives here, rather than in the page, because it was written twice and the
 * two copies had already drifted apart by a full stop.
 */
export const anonymousAlternativesNote = (choice: SupplierChoice | undefined): string | null => {
  const others = alternativeSupplierNames(choice).length;
  if (others === 0) return null;
  return others === 1
    ? '1 other supplier also stocks this item.'
    : `${others} other suppliers also stock this item.`;
};

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
  if (!choice || chosenSupplierName(choice) === null) return [];
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
 * FOR OPERATORS — a signed-in surface. Returns null for the uneventful case (a
 * supplier was chosen and nothing qualifies it) so a caller can render nothing
 * at all rather than noise. A public surface asks
 * {@link publicSupplierChoiceNote} instead.
 */
export const supplierChoiceNote = (choice: SupplierChoice | undefined): string | null => {
  if (!choice) return SUPPLIER_CHOICE_UNKNOWN;
  if (chosenSupplierName(choice) === null) {
    const reason = choice.reason ?? null;
    return reason === null
      ? SUPPLIER_CHOICE_UNKNOWN
      : (NO_SUPPLIER_TEXT[reason] ?? SUPPLIER_CHOICE_UNKNOWN);
  }
  const caveats = supplierChoiceCaveats(choice);
  return caveats.length === 0 ? null : caveats.join('; ');
};

/**
 * The same line for a visitor who is not signed in — the no-supplier fact only.
 *
 * Null wherever a supplier WAS chosen, however it was chosen: the three
 * caveats report on the derivation and are addressed to whoever maintains the
 * links, so an anonymous reader gets the chosen name, the lead time and the
 * price exactly as they did before, and nothing about how that name was
 * reached. Null too when the field never arrived — "not included in this
 * response" is diagnostic copy about the payload, not a fact about the item.
 *
 * NOT null when there is nothing to buy from, which is the one half a member
 * can act on. An absent row there would make "we cannot get you this" look
 * like an ordinary item; stating the absence is the same discipline the rest
 * of this module keeps.
 */
export const publicSupplierChoiceNote = (choice: SupplierChoice | undefined): string | null => {
  if (!choice || chosenSupplierName(choice) !== null) return null;
  const reason = choice.reason ?? null;
  return reason === null ? null : (PUBLIC_NO_SUPPLIER_TEXT[reason] ?? null);
};
