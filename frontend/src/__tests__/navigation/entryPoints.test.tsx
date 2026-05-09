/**
 * Entry-point regression tests (AC-7).
 *
 * Asserts that every workspace called out in the spec has a registered
 * route map entry with a clickable, human-readable label. This is the
 * structural half of AC-7; the overview-render tests in
 * routeNavigation.test.tsx cover the "actually loads" half.
 *
 * If a future change removes one of these entry points without
 * deliberately replacing it, this test fails — preventing the
 * dead-link regression the spec calls out.
 */
import { getRouteEntry, isClickable } from '../../navigation/routeMap';

describe('AC-7 — top-level navigation entry points are present and clickable', () => {
  // Spec calls out: inventory/scan workflows, purchasing, assets,
  // facilities/operations dashboards, maintenance, reports, settings,
  // plus analytics (just shipped) and SIGs (existing workspace).
  const ENTRY_POINTS: Array<{ path: string; label: string }> = [
    { path: 'inventory', label: 'Inventory' },
    { path: 'inventory/scan', label: 'Scan Items' },
    { path: 'purchasing', label: 'Purchasing' },
    { path: 'assets', label: 'Assets' },
    { path: 'facilities', label: 'Facilities' },
    { path: 'maintenance', label: 'Maintenance' },
    { path: 'sigs', label: 'SIGs' },
    { path: 'reports', label: 'Reports' },
    { path: 'analytics', label: 'Analytics Pulse' },
    { path: 'settings', label: 'Settings' },
  ];

  it.each(ENTRY_POINTS)('$path is registered with the canonical label "$label"', ({ path, label }) => {
    const entry = getRouteEntry(path);
    expect(entry).toBeDefined();
    expect(entry!.label).toBe(label);
  });

  it.each(ENTRY_POINTS)('$path is clickable (no dead link)', ({ path }) => {
    expect(isClickable(path)).toBe(true);
  });
});
