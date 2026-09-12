import { LeadTimeProvenance } from '../types';

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
 * `average_lead_time_provenance` distinguishes a planning default from a
 * recorded figure. Rows predating that marker are `unknown`, which must never
 * be presented as either known state.
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
export const formatLeadTimeDays = (
  days: number | null | undefined,
  provenance?: LeadTimeProvenance | null
): string | null => {
  if (typeof days !== 'number' || !Number.isFinite(days)) return null;
  const value = `${days} day${days === 1 ? '' : 's'}`;
  if (provenance === 'default') return `${value} (planning default)`;
  if (provenance === 'unknown' || provenance == null) return `${value} (provenance unknown)`;
  return value;
};

/** The same answer with the absence already worded, for a plain text node. */
export const leadTimeText = (
  days: number | null | undefined,
  provenance?: LeadTimeProvenance | null
): string => formatLeadTimeDays(days, provenance) ?? LEAD_TIME_NOT_RECORDED;

export const aggregateLeadTimeText = (
  days: number | null | undefined,
  provenance?: LeadTimeProvenance | null
): string => {
  if (days == null || !Number.isFinite(days)) return LEAD_TIME_NOT_RECORDED;
  const value = `${days} day${days === 1 ? '' : 's'}`;
  if (provenance === 'default') return `${value} (includes planning default)`;
  if (provenance === 'unknown' || provenance == null) {
    return `${value} (includes unknown provenance)`;
  }
  return value;
};

export const leadTimeProvenanceText = (provenance?: LeadTimeProvenance | null): string => {
  if (provenance === 'default') return 'This stored value is the planning default.';
  if (provenance === 'unknown') return 'This stored value predates lead-time provenance.';
  return LEAD_TIME_DEFAULT_NOTE;
};
