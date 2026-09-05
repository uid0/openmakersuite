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

/** What a surface says in place of a withheld vendor figure. */
export const VENDOR_WITHHELD_TEXT = 'Sign in to see supplier and pricing information';
