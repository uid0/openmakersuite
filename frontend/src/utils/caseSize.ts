/**
 * The web's one wording of an unknown case size (op-2t4e).
 *
 * `backend/inventory/services/pack_size.py` decides WHICH unknown an item's case
 * size is in and serializes that state — `case_size_state` beside
 * `current_cases` on the item payload, and beside `case_size` on the metrics
 * payload. This file is the single place the web turns that state into words.
 *
 * Nothing here re-derives the state. It does not read `quantity_per_package`, or
 * count supplier links, or check `is_active` / `is_discontinued` to work out
 * what the server already said — that re-derivation is what the server exists to
 * make unnecessary, and a second copy of the rule here would be free to drift
 * from the one that actually decides.
 *
 * THE DEFECT THIS CLOSES: three web surfaces said `— (case size unknown)` and
 * one said a bare em dash, for every unknown alike. An operator reading that
 * could not tell whether they were supplying a fact nobody had recorded or
 * correcting one somebody had recorded wrong — two different problems wearing
 * one sentence, which is the same conflation as reporting "found nothing" where
 * the truth is "could not tell".
 *
 * SO EVERY WORDING BELOW NAMES AN ACTION. "Case size not recorded" states the
 * problem and leaves the operator where they started; what they are owed is the
 * next thing to do, and the remedies differ — add a supplier link, correct one,
 * or revive one. The register matches `ScanPage`'s existing `packSizeRefusal`,
 * which already names `"Quantity per Package"` on a member-facing page, because
 * naming the field is what makes the sentence followable.
 *
 * The server sends the STATE, never the sentence. What to say about
 * `recorded_zero` is a client's decision: the terminal has its own screens and
 * its own room, and a server that shipped display text would have decided for
 * both.
 */

import { CaseSizeState } from '../types';

/**
 * Re-exported so a surface wording a state imports the type from the module
 * that words it. `no_orderable_link` reaches the METRICS payload only: the item
 * payload asks the shelf question — how many units are in the box already
 * sitting there — and a dead vendor's recorded pack size still answers it.
 */
export type { CaseSizeState };

/**
 * What an operator should DO about each unknown, in one sentence.
 *
 * Keyed by the server's state, so adding a state there surfaces here as a
 * missing key rather than as a screen quietly falling back to the old
 * one-size-fits-all sentence.
 */
const UNKNOWN_CASE_SIZE_ACTIONS: Record<string, string> = {
  not_recorded:
    'No supplier link records how many units a case holds. Add a supplier ' +
    'relationship for this item, or set "Quantity per Package" on the one it has.',
  recorded_zero:
    'A supplier link records a case of 0 units — a box holding nothing. ' +
    'Correct "Quantity per Package" on that supplier relationship.',
  no_orderable_link:
    'Every supplier link for this item is inactive or discontinued, so nothing ' +
    'we can order from gives a case size. Reactivate a supplier relationship, ' +
    'or add a vendor that still carries this item.',
};

/**
 * The fallback for a state this build has never heard of.
 *
 * Deliberately not the old `— (case size unknown)`: an unrecognised state is a
 * client too old for the server it is talking to, which is a different fact
 * again from either unknown, and dressing it as one of them would be this
 * module's own conflation.
 */
export const CASE_SIZE_STATE_UNRECOGNISED =
  'The case size is unknown, and this page does not recognise the reason the ' +
  'server gave. Ask a member of staff to check this item’s supplier links.';

/** Whether the server says the case size is a usable number. */
export const caseSizeIsKnown = (state: string | undefined): boolean => state === 'known';

/**
 * The full sentence for an unknown case size, or null when it is known.
 *
 * Null — not an empty string — so a caller renders nothing at all rather than an
 * empty tooltip or a dangling parenthesis.
 *
 * `undefined` means the server did not send the field, which is a backend too
 * old for this page rather than anything about the item, and it gets the
 * unrecognised wording for the same reason: three states must not become two.
 */
export const caseSizeUnknownNote = (state: string | undefined): string | null => {
  if (caseSizeIsKnown(state)) return null;
  if (state === undefined) return CASE_SIZE_STATE_UNRECOGNISED;
  return UNKNOWN_CASE_SIZE_ACTIONS[state] ?? CASE_SIZE_STATE_UNRECOGNISED;
};

/**
 * The short form for a table cell or an inline value — "— (case size not
 * recorded)" — with the sentence above carried as a `title` by the caller.
 *
 * Short and long say the same thing at two lengths on purpose. A row that had
 * room only for the old em dash still distinguishes the states, and the surface
 * decides how much room it has rather than this module guessing.
 */
const UNKNOWN_CASE_SIZE_LABELS: Record<string, string> = {
  not_recorded: '— (case size not recorded)',
  recorded_zero: '— (case size recorded as 0 — needs correcting)',
  no_orderable_link: '— (no orderable supplier records a case size)',
};

/** The em-dash form, or null when the case size is known. */
export const caseSizeUnknownLabel = (state: string | undefined): string | null => {
  if (caseSizeIsKnown(state)) return null;
  return UNKNOWN_CASE_SIZE_LABELS[state ?? ''] ?? '— (case size unknown)';
};
