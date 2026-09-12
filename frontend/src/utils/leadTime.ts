/**
 * What a supplier's lead time MEANS when nobody recorded one (op-lead-time-default).
 *
 * `ItemSupplier.average_lead_time` is a `PositiveIntegerField(default=7)` and
 * is NOT NULL, so the column has exactly three readings and this module is the
 * ONE place the web words them. Two surfaces each invented a different answer
 * before it existed — the purchase-order form rewrote a lead time as `7`, the
 * item form's relationship editor seeded a new row as `0` — which is what
 * happens when the question is answered per-surface instead of once.
 *
 * 1. **A number, including `0`, is the supplier's own figure.** `0` is a
 *    RECORDED answer: a counter-pickup vendor who hands the part over the same
 *    day. It is never a placeholder and never "unknown", so no reader may
 *    guard this column with truthiness — `|| 7`, `|| 0` and `if (days)` all
 *    turn a vendor you could walk to into one that takes a week, or drop the
 *    figure entirely. The backend removed the same guards from supplier
 *    scoring and from `receiving.create_lead_time_log`; see the
 *    alert-suppression class in `AGENTS.md`.
 * 2. **`null`/`undefined` is an ABSENCE**, and is said out loud rather than
 *    rendered as a number. It reaches the web where a payload names no
 *    supplier to quote a wait — an item with no link at all — never from a
 *    stored `ItemSupplier` row.
 * 3. **`7` is the system's PLANNING DEFAULT, and it belongs to the model.** A
 *    create that omits the key takes it; every write path does exactly that
 *    (the kit form's `supplier_terms`, `_sync_primary_supplier`, a bare POST
 *    to `/inventory/item-suppliers/`) so that the default has one definition.
 *    Do not restate the `7` as a frontend constant to seed a form with — an
 *    editor that has no figure yet sends no key, and
 *    {@link LEAD_TIME_DEFAULT_NOTE} tells the operator what will be used
 *    instead. The number in that sentence is prose about the server's
 *    behaviour, not a second source of the default.
 *
 * What this canNOT say, deliberately: whether a stored `7` is the default
 * nobody touched or a seven the supplier actually quoted. The column has no
 * room for that distinction, and giving it one is a migration over supplier
 * data rather than a rendering choice — filed as `oms-lead-time-nullable`.
 */

/** How an unrecorded lead time reads. One wording, every surface. */
export const LEAD_TIME_NOT_RECORDED = 'Not recorded';

/** What an operator who leaves the box empty is agreeing to. */
export const LEAD_TIME_DEFAULT_NOTE =
  'Leave blank to use the default of 7 days until this supplier quotes their own.';

/**
 * A recorded lead time in words, or `null` where none is recorded.
 *
 * Returning `null` rather than the "Not recorded" string keeps the CHOICE with
 * the caller: a table cell and a Mantine `<Text>` dim an absence differently,
 * and both should read the same words when they do.
 */
export const formatLeadTimeDays = (days: number | null | undefined): string | null =>
  typeof days === 'number' && Number.isFinite(days) ? `${days} day${days === 1 ? '' : 's'}` : null;

/** The same answer with the absence already worded, for a plain text node. */
export const leadTimeText = (days: number | null | undefined): string =>
  formatLeadTimeDays(days) ?? LEAD_TIME_NOT_RECORDED;
