/**
 * Unit tests for the route-map helpers powering Breadcrumbs.
 *
 * Companion: routeNavigation.test.tsx exercises the actual rendering of
 * the previously-404 parent paths.
 */
import {
  ROUTE_MAP_ENTRIES,
  getRouteEntry,
  isClickable,
  labelFor,
  titleCaseSegment,
} from '../../navigation/routeMap';

describe('routeMap helpers', () => {
  describe('getRouteEntry', () => {
    it('returns the entry for a registered path', () => {
      const entry = getRouteEntry('inventory/assets');
      expect(entry).toBeDefined();
      expect(entry?.label).toBe('Assets');
    });

    it('returns undefined for an unregistered path', () => {
      expect(getRouteEntry('not/a/real/path')).toBeUndefined();
    });
  });

  describe('labelFor', () => {
    it('uses canonical label when registered', () => {
      expect(labelFor('purchasing', 'purchasing')).toBe('Purchasing');
      expect(labelFor('facilities/tv-dashboard', 'tv-dashboard')).toBe('TV Dashboard');
    });

    it('falls back to title-cased segment when unregistered', () => {
      expect(labelFor('not/in/map', 'foo-bar-baz')).toBe('Foo Bar Baz');
    });
  });

  describe('isClickable', () => {
    it('defaults to true for registered entries', () => {
      expect(isClickable('inventory')).toBe(true);
      expect(isClickable('purchasing')).toBe(true);
    });

    it('respects clickable: false on registered entries', () => {
      // /maintenance/work-orders has no list page (only /maintenance/work-orders/:id),
      // so the segment must render as a non-clickable label.
      expect(isClickable('maintenance/work-orders')).toBe(false);
      expect(isClickable('settings/tax-receipt')).toBe(false);
    });

    it('defaults to true for unregistered paths', () => {
      // Unregistered paths fall through; if they're truly orphaned they will
      // be caught by the routeNavigation regression test instead.
      expect(isClickable('something/random')).toBe(true);
    });
  });

  describe('titleCaseSegment', () => {
    it('handles single words', () => {
      expect(titleCaseSegment('inventory')).toBe('Inventory');
    });

    it('handles hyphenated slugs', () => {
      expect(titleCaseSegment('tv-dashboard')).toBe('Tv Dashboard');
    });

    it('handles empty string', () => {
      expect(titleCaseSegment('')).toBe('');
    });
  });

  describe('AC-5 canonical labels per major workspace', () => {
    // Smoke test that the map has at least one entry covering every
    // top-level workspace called out in the spec. If this fails, a
    // workspace was removed from the route map and breadcrumbs in that
    // workspace will fall back to slug-formatted labels.
    const required = [
      'inventory',
      'purchasing',
      'assets',
      'facilities',
      'maintenance',
      'sigs',
      'reports',
      'settings',
      'analytics',
    ];
    it.each(required)('has a label for %s', (workspace) => {
      const entry = getRouteEntry(workspace);
      expect(entry).toBeDefined();
      expect(entry!.label.length).toBeGreaterThan(0);
    });
  });

  describe('ROUTE_MAP_ENTRIES', () => {
    it('has no duplicate paths', () => {
      const paths = ROUTE_MAP_ENTRIES.map((e) => e.path);
      const unique = new Set(paths);
      expect(unique.size).toBe(paths.length);
    });
  });
});
