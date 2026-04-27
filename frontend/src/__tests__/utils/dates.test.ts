import { formatDateOnly, formatYmd, parseYmd } from '../../utils/dates';

/**
 * These tests target the off-by-one date bug (oms-06i): in any timezone west
 * of UTC, `new Date('YYYY-MM-DD')` parses as UTC midnight, then renders as
 * the previous calendar day in the user's local time. The test suite is
 * pinned to TZ=America/Chicago via package.json so the bug would reproduce
 * if the helpers are wrong.
 */
describe('utils/dates', () => {
  it('runs in a TZ that would expose the off-by-one bug', () => {
    // Sanity check: confirm we're in a TZ where the naive parse is wrong.
    // If this assertion ever fails, the test environment changed and the
    // round-trip tests below stop being meaningful.
    const naive = new Date('2026-04-28');
    expect(naive.getDate()).toBe(27);
  });

  describe('parseYmd', () => {
    it('parses a YYYY-MM-DD string at local midnight on that calendar day', () => {
      const d = parseYmd('2026-04-28');
      expect(d).not.toBeNull();
      expect(d!.getFullYear()).toBe(2026);
      expect(d!.getMonth()).toBe(3); // April (0-indexed)
      expect(d!.getDate()).toBe(28);
      expect(d!.getHours()).toBe(0);
    });

    it('returns null for empty / invalid input', () => {
      expect(parseYmd('')).toBeNull();
      expect(parseYmd(null)).toBeNull();
      expect(parseYmd(undefined)).toBeNull();
      expect(parseYmd('not-a-date')).toBeNull();
      expect(parseYmd('2026-04-28T10:00:00Z')).toBeNull();
    });
  });

  describe('formatYmd', () => {
    it('serializes a Date using its local Y/M/D fields', () => {
      const d = new Date(2026, 3, 28); // local 2026-04-28
      expect(formatYmd(d)).toBe('2026-04-28');
    });

    it('preserves the local calendar day even at the end-of-day boundary', () => {
      // 23:30 local on Apr 28 — `toISOString()` would push this into UTC
      // Apr 29 in TZs east of UTC. formatYmd reads local fields, so it
      // always returns the local calendar date.
      const lateNight = new Date(2026, 3, 28, 23, 30);
      expect(formatYmd(lateNight)).toBe('2026-04-28');
    });

    it('handles null/invalid input', () => {
      expect(formatYmd(null)).toBe('');
      expect(formatYmd(undefined)).toBe('');
      expect(formatYmd(new Date('not-a-date'))).toBe('');
    });
  });

  describe('formatDateOnly', () => {
    it('renders a YYYY-MM-DD string as the same calendar day', () => {
      // Naive `new Date('2026-04-28').toLocaleDateString('en-US', ...)`
      // would render "Apr 27, 2026" in TZ=America/Chicago. The helper
      // must render "Apr 28, 2026".
      expect(
        formatDateOnly('2026-04-28', { year: 'numeric', month: 'short', day: 'numeric' }),
      ).toBe('Apr 28, 2026');
    });

    it('round-trips: save Apr 28 → read Apr 28 (no off-by-one)', () => {
      // Simulate the user flow: pick Apr 28 in a date input, submit, store
      // as 'YYYY-MM-DD', read back, format for display.
      const picked = new Date(2026, 3, 28);
      const submitted = formatYmd(picked);
      expect(submitted).toBe('2026-04-28');
      const displayed = formatDateOnly(submitted, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      });
      expect(displayed).toBe('Apr 28, 2026');
    });

    it('falls through to standard parsing for ISO datetime strings', () => {
      // Datetime fields (DateTimeField) come back with time + offset.
      // Those are correctly handled by `new Date(iso)` and should still
      // render a valid date.
      const result = formatDateOnly('2026-04-28T15:30:00Z', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      });
      expect(result).toMatch(/Apr (27|28), 2026/);
    });

    it('returns the fallback for null / empty / invalid input', () => {
      expect(formatDateOnly(null)).toBe('—');
      expect(formatDateOnly(undefined)).toBe('—');
      expect(formatDateOnly('')).toBe('—');
      expect(formatDateOnly('not-a-date')).toBe('—');
      expect(formatDateOnly(null, undefined, 'N/A')).toBe('N/A');
    });
  });
});
