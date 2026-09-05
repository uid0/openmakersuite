/**
 * The web's one reading of the server's vendor-data gate (op-anonymous-read-posture).
 *
 * The captain's decision — "Vendor names should not be public, same with Vendor
 * Pricing" — is enforced in `inventory/services/vendor_visibility.py`. Endpoints
 * that exist only to serve vendor data refuse an anonymous caller outright; the
 * ones the QR-scan flow needs stay open and OMIT their vendor keys, marking the
 * payload `vendor_data_withheld: true`.
 *
 * THE KEYS ARE ABSENT, NOT NULL, and that is what this module exists for. `null`
 * already means "no price on file" / "no supplier on this item" across these
 * payloads (op-9m2v), so the server could not use it for "not shown to you"
 * without saying something false about the item. The cost to the web is that a
 * guard spelled `=== null` no longer catches the case: `undefined === null` is
 * `false`, so `item.unit_cost.toFixed(2)` ran on `undefined` and took the item
 * page down for a logged-out visitor. Ask {@link vendorDataWithheld} before
 * rendering a vendor row, rather than widening each guard to `== null` — the
 * two conditions mean different things to a reader and should read differently.
 *
 * This module does NOT read auth state. The server has already decided, and the
 * payload says so; a second, client-side derivation of the same answer is how
 * the two come to disagree.
 *
 * WHAT A SURFACE DOES ABOUT IT LIVES HERE TOO, because there are exactly two
 * right answers and the bug is always picking neither. This class has now been
 * missed on five separate screens, each of which hand-rolled its own decision:
 *
 * - **DROP** — a table column or a CSV column. The header AND every cell go.
 *   An absent column cannot be misread as an empty value. Ask
 *   {@link vendorColumnsDropped} with the rows you are about to render.
 * - **LABEL** — a single detail row or cell, where dropping would leave a hole
 *   the reader cannot interpret. Render {@link VENDOR_WITHHELD_TEXT}. Ask
 *   {@link labelIfWithheld}.
 *
 * Choose by whether the surface repeats: a column repeats down a table and its
 * absence is legible; one cell on a detail card does not, so it says so.
 *
 * TWO THINGS A SURFACE MUST NEVER DO:
 *
 * 1. **Never render `—`, `-`, `N/A` or a blank for a WITHHELD value.** Every
 *    one of those means "nothing on file" — a claim about the ITEM where the
 *    truth is a fact about the READER. That single sentence is the whole class.
 * 2. **Never index a lookup table with a possibly-withheld key.**
 *    `TREND_LABEL[metrics.cost_trend]` printed the literal string `undefined`
 *    into a tooltip. Guard on the marker BEFORE the lookup, not after.
 */

/** Any payload that may carry the server's withheld marker. */
export interface MaybeVendorGated {
  vendor_data_withheld?: boolean;
}

/**
 * Whether the server withheld this payload's vendor block.
 *
 * `false` for a payload that never carried one, and for a signed-in caller's —
 * the marker is only ever added when the gate ran.
 */
export const vendorDataWithheld = (payload: MaybeVendorGated | null | undefined): boolean =>
  payload?.vendor_data_withheld === true;

/** What a surface says in place of a withheld vendor figure — the LABEL shape. */
export const VENDOR_WITHHELD_TEXT = 'Sign in to see supplier and pricing information';

/**
 * DROP: whether a table must render its vendor columns at all.
 *
 * `some` rather than row 0, because one response is gated as a whole — so this
 * agrees with row 0 whenever there is one, and stays `false` for an empty list,
 * where a dropped column would say nothing to nobody.
 *
 * Takes the rows a surface is ABOUT TO RENDER, never `isAuthenticated()`: a
 * token in localStorage is not the same answer as the payload's, and the
 * response interceptor clears that token on any background refresh failure, so
 * the two do come apart while a table is on screen.
 */
export const vendorColumnsDropped = (rows: readonly (MaybeVendorGated | null | undefined)[]) =>
  rows.some(vendorDataWithheld);

/**
 * LABEL: {@link VENDOR_WITHHELD_TEXT} where the gate ran, otherwise `render()`.
 *
 * `render` is a THUNK so the withheld branch never evaluates it — that is what
 * keeps a lookup, a `.toFixed()` or a template read off a key the server did
 * not send. Constraint 2 in the module header is the bug this shape prevents.
 */
export const labelIfWithheld = <T,>(
  payload: MaybeVendorGated | null | undefined,
  render: () => T
): T | string => (vendorDataWithheld(payload) ? VENDOR_WITHHELD_TEXT : render());
