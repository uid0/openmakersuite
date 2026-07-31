/**
 * Date-only helpers.
 *
 * The browser's `new Date('2026-04-28')` interprets a bare ISO date as UTC
 * midnight, which then renders in the user's local time as the previous day
 * for any timezone west of UTC. For date-only fields (Django `DateField`)
 * we never want that round-trip — these helpers keep everything in local
 * Y/M/D space.
 */

const YMD_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * Parse a 'YYYY-MM-DD' string as local midnight on that calendar day.
 * Returns null for empty / invalid input. Strings containing time
 * components are NOT accepted — use `new Date(iso)` for datetimes.
 */
export function parseYmd(value: string | null | undefined): Date | null {
  if (!value) return null;
  const match = YMD_RE.exec(value);
  if (!match) return null;
  const [, y, m, d] = match;
  const date = new Date(Number(y), Number(m) - 1, Number(d));
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * Format a Date as 'YYYY-MM-DD' using its local fields. Use this when
 * submitting a date-only value to the backend so we don't accidentally
 * send the previous day after a UTC conversion.
 */
export function formatYmd(date: Date | null | undefined): string {
  if (!date || Number.isNaN(date.getTime())) return '';
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

/**
 * Add whole days to a 'YYYY-MM-DD' string, staying in local Y/M/D space so
 * DST transitions can't shift the answer. Returns '' for invalid input.
 */
export function addDaysToYmd(value: string | null | undefined, days: number): string {
  const date = parseYmd(value);
  if (!date) return '';
  date.setDate(date.getDate() + days);
  return formatYmd(date);
}

/**
 * The calendar day a value falls on **in UTC**, as 'YYYY-MM-DD'.
 *
 * Some Django `DateTimeField`s carry a business *date* rather than a moment —
 * `PurchaseOrder.order_date` is one: the server derives the payment schedule
 * from `order_date.date()`, and `TIME_ZONE` is UTC. Editing such a field as a
 * date means editing the day the server reasons about, not the day the
 * viewer's timezone happens to render. A bare 'YYYY-MM-DD' passes through.
 */
export function utcYmd(value: string | Date | null | undefined): string {
  if (!value) return '';
  if (typeof value === 'string' && YMD_RE.test(value)) return value;
  const date = typeof value === 'string' ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return '';
  return date.toISOString().slice(0, 10);
}

/**
 * The ISO datetime to send for a date-only edit of a UTC business-date field
 * (see `utcYmd`). Midday UTC, so the day survives both directions: the server
 * stores exactly the day picked, and every timezone from UTC-11 to UTC+11
 * renders it back as that same day.
 */
export function ymdToUtcDateTime(value: string): string {
  return `${value}T12:00:00Z`;
}

const DEFAULT_DISPLAY_OPTS: Intl.DateTimeFormatOptions = {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
};

/**
 * Render a date-only string ('YYYY-MM-DD') for display in the user's
 * local locale without the off-by-one drift that
 * `new Date('YYYY-MM-DD').toLocaleDateString()` causes in TZs west of UTC.
 *
 * Strings with a time component (e.g. ISO datetimes from `DateTimeField`)
 * fall through to the default `Date` parser, which is correct for those.
 */
export function formatDateOnly(
  value: string | null | undefined,
  options: Intl.DateTimeFormatOptions = DEFAULT_DISPLAY_OPTS,
  fallback = '—',
  locale: string | string[] = 'en-US',
): string {
  if (!value) return fallback;
  const date = YMD_RE.test(value) ? parseYmd(value) : new Date(value);
  if (!date || Number.isNaN(date.getTime())) return fallback;
  return date.toLocaleDateString(locale, options);
}
