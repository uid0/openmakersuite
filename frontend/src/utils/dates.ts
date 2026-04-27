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
